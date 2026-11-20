"""Audit-only isolated QEMU start/stop test for a previously restored LAB clone.

No guest network, no production standby boot, no application-level claims.
"""
import json
import threading
from . import db, dr_protected_vms, pra_lvm_lab, pra_lvm_jobs

ACK = "DISPOSABLE-LAB-ONLY"


def initialize():
    with db.session() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS pra_lvm_boot_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            protected_vm_id INTEGER NOT NULL,
            restore_run_id INTEGER NOT NULL,
            test_vmid INTEGER NOT NULL,
            checkpoint_token TEXT NOT NULL,
            actor TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_pra_lvm_boot_tests_pending "
                    "ON pra_lvm_boot_tests(protected_vm_id) "
                    "WHERE status IN ('queued','running','uncertain')")
        con.execute("UPDATE pra_lvm_boot_tests SET status='uncertain',"
                    "details='Contrôleur redémarré pendant le test de démarrage : vérifier et arrêter le clone',"
                    "updated_at=? WHERE status IN ('queued','running')",(db.now(),))


def _update(test_id,status,details,result=None):
    with db.session() as con:
        con.execute("UPDATE pra_lvm_boot_tests SET status=?,details=?,"
                    "updated_at=?,result_json=COALESCE(?,result_json) WHERE id=?",
                    (status,str(details)[:750],db.now(),
                     json.dumps(result,sort_keys=True) if result is not None else None,test_id))


def _work(test_id):
    with db.session() as con:
        item=con.execute("SELECT * FROM pra_lvm_boot_tests WHERE id=?",(test_id,)).fetchone()
        restore=con.execute("SELECT result_json FROM pra_lvm_lab_runs WHERE id=?",
                            (item["restore_run_id"],)).fetchone() if item else None
    if not item:
        return
    try:
        _update(test_id,"running","Vérification des volumes et démarrage isolé du clone")
        if not restore or not restore["result_json"]:
            raise ValueError("Manifeste du clone indisponible")
        details=json.loads(restore["result_json"])
        volumes=details.get("test_volumes")
        if not isinstance(volumes,dict) or not volumes:
            raise ValueError("Volumes indépendants du clone non confirmés")
        vm=dr_protected_vms._one(item["protected_vm_id"])
        info=pra_lvm_lab._runtime(vm)
        conn=pra_lvm_lab.pra_live._connect(info["destination"])
        try:
            result=pra_lvm_lab.pra_live._run_helper(
                conn,"lvm_lab_test_boot",{
                    **pra_lvm_lab._request(vm,item["checkpoint_token"]),
                    "test_vmid":item["test_vmid"],"test_volumes":volumes
                },timeout=900)
        finally:
            conn.close()
        if (result.get("test_vmid") != item["test_vmid"]
                or result.get("checkpoint") != item["checkpoint_token"]
                or result.get("qemu_started") is not True
                or result.get("stopped_after_test") is not True
                or result.get("network_isolated") is not True
                or result.get("guest_boot_verified") is not False
                or result.get("production_failover") is not False):
            raise ValueError("Preuve d'un test QEMU isolé et arrêté insuffisante")
        _update(test_id,"completed","Clone démarré par QEMU puis arrêté ; OS et application NON vérifiés",result)
        db.audit("system","pra.lvm.boot.test.completed",item["protected_vm_id"],
                 "test="+str(test_id)+" clone="+str(item["test_vmid"]))
    except BaseException as exc:
        _update(test_id,"uncertain","Test non confirmé : "+type(exc).__name__+" : "
                +str(exc)[:450]+". Vérifier le statut du clone sur Proxmox")
        db.audit("system","pra.lvm.boot.test.uncertain",item["protected_vm_id"],
                 "test="+str(test_id)+" : "+type(exc).__name__)


def start(vm_id, restore_run_id, actor, confirmation):
    if confirmation != ACK:
        raise ValueError("Confirmation de recette LAB requise")
    if type(restore_run_id) is not int or restore_run_id <= 0:
        raise ValueError("Identifiant du clone invalide")
    vm=dr_protected_vms._one(vm_id)
    if vm["engine"] != "qmp_live_incremental" or vm["state"] != "planned":
        raise ValueError("La protection QMP doit être active")
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        restore=con.execute(
            "SELECT test_vmid,checkpoint_token,status,result_json FROM pra_lvm_lab_runs "
            "WHERE id=? AND protected_vm_id=? AND mode='restore'",
            (restore_run_id,vm_id)).fetchone()
        if not restore or restore["status"]!="completed":
            raise ValueError("Clone restauré et confirmé requis")
        try:
            details=json.loads(restore["result_json"] or "{}")
        except (TypeError,ValueError):
            raise ValueError("Journal du clone invalide") from None
        if (details.get("test_vmid")!=restore["test_vmid"]
                or details.get("network_isolated") is not True
                or details.get("vm_started") is not False
                or not isinstance(details.get("test_volumes"),dict)):
            raise ValueError("Isolation du clone non attestée")
        latest=con.execute(
            "SELECT status,checkpoint_token FROM pra_lvm_lab_runs "
            "WHERE protected_vm_id=? AND mode IN ('full','incremental') "
            "ORDER BY id DESC LIMIT 1",(vm_id,)).fetchone()
        if not latest or latest["status"]!="completed" or latest["checkpoint_token"]!=restore["checkpoint_token"]:
            raise ValueError("Checkpoint du clone périmé ; cloner le dernier point vérifié")
        active=con.execute(
            "SELECT 1 FROM pra_lvm_lab_runs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') LIMIT 1",(vm_id,)).fetchone()
        if active:
            raise ValueError("Copie LVM active ou incertaine : test interdit")
        already=con.execute(
            "SELECT 1 FROM pra_lvm_boot_tests WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') LIMIT 1",(vm_id,)).fetchone()
        if already:
            raise ValueError("Test existant actif ou non confirmé")
        test_id=con.execute(
            "INSERT INTO pra_lvm_boot_tests "
            "(protected_vm_id,restore_run_id,test_vmid,checkpoint_token,actor,"
            "status,details,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (vm_id,restore_run_id,restore["test_vmid"],restore["checkpoint_token"],
             actor,"queued","Test QEMU de clone isolé demandé",db.now(),db.now())).lastrowid
    db.audit(actor,"pra.lvm.boot.test.request",vm_id,"test="+str(test_id))
    threading.Thread(target=_work,args=(test_id,),daemon=True,
                     name="pra-lvm-boot-"+str(test_id)).start()
    return {"test_id":test_id,"status":"queued","production_failover":False}


def history(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        return [dict(r) for r in con.execute(
            "SELECT id,test_vmid,restore_run_id,status,details,created_at,updated_at "
            "FROM pra_lvm_boot_tests WHERE protected_vm_id=? ORDER BY id DESC LIMIT 30",
            (vm_id,)).fetchall()]
