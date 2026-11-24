"""Supervised LAB-only hot QMP -> Proxmox LVM replication jobs.

Makes the existing *real* app.pra_lvm_lab data plane accessible from the UI
without weakening its host-side lab-only restrictions. Each run is auditable,
exclusive per VM, asynchronous, and fail closed on power loss/restart.

NO automatic scheduling, source power operations, standby start, failover,
fencing, cleanup, or production LVM reception is enabled by these endpoints.
The operator must label the disposable, networkless standby and explicitly
approve every run. Never retry an uncertain generation automatically.
"""
import json
import threading

from . import db, dr_protected_vms, pra_lvm_lab

LAB_ACK = pra_lvm_lab.LAB_ACK


class LabJobError(ValueError):
    pass


def initialize():
    """Mark orphaned attempts uncertain before serving new requests."""
    with db.session() as con:
        con.execute(
            "UPDATE pra_lvm_lab_runs SET status='uncertain', "
            "details='Contrôleur redémarré : vérifier les jobs QMP, exports NBD et LV sur le nœud', "
            "finished_at=? WHERE status IN ('queued','running')", (db.now(),)
        )


def preflight(vm_id):
    """Read-only end-to-end host/SSH/LVM capability check."""
    try:
        return pra_lvm_lab.run(vm_id, execute=False)
    except (ValueError, RuntimeError) as exc:
        raise LabJobError(str(exc)) from exc


def _status(run_id, status, details, *, result=None):
    point_at = (result or {}).get("point_at")
    token = (result or {}).get("checkpoint_token")
    with db.session() as con:
        con.execute(
            "UPDATE pra_lvm_lab_runs SET status=?,details=?,"
            "result_json=COALESCE(?,result_json),"
            "point_at=COALESCE(?,point_at),checkpoint_token=COALESCE(?,checkpoint_token),"
            "started_at=CASE WHEN ?='running' THEN COALESCE(started_at,?) ELSE started_at END,"
            "finished_at=CASE WHEN ? IN ('completed','uncertain','failed') THEN ? ELSE finished_at END "
            "WHERE id=?",
            (status, str(details)[:700],
             json.dumps(result, separators=(',', ':'), sort_keys=True) if result is not None else None,
             point_at, token, status, db.now(), status, db.now(), run_id)
        )


def _prepare_isolated_clone(item):
    """Make a testable, independent OFF clone from the current LAB checkpoint.

    Source hardware metadata comes directly from a live QMP probe (never the
    browser). The receiver independently checks cluster VMID availability,
    owns every LV and enforces an empty networkless test VM.
    """
    vm = dr_protected_vms._one(item["protected_vm_id"])
    info = pra_lvm_lab._runtime(vm)
    src = dst = None
    try:
        src = pra_lvm_lab.pra_live._connect(info["source"])
        dst = pra_lvm_lab.pra_live._connect(info["destination"])
        source = pra_lvm_lab.pra_live._run_helper(src, "source_probe", {
            "vmid": vm["source_vmid"], "slots": info["slots"]
        }, timeout=90)
        target = pra_lvm_lab.pra_live._run_helper(
            dst, "lvm_lab_probe", pra_lvm_lab._request(vm), timeout=90)
        checkpoint = target.get("checkpoint")
        if (source.get("pra_protocol", 0) < 2
                or target.get("pra_protocol", 0) < 3
                or target.get("pending")
                or not isinstance(checkpoint, dict)
                or checkpoint.get("status") != "committed"
                or checkpoint.get("token") != item["checkpoint_token"]):
            raise LabJobError("Checkpoint de recette indisponible, modifié ou non confirmé")
        hardware = source.get("config")
        if not isinstance(hardware, dict):
            raise LabJobError("Configuration matérielle source non confirmée")
        result = pra_lvm_lab.pra_live._run_helper(
            dst, "lvm_lab_prepare_clone",
            {**pra_lvm_lab._request(vm, item["checkpoint_token"]),
             "test_vmid": item["test_vmid"], "source_config": hardware},
            timeout=21660
        )
        if (result.get("test_vmid") != item["test_vmid"]
                or result.get("checkpoint") != item["checkpoint_token"]
                or result.get("vm_started") is not False
                or result.get("standby_started") is not False
                or result.get("network_isolated") is not True
                or result.get("boot_verified") is not False
                or result.get("failover_authorized") is not False):
            raise LabJobError("Le clone d'essai n'a pas confirmé son isolation et son arrêt")
        return {**result, "checkpoint_token": result["checkpoint"],
                "source_running": True, "target_stopped": True,
                "offline_structure_verified": True,
                "real_pve_replication_performed": False}
    finally:
        if src:
            src.close()
        if dst:
            dst.close()


def _perform(run_id):
    with db.session() as con:
        item = con.execute(
            "SELECT protected_vm_id,mode,test_vmid,checkpoint_token,"
            "source_vmid,target_vmid,target_storage,"
            "source_dc_id,target_dc_id FROM pra_lvm_lab_runs WHERE id=?",
            (run_id,)
        ).fetchone()
    if item is None:
        return
    try:
        current = dr_protected_vms._one(item["protected_vm_id"])
        for key in ("source_vmid", "target_vmid", "target_storage",
                    "source_dc_id", "target_dc_id"):
            if current[key] != item[key]:
                raise LabJobError("Protection modifiée depuis la demande : aucune copie démarrée")
        if item["mode"] == "restore":
            _status(run_id, "running",
                    "Création du clone de restauration isolé et arrêté (LVM recette)")
            result = _prepare_isolated_clone(item)
            if (result.get("network_isolated") is not True
                    or result.get("vm_started") is not False
                    or result.get("boot_verified") is not False):
                raise LabJobError("Clone non isolé ou état de démarrage incertain")
            _status(run_id, "completed",
                    "Clone indépendant PRA_TEST_" + str(item["test_vmid"]) +
                    " prêt pour inspection, arrêté et sans réseau (boot NON testé)",
                    result=result)
        else:
            _status(run_id, "running", "Transfert QMP/NBD à chaud vers des LV LVM de recette")
            result = pra_lvm_lab.run(
                item["protected_vm_id"], execute=True,
                incremental=item["mode"] == "incremental", lab_confirm=LAB_ACK
            )
            if (result.get("real_pve_replication_performed") is not True
                    or result.get("offline_structure_verified") is not True
                    or result.get("vm_started") is not False
                    or result.get("target_stopped") is not True
                    or not result.get("checkpoint_token")):
                raise LabJobError("Le récepteur n'a pas attesté une copie LVM vérifiée")
            _status(run_id, "completed",
                    "Copie " + item["mode"] +
                    " QMP/LVM vérifiée hors ligne ; réplique non démarrée",
                    result=result)
        db.audit("system", "pra.lvm.lab.completed", item["protected_vm_id"],
                 "run=" + str(run_id) + " checkpoint=" + str(result["checkpoint_token"])[:40])
    except BaseException as exc:
        # Even a lost ACK after writing the last block can leave a good
        # checkpoint. Never automatically retry or free PVE-allocated LVs.
        _status(run_id, "uncertain",
                "Copie LVM de recette NON CONFIRMÉE : "
                + type(exc).__name__ + " : " + str(exc)[:450]
                + " ; réconciliation manuelle obligatoire")
        db.audit("system", "pra.lvm.lab.uncertain", item["protected_vm_id"],
                 "run=" + str(run_id) + " ; vérification manuelle des LV nécessaire")


def list_runs(vm_id, limit=20):
    dr_protected_vms._one(vm_id)
    if type(limit) is not int:
        raise LabJobError("Invalid history limit")
    limit = max(1, min(limit, 100))
    with db.session() as con:
        rows = con.execute(
            "SELECT id,protected_vm_id,status,mode,test_vmid,details,actor,created_at,"
            "started_at,finished_at,point_at,checkpoint_token "
            "FROM pra_lvm_lab_runs WHERE protected_vm_id=? ORDER BY id DESC LIMIT ?",
            (vm_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def start(vm_id, actor, *, mode, confirmation):
    if mode not in ("full", "incremental"):
        raise LabJobError("Choose full or incremental")
    if confirmation != LAB_ACK:
        raise LabJobError("Saisir exactement DISPOSABLE-LAB-ONLY pour autoriser des écritures sur la recette")
    if not isinstance(actor, str) or not actor:
        raise LabJobError("Administrateur non identifié")

    vm = dr_protected_vms._one(vm_id)
    if vm["engine"] != "qmp_live_incremental" or vm["state"] != "planned":
        raise LabJobError("Protection PRA QMP inactive ou moteur incompatible")

    # BEFORE reserving the run, contact both hosts and check disposable LAB
    # marker, no network, onboot disabled, source ON, target OFF, target LVM.
    checked = preflight(vm_id)
    if checked.get("source_running") is not True or checked.get("target_stopped") is not True:
        raise LabJobError("VM source ON et cible OFF non confirmées")
    if checked.get("destination_storage") != vm["target_storage"]:
        raise LabJobError("Destination PRA modifiée durant le prévol")
    if checked.get("retention_blocked") is True:
        raise LabJobError("Limite de "+str(checked.get("retention_limit",8))+
                          " générations LVM atteinte : aucune nouvelle copie lancée. "
                          "Consolidation sûre nécessaire ; ne supprimer aucun LV.")

    if mode == "incremental" and not checked.get("checkpoint"):
        raise LabJobError("Aucun checkpoint LVM de recette compatible : amorçage FULL nécessaire")

    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        blocking = con.execute(
            "SELECT id,status FROM pra_lvm_lab_runs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') ORDER BY id DESC LIMIT 1",
            (vm_id,)
        ).fetchone()
        if blocking:
            raise LabJobError("Transfert LVM actif ou incertain : résoudre le run #"
                              + str(blocking["id"]) + " avant une nouvelle copie")
        conflict = con.execute(
            "SELECT id FROM pra_live_runs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') LIMIT 1",
            (vm_id,)
        ).fetchone()
        if conflict:
            raise LabJobError("Une réplication QMP DIR est active ou incertaine pour cette VM")
        live = con.execute(
            "SELECT source_vmid,target_vmid,target_storage,source_dc_id,"
            "target_dc_id,engine,state FROM dr_protected_vms WHERE id=?",
            (vm_id,)
        ).fetchone()
        if (live is None or live["engine"] != "qmp_live_incremental"
                or live["state"] != "planned"
                or any(live[k] != vm[k] for k in
                       ("source_vmid","target_vmid","target_storage","source_dc_id","target_dc_id"))):
            raise LabJobError("Protection modifiée pendant le prévol : recommencer")
        run_id = con.execute(
            "INSERT INTO pra_lvm_lab_runs("
            "protected_vm_id,status,mode,actor,created_at,details,"
            "source_vmid,target_vmid,target_storage,source_dc_id,target_dc_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (vm_id, "queued", mode, actor, db.now(),
             "Copie QMP/LVM de recette approuvée, en attente d'exécution",
             vm["source_vmid"],vm["target_vmid"],vm["target_storage"],
             vm["source_dc_id"],vm["target_dc_id"])
        ).lastrowid
    db.audit(actor, "pra.lvm.lab.request", vm_id,
             "run=" + str(run_id) + " mode=" + mode)
    threading.Thread(target=_perform, args=(run_id,), daemon=True,
                     name="pra-lvm-lab-" + str(run_id)).start()
    return {
        "run_id": run_id, "status": "queued", "mode": mode,
        "source_remains_running": True, "standby_must_remain_stopped": True,
        "lab_only": True, "failover_available": False
    }

def start_restore(vm_id, actor, *, test_vmid, confirmation):
    """Authorize one LAB-only independent test clone; never start the guest."""
    if confirmation != LAB_ACK:
        raise LabJobError("Saisir exactement DISPOSABLE-LAB-ONLY pour un clone de recette")
    if type(test_vmid) is not int or not 100 <= test_vmid <= 999999999:
        raise LabJobError("VMID de clone de recette invalide")
    if not isinstance(actor, str) or not actor:
        raise LabJobError("Administrateur non identifié")
    vm = dr_protected_vms._one(vm_id)
    if vm["engine"] != "qmp_live_incremental" or vm["state"] != "planned":
        raise LabJobError("Protection QMP inactive ou incompatible")
    if test_vmid == vm["target_vmid"]:
        raise LabJobError("VMID du clone et de la réplique doivent être distincts")
    checked = preflight(vm_id)
    token = checked.get("checkpoint")
    if not isinstance(token, str) or len(token) != 32:
        raise LabJobError("Aucun checkpoint LVM confirmé disponible pour le clone")
    if checked.get("source_running") is not True or checked.get("target_stopped") is not True:
        raise LabJobError("Le test exige une source allumée et la réplique arrêtée")

    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        pending = con.execute(
            "SELECT id FROM pra_lvm_lab_runs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') LIMIT 1",
            (vm_id,)
        ).fetchone()
        if pending:
            raise LabJobError("Transfert ou clone LVM actif/incertain ; réconcilier avant une restauration")
        conflict = con.execute(
            "SELECT id FROM pra_live_runs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') LIMIT 1",
            (vm_id,)
        ).fetchone()
        if conflict:
            raise LabJobError("Une copie QMP DIR active/incertaine bloque la restauration LVM")
        latest = con.execute(
            "SELECT status,checkpoint_token FROM pra_lvm_lab_runs "
            "WHERE protected_vm_id=? AND mode IN ('full','incremental') "
            "ORDER BY id DESC LIMIT 1", (vm_id,)
        ).fetchone()
        if (latest is None or latest["status"] != "completed"
                or latest["checkpoint_token"] != token):
            raise LabJobError("Checkpoint LVM non suivi comme copie complète/incrémentale vérifiée")
        row = con.execute(
            "SELECT source_vmid,target_vmid,target_storage,source_dc_id,target_dc_id,"
            "engine,state FROM dr_protected_vms WHERE id=?", (vm_id,)
        ).fetchone()
        if (row is None or row["state"] != "planned"
                or row["engine"] != "qmp_live_incremental"
                or any(row[k] != vm[k] for k in
                       ("source_vmid","target_vmid","target_storage","source_dc_id","target_dc_id"))):
            raise LabJobError("Protection modifiée pendant le prévol du clone")
        run_id = con.execute(
            "INSERT INTO pra_lvm_lab_runs("
            "protected_vm_id,status,mode,test_vmid,checkpoint_token,actor,created_at,details,"
            "source_vmid,target_vmid,target_storage,source_dc_id,target_dc_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vm_id,"queued","restore",test_vmid,token,actor,db.now(),
             "Clone LVM isolé de recette demandé, aucun démarrage",
             vm["source_vmid"],vm["target_vmid"],vm["target_storage"],
             vm["source_dc_id"],vm["target_dc_id"])
        ).lastrowid
    db.audit(actor,"pra.lvm.lab.restore.request",vm_id,
             "run="+str(run_id)+" test_vmid="+str(test_vmid))
    threading.Thread(target=_perform,args=(run_id,),daemon=True,
                     name="pra-lvm-restore-"+str(run_id)).start()
    return {"run_id":run_id, "status":"queued","mode":"restore",
            "test_vmid":test_vmid,"lab_only":True,
            "test_vm_started":False,"failover_available":False}

