"""Explicitly approved PBS backup runs for protected VMs.

This is a real PVE/PBS snapshot BACKUP, not cross-cluster replication, restore,
witness fencing, hot-standby, or automatic failover. No VM start/stop is issued.
"""
import re
import httpx
from urllib.parse import quote
from . import config, db, dr_protected_vms, integrations


class PBSBackupError(ValueError):
    pass


def _source(vm):
    from .executor import credential
    dc=integrations.configured_datacenter(vm["source_dc_id"])
    stored=credential(dc["credential_id"])
    cred=dict(stored,ca_cert=dc.get("ca_cert") or stored.get("ca_cert") or "",
              skip_tls_verification=bool(dc.get("skip_tls_verification",False)))
    return dc,cred


def start_backup(vm_id, storage, approved, actor):
    if approved is not True:
        raise PBSBackupError("Validation explicite requise")
    if not config.ALLOW_PROXMOX_ACTIONS:
        raise PBSBackupError("Activer ALLOW_PROXMOX_ACTIONS pour autoriser les sauvegardes PVE")
    if not isinstance(storage,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}",storage):
        raise PBSBackupError("Identifiant stockage PBS invalide")
    vm=dr_protected_vms._one(vm_id)
    if vm["state"]!="planned" or vm["engine"]!="pbs_backup_restore":
        raise PBSBackupError("Protection active et stratégie PBS requises")
    dc,cred=_source(vm)
    pbs=integrations.proxmox_request(dc["url"],cred,"/api2/json/storage",18)
    if not isinstance(pbs,list) or not any(
        p.get("storage")==storage and p.get("type")=="pbs" and
        "backup" in str(p.get("content") or "").split(",")
        for p in pbs if isinstance(p,dict)
    ):
        raise PBSBackupError("Le stockage sélectionné doit être un PBS avec contenu backup")
    current=integrations.discover_datacenter(dc,cred,18)
    guests=[g for g in current if g.get("type")=="qemu"
            and g.get("vmid")==vm["source_vmid"]]
    if len(guests)!=1:
        raise PBSBackupError("VM absente ou non visible dans le cluster source")
    node=str(guests[0].get("node") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}",node):
        raise PBSBackupError("Nœud courant invalide")
    # DB reservation precedes the network call. If API times out after PVE
    # accepted a request, mark uncertain and require reconciliation.
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        pending=con.execute(
            "SELECT id FROM pra_pbs_backups WHERE protected_vm_id=? "
            "AND status IN ('starting','submitted','running','uncertain')",
            (vm_id,)
        ).fetchone()
        if pending:
            raise PBSBackupError("Sauvegarde déjà lancée ou état indéterminé : vérifier son statut")
        job=con.execute(
            "INSERT INTO pra_pbs_backups(protected_vm_id,storage,node,status,actor,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (vm_id,storage,node,"starting",actor,db.now())
        ).lastrowid
    try:
        upid=integrations.proxmox_request(
            dc["url"],cred,"/api2/json/nodes/"+quote(node)+"/vzdump",
            30,method="POST",
            data={"vmid":str(vm["source_vmid"]),"storage":storage,"mode":"snapshot"}
        )
        if not isinstance(upid,str) or not upid.startswith("UPID:"):
            raise PBSBackupError("Proxmox n'a pas renvoyé d'identifiant de tâche valide")
    except httpx.HTTPStatusError:
        # An explicit PVE rejection is retryable after correcting the cause.
        # Do not expose potentially sensitive remote response text.
        with db.session() as con:
            con.execute("UPDATE pra_pbs_backups SET status='failed',finished_at=? WHERE id=?",
                        (db.now(),job))
        raise PBSBackupError("Proxmox a refusé la sauvegarde PBS : vérifier stockage, droits et état de la VM") from None
    except Exception:
        with db.session() as con:
            con.execute("UPDATE pra_pbs_backups SET status='uncertain' WHERE id=?",(job,))
        raise PBSBackupError("Soumission PVE indéterminée : vérifier les tâches sur le cluster avant de réessayer") from None
    with db.session() as con:
        con.execute("UPDATE pra_pbs_backups SET status='submitted',upid=? WHERE id=?",
                    (upid,job))
    db.audit(actor,"pra.pbs.backup.start",vm_id,
             "job="+str(job)+" storage="+storage+" source_node="+node)
    return {"job_id":job,"status":"submitted","node":node,"upid":upid,
            "replica_created":False,"backup_verified":False,
            "note":"Sauvegarde PBS demandée. Attendre la fin de la tâche et vérifier la restauration."}


def status(vm_id, job_id):
    vm=dr_protected_vms._one(vm_id)
    with db.session() as con:
        row=con.execute(
            "SELECT * FROM pra_pbs_backups WHERE id=? AND protected_vm_id=?",
            (job_id,vm_id)
        ).fetchone()
    if not row:
        raise PBSBackupError("Tâche PBS introuvable")
    record=dict(row)
    if not record["upid"]:
        return {"job_id":job_id,"status":record["status"],"backup_verified":False,
                "replica_created":False}
    dc,cred=_source(vm)
    task=integrations.proxmox_request(
        dc["url"],cred,
        "/api2/json/nodes/"+quote(record["node"])+"/tasks/"+quote(record["upid"],safe="")+"/status",18)
    if not isinstance(task,dict):
        raise PBSBackupError("Statut de tâche Proxmox invalide")
    completed=task.get("status")=="stopped"
    exitstatus=str(task.get("exitstatus") or "")
    state=("completed" if exitstatus=="OK" else "failed") if completed else "running"
    with db.session() as con:
        con.execute("UPDATE pra_pbs_backups SET status=?,finished_at=CASE WHEN ? THEN COALESCE(finished_at,?) ELSE finished_at END "
                    "WHERE id=?",(state,int(completed),db.now(),job_id))
    return {"job_id":job_id,"status":state,"exitstatus":exitstatus[:100],
            "backup_verified":completed and exitstatus=="OK",
            "replica_created":False,
            "note":"Un backup terminé ne prouve pas qu'une VM secours est restaurée ou démarrable."}


def list_backups(vm_id):
    """Return the existing per-VM PBS jobs without contacting hypervisors."""
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        rows=con.execute(
            "SELECT id,storage,node,status,created_at,finished_at "
            "FROM pra_pbs_backups WHERE protected_vm_id=? ORDER BY id DESC LIMIT 30",
            (vm_id,)
        ).fetchall()
    return {"items":[dict(row) for row in rows]}
