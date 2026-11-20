"""Mock-based contract tests for the QMP live incremental transport.

They deliberately DO NOT run destructive QMP commands on a Proxmox host.
"""
import importlib.util
from pathlib import Path

import pytest

FILE = Path(__file__).resolve().parents[1] / "agents/linux/pra_qmp_incremental.py"
spec = importlib.util.spec_from_file_location("pra_qmp_incremental", FILE)
pra = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = pra
spec.loader.exec_module(pra)


class FakeQMP:
    def __init__(self, bitmap_present=False, inconsistent=False, job_error=None):
        self.calls = []
        self.events = []
        self.bitmap_present = bitmap_present
        self.inconsistent = inconsistent
        self.job_error = job_error
        self.jobs = []
        self.is_source = True

    def execute(self, command, arguments=None):
        self.calls.append((command, arguments))
        if command == "query-named-block-nodes":
            return [{"node-name": "drive-scsi0",
                     "dirty-bitmaps": ([{"name": "origami_106_scsi0",
                                        "recording": True,
                                        "inconsistent": self.inconsistent,
                                        "busy": False}]
                                       if self.bitmap_present else [])},
                    {"node-name": "drive-scsi1",
                     "dirty-bitmaps": ([{"name": "origami_106_scsi1",
                                        "recording": True,
                                        "inconsistent": self.inconsistent,
                                        "busy": False}]
                                       if self.bitmap_present else [])}]
        if command == "transaction":
            actions = arguments["actions"]
            self.jobs = [a["data"]["job-id"] for a in actions if a["type"] == "blockdev-backup"]
            return {}
        if command == "query-jobs":
            return [{"id": item, "status": "concluded",
                     **({"error": self.job_error} if self.job_error else {})}
                    for item in self.jobs]
        if command == "job-dismiss":
            self.jobs.remove(arguments["id"])
        return {}


def disk(slot="scsi0", port=10809):
    return pra.Disk(source="drive-" + slot, bitmap="origami_106_" + slot,
                    host="127.0.0.1", port=port)


def test_full_live_copy_uses_one_qmp_transaction_and_does_not_start_vm():
    qmp = FakeQMP()
    result = pra.backup(qmp, [disk(), disk("scsi1", 10810)], "full", timeout=2)
    assert result["qmp_jobs_completed"] is True
    assert result["target_checkpoint_committed"] is False
    assert result["standby_vm_started"] is False
    tx = next(args for cmd, args in qmp.calls if cmd == "transaction")
    assert "properties" not in tx  # QEMU rejects grouped with bitmap-add actions
    assert [a["type"] for a in tx["actions"]].count("blockdev-backup") == 2
    assert [a["type"] for a in tx["actions"]].count("block-dirty-bitmap-add") == 2
    assert all(a["data"]["sync"] == "full" for a in tx["actions"]
               if a["type"] == "blockdev-backup")
    assert not any(cmd in ("stop", "cont", "system_powerdown", "system_reset")
                   for cmd, _ in qmp.calls)


def test_incremental_uses_source_dirty_bitmaps():
    qmp = FakeQMP(bitmap_present=True)
    pra.backup(qmp, [disk()], "incremental", timeout=2)
    tx = next(args for cmd, args in qmp.calls if cmd == "transaction")
    assert len(tx["actions"]) == 1
    assert tx["actions"][0]["data"]["sync"] == "incremental"
    assert tx["actions"][0]["data"]["bitmap"] == "origami_106_scsi0"
    assert tx["properties"]["completion-mode"] == "grouped"


def test_lost_bitmap_fails_before_attaching_nbd():
    qmp = FakeQMP(bitmap_present=False)
    with pytest.raises(pra.ReplicationError, match="FULL reseed required"):
        pra.backup(qmp, [disk()], "incremental", timeout=2)
    assert not any(cmd == "blockdev-add" for cmd, _ in qmp.calls)


def test_inconsistent_bitmap_fails_before_writes():
    qmp = FakeQMP(bitmap_present=True, inconsistent=True)
    with pytest.raises(pra.ReplicationError, match="FULL reseed required"):
        pra.backup(qmp, [disk()], "incremental", timeout=2)
    assert not any(cmd == "transaction" for cmd, _ in qmp.calls)


def test_failed_job_must_not_be_published():
    qmp = FakeQMP(bitmap_present=True, job_error="I/O failure")
    with pytest.raises(pra.ReplicationError, match="failed"):
        pra.backup(qmp, [disk()], "incremental", timeout=2)
    assert any(cmd == "blockdev-del" for cmd, _ in qmp.calls)


def test_nbd_requires_ssh_loopback_and_valid_ranges():
    with pytest.raises(pra.ReplicationError, match="SSH tunnel"):
        pra.Disk(source="drive-scsi0", bitmap="origami_106_scsi0",
                 host="192.0.2.5", port=10809).validate()
    with pytest.raises(pra.ReplicationError, match="Invalid NBD port"):
        pra.Disk(source="drive-scsi0", bitmap="origami_106_scsi0",
                 host="127.0.0.1", port=70000).validate()
    with pytest.raises(pra.ReplicationError, match="Invalid QEMU source"):
        pra.Disk(source=";evil", bitmap="origami_106_scsi0",
                 host="127.0.0.1", port=10809).validate()


def test_duplicate_disk_names_are_rejected():
    with pytest.raises(pra.ReplicationError, match="Duplicate"):
        pra.backup(FakeQMP(), [disk(), disk()], "full", timeout=2)
