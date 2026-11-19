"""Checkpoint publication must be atomic and preserve the last valid point."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

FILE = Path(__file__).resolve().parents[1] / "agents/linux/pra_checkpoint.py"
spec = importlib.util.spec_from_file_location("pra_checkpoint", FILE)
cp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cp
spec.loader.exec_module(cp)


@pytest.fixture
def store(tmp_path, monkeypatch):
    calls = []
    def fake(*argv):
        calls.append(argv)
        if argv[1] == "create":
            Path(argv[-2] if argv[-1].isdigit() else argv[-1]).touch()
        return ""
    monkeypatch.setattr(cp, "_command", fake)
    return cp.CheckpointStore(tmp_path), calls


def test_full_then_incremental_is_cow_and_commit_only_after_check(store):
    s, commands = store
    first = s.begin("full", {"scsi0": 2048, "scsi1": 4096}, "vm-generation-001")
    assert s.current() is None
    assert len(first["disks"]) == 2
    committed = s.commit(first["token"])
    assert committed["status"] == "committed"
    assert all(c[1] == "check" for c in commands[-2:])
    second = s.begin("incremental", {"scsi0": 2048, "scsi1": 4096},
                     "vm-generation-001")
    assert s.current()["token"] == first["token"]
    create = [c for c in commands if c[1] == "create"]
    assert len(create) == 4
    assert all("-b" in c and "-F" in c for c in create[-2:])
    s.commit(second["token"])
    assert s.current()["token"] == second["token"]
    assert all(Path(s.root / item["file"]).is_file()
               for item in committed["disks"].values())


def test_aborted_delta_preserves_base(store):
    s, _ = store
    first = s.begin("full", {"scsi0": 2048}, "vm-001")
    s.commit(first["token"])
    delta = s.begin("incremental", {"scsi0": 2048}, "vm-001")
    assert s.abort(delta["token"])["last_checkpoint_preserved"]
    assert s.current()["token"] == first["token"]
    assert not (s.root / delta["disks"]["scsi0"]["file"]).exists()


def test_lost_base_requires_reseed(store):
    s, _ = store
    with pytest.raises(cp.CheckpointError, match="FULL reseed required"):
        s.begin("incremental", {"scsi0": 2048}, "vm-001")
    first = s.begin("full", {"scsi0": 2048}, "vm-001")
    s.commit(first["token"])
    with pytest.raises(cp.CheckpointError, match="FULL reseed required"):
        s.begin("incremental", {"scsi0": 2048}, "new-vm")
    with pytest.raises(cp.CheckpointError, match="FULL reseed required"):
        s.begin("incremental", {"scsi0": 8192}, "vm-001")


def test_pending_checkpoint_blocks_new_attempt(store):
    s, _ = store
    first = s.begin("full", {"scsi0": 2048}, "vm-001")
    with pytest.raises(cp.CheckpointError, match="Unresolved pending"):
        s.begin("full", {"scsi0": 2048}, "vm-001")
    s.abort(first["token"])
    assert s.current() is None


def test_check_failure_never_publishes(store, monkeypatch):
    s, _ = store
    first = s.begin("full", {"scsi0": 2048}, "vm-001")
    monkeypatch.setattr(cp, "_command",
                        lambda *args: (_ for _ in ()).throw(cp.CheckpointError("qemu-img rejected"))
                        if args[1] == "check" else "")
    with pytest.raises(cp.CheckpointError, match="qemu-img rejected"):
        s.commit(first["token"])
    assert s.current() is None
    assert s.pending.exists()


def test_invalid_slot_or_nonwritable_root_rejected(store):
    s, _ = store
    with pytest.raises(cp.CheckpointError, match="Invalid disk slot"):
        s.begin("full", {"../../foo": 2048}, "vm-001")
    with pytest.raises(cp.CheckpointError, match="Invalid disk sizes"):
        s.begin("full", {"scsi0": -10}, "vm-001")
    assert s.current() is None
