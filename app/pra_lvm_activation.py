"""Persisted LAB-only activation: prepare OFF standby, then seed QMP/LVM.

No automatic reverse synchronization, standby boot or promotion is authorized.
A controller restart marks in-progress activations uncertain; no blind retry.
"""
import json
import threading

from . import db, dr_protected_vms, pra_lvm_standby, pra_lvm_jobs

ACK = "DISPOSABLE-LAB-ONLY"


def initialize():
    with db.session() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS pra_lvm_activations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          protected_vm_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          phase TEXT NOT NULL,
          details TEXT NOT NULL,
          actor TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          replication_run_id INTEGER
        )""")
        con.execute(
            "UPDATE pra_lvm_activations SET status='uncertain', phase='interrupted', "
            "details='Contrôleur redémarré : contrôler la VM et les LV avant toute relance', "
            "updated_at=? WHERE status IN ('queued','running')", (db.now(),))


def _step(activation_id, status, phase, detail, run_id=None):
    with db.session() as con:
        con.execute("UPDATE pra_lvm_activations SET status=?,phase=?,details=?,"
                    "updated_at=?,replication_run_id=COALESCE(?,replication_run_id) "
                    "WHERE id=?", (status,phase,str(detail)[:1000],db.now(),run_id,activation_id))
    db.audit("system","pra.lvm.lab.activation."+phase,activation_id,str(detail)[:450])


def _work(activation_id, vm_id):
    with db.session() as con:
        row=con.execute("SELECT actor FROM pra_lvm_activations WHERE id=?",(activation_id,)).fetchone()
    if not row:
        return
    try:
        _step(activation_id,"running","provisioning",
              "Vérification source ON, VMID et stockage ; création REP_ arrêtée")
        prepared=pra_lvm_standby.prepare(vm_id,ACK)
        if prepared.get("target_stopped") is not True or prepared.get("network_isolated") is not True:
            raise ValueError("Préparation du secours non confirmée")
        _step(activation_id,"running","seeding","VM REP_ prête ; prévol et copie initiale à chaud")
        started=pra_lvm_jobs.start(vm_id,row["actor"],mode="full",confirmation=ACK)
        run_id=started["run_id"]
        _step(activation_id,"running","replicating",
              "Copie QMP/NBD vers LVM démarrée ; suivi dans l'historique des réplications",
              run_id=run_id)
        # The supervised replication job persists its own independent final
        # outcome; do not claim success or infer progress from a thread launch.
        # Activation remains running until its linked job has a terminal state.
    except BaseException as exc:
        _step(activation_id,"uncertain","blocked",
              "Activation non confirmée : "+type(exc).__name__+" : "+str(exc)[:600]
              +". Contrôler les hôtes et volumes avant tout nouvel essai")


def start(vm_id, actor, confirmation):
    if confirmation != ACK:
        raise ValueError("Autorisation explicite de recette requise")
    vm=dr_protected_vms._one(vm_id)
    if vm["engine"]!="qmp_live_incremental" or vm["state"]!="planned":
        raise ValueError("Le moteur QMP actif est requis")
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM pra_lvm_activations WHERE protected_vm_id=? "
                       "AND status IN ('queued','running','uncertain','completed') LIMIT 1",
                       (vm_id,)).fetchone():
            raise ValueError("Protection déjà activée ou état incertain : ne pas recommencer une copie FULL")
        identifier=con.execute(
            "INSERT INTO pra_lvm_activations "
            "(protected_vm_id,status,phase,details,actor,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (vm_id,"queued","queued","Protection enregistrée ; attente de préparation",
             actor,db.now(),db.now())).lastrowid
    db.audit(actor,"pra.lvm.lab.activation.request",vm_id,"activation="+str(identifier))
    threading.Thread(target=_work,args=(identifier,vm_id),daemon=True,
                     name="pra-lvm-activation-"+str(identifier)).start()
    return {"activation_id":identifier,"status":"queued","lab_only":True}


def retry_authorization_bug(vm_id, actor, confirmation):
    """Requeue only the known controller allowlist failure BEFORE host IO.

    Never retry a failed or ambiguous copy. All other uncertain states require
    external reconciliation of the QMP jobs and volume journal.
    """
    if confirmation != ACK:
        raise ValueError("Confirmation de recette requise")
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        last=con.execute(
            "SELECT id,status,phase,details,replication_run_id FROM pra_lvm_activations "
            "WHERE protected_vm_id=? ORDER BY id DESC LIMIT 1",(vm_id,)).fetchone()
        if (not last or last["status"]!="uncertain" or last["phase"]!="blocked"
                or last["replication_run_id"] is not None
                or "LiveReplicationError: Action PRA non autorisée" not in last["details"]):
            raise ValueError("Reprise automatique interdite : contrôler les écritures et les volumes")
        copies=con.execute("SELECT 1 FROM pra_lvm_lab_runs WHERE protected_vm_id=? LIMIT 1",
                           (vm_id,)).fetchone()
        if copies:
            raise ValueError("Un transfert a déjà été enregistré : vérification manuelle obligatoire")
        con.execute("UPDATE pra_lvm_activations SET status='failed',"
                    "details='Ancien échec local de liste blanche, remplacé par reprise contrôlée',"
                    "updated_at=? WHERE id=?",(db.now(),last["id"]))
    db.audit(actor,"pra.lvm.lab.activation.local_error_reconciled",vm_id,
             "Ancien run #"+str(last["id"])+" : refus local avant toute commande distante")
    return start(vm_id,actor,confirmation)


def status(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        rows=con.execute("SELECT id,status,phase,details,actor,created_at,"
                         "updated_at,replication_run_id FROM pra_lvm_activations "
                         "WHERE protected_vm_id=? ORDER BY id DESC LIMIT 30",
                         (vm_id,)).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            run_id=item["replication_run_id"]
            if run_id and item["status"]=="running":
                run=con.execute("SELECT status,details,point_at FROM pra_lvm_lab_runs "
                                "WHERE id=? AND protected_vm_id=?",
                                (run_id,vm_id)).fetchone()
                if run:
                    item["replication_status"]=run["status"]
                    item["replication_details"]=run["details"]
                    item["point_at"]=run["point_at"]
                    if run["status"] in ("completed","failed","uncertain"):
                        item["status"]=run["status"]
                        item["phase"]="verified" if run["status"]=="completed" else "blocked"
                        item["details"]=run["details"]
                        con.execute("UPDATE pra_lvm_activations SET status=?,phase=?,"
                                    "details=?,updated_at=? WHERE id=? AND status='running'",
                                    (item["status"],item["phase"],str(item["details"])[:1000],
                                     db.now(),item["id"]))
            result.append(item)
    return result
