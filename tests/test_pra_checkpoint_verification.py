"""Offline checkpoint verification is read-only and never authorizes failover.

The tests use mocked qemu-img / qm, not a real Proxmox node.
"""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HOST = Path(__file__).resolve().parents[1] / "agents/linux"
sys.path.insert(0, str(HOST))
spec = importlib.util.spec_from_file_location("pra_host_worker_verify", HOST / "pra_host_worker.py")
worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker
spec.loader.exec_module(worker)

TOKEN = "c" * 32


@pytest.fixture
def verification(tmp_path, monkeypatch):
    file = tmp_path / "vm-321-disk-170.qcow2"
    file.write_bytes(b"mocked qcow2")
    checkpoint = {
        "token": TOKEN, "status": "committed", "target_vmid": 321,
        "disks": {"scsi0": {"file": file.name, "size": 1024**3}},
    }
    class Store:
        root = tmp_path
        pending = tmp_path / "pending.json"
        def current(self):
            return checkpoint
    config = {
        "onboot": "0",
        "description": "ORIGAMITASKS_PRA_17",
        "scsi0": "target-dir:321/" + file.name,
    }
    commands = []
    def fake_cmd(*args, **kwargs):
        commands.append(args)
        if args[:2] == ("qm", "status"):
            return "status: stopped"
        if args[:2] == ("qemu-img", "info"):
            return json.dumps([{"filename": str(file), "format": "qcow2"}])
        if args[:2] == ("qemu-img", "check"):
            return "No errors were found on the image."
        raise AssertionError(args)
    monkeypatch.setattr(worker, "_store", lambda *_: Store())
    monkeypatch.setattr(worker, "_is_target_stopped", lambda _: True)
    monkeypatch.setattr(worker, "_vm_config", lambda _: config)
    monkeypatch.setattr(worker, "cmd", fake_cmd)
    return checkpoint, config, file, commands


def payload():
    return {"vmid": 321, "storage": "target-dir",
            "token": TOKEN, "protected_vm_id": 17}


def test_offline_verified_image_never_authorizes_failover(verification):
    _, _, file, commands = verification
    proof = worker.target_verify(payload())
    assert proof["checkpoint_token"] == TOKEN
    assert proof["disks"] == ["scsi0"]
    assert proof["verified_offline"] and proof["disk_format_checked"]
    assert not proof["boot_tested"]
    assert not proof["application_consistency_verified"]
    assert not proof["failover_authorized"]
    assert not proof["vm_started"]
    assert any(x[:2] == ("qemu-img", "check") for x in commands)
    assert not any(x[:2] in (("qm", "start"), ("qm", "stop"), ("qm", "set"),
                             ("pvesm", "free")) for x in commands)
    assert file.exists()


@pytest.mark.parametrize("change,error", [
    ({"onboot":"1"}, "Automatic|automatic"),
    ({"scsi0":"other-store:321/vm-321-disk-170.qcow2"}, "does not match"),
    ({"net0":"virtio,bridge=vmbr0"}, "network"),
    ({"description":"different owner"}, "ownership"),
])
def test_unsafe_standby_configuration_fails_closed(verification, change, error):
    _, config, _, commands = verification
    config.update(change)
    with pytest.raises(worker.HostError, match=error):
        worker.target_verify(payload())
    assert not any(x[:2] == ("qemu-img", "check") for x in commands)


def test_missing_or_wrong_checkpoint_token_rejected(verification):
    checkpoint, _, _, _ = verification
    checkpoint["token"] = "d" * 32
    with pytest.raises(worker.HostError, match="No matching committed"):
        worker.target_verify(payload())


def test_backing_chain_outside_managed_store_rejected(verification,monkeypatch,tmp_path):
    checkpoint, config, file, commands = verification
    external=tmp_path/"another-image.qcow2"
    external.write_bytes(b"external")
    def bad_cmd(*args,**kwargs):
        if args[:2] == ("qm", "status"):
            return "status: stopped"
        if args[:2] == ("qemu-img", "info"):
            return json.dumps([{"filename":str(file)},{"filename":str(external)}])
        if args[:2] == ("qemu-img", "check"):
            raise AssertionError("No checking of untrusted backing chain")
        raise AssertionError(args)
    monkeypatch.setattr(worker, "cmd", bad_cmd)
    # External file is in same tmp_path; use a distinct subdirectory.
    extdir=tmp_path/"outside"
    extdir.mkdir()
    actual=extdir/"base.qcow2"
    actual.write_bytes(b"base")
    def modified_cmd(*args,**kwargs):
        if args[:2] == ("qm", "status"):
            return "status: stopped"
        if args[:2] == ("qemu-img", "info"):
            return json.dumps([{"filename":str(file)},{"filename":str(actual)}])
        raise AssertionError(args)
    monkeypatch.setattr(worker, "cmd", modified_cmd)
    with pytest.raises(worker.HostError, match="outside managed"):
        worker.target_verify(payload())


def test_finish_and_abort_refuse_if_standby_became_running(monkeypatch):
    monkeypatch.setattr(worker, "_is_target_stopped",
        lambda _: (_ for _ in ()).throw(worker.HostError("standby running")))
    monkeypatch.setattr(worker, "_store",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not touch storage")))
    for action in (worker.target_finish, worker.target_abort):
        with pytest.raises(worker.HostError, match="standby running"):
            action({"vmid":321,"storage":"test-dir","token":TOKEN})


@pytest.mark.parametrize("special", [
    {"bios": "ovmf"}, {"efidisk0": "target:efi0"},
    {"tpmstate0": "target:tpm0"}, {"unused7": "target:orphan0"},
    {"hostpci1": "0000:01:00.0"},
])
def test_source_probe_rejects_complex_hardware_before_qmp_access(monkeypatch, special):
    config = {"scsi0": "test-dir:disk0", **special}
    monkeypatch.setattr(worker, "_vm_config", lambda _: config)
    monkeypatch.setattr(worker, "_qmp_path",
        lambda _: (_ for _ in ()).throw(AssertionError("QMP must not be touched")))
    with pytest.raises(worker.HostError, match="recovery profile"):
        worker.source_probe({"vmid":321,"slots":["scsi0"]})
