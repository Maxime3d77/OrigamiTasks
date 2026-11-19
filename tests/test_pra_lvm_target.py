"""Generic LVM staging pilot: PVE allocation only, never VM actions or failover."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

LOCATION = Path(__file__).resolve().parents[1] / "agents/linux/pra_lvm_target.py"
spec = importlib.util.spec_from_file_location("pra_lvm_target", LOCATION)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


@pytest.fixture
def adapter(tmp_path):
    calls = []
    config = {"storage": "any-lvm", "type": "lvm", "vgname": "vg-customer",
              "content": "images,rootdir", "shared": 1, "snapshot-as-volume-chain": 1}
    def runner(*args):
        calls.append(args)
        if args[:2] == ("pvesh", "get"):
            return json.dumps(config)
        if args[:2] == ("qm", "status"):
            return "status: stopped"
        if args[:2] == ("qm", "config"):
            return "onboot: 0"
        if args[:2] == ("pvesm", "alloc"):
            return "created"
        raise AssertionError(f"Unexpected command: {args}")
    obj = mod.PveLvmTarget("any-lvm", 301, runner, tmp_path)
    return obj, config, calls, tmp_path


def test_inspect_read_only_no_vendor_assumptions(adapter):
    obj, _, calls, root = adapter
    result = obj.inspect()
    assert result["type"] == "lvm"
    assert result["shared"] is True
    assert result["replication_enabled"] is False
    assert result["failover_enabled"] is False
    assert calls == [("pvesh","get","/storage/any-lvm","--output-format","json")]
    assert not (root/"any-lvm").exists()


@pytest.mark.parametrize("changes", [
    {"type":"dir"}, {"storage":"other-store"}, {"content":"iso"},
    {"disable":1}, {"snapshot-as-volume-chain":0}, {"vgname":"../bad"},
])
def test_unsafe_stores_blocked_before_allocation(adapter, changes):
    obj, config, calls, root = adapter
    config.update(changes)
    with pytest.raises(mod.LvmTargetError):
        obj.plan({"scsi0": 4*1024*1024}, "full")
    assert all(x[:2] != ("pvesm", "alloc") for x in calls)
    assert not (root/"any-lvm").exists()


def test_full_plan_requires_capacity_for_thick_lvm_checkpoints(adapter):
    obj, _, calls, root = adapter
    sizes={"scsi0": 64*1024**3, "virtio1": 32*1024**3}
    p=obj.plan(sizes,"full")
    assert p["disks"]==sizes
    assert p["required_bytes_lower_bound"] >= sum(sizes.values())
    assert p["allocation_is_thick"] is True
    assert p["can_failover"] is False
    assert not (root/"any-lvm").exists()


def test_incremental_needs_verified_point_and_matching_disk_sizes(adapter):
    obj,_,_,_=adapter
    sizes={"scsi0": 4*1024*1024}
    with pytest.raises(mod.LvmTargetError):
        obj.plan(sizes,"incremental")
    previous={"status":"committed","disks":{"scsi0":{"size":sizes["scsi0"]}}}
    p=obj.plan(sizes,"incremental", previous)
    assert p["mode"]=="incremental"
    previous["disks"]["scsi0"]["size"]=1
    with pytest.raises(mod.LvmTargetError):
        obj.plan(sizes,"incremental", previous)


def test_no_staging_without_explicit_operator_approval(adapter):
    obj, _, calls, root=adapter
    p=obj.plan({"scsi0":1024*1024}, "full")
    with pytest.raises(mod.LvmTargetError,match="approval"):
        obj.stage_allocations(p)
    assert all(x[:2] != ("pvesm","alloc") for x in calls)
    assert not (root/"any-lvm").exists()


def test_journal_written_before_each_pve_allocation_and_never_auto_free(adapter):
    obj, _, calls, root = adapter
    p=obj.plan({"scsi0":1024*1024,"scsi1":1024*1024},"full")
    r=obj.stage_allocations(p,approved=True)
    assert r["status"] == "staged_only"
    assert r["checkpoint_committed"] is False
    alloc=[x for x in calls if x[:2]==("pvesm","alloc")]
    assert len(alloc)==2
    assert all(x[2]=="any-lvm" and x[3]=="301" and x[-2:]==("--format","qcow2") for x in alloc)
    assert all(x[4].startswith("vm-301-disk-") and x[4].endswith(".qcow2") for x in alloc)
    journal=json.loads((root/"any-lvm"/"301"/"pending.json").read_text())
    assert journal["token"]==r["token"] and len(journal["allocated"])==2
    with pytest.raises(mod.LvmTargetError, match="Unresolved"):
        obj.stage_allocations(p,approved=True)
    assert not any(x[:2] == ("pvesm","free") for x in calls)
    assert not any(x[:2] in (("qm","start"),("qm","stop"),("qm","set")) for x in calls)


def test_ambiguous_allocation_failure_requires_manual_reconciliation(adapter,monkeypatch):
    obj, _, calls, root=adapter
    real=obj.run
    def fail_second(*args):
        if args[:2]==("pvesm","alloc") and sum(x[:2]==("pvesm","alloc") for x in calls) >= 1:
            calls.append(args)
            raise RuntimeError("unknown remote outcome")
        return real(*args)
    obj.run=fail_second
    p=obj.plan({"scsi0":1024*1024,"scsi1":1024*1024},"full")
    with pytest.raises(RuntimeError):
        obj.stage_allocations(p,approved=True)
    record=json.loads((root/"any-lvm"/"301"/"pending.json").read_text())
    assert record["status"]=="uncertain"
    assert len(record["allocated"])==1
    assert not any(x[:2]==("pvesm","free") for x in calls)


@pytest.mark.parametrize("bad", [
    "other:vm-301-disk-1.qcow2",
    "any-lvm:vm-302-disk-1.qcow2",
    "any-lvm:vm-301-disk-1",
    "any-lvm:../../../root",
    "any-lvm:vm-301-disk-1.qcow2;whoami",
])
def test_reject_foreign_or_unsafe_volids_before_any_lookup(adapter,bad):
    obj, _, calls, _ = adapter
    with pytest.raises(mod.LvmTargetError):
        obj.resolve_existing(bad)
    assert not calls


@pytest.mark.parametrize("bad_storage", ["my/vg", "../other", "foo;sh", "", "a b"])
def test_reject_unsafe_storage_names(bad_storage):
    with pytest.raises(mod.LvmTargetError):
        mod.PveLvmTarget(bad_storage,301,lambda *args: "")


def test_reject_invalid_vmid():
    for value in (0, 99, "301", 1000000000, True):
        with pytest.raises(mod.LvmTargetError):
            mod.PveLvmTarget("test",value,lambda *args: "")


def test_staging_rejects_running_or_autostart_target(adapter):
    obj, _, calls, root = adapter
    plan=obj.plan({"scsi0": 1048576},"full")
    real=obj.run
    def running(*args):
        if args[:2]==("qm","status"):
            return "status: running"
        return real(*args)
    obj.run=running
    with pytest.raises(mod.LvmTargetError,match="stopped"):
        obj.stage_allocations(plan,approved=True)
    assert not any(x[:2]==("pvesm","alloc") for x in calls)
    assert not (root/"any-lvm").exists()

    def onboot(*args):
        if args[:2]==("qm","config"):
            return "onboot: 1"
        return real(*args)
    obj.run=onboot
    with pytest.raises(mod.LvmTargetError,match="onboot"):
        obj.stage_allocations(plan,approved=True)
    assert not any(x[:2]==("pvesm","alloc") for x in calls)
