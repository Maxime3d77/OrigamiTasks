"""Fail-closed periodic LAB schedule contracts."""
from datetime import datetime, timezone, timedelta
import pytest
from app import pra_lvm_schedule


class Cursor:
    def __init__(self, rows=None): self.rows=rows or []
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None


class Session:
    def __init__(self, vmid=17, previous=None, blocked=False, active=False):
        self.vmid=vmid
        self.previous=previous
        self.blocked=blocked
        self.active=active
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self,sql,args=()):
        if sql.startswith("SELECT p.id"): return Cursor([{"id":self.vmid}])
        if sql.startswith("SELECT id,status,mode,finished_at FROM pra_lvm_lab_runs"):
            return Cursor([self.previous] if self.previous is not None else [])
        if sql.startswith("SELECT 1 FROM pra_live_runs"):
            return Cursor([{"ok":1}] if self.active else [])
        if sql.startswith("SELECT status FROM pra_lvm_publications"):
            return Cursor([{"status":"completed"}])
        if sql.startswith("SELECT 1 FROM pra_lvm_schedule_events"):
            return Cursor([{"ok":1}] if self.blocked else [])
        raise AssertionError("Unexpected scheduler SQL: "+sql[:120])


@pytest.fixture
def setup(monkeypatch):
    last={"id":42,"mode":"full","status":"completed",
          "finished_at":"2026-11-20T14:00:00+00:00"}
    session=Session(previous=last)
    monkeypatch.setattr(pra_lvm_schedule.db,"session",lambda:session)
    monkeypatch.setattr(pra_lvm_schedule.pra_lvm_activation,"status",
                        lambda _: [{"id":10,"status":"completed"}])
    monkeypatch.setattr(pra_lvm_schedule.dr_protected_vms,"_one",
                        lambda _:{"interval_minutes":15})
    calls=[]
    monkeypatch.setattr(pra_lvm_schedule.pra_lvm_jobs,"start",
                        lambda *args,**kw:(calls.append((args,kw)) or {"run_id":45}))
    events=[]
    monkeypatch.setattr(pra_lvm_schedule,"_event",
                        lambda *args:events.append(args))
    return session,last,calls,events


def test_no_incremental_before_interval(setup):
    session,last,calls,events=setup
    pra_lvm_schedule.tick(datetime(2026,11,20,14,14,tzinfo=timezone.utc))
    assert not calls


def test_due_verified_seed_enqueues_only_incremental(setup):
    session,last,calls,events=setup
    pra_lvm_schedule.tick(datetime(2026,11,20,14,15,tzinfo=timezone.utc))
    assert len(calls)==1
    assert calls[0][1]["mode"]=="incremental"
    assert calls[0][1]["confirmation"]=="DISPOSABLE-LAB-ONLY"
    assert events[-1][2]=="queued"


@pytest.mark.parametrize("change",[
    {"status":"uncertain"},{"status":"failed"},{"status":"running"}])
def test_never_automatically_reseed_failed_or_active_copy(setup,change):
    session,last,calls,events=setup
    last.update(change)
    pra_lvm_schedule.tick(datetime(2026,11,20,15,0,tzinfo=timezone.utc))
    assert not calls


def test_blocked_run_requires_human_reconciliation(setup):
    session,last,calls,events=setup
    session.blocked=True
    pra_lvm_schedule.tick(datetime(2026,11,20,15,0,tzinfo=timezone.utc))
    assert not calls


def test_no_run_if_activation_unconfirmed(setup,monkeypatch):
    session,last,calls,events=setup
    monkeypatch.setattr(pra_lvm_schedule.pra_lvm_activation,"status",
                        lambda _: [{"status":"uncertain"}])
    pra_lvm_schedule.tick(datetime(2026,11,20,15,0,tzinfo=timezone.utc))
    assert not calls
