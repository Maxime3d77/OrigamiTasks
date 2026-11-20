"""Exercise REAL QEMU qcow2 full/incremental checkpoint chains without Proxmox.

This is a deterministic, non-destructive host-local smoke test: all disks
live in pytest's temporary directory. Run this BEFORE any multi-host
replication trial, but never mistake a passing result for a DR qualification.

    python3 -m pytest -q tests/test_pra_real_qemu_checkpoints.py

Requires qemu-img and qemu-io from QEMU packages. Skips if unavailable.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

HOST = Path(__file__).resolve().parents[1] / "agents" / "linux"
spec = importlib.util.spec_from_file_location(
    "pra_checkpoint_real_qemu", HOST / "pra_checkpoint.py")
cp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cp
spec.loader.exec_module(cp)

REQUIRED = ("qemu-img", "qemu-io")
pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in REQUIRED),
    reason="qemu-img and qemu-io required for the real QCOW2 integration test",
)


def qemu_io(path, operation):
    cmd = ["qemu-io", "-f", "qcow2", "-c", operation, str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (
        "QEMU I/O failed: " + result.stderr[-500:] + result.stdout[-500:])
    return result.stdout


def backing_chain(image):
    result = subprocess.run(
        ["qemu-img", "info", "-f", "qcow2", "--backing-chain",
         "--output=json", str(image)],
        capture_output=True, text=True, timeout=60, check=True)
    return json.loads(result.stdout)


def test_real_qemu_full_then_incremental_preserves_two_generations(tmp_path):
    store = cp.CheckpointStore(tmp_path, target_vmid=901001)
    initial = store.begin("full", {"scsi0": 16 * 1024**2}, "qemu-process-gen-1")
    base = tmp_path / initial["disks"]["scsi0"]["file"]
    qemu_io(base, "write -P 0x11 0 4096")
    qemu_io(base, "write -P 0x33 4096 4096")
    full = store.commit(initial["token"])
    assert full["status"] == "committed"

    incremental = store.begin(
        "incremental", {"scsi0": 16 * 1024**2}, "qemu-process-gen-1")
    overlay = tmp_path / incremental["disks"]["scsi0"]["file"]
    assert len(backing_chain(overlay)) == 2
    # Update only one part of the virtual disk, leave the old generation intact.
    qemu_io(overlay, "write -P 0x22 4096 4096")
    current = store.commit(incremental["token"])
    assert current["status"] == "committed"
    assert current["previous_token"] == full["token"]

    qemu_io(overlay, "read -P 0x11 0 4096")
    qemu_io(overlay, "read -P 0x22 4096 4096")
    qemu_io(base, "read -P 0x11 0 4096")
    qemu_io(base, "read -P 0x33 4096 4096")
    assert len(backing_chain(overlay)) == 2
    assert store.current()["token"] == incremental["token"]


def test_real_qemu_abort_does_not_replace_last_committed_checkpoint(tmp_path):
    store = cp.CheckpointStore(tmp_path, target_vmid=901002)
    initial = store.begin("full", {"scsi0": 8 * 1024**2}, "gen-1")
    base = tmp_path / initial["disks"]["scsi0"]["file"]
    qemu_io(base, "write -P 0x55 0 4096")
    store.commit(initial["token"])

    pending = store.begin("incremental", {"scsi0": 8 * 1024**2}, "gen-1")
    overlay = tmp_path / pending["disks"]["scsi0"]["file"]
    qemu_io(overlay, "write -P 0x66 0 4096")

    result = store.abort(pending["token"])
    assert result["last_checkpoint_preserved"] is True
    assert not overlay.exists()
    assert store.current()["token"] == initial["token"]
    qemu_io(base, "read -P 0x55 0 4096")


def test_real_qemu_rejects_inconsistent_or_unavailable_base(tmp_path):
    store = cp.CheckpointStore(tmp_path, target_vmid=901003)
    initial = store.begin("full", {"scsi0": 8 * 1024**2}, "gen-1")
    store.commit(initial["token"])
    with pytest.raises(cp.CheckpointError, match="FULL reseed"):
        store.begin("incremental", {"scsi0": 8 * 1024**2}, "gen-2")

    base = tmp_path / initial["disks"]["scsi0"]["file"]
    base.unlink()
    with pytest.raises(cp.CheckpointError, match="Missing committed backing"):
        store.begin("incremental", {"scsi0": 8 * 1024**2}, "gen-1")
    assert store.current()["token"] == initial["token"]
