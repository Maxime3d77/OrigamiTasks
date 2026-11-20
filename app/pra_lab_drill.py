"""Isolated DR boot drill using a FULL clone of an already restored OFF standby.

Never starts the protected REP_ VM or the source VM. A clone can be powered
on only after a positive, independent Proxmox API check that it has no NICs,
passthrough, autostart or shared disk attachment. The clone is left for
operator inspection/removal: no automated destructive cleanup.

This is an experimental TEST workflow, not a DR failover, fencing, guest
application validation or production promotion.
"""
import re
from urllib.parse import quote
from . import config, db, dr_protected_vms, integrations, pra_restore

_DISK = re.compile(r"^(?:scsi|virtio|sata|ide)[0-9]{1,2}$|^(?:efidisk|tpmstate)[0-9]+$")
_NET = re.compile(r"^net[0-9]{1,2}$")
_PASSTHROUGH = re.compile(r"^(?:hostpci|usb|parallel|serial)[0-9]{1,2}$")
_VALID_VMID = range(100,1000000000)

class DrillError(ValueError):
    pass


def _ready(vm_id, job_id):
    vm = dr_protected_vms._one(vm_id)
    if vm["engine"] != "pbs_backup_restore" or vm["state"] != "planned":
        raise DrillError("La préparation d'un clone de test exige une protection PBS active")
    with db.session() as con:
        restored = con.execute(
            "SELECT * FROM pra_pbs_restores WHERE id=? AND protected_vm_id=?",
            (job_id, vm_id)
        ).fetchone()
    if not restored or restored["status"] != "completed":
        raise DrillError("Une restauration PBS terminée et vérifiée est nécessaire")
    return vm,dict(restored)


def _client(vm):
    return pra_restore._target(vm)


def _get(dc, cred, path):
    return pra_restore._pve(dc,cred,path)


def _post(dc, cred, path, payload):
    result = pra_restore._pve(dc,cred,path,method="POST",data=payload)
    if not isinstance(result,str) or not result.startswith("UPID:"):
        raise DrillError("Proxmox n'a pas confirmé l'opération par un identifiant de tâche")
    return result


def _inventory(dc,cred):
    resources=integrations.discover_datacenter(dc,cred,20)
    if not isinstance(resources,list):
        raise DrillError("Inventaire Proxmox indisponible")
    return resources


def _vm_entries(resources, vmid):
    return [r for r in resources if r.get("type") in ("qemu","lxc")
            and r.get("vmid")==vmid]


def _vm_config(dc,cred,node,vmid):
    path="/api2/json/nodes/"+quote(node,safe="")+"/qemu/"+str(vmid)+"/config"
    value=_get(dc,cred,path)
    if not isinstance(value,dict):
        raise DrillError("Configuration VM Proxmox indisponible")
    return value


def _source_safe(vm,dc,cred,resources):
    matching=_vm_entries(resources,vm["target_vmid"])
    if (len(matching)!=1 or matching[0].get("type")!="qemu"
            or matching[0].get("node")!=vm["target_node"]
            or matching[0].get("status")!="stopped"):
        raise DrillError("La VM REP_ doit rester présente et ÉTEINTE sur le nœud prévu")
    conf=_vm_config(dc,cred,vm["target_node"],vm["target_vmid"])
    if str(conf.get("onboot",0)).lower() not in ("0","false"):
        raise DrillError("Démarrage automatique de la VM REP_ non désactivé")
    if not any(_DISK.fullmatch(k) for k in conf):
        raise DrillError("La copie de secours ne possède aucun disque à cloner")
    if any(_PASSTHROUGH.fullmatch(k) for k in conf):
        raise DrillError("Passthrough/serial non supporté pour une copie de test isolée")
    return conf


def _owned_drill(vm,drill,dc,cred,resources):
    owners=_vm_entries(resources,drill["test_vmid"])
    if (len(owners)!=1 or owners[0].get("type")!="qemu"
            or owners[0].get("node")!=drill["node"]):
        raise DrillError("VMID de test absent, ambigu ou transféré vers un autre nœud")
    expected="LAB_PRA_"+str(vm["id"])+"_"+str(drill["id"])
    if owners[0].get("name")!=expected:
        raise DrillError("Le VMID de test n'a plus le nom réservé au drill")
    conf=_vm_config(dc,cred,drill["node"],drill["test_vmid"])
    if str(conf.get("onboot",0)).lower() not in ("0","false"):
        raise DrillError("Clone de test configuré pour démarrer automatiquement")
    if any(_PASSTHROUGH.fullmatch(k) for k in conf):
        raise DrillError("Clone de test avec matériel/passthrough non isolé")
    base=_vm_config(dc,cred,drill["node"],vm["target_vmid"])
    for slot in [x for x in base if _DISK.fullmatch(x)]:
        original=str(base.get(slot,"")).split(",",1)[0]
        cloned=str(conf.get(slot,"")).split(",",1)[0]
        if not cloned:
            raise DrillError("Disque manquant dans la copie de test")
        if original == cloned:
            raise DrillError("Disque partagé avec la VM REP_ : test interdit")
    return owners[0],conf


def _load(vm_id, drill_id):
    vm=dr_protected_vms._one(vm_id)
    with db.session() as con:
        row=con.execute(
            "SELECT * FROM pra_lab_drills WHERE id=? AND protected_vm_id=?",
            (drill_id,vm_id)
        ).fetchone()
    if not row:
        raise DrillError("Clone de test inconnu")
    return vm,dict(row)


def prepare(vm_id,restore_job_id,test_vmid,approved,actor):
    """Reserve ownership first, then request a FULL clone. No power action."""
    if approved is not True or not config.ALLOW_PROXMOX_ACTIONS:
        raise DrillError("Confirmation explicite et autorisation d'actions Proxmox nécessaires")
    if type(test_vmid) is not int or test_vmid not in _VALID_VMID:
        raise DrillError("VMID de test invalide")
    vm,restore=_ready(vm_id,restore_job_id)
    if test_vmid in (vm["source_vmid"],vm["target_vmid"]):
        raise DrillError("Le clone doit avoir un VMID différent de la source et du secours")
    dc,cred=_client(vm)
    resources=_inventory(dc,cred)
    _source_safe(vm,dc,cred,resources)
    if _vm_entries(resources,test_vmid):
        raise DrillError("VMID de test déjà utilisé sur le cluster")
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT id FROM pra_lab_drills WHERE protected_vm_id=? LIMIT 1",
                       (vm_id,)).fetchone():
            raise DrillError("Un clone de test existe déjà ou son état doit être réconcilié")
        drill_id=con.execute(
            "INSERT INTO pra_lab_drills (protected_vm_id,restore_job_id,test_vmid,node,"
            "status,actor,created_at) VALUES (?,?,?,?,?,?,?)",
            (vm_id,restore_job_id,test_vmid,vm["target_node"],"creating",actor,db.now())
        ).lastrowid
    name="LAB_PRA_"+str(vm["id"])+"_"+str(drill_id)
    try:
        # Recheck collision immediately before the destructive clone allocation.
        resources=_inventory(dc,cred)
        _source_safe(vm,dc,cred,resources)
        if _vm_entries(resources,test_vmid):
            raise DrillError("Collision VMID pendant la réservation")
        upid=_post(dc,cred,"/api2/json/nodes/"+quote(vm["target_node"],safe="")+
                   "/qemu/"+str(vm["target_vmid"])+"/clone",{
                    "newid":str(test_vmid),"name":name,
                    "full":"1","storage":vm["target_storage"]
                   })
    except Exception:
        with db.session() as con:
            # An API timeout may mean the remote PVE clone was submitted.
            # Keep the journal and block duplicate attempts until inspection.
            con.execute("UPDATE pra_lab_drills SET status='uncertain' WHERE id=?",
                        (drill_id,))
        raise DrillError("État du clonage incertain : inspecter Proxmox et le VMID de test") from None
    with db.session() as con:
        con.execute("UPDATE pra_lab_drills SET status='cloning',upid=? WHERE id=?",
                    (upid,drill_id))
    db.audit(actor,"pra.lab.clone",vm_id,"id="+str(drill_id)+" vmid="+str(test_vmid))
    return {"drill_id":drill_id,"status":"cloning","upid":upid,
            "test_vmid":test_vmid,"source_vm_stopped":False,
            "standby_vm_started":False,"clone_started":False}


def inspect(vm_id,drill_id):
    """Read-only task+guest checks. No mutation to Proxmox."""
    vm,drill=_load(vm_id,drill_id)
    dc,cred=_client(vm)
    resources=_inventory(dc,cred)
    _source_safe(vm,dc,cred,resources)
    if drill["status"]=="uncertain":
        return {"drill_id":drill_id,"status":"uncertain","test_vmid":drill["test_vmid"],
                "ready_for_boot":False,"can_promote":False}
    if drill["status"] in ("creating","cloning"):
        if not drill["upid"]:
            return {"drill_id":drill_id,"status":drill["status"],"ready_for_boot":False}
        task=_get(dc,cred,"/api2/json/nodes/"+quote(drill["node"],safe="")+
                  "/tasks/"+quote(drill["upid"],safe="")+"/status")
        if not isinstance(task,dict):
            raise DrillError("Statut du clonage indisponible")
        if task.get("status")!="stopped":
            return {"drill_id":drill_id,"status":"cloning","ready_for_boot":False}
        if task.get("exitstatus")!="OK":
            with db.session() as con:
                con.execute("UPDATE pra_lab_drills SET status='uncertain' WHERE id=?",
                            (drill_id,))
            raise DrillError("Clone interrompu : inspecter les disques sans supprimer")
    owner,conf=_owned_drill(vm,drill,dc,cred,resources)
    running=owner.get("status")=="running"
    networks=sorted(k for k in conf if _NET.fullmatch(k))
    ready=not networks and not running
    status=("running" if running else
            "ready" if ready else "needs_isolation")
    with db.session() as con:
        con.execute("UPDATE pra_lab_drills SET status=? WHERE id=?",
                    (status,drill_id))
    return {"drill_id":drill_id,"status":status,"test_vmid":drill["test_vmid"],
            "network_devices":networks,"network_isolated":not networks,
            "ready_for_boot":ready,
            "boot_tested":running,
            "app_health_verified":False,"can_promote":False,
            "source_vm_stopped":False,"standby_vm_started":False}


def isolate(vm_id,drill_id,approved,actor):
    if approved is not True or not config.ALLOW_PROXMOX_ACTIONS:
        raise DrillError("Validation explicite requise pour isoler le clone")
    vm,drill=_load(vm_id,drill_id)
    dc,cred=_client(vm)
    resources=_inventory(dc,cred)
    _source_safe(vm,dc,cred,resources)
    if drill["status"] in ("uncertain","creating","cloning"):
        raise DrillError("Terminer et inspecter le clonage avant l'isolement")
    owner,conf=_owned_drill(vm,drill,dc,cred,resources)
    if owner.get("status")!="stopped":
        raise DrillError("Le clone doit être arrêté avant modification réseau")
    networks=[k for k in conf if _NET.fullmatch(k)]
    if networks:
        try:
            # Change ONLY the dedicated full clone, never the source or REP_.
            pra_restore._pve(dc,cred,"/api2/json/nodes/"+quote(drill["node"],safe="")+
                  "/qemu/"+str(drill["test_vmid"])+"/config",method="POST",
                  data={"delete":",".join(sorted(networks)),"onboot":"0"})
        except Exception:
            with db.session() as con:
                con.execute("UPDATE pra_lab_drills SET status='uncertain' WHERE id=?",
                            (drill_id,))
            raise DrillError("Réseau du clone dans un état incertain : inspecter Proxmox") from None
    result=inspect(vm_id,drill_id)
    if not result.get("ready_for_boot"):
        raise DrillError("Clone de test non confirmé isolé et arrêté")
    db.audit(actor,"pra.lab.isolate",vm_id,"id="+str(drill_id))
    return result


def start(vm_id,drill_id,approved,actor):
    """Power on ONLY the independently cloned no-NIC test VM."""
    if approved is not True or not config.ALLOW_PROXMOX_ACTIONS:
        raise DrillError("Approbation de démarrage du clone isolé requise")
    vm,drill=_load(vm_id,drill_id)
    if drill["status"]!="ready":
        raise DrillError("Clone non validé pour démarrage ; effectuer isolement et contrôle")
    dc,cred=_client(vm)
    resources=_inventory(dc,cred)
    _source_safe(vm,dc,cred,resources)
    owner,conf=_owned_drill(vm,drill,dc,cred,resources)
    if owner.get("status")!="stopped" or any(_NET.fullmatch(k) for k in conf):
        raise DrillError("Clone de test non isolé ou déjà démarré")
    if not any(_DISK.fullmatch(k) for k in conf):
        raise DrillError("Le clone n'a pas de disque")
    with db.session() as con:
        changed=con.execute(
            "UPDATE pra_lab_drills SET status='booting' WHERE id=? AND status='ready'",
            (drill_id,)).rowcount
    if changed!=1:
        raise DrillError("L'état du clone a changé, démarrage refusé")
    try:
        upid=_post(dc,cred,"/api2/json/nodes/"+quote(drill["node"],safe="")+
                   "/qemu/"+str(drill["test_vmid"])+"/status/start",{})
    except Exception:
        with db.session() as con:
            con.execute("UPDATE pra_lab_drills SET status='uncertain' WHERE id=?",(drill_id,))
        raise DrillError("Démarrage du clone incertain : inspecter Proxmox") from None
    with db.session() as con:
        con.execute("UPDATE pra_lab_drills SET upid=? WHERE id=?",(upid,drill_id))
    db.audit(actor,"pra.lab.start",vm_id,"id="+str(drill_id))
    return {"drill_id":drill_id,"status":"booting","upid":upid,
            "started_vmid":drill["test_vmid"],"source_vm_stopped":False,
            "standby_vm_started":False,"can_promote":False}


def shutdown(vm_id,drill_id,approved,actor):
    """Request graceful shutdown of the owned isolated clone only."""
    if approved is not True or not config.ALLOW_PROXMOX_ACTIONS:
        raise DrillError("Approbation de l'arrêt du clone requise")
    vm,drill=_load(vm_id,drill_id)
    dc,cred=_client(vm)
    resources=_inventory(dc,cred)
    _source_safe(vm,dc,cred,resources)
    owner,conf=_owned_drill(vm,drill,dc,cred,resources)
    if owner.get("status")!="running" or any(_NET.fullmatch(k) for k in conf):
        raise DrillError("Clone non confirmé démarré et isolé")
    with db.session() as con:
        con.execute("UPDATE pra_lab_drills SET status='stopping' WHERE id=?",(drill_id,))
    try:
        upid=_post(dc,cred,"/api2/json/nodes/"+quote(drill["node"],safe="")+
                   "/qemu/"+str(drill["test_vmid"])+"/status/shutdown",{})
    except Exception:
        with db.session() as con:
            con.execute("UPDATE pra_lab_drills SET status='uncertain' WHERE id=?",(drill_id,))
        raise DrillError("Arrêt du clone indéterminé, inspecter Proxmox") from None
    db.audit(actor,"pra.lab.shutdown",vm_id,"id="+str(drill_id))
    return {"drill_id":drill_id,"status":"stopping","upid":upid,
            "standby_vm_started":False,"can_promote":False}


def list_drills(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        rows=con.execute(
            "SELECT id,restore_job_id,test_vmid,node,status,created_at,upid "
            "FROM pra_lab_drills WHERE protected_vm_id=? ORDER BY id DESC LIMIT 20",
            (vm_id,)
        ).fetchall()
    return {"items":[dict(row) for row in rows]}


def release(vm_id,drill_id,approved,actor):
    """Remove tracking ONLY after operator removes clone in Proxmox."""
    if approved is not True:
        raise DrillError("Confirmation de suppression manuelle du clone requise")
    vm,drill=_load(vm_id,drill_id)
    dc,cred=_client(vm)
    resources=_inventory(dc,cred)
    if _vm_entries(resources,drill["test_vmid"]):
        raise DrillError("Clone de test toujours présent : suppression manuelle nécessaire")
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM pra_lab_drills WHERE id=? AND protected_vm_id=?",
                    (drill_id,vm_id))
    db.audit(actor,"pra.lab.release",vm_id,"id="+str(drill_id))
    return {"released":True,"remote_vm_deleted":False}
