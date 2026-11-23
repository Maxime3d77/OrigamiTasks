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


def test_source_qmp_diagnostic_is_readonly_and_reports_socket_stage(monkeypatch):
    monkeypatch.setattr(host,"source_probe",lambda data:(_ for _ in ()).throw(
        host.HostError("VM QMP socket unavailable (VM must be running)")))
    report=host.source_diagnose({"vmid":183,"slots":["scsi0"]})
    assert report=={"ready":False,"stage":"QMP_SOCKET",
                    "message":"Socket QMP indisponible","vmid":183}


def test_source_qmp_diagnostic_does_not_leak_raw_qmp_details(monkeypatch):
    monkeypatch.setattr(host,"source_probe",lambda data:(_ for _ in ()).throw(
        host.ReplicationError("QMP handshake failed: example private path")))
    report=host.source_diagnose({"vmid":183,"slots":["scsi0"]})
    assert report["stage"]=="QMP_QUERY"
    assert "private path" not in str(report)


def test_source_qmp_ui_and_helper_are_explicitly_wired():
    root=Path(__file__).resolve().parents[1]
    assert '"source_diagnose": source_diagnose' in (root/"agents/linux/pra_host_worker.py").read_text()
    assert '"source_diagnose"' in (root/"app/pra_live.py").read_text()
    ui=(root/"static/app.js").read_text()
    assert "Diagnostiquer QMP source (lecture seule)" in ui
    assert "/lvm-lab/resume-source" in ui


def test_pve9_blockdev_qdev_slot_mapping_with_empty_device():
    rows=[{"device":"","qdev":"scsi0","inserted":{
        "node-name":"drive-scsi0","image":{"virtual-size":1048576}}},
          {"device":"","qdev":"ide2","tray_open":True}]
    assert host._qmp_block_for_slot(rows,"scsi0")["node-name"]=="drive-scsi0"


def test_legacy_pve_drive_device_mapping():
    rows=[{"device":"drive-virtio0","inserted":{
        "node-name":"drive-virtio0","image":{"virtual-size":1048576}}}]
    assert host._qmp_block_for_slot(rows,"virtio0")["node-name"]=="drive-virtio0"


def test_qmp_slot_mapping_fails_closed_on_ambiguous_or_missing_rows():
    row={"device":"drive-scsi0","qdev":"scsi0","inserted":{
        "node-name":"drive-scsi0","image":{"virtual-size":1048576}}}
    with pytest.raises(host.HostError,match="ambiguous"):
        host._qmp_block_for_slot([row,row],"scsi0")
    with pytest.raises(host.HostError,match="unavailable"):
        host._qmp_block_for_slot([{"device":"","qdev":"ide2"}],"scsi0")


def test_lvm_generation_diagnostic_reports_pending_without_mutation(monkeypatch,tmp_path):
    # Explicitly verify that the new helper only reads its journals and never
    # calls qm create, pvesm alloc, qemu-img or qemu-nbd.
    from types import SimpleNamespace
    journal=tmp_path/"lab"/"183"
    journal.mkdir(parents=True)
    pending=journal/"pending.json"
    pending.write_text(json.dumps({
        "token":"a"*32,"status":"staged_only","mode":"full",
        "allocated":{"scsi0":{"volid":"lab:vm-183-disk-12.qcow2"}}
    }))
    target=SimpleNamespace(storage="lab",vmid=183,protected_vm_id=1)
    store=SimpleNamespace(
        root=journal,pending=pending,current_file=journal/"current.json",
        owned=journal/"owned.json",
        _read=lambda p:json.loads(p.read_text()) if p.exists() else None,
    )
    monkeypatch.setattr(host,"_lvm_lab_store",lambda data:store)
    monkeypatch.setattr(host,"cmd",lambda *a,**kw:(_ for _ in ()).throw(
        AssertionError("No host command allowed")))
    result=host.lvm_lab_diagnose_generation({"vmid":183})
    assert result["state"]=="pending"
    assert result["requires_reconciliation"] is True
    assert result["pending"]["status"]=="staged_only"
    assert result["pending"]["volumes"]==["lab:vm-183-disk-12.qcow2"]
    assert pending.exists()


def test_lvm_generation_diagnostic_detects_unjournaled_lv_without_mutating(monkeypatch,tmp_path):
    from types import SimpleNamespace
    journal=tmp_path/"lab"/"183"
    journal.mkdir(parents=True)
    token="72c4f9ab271c46a39d2967c1ab2bbe97"
    pending=journal/"pending.json"
    pending.write_text(json.dumps({
        "token":token,"status":"uncertain","mode":"full",
        "planned":["scsi0"],"allocated":{},"failure_category":"PVE_LVM_ALLOC_INVALID_VOLUME_NAME",
    }))
    expected="vm-183-disk-"+str(int(token[:12],16))+".qcow2"
    target=SimpleNamespace(storage="lab",vmid=183,protected_vm_id=1,
                           inspect=lambda:{"vgname":"SD-PP-vg"})
    store=SimpleNamespace(root=journal,pending=pending,target=target,
        current_file=journal/"current.json",owned=journal/"owned.json",
        _read=lambda p:json.loads(p.read_text()) if p.exists() else None)
    monkeypatch.setattr(host,"_lvm_lab_store",lambda _:store)
    calls=[]
    def command(*args,**kwargs):
        calls.append(args)
        assert args==("lvs","--reportformat","json","-o","vg_name,lv_name")
        return json.dumps({"report":[{"lv":[
            {"vg_name":"SD-PP-vg","lv_name":expected},
            {"vg_name":"OTHER-vg","lv_name":"foreign-disk"}]}]})
    monkeypatch.setattr(host,"cmd",command)
    report=host.lvm_lab_diagnose_generation({"vmid":183})
    assert report["state"]=="pending"
    assert report["inventory_checked"] is True
    assert report["predicted_allocations"]==[{
        "slot":"scsi0","name":expected,"present":True}]
    assert report["pending"]["failure_category"]=="PVE_LVM_ALLOC_INVALID_VOLUME_NAME"
    assert len(calls)==1 and pending.exists()


def test_lvm_generation_diagnostic_does_not_claim_absence_if_lvs_fails(monkeypatch,tmp_path):
    from types import SimpleNamespace
    journal=tmp_path/"lab"/"183"
    journal.mkdir(parents=True)
    pending=journal/"pending.json"
    pending.write_text(json.dumps({"token":"a"*32,"planned":["scsi0"],"allocated":{}}))
    store=SimpleNamespace(root=journal,pending=pending,
        current_file=journal/"current.json",owned=journal/"owned.json",
        target=SimpleNamespace(vmid=183,inspect=lambda:{"vgname":"VG"}),
        _read=lambda p:json.loads(p.read_text()) if p.exists() else None)
    monkeypatch.setattr(host,"_lvm_lab_store",lambda _:store)
    monkeypatch.setattr(host,"cmd",lambda *a,**kw:(_ for _ in ()).throw(
        host.HostError("Host operation rejected: lvs")))
    report=host.lvm_lab_diagnose_generation({"vmid":183})
    assert report["inventory_checked"] is False
    assert report["predicted_allocations"][0]["present"] is None
    assert report["requires_reconciliation"] is True


def test_empty_pending_reconciliation_preserves_archive_and_never_frees_lv(monkeypatch,tmp_path):
    from types import SimpleNamespace
    journal=tmp_path/"lab"/"183"
    journal.mkdir(parents=True)
    token="72c4f9ab271c46a39d2967c1ab2bbe97"
    pending=journal/"pending.json"
    pending.write_text(json.dumps({
        "token":token,"status":"uncertain","mode":"full",
        "planned":["scsi0"],"allocated":{},
    }))
    expected="vm-183-disk-"+str(int(token[:12],16))+".qcow2"
    store=SimpleNamespace(
        root=journal,pending=pending,current_file=journal/"current.json",
        owned=journal/"owned.json",
        lock_file=journal/".checkpoint.lock",
        target=SimpleNamespace(vmid=183,protected_vm_id=1,storage="lab",
                               inspect=lambda:{"vgname":"LAB-VG"}),
        _read=lambda p:json.loads(p.read_text()) if p.exists() else None,
    )
    monkeypatch.setattr(host,"_lvm_lab_store",lambda _:store)
    monkeypatch.setattr(host,"lvm_lab_validate_standby",lambda _:{"owned":True})
    def readonly(*a,**kw):
        assert a==("lvs","--reportformat","json","-o","vg_name,lv_name")
        return json.dumps({"report":[{"lv":[]}]})
    monkeypatch.setattr(host,"cmd",readonly)
    response=host.lvm_lab_archive_empty_uncertain({
        "vmid":183,"protected_vm_id":1,"storage":"lab",
        "name":"REP_vl-bbiokou","token":token,
        "lab_ack":"DISPOSABLE-LAB-ONLY"})
    assert response["archived"] is True
    assert response["volumes_freed"] == 0
    assert not pending.exists()
    archived=journal/("reconciled-no-lv-"+token+".json")
    assert json.loads(archived.read_text())["token"]==token
    with pytest.raises(host.HostError):
        host.lvm_lab_archive_empty_uncertain({
            "vmid":183,"protected_vm_id":1,"storage":"lab",
            "name":"REP_vl-bbiokou","token":token,
            "lab_ack":"DISPOSABLE-LAB-ONLY"})


def test_empty_pending_reconciliation_refuses_if_expected_lv_exists(monkeypatch,tmp_path):
    from types import SimpleNamespace
    journal=tmp_path/"lab"/"183"
    journal.mkdir(parents=True)
    token="72c4f9ab271c46a39d2967c1ab2bbe97"
    pending=journal/"pending.json"
    pending.write_text(json.dumps({
        "token":token,"status":"uncertain","mode":"full",
        "planned":["scsi0"],"allocated":{},
    }))
    expected="vm-183-disk-"+str(int(token[:12],16))+".qcow2"
    store=SimpleNamespace(
        root=journal,pending=pending,current_file=journal/"current.json",
        owned=journal/"owned.json",lock_file=journal/".checkpoint.lock",
        target=SimpleNamespace(vmid=183,protected_vm_id=1,storage="lab",
                               inspect=lambda:{"vgname":"LAB-VG"}),
        _read=lambda p:json.loads(p.read_text()) if p.exists() else None)
    monkeypatch.setattr(host,"_lvm_lab_store",lambda _:store)
    monkeypatch.setattr(host,"lvm_lab_validate_standby",lambda _:{"owned":True})
    monkeypatch.setattr(host,"cmd",lambda *a,**kw:json.dumps({
        "report":[{"lv":[{"vg_name":"LAB-VG","lv_name":expected}]}]}))
    with pytest.raises(host.HostError,match="Absence de LV non confirmée"):
        host.lvm_lab_archive_empty_uncertain({
            "vmid":183,"protected_vm_id":1,"storage":"lab",
            "name":"REP_vl-bbiokou","token":token,
            "lab_ack":"DISPOSABLE-LAB-ONLY"})
    assert pending.exists()


def test_source_nic_inventory_does_not_send_qm_set(monkeypatch):
    monkeypatch.setattr(host,"_vm_config",lambda _:{
        "net0":"virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,tag=230,firewall=1",
        "net1":"e1000=AA:BB:CC:DD:EE:00,bridge=vmbr1"})
    monkeypatch.setattr(host,"cmd",lambda *args,**kwargs:(_ for _ in ()).throw(
        AssertionError("Source network inspection must not execute commands")))
    report=host.source_network_inventory({"vmid":183})
    assert report["applied"] is False
    assert report["nics"][0]["source_vlan"]==230
    assert report["nics"][0]["source_bridge"]=="vmbr0"
    assert report["nics"][1]["source_vlan"] is None
    assert "AA:BB:CC" not in str(report)


def test_destination_network_inventory_readonly(monkeypatch):
    monkeypatch.setattr(host.socket,"gethostname",lambda:"sd-bearnaise")
    calls=[]
    def readonly(*args,**kwargs):
        calls.append(args)
        return json.dumps([
            {"type":"bridge","iface":"vmbr0"},
            {"type":"eth","iface":"enp1s0"},
            {"type":"OVSBridge","iface":"vmbr1"}])
    monkeypatch.setattr(host,"cmd",readonly)
    report=host.destination_network_inventory({})
    assert report["bridges"]==["vmbr0","vmbr1"]
    assert report["applied"] is False
    assert len(calls)==1
    assert calls[0][:2]==("pvesh","get")
