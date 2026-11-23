"""Audited human OS-login evidence for isolated PRA clones (LAB ONLY).

This is an operator's explicit observation, not automated guest attestation
or application recovery proof. It NEVER authorizes failover or starts VMs.
A stale checkpoint must not be represented as current recovery evidence.
"""
import json

from . import db, dr_protected_vms


def initialize():
    with db.session() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS pra_lvm_guest_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            protected_vm_id INTEGER NOT NULL,
            restore_run_id INTEGER NOT NULL,
            test_vmid INTEGER NOT NULL,
            checkpoint_token TEXT NOT NULL,
            actor TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(protected_vm_id,restore_run_id,evidence_type)
        )""")


def record(vm_id, restore_run_id, actor, confirmation, note):
    if confirmation != "OS-LOGIN-VERIFIED":
        raise ValueError("Confirmation explicite de connexion OS requise")
    if type(restore_run_id) is not int or restore_run_id <= 0:
        raise ValueError("Identifiant de restauration invalide")
    if not isinstance(note,str) or not 8 <= len(note.strip()) <= 400:
        raise ValueError("Préciser la méthode de contrôle (8 à 400 caractères)")
    if any(ord(ch) < 32 for ch in note):
        raise ValueError("Le commentaire ne doit pas contenir de caractère de contrôle")
    if not isinstance(actor,str) or not actor:
        raise ValueError("Opérateur non identifié")
    vm=dr_protected_vms._one(vm_id)
    if vm["engine"]!="qmp_live_incremental" or vm["state"]!="planned":
        raise ValueError("Protection PRA non active")
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        restore=con.execute(
            "SELECT id,test_vmid,checkpoint_token,status,result_json "
            "FROM pra_lvm_lab_runs WHERE id=? AND protected_vm_id=? AND mode='restore'",
            (restore_run_id,vm_id)).fetchone()
        if not restore or restore["status"]!="completed":
            raise ValueError("Restauration confirmée du clone requise")
        try:
            result=json.loads(restore["result_json"] or "{}")
        except (ValueError,TypeError):
            raise ValueError("Manifeste de restauration invalide") from None
        if (result.get("test_vmid")!=restore["test_vmid"]
                or result.get("network_isolated") is not True
                or not isinstance(result.get("test_volumes"),dict)
                or not result["test_volumes"]):
            raise ValueError("Propriété et isolation du clone non attestées")
        latest=con.execute(
            "SELECT checkpoint_token,status FROM pra_lvm_lab_runs "
            "WHERE protected_vm_id=? AND mode IN ('full','incremental') "
            "ORDER BY id DESC LIMIT 1",(vm_id,)).fetchone()
        if (not latest or latest["status"]!="completed"
                or latest["checkpoint_token"]!=restore["checkpoint_token"]):
            raise ValueError("Le clone ne correspond plus au dernier checkpoint vérifié")
        row=con.execute(
            "SELECT id FROM pra_lvm_guest_evidence WHERE protected_vm_id=? "
            "AND restore_run_id=? AND evidence_type='os_console_login'",
            (vm_id,restore_run_id)).fetchone()
        if row:
            raise ValueError("Validation OS déjà enregistrée pour ce clone")
        result_id=con.execute(
            "INSERT INTO pra_lvm_guest_evidence "
            "(protected_vm_id,restore_run_id,test_vmid,checkpoint_token,actor,"
            "evidence_type,note,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (vm_id,restore_run_id,restore["test_vmid"],restore["checkpoint_token"],
             actor,"os_console_login",note.strip(),db.now())).lastrowid
    db.audit(actor,"pra.lvm.guest.os_login_observed",vm_id,
             "evidence="+str(result_id)+" restore="+str(restore_run_id)
             +" clone="+str(restore["test_vmid"]))
    return {"id":result_id,"status":"observed","guest_os_observed":True,
            "application_verified":False,"production_failover_authorized":False}


def history(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        return [dict(r) for r in con.execute(
            "SELECT id,restore_run_id,test_vmid,checkpoint_token,actor,"
            "evidence_type,note,created_at FROM pra_lvm_guest_evidence "
            "WHERE protected_vm_id=? ORDER BY id DESC LIMIT 30",(vm_id,)
        ).fetchall()]


def current(vm_id):
    """Historical human evidence only, tied to current verified checkpoint."""
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        latest=con.execute(
            "SELECT checkpoint_token,status FROM pra_lvm_lab_runs "
            "WHERE protected_vm_id=? AND mode IN ('full','incremental') "
            "ORDER BY id DESC LIMIT 1",(vm_id,)).fetchone()
        if not latest or latest["status"]!="completed":
            return None
        evidence=con.execute(
            "SELECT id,test_vmid,checkpoint_token,actor,note,created_at "
            "FROM pra_lvm_guest_evidence WHERE protected_vm_id=? "
            "AND checkpoint_token=? AND evidence_type='os_console_login' "
            "ORDER BY id DESC LIMIT 1",(vm_id,latest["checkpoint_token"])).fetchone()
        return dict(evidence) if evidence else None
