"""Persisted and fail-closed schedule for opted-in LAB QMP-to-LVM protection.

Only a *completed, verified* previous copy permits the next incremental.
An uncertain/failed transfer stops the schedule: no silent full reseed.
This scheduler NEVER starts a guest, fails over, or reverses data direction.
"""
from datetime import datetime, timezone, timedelta

from . import db, dr_protected_vms, pra_lvm_jobs, pra_lvm_activation


def initialize():
    with db.session() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS pra_lvm_schedule_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            protected_vm_id INTEGER NOT NULL,
            previous_run_id INTEGER,
            status TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(protected_vm_id, previous_run_id, status)
        )""")


def _dt(value):
    if not isinstance(value, str):
        raise ValueError("Date de synchronisation précédente absente")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _event(vm_id, prior_id, status, detail):
    with db.session() as con:
        if prior_id is None:
            # One generic error per protection rather than logging every
            # five-second scheduler tick forever.
            existing=con.execute(
                "SELECT 1 FROM pra_lvm_schedule_events WHERE protected_vm_id=? "
                "AND previous_run_id IS NULL AND status=? LIMIT 1",
                (vm_id,status)).fetchone()
            if existing:
                return
        inserted=con.execute(
            "INSERT OR IGNORE INTO pra_lvm_schedule_events"
            "(protected_vm_id,previous_run_id,status,detail,created_at) VALUES (?,?,?,?,?)",
            (vm_id,prior_id,status,str(detail)[:600],db.now())).rowcount
    if inserted:
        db.audit("system","pra.lvm.schedule."+status,vm_id,str(detail)[:400])


def history(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM pra_lvm_schedule_events WHERE protected_vm_id=? "
            "ORDER BY id DESC LIMIT 100",(vm_id,)).fetchall()]


def tick(now=None):
    """One scheduler iteration. No duplicate enqueue under concurrent workers.

    Every enqueue is handled by pra_lvm_jobs.start(), which performs its own
    source/target preflight and an immediate exclusive SQL reservation.
    """
    clock = now or datetime.now(timezone.utc)
    with db.session() as con:
        vm_ids = [r["id"] for r in con.execute(
            "SELECT p.id FROM dr_protected_vms p "
            "WHERE p.engine='qmp_live_incremental' AND p.state='planned' "
            "AND EXISTS (SELECT 1 FROM pra_lvm_activations a "
            "WHERE a.protected_vm_id=p.id)").fetchall()]
    for vm_id in vm_ids:
        try:
            activations = pra_lvm_activation.status(vm_id)
            if not activations or activations[0]["status"] != "completed":
                continue
            vm = dr_protected_vms._one(vm_id)
            minutes = vm["interval_minutes"]
            if type(minutes) is not int or not 5 <= minutes <= 10080:
                continue
            with db.session() as con:
                latest = con.execute(
                    "SELECT id,status,mode,finished_at FROM pra_lvm_lab_runs "
                    "WHERE protected_vm_id=? AND mode IN ('full','incremental') "
                    "ORDER BY id DESC LIMIT 1",(vm_id,)).fetchone()
                if latest is None or latest["status"] != "completed":
                    continue
                if not latest["finished_at"] or clock < _dt(latest["finished_at"]) + timedelta(minutes=minutes):
                    continue
                # Never initiate concurrent QMP DIR activity.
                active = con.execute(
                    "SELECT 1 FROM pra_live_runs WHERE protected_vm_id=? "
                    "AND status IN ('queued','running','uncertain') LIMIT 1",(vm_id,)
                ).fetchone()
                if active:
                    continue
            previous = latest["id"]
            with db.session() as con:
                previously_blocked=con.execute(
                    "SELECT 1 FROM pra_lvm_schedule_events WHERE protected_vm_id=? "
                    "AND previous_run_id=? AND status='blocked' LIMIT 1",
                    (vm_id,previous)).fetchone()
            if previously_blocked:
                continue
            try:
                started = pra_lvm_jobs.start(
                    vm_id,"system:pra-lvm-scheduler",mode="incremental",
                    confirmation=pra_lvm_jobs.LAB_ACK)
            except (ValueError, RuntimeError) as exc:
                # Fail closed, don't repeat the same invalid attempt forever.
                _event(vm_id,previous,"blocked",
                       "Automatisme suspendu : "+type(exc).__name__+" : "+str(exc)[:450])
                continue
            _event(vm_id,previous,"queued",
                   "Copie incrémentale programmée : run #"+str(started["run_id"]))
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            # Don't crash the generic workflow scheduler.
            _event(vm_id,None,"error","Contrôle périodique impossible : "+type(exc).__name__)
