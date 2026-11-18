"""Authenticated per-VM status for the offline direct incremental pilot.

Agent reports are historical observations, not proof of a consistent replica
or authorization to start the standby VM. Only an agent assigned to the
source PRA site and whose host is in the configured source cluster may report.
"""
import re
import uuid
from datetime import datetime, timezone, timedelta
from . import db, integrations, dr_protected_vms
from .executor import credential


class DirectSyncError(ValueError):
    pass


def _node_authorized(agent,vm):
    # Site binding + source cluster inventory prevents agents from another
    # PRA group from claiming a sync on this protection.
    with db.session() as con:
        site=con.execute("SELECT 1 FROM dr_site_agents WHERE site_id=? AND agent_id=?",
                         (vm["source_site_id"],agent["id"])).fetchone()
    if not site:
        raise DirectSyncError("Agent non autorisé sur le site principal de cette VM")
    dc=integrations.configured_datacenter(vm["source_dc_id"])
    stored=credential(dc["credential_id"])
    cre=dict(stored,ca_cert=dc.get("ca_cert") or stored.get("ca_cert") or "",
             skip_tls_verification=bool(dc.get("skip_tls_verification",False)))
    guests=integrations.discover_datacenter(dc,cre,18)
    found=[g for g in guests if g.get("type")=="qemu" and g.get("vmid")==vm["source_vmid"]]
    if len(found)!=1:
        raise DirectSyncError("VM absente du cluster source")
    node=str(found[0].get("node") or "").lower().split(".")[0]
    host=str(agent.get("hostname") or "").lower().split(".")[0]
    if not host or node!=host:
        raise DirectSyncError("Agent non installé sur le nœud source actuel de la VM")


def report(agent,payload):
    if not isinstance(payload,dict) or set(payload)!={"protected_vm_id","source_vmid","target_vmid","run_uuid","status",
                                                      "disks_total","disks_done",
                                                      "scanned_bytes","transferred_bytes","elapsed_seconds","error"}:
        raise DirectSyncError("Rapport incrémental invalide")
    vmid=payload["protected_vm_id"]
    run_uuid=payload["run_uuid"]
    status=payload["status"]
    if type(vmid) is not int or vmid<=0:
        raise DirectSyncError("Protection invalide")
    try:
        if str(uuid.UUID(run_uuid))!=run_uuid:
            raise ValueError()
    except (ValueError,TypeError):
        raise DirectSyncError("Identifiant de synchronisation invalide") from None
    if status not in ("running","success","failed"):
        raise DirectSyncError("État de synchronisation invalide")
    numbers={}
    for k,maxval in (("disks_total",64),("disks_done",64),
                     ("scanned_bytes",2**56),("transferred_bytes",2**56),("elapsed_seconds",604800)):
        value=payload[k]
        if value is not None and (type(value) is not int or not 0<=value<=maxval):
            raise DirectSyncError("Métrique "+k+" invalide")
        numbers[k]=value or 0
    if numbers["disks_done"]>numbers["disks_total"] and numbers["disks_total"]>0:
        raise DirectSyncError("Progression disque invalide")
    error=str(payload["error"] or "")[:250]
    if status=="success" and (error or not numbers["disks_total"] or
                                numbers["disks_done"]!=numbers["disks_total"]):
        raise DirectSyncError("Succès sans disques cohérents déclaré")
    vm=dr_protected_vms._one(vmid)
    if type(payload["source_vmid"]) is not int or payload["source_vmid"]!=vm["source_vmid"] or (
            type(payload["target_vmid"]) is not int or payload["target_vmid"]!=vm["target_vmid"]):
        raise DirectSyncError("VMID source / secours différent de la protection")
    if vm["state"]!="planned":
        raise DirectSyncError("Protection suspendue")
    _node_authorized(agent,vm)
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute("SELECT id,agent_id,status FROM pra_direct_sync_runs WHERE run_uuid=?",
                        (run_uuid,)).fetchone()
        if row:
            if row["agent_id"]!=agent["id"] or row["status"] in ("success","failed","interrupted"):
                raise DirectSyncError("Run terminé ou appartenant à un autre agent")
            con.execute(
                "UPDATE pra_direct_sync_runs SET status=?,disks_total=?,disks_done=?,"
                "scanned_bytes=?,transferred_bytes=?,elapsed_seconds=?,error=?,last_report_at=?,"
                "finished_at=CASE WHEN ? <> 'running' THEN ? ELSE NULL END WHERE id=?",
                (status,numbers["disks_total"],numbers["disks_done"],
                 numbers["scanned_bytes"],numbers["transferred_bytes"],numbers["elapsed_seconds"],error,db.now(),
                 status,db.now(),row["id"])
            )
            run_id=row["id"]
        else:
            if status!="running":
                raise DirectSyncError("Première notification manquante")
            if con.execute("SELECT 1 FROM pra_direct_sync_runs WHERE protected_vm_id=? "
                           "AND status='running'",(vmid,)).fetchone():
                raise DirectSyncError("Une synchronisation est déjà enregistrée sur cette VM")
            run_id=con.execute(
                "INSERT INTO pra_direct_sync_runs(protected_vm_id,run_uuid,agent_id,kind,"
                "status,disks_total,disks_done,scanned_bytes,transferred_bytes,elapsed_seconds,error,"
                "started_at,last_report_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (vmid,run_uuid,agent["id"],"offline_incremental","running",
                 numbers["disks_total"],numbers["disks_done"],numbers["scanned_bytes"],
                 numbers["transferred_bytes"],numbers["elapsed_seconds"],error,db.now(),db.now())
            ).lastrowid
    if status!="running":
        db.audit("agent:"+str(agent["id"]),"pra.direct.sync."+status,vmid,
                 "run="+str(run_id)+", disks="+str(numbers["disks_done"]))
    return {"id":run_id,"status":status,"failover_available":False}


def list_runs(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        items=[dict(x) for x in con.execute(
            "SELECT id,kind,status,disks_total,disks_done,scanned_bytes,transferred_bytes,"
            "elapsed_seconds,error,started_at,finished_at,last_report_at "
            "FROM pra_direct_sync_runs WHERE protected_vm_id=? ORDER BY id DESC LIMIT 30",
            (vm_id,)
        )]
    # Don't display a crashed/outdated source agent report as a forever-running sync.
    for x in items:
        if x["status"]=="running":
            try:
                age=datetime.now(timezone.utc)-datetime.fromisoformat(x["last_report_at"])
                x["stale"]=age>timedelta(minutes=20)
            except (TypeError,ValueError):
                x["stale"]=True
        else:
            x["stale"]=False
    return {"items":items}
