"""Regression: successful PRA publish may NEVER delete historical PVE volumes.

Tests use isolated files and mocked Proxmox; no VM, LV or external host is touched.
"""
from pathlib import Path
import importlib.util
import sys

import pytest

MODULE_DIR = Path(__file__).resolve().parents[1] / "agents/linux"
sys.path.insert(0, str(MODULE_DIR))

def _load(module, filename):
    spec = importlib.util.spec_from_file_location(module, MODULE_DIR / filename)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[module] = obj
    spec.loader.exec_module(obj)
    return obj

worker = _load("pra_host_worker_retention", "pra_host_worker.py")
checkpoint = _load("pra_checkpoint_retention", "pra_checkpoint.py")


def test_checkpoint_gc_always_fails_closed_even_when_owned_images_exist(tmp_path):
    path = tmp_path / "vm-321-disk-15.qcow2"
    path.write_bytes(b"previous-valid-checkpoint")
    store = checkpoint.CheckpointStore(tmp_path, target_vmid=321)
    with pytest.raises(checkpoint.CheckpointError, match="disabled"):
        store.garbage_collect()
    assert path.read_bytes() == b"previous-valid-checkpoint"


def test_failed_qm_status_does_not_mistake_existing_vm_for_free_vmid(monkeypatch):
    def fake(*args, **kwargs):
        if args[:2] == ("qm", "status"):
            raise worker.HostError("PVE status unavailable")
        if args[:2] == ("qm", "list"):
            return "  VMID NAME STATUS MEM\n  321 another-vm stopped 1024\n"
        raise AssertionError(args)
    monkeypatch.setattr(worker, "cmd", fake)
    with pytest.raises(worker.HostError, match="exists but cannot be queried"):
        worker._is_target_stopped(321)


def test_publish_preserves_every_old_checkpoint_and_keeps_standby_off(tmp_path,monkeypatch):
    token = "a" * 32
    previous = tmp_path / "vm-321-disk-14.qcow2"
    current = tmp_path / "vm-321-disk-15.qcow2"
    previous.write_bytes(b"previous-snapshot-data")
    current.write_bytes(b"newly-replicated-data")

    class FakeStore:
        root = tmp_path
        def current(self):
            return {"token":token,"status":"committed",
                    "disks":{"scsi0":{"file":current.name,"size":1024**3}}}
        def garbage_collect(self, *args, **kwargs):
            raise AssertionError("Automatic cleanup is forbidden during publish")

    calls = []
    def fake_cmd(*args,**kwargs):
        calls.append(args)
        if args[:2] == ("qm", "status"):
            return "status: stopped"
        return ""
    monkeypatch.setattr(worker, "_store", lambda *_: FakeStore())
    monkeypatch.setattr(worker, "_is_target_stopped", lambda _ : True)
    monkeypatch.setattr(worker, "_vm_config",
        lambda _: {"description":"ORIGAMITASKS_PRA_17","onboot":"0"})
    monkeypatch.setattr(worker, "cmd", fake_cmd)

    result = worker.target_publish({
        "vmid":321,"storage":"example-dir","token":token,
        "protected_vm_id":17,"name":"REP_example",
        "config":{"bios":"seabios","memory":"1024","cores":"1","sockets":"1"},
    })
    assert result["standby_configured"] is True
    assert result["vm_started"] is False
    assert previous.read_bytes() == b"previous-snapshot-data"
    assert current.read_bytes() == b"newly-replicated-data"
    assert not any(args[:2] in (("qm","start"),("qm","stop"),("pvesm","free"))
                   for args in calls)
