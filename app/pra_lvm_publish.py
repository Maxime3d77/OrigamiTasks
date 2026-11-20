"""Host-verified LAB publication of a committed LVM generation on an OFF REP_.

A configured boot profile is not guest boot validation, fencing or failover.
Publications are durable, generation-scoped and never blindly resumed.
"""
import threading

from . import db, dr_protected_vms, pra_lvm_lab

ACK = "DISPOSABLE-LAB-ONLY"


def initialize():
    with db.session() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS pra_lvm_publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            protected_vm_id INTEGER NOT NULL,
            replication_run_id INTEGER NOT NULL,
            checkpoint_token TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(protected_vm_id,checkpoint_token)
        )""")
        con.execute("UPDATE pra_lvm_publications SET status='uncertain',"
                    "details='Contrôleur redémarré durant l’attachement : inspecter qm config et LV',"
                    "updated_at=? WHERE status IN ('queued','running')",(db.now(),))


def _update(pub_id,status,detail):
    with db.session() as con:
        con.execute("UPDATE pra_lvm_publications SET status=?,details=?,updated_at=? WHERE id=?",
                    (status,str(detail)[:750],db.now(),pub_id))


def _work(pub_id):
    with db.session() as con:
        item=con.execute("SELECT * FROM pra_lvm_publications WHERE id=?",(pub_id,)).fetchone()
    if not item:
        return
    vm_id=item["protected_vm_id"]
    src=dst=None
    try:
        _update(pub_id,"running","Vérification du profil matériel et attachement des disques à REP_ arrêtée")
        vm=dr_protected_vms._one(vm_id)
        info=pra_lvm_lab._runtime(vm)
        src=pra_lvm_lab.pra_live._connect(info["source"])
        dst=pra_lvm_lab.pra_live._connect(info["destination"])
        source=pra_lvm_lab.pra_live._run_helper(src,"source_probe",{
            "vmid":vm["source_vmid"],"slots":info["slots"]},timeout=90)
        target=pra_lvm_lab.pra_live._run_helper(dst,"lvm_lab_probe",
                                               pra_lvm_lab._request(vm),timeout=90)
        manifest=target.get("checkpoint")
        if (not isinstance(manifest,dict) or manifest.get("status")!="committed"
                or manifest.get("token")!=item["checkpoint_token"]
                or manifest.get("source_vm_uuid")!=source.get("source_vm_uuid")
                or target.get("pending") or target.get("pra_protocol",0)<3):
            raise ValueError("Checkpoint ou génération source modifiée : publication interdite")
        hardware=source.get("config")
        if not isinstance(hardware,dict):
            raise ValueError("Profil matériel source indisponible")
        result=pra_lvm_lab.pra_live._run_helper(
            dst,"lvm_lab_publish_standby",{
                **pra_lvm_lab._request(vm,item["checkpoint_token"]),
                "name":vm["replica_name"],"source_config":hardware
            },timeout=360)
        if (result.get("checkpoint_token")!=item["checkpoint_token"]
                or result.get("standby_configured") is not True
                or result.get("target_stopped") is not True
                or result.get("network_isolated") is not True
                or result.get("boot_tested") is not False
                or result.get("failover_authorized") is not False):
            raise ValueError("Publication Proxmox non attestée")
        _update(pub_id,"completed","Disques QCOW2/LVM attachés à REP_ arrêtée ; "
                "QEMU/OS/application non testés, bascule interdite")
        db.audit("system","pra.lvm.publish.completed",vm_id,
                 "publication="+str(pub_id)+" token="+item["checkpoint_token"])
    except BaseException as exc:
        _update(pub_id,"uncertain","Publication non confirmée : "+
                type(exc).__name__+" : "+str(exc)[:450]+
                ". Inspecter la configuration VM sur le nœud PRA.")
        db.audit("system","pra.lvm.publish.uncertain",vm_id,
                 "publication="+str(pub_id)+" : "+type(exc).__name__)
    finally:
        if src: src.close()
        if dst: dst.close()


def history(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        return [dict(r) for r in con.execute(
            "SELECT id,checkpoint_token,replication_run_id,status,details,created_at,updated_at "
            "FROM pra_lvm_publications WHERE protected_vm_id=? ORDER BY id DESC LIMIT 30",
            (vm_id,)).fetchall()]


def tick():
    """Inspect opted-in protections; queue one publication per proven point."""
    with db.session() as con:
        ids=[r["id"] for r in con.execute(
            "SELECT p.id FROM dr_protected_vms p WHERE p.engine='qmp_live_incremental' "
            "AND p.state='planned' AND EXISTS (SELECT 1 FROM pra_lvm_activations a "
            "WHERE a.protected_vm_id=p.id AND a.status='completed')").fetchall()]
    for vm_id in ids:
        with db.session() as con:
            latest=con.execute(
                "SELECT id,checkpoint_token,status FROM pra_lvm_lab_runs "
                "WHERE protected_vm_id=? AND mode IN ('full','incremental') "
                "ORDER BY id DESC LIMIT 1",(vm_id,)).fetchone()
            if not latest or latest["status"]!="completed" or not latest["checkpoint_token"]:
                continue
            token=latest["checkpoint_token"]
            existing=con.execute(
                "SELECT 1 FROM pra_lvm_publications WHERE protected_vm_id=? "
                "AND checkpoint_token=?",(vm_id,token)).fetchone()
            if existing:
                continue
            conflict=con.execute(
                "SELECT 1 FROM pra_lvm_publications WHERE protected_vm_id=? "
                "AND status IN ('queued','running','uncertain')",(vm_id,)).fetchone()
            if conflict:
                continue
            con.execute("BEGIN IMMEDIATE")
            # Recheck under the write lock, a concurrent scheduler may also tick.
            if con.execute(
                "SELECT 1 FROM pra_lvm_publications WHERE protected_vm_id=? "
                "AND checkpoint_token=?",(vm_id,token)).fetchone():
                continue
            pub_id=con.execute(
                "INSERT INTO pra_lvm_publications "
                "(protected_vm_id,replication_run_id,checkpoint_token,status,details,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (vm_id,latest["id"],token,"queued","Attente attachement REP_",db.now(),db.now())
            ).lastrowid
        threading.Thread(target=_work,args=(pub_id,),daemon=True,
                         name="pra-lvm-publish-"+str(pub_id)).start()
