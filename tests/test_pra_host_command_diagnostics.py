"""Stable, non-secret diagnostics for failed Proxmox LAB preparation."""
import json
import types
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"agents/linux"))
import pra_host_worker as host
from app import pra_live


class FakeStorage:
    def __init__(self,*args,**kwargs): pass
    def inspect(self): raise host.HostError("Host operation rejected: pvesh")
    def _node_capacity(self): raise AssertionError("Must stop on config error")


def test_storage_config_error_has_exact_stage(monkeypatch):
    monkeypatch.setattr(host,"PveLvmTarget",FakeStorage)
    with pytest.raises(host.HostError,match="PRA_STEP_STORAGE_CONFIG"):
        host.lvm_lab_prepare_standby({
            "lab_ack":"DISPOSABLE-LAB-ONLY","vmid":183,
            "protected_vm_id":1,"name":"REP_test","storage":"lab-lvm"
        })


def test_helper_error_exposes_only_allowlisted_command(monkeypatch):
    class Channel:
        def settimeout(self,_): pass
        def recv_exit_status(self): return 1
    class Stdout:
        channel=Channel()
        def read(self,n):
            return json.dumps({"ok":False,"error":
                "PRA_STEP_STORAGE_CONFIG : Host operation rejected: pvesh; secret=abc"}).encode()
    class SSH:
        def exec_command(self,*args,**kwargs): return None, Stdout(), None
    with pytest.raises(pra_live.LiveReplicationError) as raised:
        pra_live._run_helper(SSH(),"lvm_lab_prepare_standby",{"vmid":183})
    assert "PRA_STEP_STORAGE_CONFIG" in str(raised.value)
    assert "pvesh" in str(raised.value)
    assert "secret" not in str(raised.value)


def test_readonly_diagnostic_reports_storage_error_without_vm_mutation(monkeypatch):
    monkeypatch.setattr(host,"PveLvmTarget",FakeStorage)
    result=host.lvm_lab_diagnose_standby({
        "vmid":183,"protected_vm_id":1,"storage":"lab-lvm"
    })
    assert result["ready_for_creation"] is False
    assert result["target_exists"] is None
    assert result["checks"] == [{
        "stage":"STORAGE_CONFIG","ok":False,
        "message":"Échec pvesh ; vérifier l’API et les permissions"
    }]


def test_readonly_diagnostic_refuses_existing_cluster_vmid(monkeypatch):
    class GoodStorage:
        def __init__(self,*args,**kwargs): pass
        def inspect(self): return {"type":"lvm"}
        def _node_capacity(self): return ("sd-bearnaise",12345)
    monkeypatch.setattr(host,"PveLvmTarget",GoodStorage)
    def readonly_command(*args,**kwargs):
        assert args[:2]==("pvesh","get")
        return json.dumps([{"type":"qemu","vmid":183,"status":"stopped"}])
    monkeypatch.setattr(host,"cmd",readonly_command)
    result=host.lvm_lab_diagnose_standby({
        "vmid":183,"protected_vm_id":1,"storage":"lab-lvm"
    })
    assert result["target_exists"] is True
    assert result["ready_for_creation"] is False
    assert result["checks"][-1]["stage"]=="OWNERSHIP"
    assert result["checks"][-1]["ok"] is False
    assert result["owned_standby"] is False


def test_ui_exposes_readonly_diagnostic():
    ui=(Path(__file__).resolve().parents[1]/"static/app.js").read_text()
    assert "Diagnostiquer Proxmox (lecture seule)" in ui
    assert "lvm-lab/diagnose-standby" in ui


def test_proxmox_logical_replica_name_is_dns_compatible():
    assert host.pve_guest_name("REP_vl-bbiokou") == "REP-vl-bbiokou"
    assert host.pve_guest_name("REP_vm_foo") == "REP-vm-foo"
    assert host.pve_guest_name("PRA_TEST_4301") == "PRA-TEST-4301"
    with pytest.raises(host.HostError):
        host.pve_guest_name("REP_invalid name")
    with pytest.raises(host.HostError):
        host.pve_guest_name("REP_--")


def test_standby_qm_create_uses_dns_name_and_preserves_policy_markers(monkeypatch):
    class Store:
        def __init__(self,*args,**kwargs): pass
        def inspect(self): return {"type":"lvm"}
        def _node_capacity(self): return ("sd-bearnaise",1024*1024)
    monkeypatch.setattr(host,"PveLvmTarget",Store)
    monkeypatch.setattr(host.socket,"gethostname",lambda:"sd-bearnaise")
    monkeypatch.setattr(host,"_is_target_stopped",lambda _:True)
    created=False
    description=""
    qname=""
    calls=[]
    def command(*argv,timeout=25):
        nonlocal created,description,qname
        calls.append(argv)
        if argv[:2]==("pvesh","get"):
            return json.dumps([{"vmid":183,"node":"sd-bearnaise","type":"qemu"}] if created else [])
        if argv[:2]==("qm","create"):
            created=True
            qname=argv[argv.index("--name")+1]
            description=argv[argv.index("--description")+1]
            return ""
        raise AssertionError(argv)
    monkeypatch.setattr(host,"cmd",command)
    monkeypatch.setattr(host,"_vm_config",lambda _:{
        "name":qname,"description":description,"onboot":"0",
    })
    result=host.lvm_lab_prepare_standby({
        "vmid":183,"protected_vm_id":1,"storage":"lab-lvm",
        "name":"REP_vl-bbiokou","lab_ack":"DISPOSABLE-LAB-ONLY",
        "source_name":"vl-bbiokou","source_node":"au-dodo",
        "source_vmid":183,"reference":"","protection_note":"",
    })
    assert result["created"] is True
    assert qname=="REP-vl-bbiokou"
    assert "ORIGAMITASKS_PRA_1" in description
    assert "ORIGAMITASKS_LAB_ONLY" in description
    assert "vl-bbiokou" in description
    assert all(x[:2] not in [("qm","start"),("qm","stop")] for x in calls)


def test_existing_owned_guest_validated_without_mutation(monkeypatch):
    monkeypatch.setattr(host.socket,"gethostname",lambda:"sd-bearnaise")
    monkeypatch.setattr(host,"_is_target_stopped",lambda _:True)
    config={"name":"REP-vl-bbiokou","onboot":"0",
            "description":"ORIGAMITASKS_PRA_1 ORIGAMITASKS_LAB_ONLY"}
    monkeypatch.setattr(host,"_vm_config",lambda _:config)
    def readonly(*args,**kwargs):
        assert args[:2]==("pvesh","get")
        return json.dumps([{"type":"qemu","vmid":183,"node":"sd-bearnaise","status":"unknown"}])
    monkeypatch.setattr(host,"cmd",readonly)
    verified=host.lvm_lab_validate_standby({
        "vmid":183,"protected_vm_id":1,"name":"REP_vl-bbiokou"})
    assert verified["owned"] is True
    assert verified["target_stopped"] is True
    assert verified["network_isolated"] is True


def test_foreign_occupied_vmid_is_not_adopted(monkeypatch):
    monkeypatch.setattr(host.socket,"gethostname",lambda:"sd-bearnaise")
    monkeypatch.setattr(host,"_is_target_stopped",lambda _:True)
    monkeypatch.setattr(host,"_vm_config",lambda _:{
        "name":"REP-vl-bbiokou","description":"ORIGAMITASKS_PRA_9 ORIGAMITASKS_LAB_ONLY"})
    monkeypatch.setattr(host,"cmd",lambda *a,**kw:json.dumps([
        {"type":"qemu","vmid":183,"node":"sd-bearnaise"}]))
    with pytest.raises(host.HostError,match="another protection"):
        host.lvm_lab_validate_standby({
            "vmid":183,"protected_vm_id":1,"name":"REP_vl-bbiokou"})
