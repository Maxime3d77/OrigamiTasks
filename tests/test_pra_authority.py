"""Safety regression tests for PRA role decisions (no real PVE API)."""
import pytest
from app.pra_authority import (
    PRAError, Phase, Protection, PromotionEvidence,
    override_request_allowed, promote, start_allowed,
)


@pytest.fixture
def protection():
    return Protection("vm-sd-100", "SD-PP", 100, "AU-PP", 900100, "SD-PP",
                      phase=Phase.READY)


def evidence(p, **changes):
    params = dict(witness_quorum=True, old_master_fenced=True,
                  destination_copy_verified=True, replication_stopped=True,
                  independent_witness=True, current_epoch=p.epoch,
                  generation="generation-7")
    params.update(changes)
    return PromotionEvidence(**params)


def test_standby_never_starts(protection):
    assert not start_allowed(protection, "AU-PP", 1, True, True)
    assert start_allowed(protection, "SD-PP", 1, True, True)
    assert not start_allowed(protection, "SD-PP", 1, True, False)
    assert not start_allowed(protection, "SD-PP", 1, False, True)


def test_migration_within_cluster_keeps_master(protection):
    assert protection.node_moved("SD-PP", "sd-pistou") == protection


@pytest.mark.parametrize("missing", [
    "witness_quorum", "old_master_fenced", "destination_copy_verified",
    "replication_stopped", "independent_witness",
])
def test_no_promotion_without_evidence(protection, missing):
    with pytest.raises(PRAError):
        promote(protection, "AU-PP", evidence(protection, **{missing: False}))


def test_stale_epoch_rejected(protection):
    with pytest.raises(PRAError):
        promote(protection, "AU-PP", evidence(protection, current_epoch=0))


def test_failover_and_failback_require_new_fence(protection):
    switched = promote(protection, "AU-PP", evidence(protection))
    assert switched.epoch == 2
    assert switched.master_cluster_id == "AU-PP"
    assert not start_allowed(switched, "SD-PP", 1, True, True)
    assert start_allowed(switched, "AU-PP", 2, True, True)
    with pytest.raises(PRAError):
        promote(switched, "SD-PP", evidence(switched, old_master_fenced=False))
    returned = promote(switched, "SD-PP", evidence(switched, generation="generation-8"))
    assert returned.epoch == 3
    assert returned.master_cluster_id == "SD-PP"


def test_manual_override_is_not_unsafe_bypass():
    assert not override_request_allowed(True, "incident", "INC-1", False, True)
    assert not override_request_allowed(True, "incident", "INC-1", True, False)
    assert override_request_allowed(True, "incident", "INC-1", True, True)
