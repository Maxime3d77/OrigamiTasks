"""Lab QMP-to-LVM controller only uses vetted remote helper actions."""
import pytest

from app import pra_lvm_lab as lab


class SSH:
    def __init__(self, host):
        self.host=host
        self.closed=False
        self.forwards=[]
    def close(self):
        self.closed=True
    def get_transport(self):
        return self
    def cancel_port_forward(self, bind,port):
        self.forwards.append((bind,port))


@pytest.fixture
def pipeline(monkeypatch):
    vm={
        "id":17,"plan_id":4,"source_dc_id":1,"target_dc_id":2,
        "source_site_id":3,"target_site_id":5,
        "source_vmid":301,"target_vmid":3301,
        "target_node":"lab-node","target_storage":"lab-lvm",
        "engine":"qmp_live_incremental","state":"planned"
    }
    monkeypatch.setattr(lab.dr_protected_vms,"_one",lambda vmid:vm)
    monkeypatch.setattr(lab,"_runtime",lambda policy:{
        "source":{"host":"src.invalid"},"destination":{"host":"dst.invalid"},
        "slots":["scsi0","scsi1"],"available_bytes":500*1024**3,
        "source_node":"lab-src"})
    connections=[SSH("src"),SSH("dst")]
    monkeypatch.setattr(lab.pra_live,"_connect",
                        lambda binding:connections[0] if binding["host"]=="src.invalid"
                        else connections[1])
    source={"pra_protocol":2, "source_vm_uuid":"vm-301-generation1",
            "active_jobs":[],"sizes":{"scsi0":1024**3,"scsi1":2*1024**3},
            "bitmaps":{
                slot:{"origami_301_"+slot:{"recording":True,"busy":False,
                                           "inconsistent":False}}
                for slot in ["scsi0","scsi1"]
            }}
    current=None
    calls=[]
    def helper(conn,action,data,timeout=60):
        calls.append((action,data))
        if action=="source_probe":
            return source
        if action=="lvm_lab_probe":
            return {"pra_protocol":3,"checkpoint":current,"pending":None,
                    "generation_count":0,"retention_limit":8,
                    "lab_only":True}
        if action=="lvm_lab_begin":
            return {"token":"b"*32,"ports":{"scsi0":10809,"scsi1":10810}}
        if action=="source_copy":
            return {"qmp_jobs_completed":True,"snapshot_epoch":1784600000}
        if action=="lvm_lab_finish":
            return {"checkpoint":{"token":"b"*32}}
        if action=="lvm_lab_verify":
            return {"checkpoint_token":"b"*32,"verified_offline":True,
                    "vm_started":False,"disks":["scsi0","scsi1"]}
        raise AssertionError(action)
    monkeypatch.setattr(lab.pra_live,"_run_helper",helper)
    monkeypatch.setattr(lab.pra_live,"_forward",
                        lambda *_:({"scsi0":30009,"scsi1":30010},[30009,30010],[],[]))
    return vm,connections,source,calls


def test_read_only_preflight_never_allocates_lab_lvs(pipeline):
    vm,connections,source,calls=pipeline
    result=lab.run(17)
    assert result["real_pve_replication_performed"] is False
    assert result["source_running"] and result["target_stopped"]
    assert not any(x[0] in ("lvm_lab_begin","source_copy","lvm_lab_finish") for x in calls)
    assert connections[0].closed and connections[1].closed


def test_complete_lab_copy_only_to_owned_stopped_lvm(pipeline):
    vm,connections,source,calls=pipeline
    result=lab.run(17,execute=True,lab_confirm=lab.LAB_ACK)
    assert result["real_pve_replication_performed"] is True
    assert result["offline_structure_verified"]
    assert result["vm_started"] is False and result["failover_available"] is False
    assert [action for action,_ in calls]==[
        "source_probe","lvm_lab_probe","lvm_lab_begin",
        "source_copy","lvm_lab_finish","lvm_lab_verify"]
    assert connections[0].forwards==[("127.0.0.1",30009),("127.0.0.1",30010)]
    assert connections[0].closed and connections[1].closed


def test_missing_lab_ack_blocks_unsafe_volume_creation(pipeline):
    vm,connections,source,calls=pipeline
    with pytest.raises(lab.LabError,match="acknowledgement"):
        lab.run(17,execute=True)
    assert not any(x[0]=="lvm_lab_begin" for x in calls)


def test_incremental_without_committed_point_is_denied(pipeline):
    vm,connections,source,calls=pipeline
    with pytest.raises(lab.LabError,match="FULL LAB reseed"):
        lab.run(17,execute=True,incremental=True,lab_confirm=lab.LAB_ACK)
    assert not any(x[0]=="lvm_lab_begin" for x in calls)


def test_failed_source_copy_preserves_lvm_checkpoint_for_reconciliation(pipeline,monkeypatch):
    vm,connections,source,calls=pipeline
    orig=lab.pra_live._run_helper
    def fail_copy(conn,action,data,timeout=60):
        if action=="source_copy":
            raise RuntimeError("source link lost")
        return orig(conn,action,data,timeout)
    monkeypatch.setattr(lab.pra_live,"_run_helper",fail_copy)
    with pytest.raises(lab.LabError,match="UNCONFIRMED: token="):
        lab.run(17,execute=True,lab_confirm=lab.LAB_ACK)
    assert not any(action in ("lvm_lab_abort","lvm_lab_finish") for action,_ in calls)
    assert all(c.closed for c in connections)


def test_lvm_lab_helper_requires_label_and_protection_marker(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/"agents/linux"
    sys.path.insert(0,str(root))
    spec=importlib.util.spec_from_file_location("pra_host_worker_lab_only",root/"pra_host_worker.py")
    worker=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=worker
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker,"_is_target_stopped",lambda _:True)
    cfg={"onboot":"0","description":"ORIGAMITASKS_PRA_17"}
    monkeypatch.setattr(worker,"_vm_config",lambda _:cfg)
    payload={"vmid":3301,"storage":"lab-lvm","protected_vm_id":17,
             "lab_ack":"DISPOSABLE-LAB-ONLY"}
    with pytest.raises(worker.HostError,match="LAB-ONLY"):
        worker._lvm_lab_store(payload)
    cfg["description"]="ORIGAMITASKS_PRA_170 ORIGAMITASKS_LAB_ONLY"
    with pytest.raises(worker.HostError,match="LAB-ONLY"):
        worker._lvm_lab_store(payload)
    cfg["description"]="ORIGAMITASKS_PRA_17 ORIGAMITASKS_LAB_ONLY_BUT_NOT_REAL"
    with pytest.raises(worker.HostError,match="LAB-ONLY"):
        worker._lvm_lab_store(payload)
    cfg["description"]="ORIGAMITASKS_PRA_17 ORIGAMITASKS_LAB_ONLY"
    cfg["net0"]="virtio,bridge=vmbr0"
    with pytest.raises(worker.HostError,match="zero network"):
        worker._lvm_lab_store(payload)


def test_retention_limit_blocks_next_lvm_allocation_before_begin(pipeline,monkeypatch):
    vm,connections,source,calls=pipeline
    original=lab.pra_live._run_helper
    def full_ledger(conn,action,data,timeout=60):
        response=original(conn,action,data,timeout)
        if action=="lvm_lab_probe":
            return {**response,"generation_count":8,"retention_limit":8}
        return response
    monkeypatch.setattr(lab.pra_live,"_run_helper",full_ledger)
    report=lab.run(17)
    assert report["generation_count"]==8
    assert report["retention_blocked"] is True
    assert report["retention_limit"]==8
    with pytest.raises(lab.LabError,match="8 générations LVM"):
        lab.run(17,execute=True,lab_confirm=lab.LAB_ACK)
    assert not any(a=="lvm_lab_begin" for a,_ in calls)


def test_missing_retention_inventory_fails_closed(pipeline,monkeypatch):
    vm,connections,source,calls=pipeline
    original=lab.pra_live._run_helper
    def legacy(conn,action,data,timeout=60):
        response=original(conn,action,data,timeout)
        if action=="lvm_lab_probe":
            return {"pra_protocol":3,"checkpoint":None,"pending":None}
        return response
    monkeypatch.setattr(lab.pra_live,"_run_helper",legacy)
    with pytest.raises(lab.LabError,match="Inventaire de rétention LVM"):
        lab.run(17)
    assert not any(a=="lvm_lab_begin" for a,_ in calls)
