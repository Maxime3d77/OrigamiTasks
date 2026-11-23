"""Mocked host contract tests: REP_ publishing never boots and PRA_TEST boot is isolated."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]/"agents/linux"
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location("pra_host_worker_lab_contracts",ROOT/"pra_host_worker.py")
host=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=host
spec.loader.exec_module(host)

TOKEN="f"*32


class Target:
    storage="lab"
    vmid=321
    protected_vm_id=17
    def resolve_existing(self,volid): return "/dev/mock-" + volid.split(":")[-1]


class Store:
    target=Target()
    pending=Path("/nonexistent-pending-origamitasks-lab-test.json")
    owned=Path("/nonexistent-owned-origamitasks-lab-test.json")
    def current(self):
        return {"status":"committed","token":TOKEN,
                "disks":{"scsi0":{"volid":"lab:vm-321-disk-7.qcow2","size":1048576}}}
    def _read(self,path):
        return {"generations":[{"volids":["lab:vm-321-disk-7.qcow2"]}]}


@pytest.fixture
def setup(monkeypatch):
    store=Store()
    monkeypatch.setattr(host,"_lvm_lab_store",lambda data:store)
    monkeypatch.setattr(host,"lvm_lab_verify",lambda _:{"verified_offline":True})
    monkeypatch.setattr(host,"_is_target_stopped",lambda ident:True)
    config={"name":"REP-lab","onboot":"0","description":"ORIGAMITASKS_PRA_17 ORIGAMITASKS_LAB_ONLY"}
    monkeypatch.setattr(host,"_vm_config",lambda _:config)
    calls=[]
    def cmd(*argv,timeout=25):
        calls.append(argv)
        if argv[:2]==("qm","set"):
            config["scsi0"]="lab:vm-321-disk-7.qcow2"
            return ""
        raise AssertionError(argv)
    monkeypatch.setattr(host,"cmd",cmd)
    return store,config,calls


def test_publishing_owned_lvm_config_never_starts_guest(setup):
    store,config,calls=setup
    result=host.lvm_lab_publish_standby({
        "vmid":321,"protected_vm_id":17,"lab_ack":"DISPOSABLE-LAB-ONLY",
        "token":TOKEN,"name":"REP_lab",
        "source_config":{"bios":"seabios","memory":"2048","cores":"2","sockets":"1"}})
    assert result["standby_configured"] is True
    assert result["boot_tested"] is False
    assert result["failover_authorized"] is False
    assert len(calls)==1 and calls[0][:2]==("qm","set")


def test_publish_rejects_unsupported_efi_before_mutation(setup):
    store,config,calls=setup
    with pytest.raises(host.HostError,match="UEFI"):
        host.lvm_lab_publish_standby({
            "vmid":321,"protected_vm_id":17,"lab_ack":"DISPOSABLE-LAB-ONLY",
            "token":TOKEN,"name":"REP_lab","source_config":{"bios":"ovmf"}})
    assert not calls


def test_test_boot_only_starts_and_stops_owned_networkless_clone(monkeypatch):
    store=Store()
    monkeypatch.setattr(host,"_lvm_lab_store",lambda _:store)
    monkeypatch.setattr(host.socket,"gethostname",lambda:"labnode")
    monkeypatch.setattr(host,"_is_target_stopped",lambda _:True)
    config={"name":"PRA_TEST_555","description":"ORIGAMITASKS_LAB_CLONE_17",
            "onboot":"0","scsi0":"lab:vm-555-disk-9.qcow2"}
    monkeypatch.setattr(host,"_vm_config",lambda _:config)
    class CloneTarget:
        def __init__(self,*args,**kw): pass
        def resolve_existing(self,volid):return "/dev/mock-clone"
    monkeypatch.setattr(host,"PveLvmTarget",CloneTarget)
    powered=False
    commands=[]
    def cmd(*args,timeout=25):
        nonlocal powered
        commands.append(args)
        if args[:2]==("pvesh","get"):
            return json.dumps([{"type":"qemu","vmid":555,"node":"labnode"}])
        if args[:2]==("qm","status"):
            return "status: running" if powered else "status: stopped"
        if args[:2]==("qm","start"):
            powered=True
            return ""
        if args[:2]==("qm","stop"):
            powered=False
            return ""
        if args[:2]==("qemu-img","check"):
            return ""
        raise AssertionError(args)
    monkeypatch.setattr(host,"cmd",cmd)
    result=host.lvm_lab_test_boot({
        "vmid":321,"protected_vm_id":17,"lab_ack":"DISPOSABLE-LAB-ONLY",
        "token":TOKEN,"test_vmid":555,"test_volumes":{"scsi0":"lab:vm-555-disk-9.qcow2"}})
    assert result["qemu_started"] is True
    assert result["stopped_after_test"] is True
    assert result["guest_boot_verified"] is False
    assert not powered
    assert [x[:2] for x in commands if x[0]=="qm" and x[1] in ("start","stop")]==[
        ("qm","start"),("qm","stop")]


def test_test_boot_blocks_networked_clone(monkeypatch):
    store=Store()
    monkeypatch.setattr(host,"_lvm_lab_store",lambda _:store)
    monkeypatch.setattr(host.socket,"gethostname",lambda:"labnode")
    monkeypatch.setattr(host,"_is_target_stopped",lambda _:True)
    monkeypatch.setattr(host,"_vm_config",lambda _:{"description":"ORIGAMITASKS_LAB_CLONE_17",
                          "onboot":"0","net0":"virtio,bridge=vmbr0"})
    calls=[]
    def cmd(*args,timeout=25):
        calls.append(args)
        if args[:2]==("pvesh","get"):
            return json.dumps([{"type":"qemu","vmid":555,"node":"labnode"}])
        raise AssertionError(args)
    monkeypatch.setattr(host,"cmd",cmd)
    with pytest.raises(host.HostError,match="isolation"):
        host.lvm_lab_test_boot({
            "vmid":321,"protected_vm_id":17,"lab_ack":"DISPOSABLE-LAB-ONLY",
            "token":TOKEN,"test_vmid":555,"test_volumes":{"scsi0":"lab:vm-555-disk-9.qcow2"}})
    assert not any(x[0]=="qm" and x[1] in ("start","stop") for x in calls)
