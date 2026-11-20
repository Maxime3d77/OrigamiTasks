"""Fail-closed NBD writer shutdown: preserve images if identity is uncertain."""
import importlib.util
from pathlib import Path
import signal
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1] / "agents/linux"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("pra_host_worker_nbd_guard", ROOT / "pra_host_worker.py")
worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker
spec.loader.exec_module(worker)

IMAGE = "vm-321-disk-998.qcow2"


class Store:
    def __init__(self, root):
        self.root = root


def test_missing_pid_refuses_to_claim_writer_closed(tmp_path):
    store = Store(tmp_path)
    (tmp_path / IMAGE).write_bytes(b"known checkpoint")
    with pytest.raises(worker.HostError, match="PID missing"):
        worker._kill_export(store, "scsi0", image_name=IMAGE)
    assert (tmp_path / IMAGE).read_bytes() == b"known checkpoint"


def test_foreign_pid_cannot_kill_another_disk_process(tmp_path, monkeypatch):
    store = Store(tmp_path)
    (tmp_path / ".origami-scsi0.pid").write_text("4242")
    def fake_read_bytes(self):
        return b"/usr/bin/qemu-nbd\x00--format=qcow2\x00" + str(tmp_path / "vm-321-disk-999.qcow2").encode() + b"\x00"
    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    monkeypatch.setattr(worker.os, "kill",
        lambda *_: (_ for _ in ()).throw(AssertionError("Wrong PID killed!")))
    with pytest.raises(worker.HostError, match="identity mismatch"):
        worker._kill_export(store, "scsi0", image_name=IMAGE)
    assert (tmp_path / ".origami-scsi0.pid").exists()


def test_writing_pid_remains_pending_when_shutdown_not_proven(tmp_path,monkeypatch):
    store=Store(tmp_path)
    (tmp_path/".origami-scsi0.pid").write_text("4242")
    monkeypatch.setattr(Path, "read_bytes",
        lambda self: b"/usr/bin/qemu-nbd\x00" + str(tmp_path/IMAGE).encode() + b"\x00")
    calls=[]
    def killer(pid, sig):
        calls.append((pid,sig))
        return None
    monkeypatch.setattr(worker.os, "kill", killer)
    monkeypatch.setattr(worker.time, "sleep", lambda *_: None)
    with pytest.raises(worker.HostError, match="still active"):
        worker._kill_export(store,"scsi0",image_name=IMAGE)
    assert calls[0] == (4242, signal.SIGTERM)
    assert (tmp_path/".origami-scsi0.pid").exists()


def test_terminated_exact_writer_can_be_confirmed(tmp_path,monkeypatch):
    store=Store(tmp_path)
    (tmp_path/".origami-scsi0.pid").write_text("4242")
    monkeypatch.setattr(Path, "read_bytes",
        lambda self: b"/usr/bin/qemu-nbd\x00" + str(tmp_path/IMAGE).encode() + b"\x00")
    def killer(pid, sig):
        if sig == 0:
            raise ProcessLookupError()
    monkeypatch.setattr(worker.os, "kill", killer)
    worker._kill_export(store,"scsi0",image_name=IMAGE)
    assert not (tmp_path/".origami-scsi0.pid").exists()


def test_partial_nbd_start_never_auto_discards_staging(tmp_path,monkeypatch):
    class FakeStore:
        def __init__(self):
            self.abort_called=False
        def begin(self, mode, sizes, generation):
            return {"token":"a"*32,
                    "disks":{"scsi0":{"file":IMAGE}}}
        def abort(self, token):
            self.abort_called=True
            raise AssertionError("Staged image must be preserved")
    store=FakeStore()
    monkeypatch.setattr(worker,"_is_target_stopped",lambda _: True)
    monkeypatch.setattr(worker,"_store",lambda *_:store)
    monkeypatch.setattr(worker.shutil,"disk_usage",
        lambda _: type("Usage", (), {"free":100*1024**3})())
    store.root=tmp_path
    monkeypatch.setattr(worker.socket.socket, "bind",
        lambda *args,**kwargs: (_ for _ in ()).throw(RuntimeError("NBD launch failed")))
    with pytest.raises(RuntimeError,match="NBD launch failed"):
        worker.target_begin({
            "vmid":321,"storage":"test-dir","mode":"full",
            "sizes":{"scsi0":1024**2},"source_vm_uuid":"vm-321",
        })
    assert not store.abort_called
