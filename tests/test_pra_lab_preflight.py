"""Read-only Proxmox inventory preflight with a fake PVE API.

The command list is asserted to exclude all mutating APIs.
"""
from pathlib import Path
import importlib.util
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "pra_lab_preflight", ROOT / "scripts/pra_lab_preflight.py")
lab = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lab
spec.loader.exec_module(lab)


@pytest.fixture
def mock_lab(monkeypatch):
    monkeypatch.setattr(lab.socket, "gethostname", lambda: "pve-a.example.invalid")
    monkeypatch.setattr(lab.shutil, "which", lambda binary: "/usr/bin/" + binary)
    calls = []
    cfg = {"storage": "generic-LVM", "type": "lvm", "content": "images,rootdir",
           "shared": 1, "snapshot-as-volume-chain": 1}
    status = [{"storage": "generic-LVM", "active": 1, "avail": 4*1024**4}]
    guest = ("bios: seabios\nscsi0: generic-LVM:vm-101-disk-0.qcow2,size=10G\n"
             "onboot: 0\n")
    def execute(*argv):
        calls.append(argv)
        if argv[0] == "pvesh":
            if argv[2].startswith("/storage/"):
                return json.dumps(cfg)
            if argv[2].startswith("/nodes/"):
                return json.dumps(status)
        if argv[:2] == ("qm", "config"):
            return guest
        raise AssertionError("Unexpected or mutating call " + str(argv))
    return execute, calls, cfg, status


def test_generic_storage_inventory_no_lv_ops(mock_lab):
    run, calls, cfg, status=mock_lab
    report=lab.preflight(storage="generic-LVM",vmid=101,executor=run)
    assert report["node"]=="pve-a"
    assert report["read_only"] is True
    assert report["hypervisor_mutated"] is False
    assert report["storage"]["shared"] is True
    assert report["storage"]["snapshot_as_volume_chain"] is True
    assert report["storage"]["receiver_available_now"] is False
    assert report["storage"]["lvm_receiver_status"]=="unimplemented"
    assert report["guest"]["eligible_for_current_dir_qmp_pilot"] is True
    assert all(cmd[0] in ("qm", "pvesh") for cmd in calls)
    assert not any("start" in cmd or "alloc" in cmd for cmd in calls)


@pytest.mark.parametrize("type_,active,expected", [
    ("dir",1,True), ("dir",0,False), ("lvm",1,False), ("zfspool",1,False),
])
def test_receiver_support_never_assumes_storage_capability(
        mock_lab,type_,active,expected):
    run, calls, cfg, status=mock_lab
    cfg["type"]=type_
    status[0]["active"]=active
    report=lab.preflight(storage="generic-LVM",executor=run)
    assert report["storage"]["receiver_available_now"] is expected
    assert report["real_failover_qualified"] is False


@pytest.mark.parametrize("extra", [
    "bios: ovmf\n",
    "efidisk0: generic-LVM:vm-101-disk-1\n",
    "tpmstate0: generic-LVM:vm-101-disk-2\n",
    "hostpci0: 00:02.0\n",
    "unused0: generic-LVM:vm-101-disk-3\n",
])
def test_unsupported_guest_profile_not_marked_ready(mock_lab,extra):
    run, calls, cfg, status=mock_lab
    def changed(*argv):
        if argv[:2] == ("qm", "config"):
            return "scsi0: generic-LVM:vm-101-disk-0,size=10G\n"+extra
        return run(*argv)
    report=lab.preflight(storage="generic-LVM",vmid=101,executor=changed)
    assert report["guest"]["eligible_for_current_dir_qmp_pilot"] is False
    assert report["real_failover_qualified"] is False


def test_bad_storage_or_vmid_is_rejected_without_running_any_command(mock_lab):
    run, calls, _, _=mock_lab
    with pytest.raises(lab.ProbeError, match="Invalid storage"):
        lab.preflight(storage="../bad",executor=run)
    with pytest.raises(lab.ProbeError,match="Invalid VMID"):
        lab.preflight(vmid=-1,executor=run)
    assert calls==[]
