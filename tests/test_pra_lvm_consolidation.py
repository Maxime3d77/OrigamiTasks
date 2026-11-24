"""Fail-closed legacy LVM chain maintenance tests: no real PVE commands."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1] / "agents/linux"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("pra_lvm_consolidation", ROOT / "pra_lvm_consolidation.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.fixture
def context(tmp_path):
    m = mod.Maintenance.__new__(mod.Maintenance)
    m.vmid = 183
    m.storage = "sd-PP-Proxmox"
    m.protection = 1
    m.root = tmp_path
    m.config_root = tmp_path / "nodes"
    m.state_file = tmp_path / "consolidation.json"
    m.current_file = tmp_path / "current.json"
    m.owned_file = tmp_path / "owned.json"
    m.pending_file = tmp_path / "pending.json"
    m.target = SimpleNamespace(inspect=lambda: {"vgname": "SD-PP-vg"})
    return m


def test_prune_requires_exact_destructive_confirmation(context):
    with pytest.raises(mod.ConsolidationError, match="Confirmation"):
        context.prune("")
    with pytest.raises(mod.ConsolidationError, match="Confirmation"):
        context.prune("yes")


def test_prepare_refuses_existing_maintenance_journal_before_allocation(context, monkeypatch):
    context.state_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(context, "inspect", lambda: ({}, {"scsi0": "s:vm-183-disk-123.qcow2"}))
    monkeypatch.setattr(context, "records", lambda: ({}, {}, "scsi0",
                                                     "s:vm-183-disk-123.qcow2", []))
    with pytest.raises(mod.ConsolidationError, match="déjà présent"):
        context.prepare()


def test_plan_never_activates_inactive_disks(context, monkeypatch):
    old = ["sd-PP-Proxmox:vm-183-disk-100.qcow2",
           "sd-PP-Proxmox:vm-183-disk-101.qcow2"]
    current = {"disks": {"scsi0": {"size": 5 * 1024**3, "volid": old[-1]}}}
    monkeypatch.setattr(context, "inspect", lambda: ({}, {"scsi0": old[-1]}))
    monkeypatch.setattr(context, "records", lambda: (current, {}, "scsi0", old[-1], old))
    monkeypatch.setattr(context, "references", lambda old, slot, conf: {
        old[0]: "unused0", old[1]: "scsi0"})
    monkeypatch.setattr(mod, "path_of", lambda _: context.root / "inactive-LV")
    report = context.plan()
    assert report["generation_count"] == 2
    assert report["destructive_cleanup_performed"] is False
    assert all(not x["active_on_node"] for x in report["old_volumes"])


def test_reference_audit_rejects_another_vm_configuration(context):
    own = context.config_root / "sd-bearnaise/qemu-server/183.conf"
    other = context.config_root / "sd-bearnaise/qemu-server/191.conf"
    own.parent.mkdir(parents=True)
    volid = "sd-PP-Proxmox:vm-183-disk-111.qcow2"
    own.write_text("unused0: " + volid + "\n")
    other.write_text("scsi0: " + volid + "\n")
    with pytest.raises(mod.ConsolidationError, match="Référence étrangère ou multiple"):
        context.references([volid], "scsi0", {}, prepared=True)


def test_reference_audit_accepts_only_local_unused(context, monkeypatch):
    own = context.config_root / "sd-bearnaise/qemu-server/183.conf"
    own.parent.mkdir(parents=True)
    volid = "sd-PP-Proxmox:vm-183-disk-111.qcow2"
    own.write_text("unused1: " + volid + "\n")
    monkeypatch.setattr(mod.socket, "gethostname", lambda: "sd-bearnaise.infra.test")
    assert context.references([volid], "scsi0", {}, prepared=True) == {
        volid: "unused1"}
    own.write_text("scsi0: " + volid + "\n")
    with pytest.raises(mod.ConsolidationError, match="Ancien disque encore actif"):
        context.references([volid], "scsi0", {}, prepared=True)


def test_prune_requires_standalone_image_before_any_unlink(context, monkeypatch):
    old = ["sd-PP-Proxmox:vm-183-disk-100.qcow2"]
    fresh = "sd-PP-Proxmox:vm-183-disk-999.qcow2"
    mod._atomic(context.state_file, {
        "status": "consolidated", "verified": True, "fresh": fresh,
        "slot": "scsi0", "old_volumes": old, "token": "a" * 32,
        "created_at": 42, "pruned": []})
    mod._atomic(context.current_file, {
        "disks": {"scsi0": {"volid": fresh}}})
    monkeypatch.setattr(context, "inspect", lambda: ({}, {"scsi0": fresh}))
    monkeypatch.setattr(mod, "active_path", lambda *_args, **_kw: "/dev/dm-0")
    monkeypatch.setattr(mod, "image_chain",
                        lambda path: ["/dev/dm-0", "/dev/dm-1"])
    monkeypatch.setattr(mod, "run",
                        lambda *args, **kw: pytest.fail("External command attempted before stand-alone check"))
    with pytest.raises(mod.ConsolidationError, match="backing"):
        context.prune(mod.MARKER)


def test_prune_two_old_volumes_via_proxmox_only(context, monkeypatch):
    old = ["sd-PP-Proxmox:vm-183-disk-100.qcow2",
           "sd-PP-Proxmox:vm-183-disk-101.qcow2"]
    fresh = "sd-PP-Proxmox:vm-183-disk-999.qcow2"
    mod._atomic(context.state_file, {
        "status": "consolidated", "verified": True, "fresh": fresh,
        "slot": "scsi0", "old_volumes": old, "token": "a" * 32,
        "created_at": 42, "pruned": []})
    mod._atomic(context.current_file, {
        "disks": {"scsi0": {"volid": fresh}}})
    mod._atomic(context.owned_file, {"generations": [
        {"token": "o0", "volids": [old[0]]},
        {"token": "o1", "volids": [old[1]]},
        {"token": "a" * 32, "volids": [fresh]}]})
    cfg = {"scsi0": fresh, "unused0": old[0], "unused1": old[1]}
    monkeypatch.setattr(context, "inspect", lambda: ({}, cfg.copy()))
    monkeypatch.setattr(mod, "qm_config", lambda _: cfg.copy())
    monkeypatch.setattr(context, "references",
                        lambda vols, slot, conf, prepared=False: {
                            v: next(k for k, value in cfg.items() if value == v) for v in vols})
    monkeypatch.setattr(mod, "active_path", lambda *_args, **_kw: "/dev/dm-0")
    monkeypatch.setattr(mod, "image_chain", lambda path: ["/dev/dm-0"])
    commands = []

    def fake_run(*args, **kwargs):
        commands.append(args)
        if args[:3] == ("qm", "disk", "unlink"):
            cfg.pop(args[-1])
        return ""

    monkeypatch.setattr(mod, "run", fake_run)
    report = context.prune(mod.MARKER)
    assert report["removed_old_volumes"] == 2
    assert report["generation_count"] == 1
    assert [x[-1] for x in commands if x[:3] == ("qm", "disk", "unlink")] == [
        "unused1", "unused0"]
    ledger = json.loads(context.owned_file.read_text())
    assert ledger["generations"][0]["volids"] == [fresh]
    assert len(ledger["generations"]) == 1
    assert context.state_file.exists()


def test_bad_lv_identifier_rejected():
    with pytest.raises(mod.ConsolidationError, match="Nom de LV"):
        mod.volume("sd-PP-Proxmox:../../etc/passwd")
