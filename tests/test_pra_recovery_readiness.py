"""Recovery evidence gates: no false PRA readiness from a completed copy.

The tests use mocked discovery and never start, stop or access a PVE VM.
"""
from fastapi.testclient import TestClient

from app import pra_recovery_readiness as gates
from app.main import app


def fake_vm(monkeypatch, **changes):
    vm={"id":71,"source_vmid":117,"target_vmid":8117,"engine":"qmp_live_incremental",
        "state":"planned","source_dc_id":1,"target_dc_id":2}
    vm.update(changes)
    monkeypatch.setattr(gates.dr_protected_vms,"_one",lambda _: vm)
    return vm


def fake_storage(monkeypatch, *, online=True, backend="dir-qcow2-cow"):
    monkeypatch.setattr(gates.dr_protected_vms,"storage_readiness",
                        lambda _: {"backend":backend,"node_online":online,
                                   "node_storage_active":online})


def fake_verified_run(monkeypatch, *, verified=True, completed=True):
    monkeypatch.setattr(gates.pra_live,"latest_status",lambda _:{
        "replication_status":"completed" if completed else "uncertain",
        "offline_image_verified":verified,
        "last_successful_sync":"2026-11-19T10:00:00+00:00" if verified else None})


def test_all_structural_checks_cannot_authorize_failover_without_witness_and_fence(monkeypatch):
    fake_vm(monkeypatch)
    fake_storage(monkeypatch)
    fake_verified_run(monkeypatch)
    report=gates.assess(71)
    checks={c["code"]:c["passed"] for c in report["checks"]}
    assert checks["disk_checkpoint"] is True
    assert checks["destination"] is True
    assert checks["replication_engine"] is True
    for gate in ("isolated_restore","application_health","witness","fencing",
                 "start_guard","network","failback"):
        assert checks[gate] is False
    assert report["last_structural_sync"] is not None
    assert report["readiness"]=="blocked"
    assert report["can_promote"] is False
    assert report["can_failback"] is False


def test_old_unverified_checkpoint_cannot_be_used_as_recovery_proof(monkeypatch):
    fake_vm(monkeypatch)
    fake_storage(monkeypatch)
    fake_verified_run(monkeypatch,verified=False)
    report=gates.assess(71)
    assert "disk_checkpoint" in report["blockers"]
    assert report["last_structural_sync"] is None
    assert not report["can_promote"]


def test_unreachable_storage_still_explains_failover_blockers(monkeypatch):
    fake_vm(monkeypatch)
    fake_verified_run(monkeypatch)
    def fail(_):
        raise RuntimeError("API offline")
    monkeypatch.setattr(gates.dr_protected_vms,"storage_readiness",fail)
    report=gates.assess(71)
    assert report["storage_inspection_error"]=="RuntimeError"
    assert "destination" in report["blockers"]
    assert report["target_backend"] is None
    assert not report["can_promote"]


def test_lvm_candidate_not_considered_ready_to_receive(monkeypatch):
    fake_vm(monkeypatch)
    fake_storage(monkeypatch,backend="lvm-qcow2-staging")
    fake_verified_run(monkeypatch)
    report=gates.assess(71)
    assert "replication_engine" in report["blockers"]
    assert not report["can_promote"]


def test_paused_policy_is_blocked(monkeypatch):
    fake_vm(monkeypatch,state="paused")
    fake_storage(monkeypatch)
    fake_verified_run(monkeypatch)
    assert "policy" in gates.assess(71)["blockers"]


def test_recovery_readiness_endpoint_requires_auth():
    with TestClient(app) as client:
        response=client.get("/api/dr/vms/71/recovery-readiness")
        assert response.status_code in (401,403)
