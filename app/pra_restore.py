"""Real PBS -> Proxmox standby materialization for a TEST VM.

Requires a completed PBS backup and a target-cluster PBS storage exposing
that exact backup snapshot (shared datastore or pre-synchronized PBS).
Never powers on, overwrites or deletes VMs. This is not realtime replication,
automatic standby refresh, nor safe unattended failover.
"""
import re
import httpx
from datetime import datetime
from urllib.parse import quote
from . import db, config, integrations, dr_protected_vms, pra_pbs


class RestoreError(ValueError):
    pass


def _target(vm):
    from .executor import credential
    dc=integrations.configured_datacenter(vm["target_dc_id"])
    saved=credential(dc["credential_id"])
    cred=dict(saved,ca_cert=dc.get("ca_cert") or saved.get("ca_cert") or "",
              skip_tls_verification=bool(dc.get("skip_tls_verification",False)))
    return dc,cred


def _pve(dc,cred,path,method="GET",data=None):
    return integrations.proxmox_request(dc["url"],cred,path,20,method=method,data=data)


def _backup_volumes(dc,cred,node,storage):
    path="/api2/json/nodes/"+quote(node)+"/storage/"+quote(storage)+"/content?content=backup"
    result=_pve(dc,cred,path)
    if not isinstance(result,list):
        raise RestoreError("Inventaire PBS indisponible sur le nœud de destination")
    return result


def _path_segment(s):
    return isinstance(s,str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}",s)


def options(vm_id,backup_job_id,target_pbs_storage):
    vm=dr_protected_vms._one(vm_id)
    if vm["engine"]!="pbs_backup_restore" or vm["state"]!="planned":
        raise RestoreError("Plan PBS requis et protection non suspendue")
    if not _path_segment(target_pbs_storage):
        raise RestoreError("Stockage PBS destination invalide")
    with db.session() as con:
        backup=con.execute(
            "SELECT id,status,storage,node,created_at FROM pra_pbs_backups WHERE id=? AND protected_vm_id=?",
            (backup_job_id,vm_id)
        ).fetchone()
    if not backup or backup["status"]!="completed":
        raise RestoreError("Sauvegarde source terminée requise; vérifier d'abord son UPID")
    source_dc,source_cred=pra_pbs._source(vm)
    # Only offer snapshots that are actually present in the source backup
    # datastore and created after this backup job was submitted.
    source_volumes=_backup_volumes(source_dc,source_cred,backup["node"],backup["storage"])
    cutoff=datetime.fromisoformat(backup["created_at"]).timestamp()-300
    source_versions={}
    for v in source_volumes:
        volid=v.get("volid","") if isinstance(v,dict) else ""
        ctime=v.get("ctime") if isinstance(v,dict) else None
        prefix=backup["storage"]+":backup/vm/"+str(vm["source_vmid"])+"/"
        if not isinstance(volid,str) or not volid.startswith(prefix) or not isinstance(ctime,(int,float)) or ctime<cutoff:
            continue
        source_versions[volid.split(":",1)[1]]=ctime
    if not source_versions:
        raise RestoreError("Aucun snapshot récent de ce job trouvé sur le stockage PBS du cluster source")
    dc,cred=_target(vm)
    resources=integrations.discover_datacenter(dc,cred,20)
    if any(x.get("type")=="qemu" and x.get("vmid")==vm["target_vmid"] for x in resources):
        raise RestoreError("VMID secours déjà utilisé : aucun remplacement automatique")
    if not any(x.get("type")=="node" and x.get("node")==vm["target_node"]
               and x.get("status")=="online" for x in resources):
        raise RestoreError("Nœud cible hors ligne ou absent")
    storages=_pve(dc,cred,"/api2/json/storage")
    if not isinstance(storages,list) or not any(
        p.get("storage")==target_pbs_storage and p.get("type")=="pbs"
        and "backup" in str(p.get("content") or "").split(",") for p in storages if isinstance(p,dict)
    ):
        raise RestoreError("Stockage source PBS non visible comme stockage backup sur le cluster cible")
    # Validate the QEMU image destination: a checked-in planned storage is
    # not a guarantee it still exists when the actual restore is submitted.
    if not any(p.get("storage")==vm["target_storage"] and
               "images" in str(p.get("content") or "").split(",")
               for p in storages if isinstance(p,dict)):
        raise RestoreError("Stockage VM cible n'accepte pas les images")
    entries=_backup_volumes(dc,cred,vm["target_node"],target_pbs_storage)
    matching=[]
    for v in entries:
        if not isinstance(v,dict):
            continue
        volid=v.get("volid","")
        if not isinstance(volid,str) or not volid.startswith(target_pbs_storage+":backup/vm/"+str(vm["source_vmid"])+"/"):
            continue
        suffix=volid.split(":",1)[1]
        if suffix not in source_versions or v.get("ctime")!=source_versions[suffix]:
            continue
        matching.append({"volid":volid,"ctime":v.get("ctime"),"size":v.get("size")})
    matching.sort(key=lambda v:v.get("ctime") or 0,reverse=True)
    return {"vmid":vm["source_vmid"],"target_vmid":vm["target_vmid"],
            "target_node":vm["target_node"],"target_storage":vm["target_storage"],
            "pbs_storage":target_pbs_storage,"archives":matching[:25],
            "target_is_free":True,
            "note":"Les snapshots listés sont visibles depuis le PBS cible. Confirmer leur provenance avant restauration."}


def start_restore(vm_id, backup_job_id, target_pbs_storage, archive, approved, actor):
    if approved is not True or not config.ALLOW_PROXMOX_ACTIONS:
        raise RestoreError("Approbation et ALLOW_PROXMOX_ACTIONS obligatoires")
    if not isinstance(archive,str) or len(archive)>300 or not re.fullmatch(r"[A-Za-z0-9_.:/-]+",archive):
        raise RestoreError("Référence archive invalide")
    opts=options(vm_id,backup_job_id,target_pbs_storage)
    if archive not in {v["volid"] for v in opts["archives"]}:
        raise RestoreError("Archive absente de la liste PBS validée sur le cluster destination")
    vm=dr_protected_vms._one(vm_id)
    dc,cred=_target(vm)
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        active=con.execute(
            "SELECT id FROM pra_pbs_restores WHERE protected_vm_id=? "
            "AND status IN ('starting','submitted','running','uncertain')",(vm_id,)
        ).fetchone()
        if active:
            raise RestoreError("Une restauration est déjà active ou indéterminée")
        job_id=con.execute(
            "INSERT INTO pra_pbs_restores(protected_vm_id,backup_job_id,pbs_storage,"
            "archive,node,target_vmid,target_storage,actor,status,created_at)"
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (vm_id,backup_job_id,target_pbs_storage,archive,vm["target_node"],
             vm["target_vmid"],vm["target_storage"],actor,"starting",db.now())
        ).lastrowid
    # Recheck VM collision as near as possible to the mutating operation.
    try:
        resources=integrations.discover_datacenter(dc,cred,20)
        if any(x.get("type")=="qemu" and x.get("vmid")==vm["target_vmid"] for x in resources):
            raise RestoreError("Collision VMID cible pendant la préparation; aucune restauration lancée")
    except Exception:
        with db.session() as con:
            con.execute("UPDATE pra_pbs_restores SET status='failed',finished_at=? WHERE id=?",(db.now(),job_id))
        raise
    try:
        upid=_pve(dc,cred,"/api2/json/nodes/"+quote(vm["target_node"])+"/qemu",method="POST",
                   data={"vmid":str(vm["target_vmid"]),"archive":archive,
                         "storage":vm["target_storage"],"start":"0","unique":"1",
                         "onboot":"0","ha-managed":"0"})
        if not isinstance(upid,str) or not upid.startswith("UPID:"):
            raise RestoreError("La restauration n'a pas retourné d'identifiant de tâche Proxmox")
    except httpx.HTTPStatusError:
        with db.session() as con:
            con.execute("UPDATE pra_pbs_restores SET status='failed',finished_at=? WHERE id=?",(db.now(),job_id))
        raise RestoreError("Restauration refusée par Proxmox; vérifier les permissions, le stockage et les collisions") from None
    except Exception:
        with db.session() as con:
            con.execute("UPDATE pra_pbs_restores SET status='uncertain' WHERE id=?",(job_id,))
        raise RestoreError("État de soumission incertain : contrôler les tâches Proxmox et le VMID cible") from None
    with db.session() as con:
        con.execute("UPDATE pra_pbs_restores SET status='submitted',upid=? WHERE id=?",(upid,job_id))
    db.audit(actor,"pra.pbs.restore.start",vm_id,"job="+str(job_id)+", node="+vm["target_node"])
    return {"restore_job_id":job_id,"status":"submitted","upid":upid,
            "target_vmid":vm["target_vmid"],"will_start":False,
            "note":"Restauration réelle lancée. AUCUNE bascule ou démarrage autorisés."}


def restore_status(vm_id,job_id):
    vm=dr_protected_vms._one(vm_id)
    with db.session() as con:
        row=con.execute("SELECT * FROM pra_pbs_restores WHERE id=? AND protected_vm_id=?",
                        (job_id,vm_id)).fetchone()
    if not row:
        raise RestoreError("Restauration inconnue")
    record=dict(row)
    if not record["upid"]:
        return {"status":record["status"],"restored":False}
    dc,cred=_target(vm)
    task=_pve(dc,cred,"/api2/json/nodes/"+quote(record["node"])+"/tasks/"+
              quote(record["upid"],safe="")+"/status")
    if not isinstance(task,dict):
        raise RestoreError("Statut PVE invalide")
    completed=task.get("status")=="stopped"
    state=("completed" if task.get("exitstatus")=="OK" else "failed") if completed else "running"
    # A completed task is not enough; the actual target VM must exist and be stopped.
    verified=False
    if state=="completed":
        resources=integrations.discover_datacenter(dc,cred,20)
        entries=[r for r in resources if r.get("type")=="qemu" and r.get("vmid")==record["target_vmid"]]
        if len(entries)!=1 or entries[0].get("status")!="stopped":
            state="uncertain"
        else:
            # Fail closed: standby must have no automatic boot before reporting
            # success, even if backup archive retained an onboot setting.
            conf=_pve(dc,cred,"/api2/json/nodes/"+quote(record["node"])+
                      "/qemu/"+str(record["target_vmid"])+"/config")
            if not isinstance(conf,dict) or str(conf.get("onboot",0)) not in ("0","False","false"):
                state="uncertain"
            else:
                verified=True
    with db.session() as con:
        con.execute("UPDATE pra_pbs_restores SET status=?,finished_at=CASE WHEN ? THEN COALESCE(finished_at,?) ELSE finished_at END WHERE id=?",
                    (state,int(state in ("completed","failed")),db.now(),job_id))
    return {"restore_job_id":job_id,"status":state,"target_vmid":record["target_vmid"],
            "restored":verified,"running":False if verified else None,
            "failover_ready":False,"fencing_verified":False,
            "note":"VM secours arrêtée. Son démarrage reste interdit tant que le fencing n'est pas validé."}


def list_restores(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        rows=con.execute("SELECT id,status,archive,target_vmid,created_at,finished_at "
                         "FROM pra_pbs_restores WHERE protected_vm_id=? ORDER BY id DESC LIMIT 20",(vm_id,)).fetchall()
    return {"items":[dict(r) for r in rows]}
