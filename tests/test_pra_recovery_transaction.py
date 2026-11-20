"""Fault-injection contracts for future REAL PRA recovery adapters.

No PVE, fencing or witness operation is performed by these tests. A verified
source fence and independent witness are required before the mock target can
start. These tests alone DO NOT prove production failover safety.
"""
import pytest

from app.pra_recovery_transaction import (
    CopyProof, DRUnsafe, FenceProof, Lease, Protection, RecoveryController,
)


class FakeApproval:
    def __init__(self, events):
        self.events = events
        self.allowed = True

    def require_approval(self, ident, action, actor):
        self.events.append(("approve", ident, action, actor))
        if not self.allowed:
            raise DRUnsafe("Approval denied")


class FakeWitness:
    def __init__(self, events):
        self.events = events
        self.prepared = False
        self.current = None
        self.fail_on = None

    def prepare(self, ident, old, new, epoch):
        self.events.append(("prepare", ident, old, new, epoch))
        if self.fail_on == "prepare":
            raise DRUnsafe("Witness partition")
        if self.prepared:
            raise DRUnsafe("Concurrent witness transfer")
        self.prepared = True
        return "witness-session-1"

    def verify_prepared(self, token, ident, epoch):
        self.events.append(("verify_prepared", token))
        assert token == "witness-session-1"
        if self.fail_on == "verify_prepared":
            raise DRUnsafe("Witness lacks quorum")

    def commit(self, token, fence, generation):
        self.events.append(("commit", fence, generation))
        if self.fail_on == "commit":
            raise DRUnsafe("Witness commit unavailable")
        if fence.confirmed_off is not True:
            raise DRUnsafe("Source not fenced")
        self.current = Lease("protection-42", "cluster-b", 2, "signed-receipt")
        return self.current

    def verify_current(self, lease):
        self.events.append(("verify_current", lease.epoch))
        if self.fail_on == "verify_current":
            raise DRUnsafe("Lease expired")
        if self.current != lease:
            raise DRUnsafe("Stale lease")


class FakeFence:
    def __init__(self, events):
        self.events = events
        self.confirmed = True
        self.fail_on = None

    def fence_and_verify(self, cluster, vmid, token):
        self.events.append(("fence", cluster, vmid, token))
        if self.fail_on == "fence":
            raise DRUnsafe("Source unreachable, fencing uncertain")
        return FenceProof(cluster, vmid, self.confirmed, "hardware-power-off-attestation")

    def verify_proof(self, proof, transfer):
        self.events.append(("verify_fence", proof.confirmed_off))
        if self.fail_on == "verify_fence":
            raise DRUnsafe("Fence proof expired")


class FakeVM:
    def __init__(self, events):
        self.events = events
        self.boot_ok = True
        self.app_ok = True
        self.stopped = True
        self.fail_on = None

    def verify_copy(self, cluster, vmid, generation):
        self.events.append(("verify_copy", cluster, vmid, generation))
        if self.fail_on == "verify_copy":
            raise DRUnsafe("No committed checkpoint")
        return CopyProof(generation, cluster, True, self.boot_ok, self.app_ok)

    def require_stopped(self, cluster, vmid):
        self.events.append(("require_stopped", cluster, vmid))
        if not self.stopped:
            raise DRUnsafe("Target VM already running")

    def arm_exclusive_start_guard(self, cluster, vmid, lease):
        self.events.append(("arm_guard", cluster, vmid, lease.epoch))
        if self.fail_on == "arm_guard":
            raise DRUnsafe("Hypervisor start guard missing")

    def guarded_start(self, cluster, vmid, lease):
        self.events.append(("start", cluster, vmid, lease.epoch))
        if self.fail_on == "start":
            raise DRUnsafe("Start denied by hypervisor")

    def verify_running_with_guard(self, cluster, vmid, lease):
        self.events.append(("verify_running", cluster, vmid, lease.epoch))
        if self.fail_on == "verify_running":
            raise DRUnsafe("Failed to prove guarded run")


@pytest.fixture
def rig():
    events = []
    approval = FakeApproval(events)
    witness = FakeWitness(events)
    fence = FakeFence(events)
    vms = FakeVM(events)
    controller = RecoveryController(approval, witness, fence, vms)
    protection = Protection(
        "protection-42", "cluster-a", "cluster-b",
        110, 111, "cluster-a", 1)
    return events, approval, witness, fence, vms, controller, protection


def test_positive_path_orders_fence_before_new_authority_and_guarded_start(rig):
    events, _, witness, _, _, controller, protection = rig
    lease = controller.promote(protection, "cluster-b", "checkpoint-7", "operator")
    assert lease.epoch == 2
    assert lease.cluster_id == "cluster-b"
    names = [e[0] for e in events]
    assert names.index("verify_copy") < names.index("prepare")
    assert names.index("prepare") < names.index("fence")
    assert names.index("fence") < names.index("commit")
    assert names.index("commit") < names.index("arm_guard")
    assert names.index("arm_guard") < names.index("start")
    assert names.index("verify_current") < names.index("start")
    assert names[-1] == "verify_running"


@pytest.mark.parametrize("failure", [
    "approval", "boot", "application", "running_target", "copy",
    "witness_prepare", "witness_quorum", "fence", "fence_proof",
    "fence_false", "witness_commit", "lease", "start_guard",
])
def test_every_unproven_transition_must_not_start_the_target(rig, failure):
    events, approval, witness, fence, vms, controller, protection = rig
    if failure == "approval": approval.allowed = False
    elif failure == "boot": vms.boot_ok = False
    elif failure == "application": vms.app_ok = False
    elif failure == "running_target": vms.stopped = False
    elif failure == "copy": vms.fail_on = "verify_copy"
    elif failure == "witness_prepare": witness.fail_on = "prepare"
    elif failure == "witness_quorum": witness.fail_on = "verify_prepared"
    elif failure == "fence": fence.fail_on = "fence"
    elif failure == "fence_proof": fence.fail_on = "verify_fence"
    elif failure == "fence_false": fence.confirmed = False
    elif failure == "witness_commit": witness.fail_on = "commit"
    elif failure == "lease": witness.fail_on = "verify_current"
    elif failure == "start_guard": vms.fail_on = "arm_guard"

    with pytest.raises(DRUnsafe):
        controller.promote(protection, "cluster-b", "checkpoint-7", "operator")
    assert not any(e[0] == "start" for e in events)


def test_unapproved_or_invalid_destination_never_calls_an_adapter(rig):
    events, _, _, _, _, controller, protection = rig
    with pytest.raises(DRUnsafe, match="Destination"):
        controller.promote(protection, "cluster-unknown", "checkpoint-7", "operator")
    assert events == []
    with pytest.raises(DRUnsafe, match="already"):
        controller.promote(protection, "cluster-a", "checkpoint-7", "operator")
    assert events == []


def test_guarded_start_failure_never_restarts_old_master(rig):
    events, _, _, _, vms, controller, protection = rig
    vms.fail_on = "start"
    with pytest.raises(DRUnsafe, match="Start denied"):
        controller.promote(protection, "cluster-b", "checkpoint-7", "operator")
    assert "fence" in [x[0] for x in events]
    assert "commit" in [x[0] for x in events]
    assert not any(x[0] in ("start_old", "unfence", "rollback") for x in events)


def test_missing_independent_adapters_rejected():
    with pytest.raises(DRUnsafe, match="Missing"):
        RecoveryController(None, None, None, None)


def test_only_one_generation_and_valid_protection_allowed(rig):
    events, _, _, _, _, controller, protection = rig
    with pytest.raises(DRUnsafe, match="generation"):
        controller.promote(protection, "cluster-b", "", "operator")
    assert events == []
    with pytest.raises(DRUnsafe, match="identity"):
        Protection("bad", "same", "same", 110, 111, "same", 1)
