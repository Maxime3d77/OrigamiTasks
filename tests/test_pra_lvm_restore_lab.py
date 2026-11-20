"""Isolated recovery-clone preparation never touches committed checkpoint data."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

PATH=Path(__file__).resolve().parents[1]/"agents/linux"
sys.path.insert(0,str(PATH))
spec=importlib.util.spec_from_file_location("pra_lvm_restore_lab",PATH/"pra_lvm_restore_lab.py")
mod=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=mod
spec.loader.exec_module(mod)


@pytest.fixture
def lab(tmp_path,monkeypatch):
    devices={}
    ledger={"generations":[{"volids":["lab:vm-321-disk-0.qcow2"]}]}
    old=tmp_path/"base.qcow2"
    old.touch()
    devices["lab:vm-321-disk-0.qcow2"]=str(old)
    curr={"status":"committed","target_vmid":321,"protected_vm_id":17,
          "token":"f"*32,"disks":{"scsi0":{
              "size":1048576,"volid":"lab:vm-321-disk-0.qcow2"}}}
    calls=[]
    cfg={321:{"description":"ORIGAMITASKS_PRA_17","onboot":"0"}}
    class Target:
        storage="lab"
        vmid=321
        protected_vm_id=17
        def resolve_existing(self,volid):
            return devices[volid]
    class Store:
        target=Target()
        pending=tmp_path/"absent-pending.json"
        owned=tmp_path/"owned.json"
        def current(self):
            return curr
        def _read(self,path):
            return ledger
    class NewTarget:
        def __init__(self,storage,vmid,runner,protected_vm_id):
            self.vmid=vmid
        def plan(self,sizes,mode):
            calls.append(("plan",sizes,mode))
            return {"disks":sizes}
        def stage_allocations(self,plan,approved=False):
            assert approved is True
            devices["lab:vm-555-disk-11.qcow2"]=str(tmp_path/"clone.qcow2")
            (tmp_path/"clone.qcow2").touch()
            calls.append(("allocate",))
            return {"allocated":{"scsi0":{"volid":"lab:vm-555-disk-11.qcow2"}}}
        def resolve_existing(self,volid):
            return devices[volid]
    from pra_lvm_target import PveLvmTarget
    monkeypatch.setattr(mod,"PveLvmTarget",NewTarget)
    monkeypatch.setattr(NewTarget,"validate_sizes",staticmethod(PveLvmTarget.validate_sizes), raising=False)
    def runner(*args,**kwargs):
        calls.append(args)
        if args[:2]==("qm","status"):
            return "status: stopped"
        if args[:2]==("pvesh","get"):
            return json.dumps([{"vmid":321,"type":"qemu","node":"lab-node"}])
        if args[:2]==("qemu-img","info"):
            return json.dumps([{"filename":devices["lab:vm-555-disk-11.qcow2"],"format":"qcow2"}])
        if args[:2]==("qm","create"):
            cfg[int(args[2])]={"description":args[args.index("--description")+1],
                               "onboot":"0"}
            return ""
        if args[:2]==("qm","set"):
            cfg[int(args[2])]["scsi0"]="lab:vm-555-disk-11.qcow2"
            return ""
        if args[:2] in (("qemu-img","check"),("qemu-img","convert")):
            return ""
        raise AssertionError(args)
    return Store(),runner,lambda vmid:cfg.get(vmid),calls,cfg


def test_clone_requires_explicit_lab_approval(lab):
    store,cmd,inspect,calls,_=lab
    with pytest.raises(mod.RestoreLabError,match="approval"):
        mod.prepare_clone(store,vmid=555,source_config={},
                          command=cmd,inspect_config=inspect)
    assert not calls


def test_clone_creation_is_stopped_and_networkless_and_uses_new_disk(lab):
    store,cmd,inspect,calls,cfg=lab
    result=mod.prepare_clone(store,vmid=555,source_config={"memory":"2048"},
                             command=cmd,inspect_config=inspect,
                             approved=True,lab_ack="DISPOSABLE-LAB-ONLY")
    assert result["test_vmid"]==555 and result["network_isolated"]
    assert result["vm_started"] is False
    assert result["boot_verified"] is False
    assert result["failover_authorized"] is False
    assert cfg[555]["scsi0"]=="lab:vm-555-disk-11.qcow2"
    assert not any(x[:2] in (("qm","start"),("qm","stop"),("pvesm","free")) for x in calls)


def test_clone_never_reuses_standby_vmid(lab):
    store,cmd,inspect,calls,_=lab
    with pytest.raises(mod.RestoreLabError,match="differ"):
        mod.prepare_clone(store,vmid=321,source_config={},command=cmd,
                          inspect_config=inspect,approved=True,
                          lab_ack="DISPOSABLE-LAB-ONLY")
    assert not calls


def test_clone_denies_reusing_cluster_vmid(lab):
    store,cmd,inspect,calls,cfg=lab
    cfg[555]={"description":"user-vm"}
    with pytest.raises(mod.RestoreLabError,match="occupied"):
        mod.prepare_clone(store,vmid=555,source_config={},command=cmd,
                          inspect_config=inspect,approved=True,
                          lab_ack="DISPOSABLE-LAB-ONLY")
    assert not any(x[0]=="allocate" for x in calls)


def test_clone_denies_live_standby_before_writes(lab):
    store,cmd,inspect,calls,_=lab
    def running(*args,**kwargs):
        if args[:2]==("qm","status"):
            return "status: running"
        return cmd(*args,**kwargs)
    with pytest.raises(mod.RestoreLabError,match="OFF"):
        mod.prepare_clone(store,vmid=555,source_config={},command=running,
                          inspect_config=inspect,approved=True,
                          lab_ack="DISPOSABLE-LAB-ONLY")
    assert not any(x[0]=="allocate" for x in calls)
