"""Isolated restore clone SSH orchestration contracts (no hypervisor touched)."""
from app import pra_lvm_jobs


class SSH:
    def __init__(self, name):
        self.name=name
        self.closed=False
    def close(self):
        self.closed=True


def test_restored_lab_clone_receives_source_proven_hardware_and_stays_off(monkeypatch):
    token="f"*32
    source=SSH("source")
    target=SSH("target")
    vm={"id":17,"source_vmid":301,"target_vmid":3301,"target_storage":"lab-lvm"}
    monkeypatch.setattr(pra_lvm_jobs.dr_protected_vms,"_one",lambda _:vm)
    monkeypatch.setattr(pra_lvm_jobs.pra_lvm_lab,"_runtime",lambda _: {
        "source":{"host":"src.invalid"},"destination":{"host":"dst.invalid"},
        "slots":["scsi0"],
    })
    monkeypatch.setattr(pra_lvm_jobs.pra_lvm_lab.pra_live,"_connect",
                        lambda binding: source if binding["host"]=="src.invalid" else target)
    calls=[]
    def helper(ssh,action,data,timeout=60):
        calls.append((action,data))
        if action=="source_probe":
            return {"pra_protocol":2,"config":{"bios":"seabios","cores":"2",
                                                "memory":"2048","sockets":"1"}}
        if action=="lvm_lab_probe":
            return {"pra_protocol":3,"pending":None,
                    "checkpoint":{"status":"committed","token":token}}
        if action=="lvm_lab_prepare_clone":
            assert data["test_vmid"]==4301
            assert data["source_config"]["memory"]=="2048"
            assert data["lab_ack"]==pra_lvm_jobs.LAB_ACK
            assert data["token"]==token
            return {"test_vmid":4301,"checkpoint":token,
                    "vm_started":False,"standby_started":False,
                    "network_isolated":True,"boot_verified":False,
                    "failover_authorized":False}
        raise AssertionError("Unexpected helper action "+action)
    monkeypatch.setattr(pra_lvm_jobs.pra_lvm_lab.pra_live,"_run_helper",helper)
    item={"protected_vm_id":17,"checkpoint_token":token,"test_vmid":4301}
    result=pra_lvm_jobs._prepare_isolated_clone(item)
    assert result["checkpoint_token"]==token
    assert result["network_isolated"] is True
    assert result["vm_started"] is False
    assert result["real_pve_replication_performed"] is False
    assert [x[0] for x in calls]==[
        "source_probe","lvm_lab_probe","lvm_lab_prepare_clone"]
    assert source.closed and target.closed


def test_restore_does_not_invoke_mutation_on_changed_checkpoint(monkeypatch):
    token="f"*32
    src=SSH("src")
    dst=SSH("dst")
    monkeypatch.setattr(pra_lvm_jobs.dr_protected_vms,"_one",
                        lambda _:{"id":17,"source_vmid":301,"target_vmid":3301,"target_storage":"lab-lvm"})
    monkeypatch.setattr(pra_lvm_jobs.pra_lvm_lab,"_runtime",lambda _: {
        "source":{"host":"src.invalid"},"destination":{"host":"dst.invalid"},"slots":["scsi0"]
    })
    monkeypatch.setattr(pra_lvm_jobs.pra_lvm_lab.pra_live,"_connect",
                        lambda binding:src if binding["host"]=="src.invalid" else dst)
    actions=[]
    def helper(conn,action,payload,timeout=60):
        actions.append(action)
        if action=="source_probe":return {"pra_protocol":2,"config":{"memory":"1024"}}
        if action=="lvm_lab_probe":
            return {"pra_protocol":3,"checkpoint":{"token":"0"*32,"status":"committed"},
                    "pending":None}
        raise AssertionError("No restore helper is allowed on mismatched checkpoint")
    monkeypatch.setattr(pra_lvm_jobs.pra_lvm_lab.pra_live,"_run_helper",helper)
    try:
        pra_lvm_jobs._prepare_isolated_clone({
            "protected_vm_id":17,"checkpoint_token":token,"test_vmid":4301})
    except pra_lvm_jobs.LabJobError as exc:
        assert "Checkpoint" in str(exc)
    else:
        raise AssertionError("Mismatched checkpoint accepted")
    assert actions==["source_probe","lvm_lab_probe"]
    assert src.closed and dst.closed
