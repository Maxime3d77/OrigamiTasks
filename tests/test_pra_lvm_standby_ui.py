"""Regression checks for UI-based, fail-closed LAB standby creation."""
import pytest
from app import pra_lvm_standby


class Connection:
    closed = False
    def close(self):
        self.closed = True


class Session:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, query, args):
        class Cursor:
            def fetchone(self): return None
        return Cursor()


@pytest.fixture
def setup(monkeypatch):
    vm = dict(id=17, plan_id=1, source_dc_id=1, target_dc_id=2,
              source_site_id=11, target_site_id=12, source_vmid=301,
              target_vmid=4301, target_node="pve-backup",
              target_storage="lab-lvm", replica_name="REP_lab",
              engine="qmp_live_incremental", state="planned")
    monkeypatch.setattr(pra_lvm_standby.dr_protected_vms, "_one", lambda _: vm)
    monkeypatch.setattr(pra_lvm_standby.pra_live, "_scope", lambda _: None)
    source = [{"type":"qemu","vmid":301,"status":"running","node":"pve-source"}]
    target = [{"type":"node","node":"pve-backup","status":"online"}]
    def inventory(dc):
        return ({}, {}, source if dc == 1 else target)
    monkeypatch.setattr(pra_lvm_standby.dr_protected_vms, "_inventory", inventory)
    monkeypatch.setattr(pra_lvm_standby.dr_protected_vms, "_storages",
                        lambda *a:[{"storage":"lab-lvm","type":"lvm",
                                     "images":True,"snapshot_as_volume_chain":True}])
    monkeypatch.setattr(pra_lvm_standby.dr_protected_vms, "_node_storage",
                        lambda *a:{"active":True})
    monkeypatch.setattr(pra_lvm_standby.db,"session",lambda: Session())
    monkeypatch.setattr(pra_lvm_standby.pra_live,"_remote_ssh_binding",
                        lambda *a:{"host":"pve-backup"})
    conn=Connection()
    monkeypatch.setattr(pra_lvm_standby.pra_live,"_connect",lambda _:conn)
    events=[]
    def helper(conn, action, data, timeout=60):
        events.append((action,data))
        return {"created":True,"vmid":4301,"target_stopped":True,
                "network_isolated":True,"lab_only":True}
    monkeypatch.setattr(pra_lvm_standby.pra_live,"_run_helper",helper)
    return vm, source, target, events, conn


def test_automatic_standby_uses_scoped_host_helper(setup):
    vm, source, target, events, conn=setup
    assert pra_lvm_standby.prepare(17,"DISPOSABLE-LAB-ONLY")["created"]
    assert [name for name, _ in events]==["lvm_lab_prepare_standby"]
    assert events[0][1]["protected_vm_id"]==vm["id"]
    assert events[0][1]["name"]==vm["replica_name"]
    assert conn.closed


def test_reject_unapproved_or_running_source_without_ssh(setup):
    vm, source, target, events, conn=setup
    with pytest.raises(ValueError, match="DISPOSABLE"):
        pra_lvm_standby.prepare(17,"")
    source[0]["status"]="stopped"
    with pytest.raises(ValueError, match="source"):
        pra_lvm_standby.prepare(17,"DISPOSABLE-LAB-ONLY")
    assert not events


def test_reject_foreign_cluster_vmid_without_ssh(setup):
    vm, source, target, events, conn=setup
    target.append({"type":"qemu","vmid":4301,"node":"other-node","status":"stopped"})
    with pytest.raises(ValueError, match="VMID"):
        pra_lvm_standby.prepare(17,"DISPOSABLE-LAB-ONLY")
    assert not events


def test_reject_unverified_host_response(setup, monkeypatch):
    vm, source, target, events, conn=setup
    monkeypatch.setattr(pra_lvm_standby.pra_live,"_run_helper",
                        lambda *a,**k:{"created":True,"vmid":4301,
                                      "target_stopped":False,
                                      "network_isolated":True,"lab_only":True})
    with pytest.raises(ValueError, match="confirm"):
        pra_lvm_standby.prepare(17,"DISPOSABLE-LAB-ONLY")
    assert conn.closed


def test_existing_owned_target_with_stale_inventory_state_is_host_verified(setup):
    vm, source, target, events, conn = setup
    target.append({"type":"qemu","vmid":4301,"node":"pve-backup","status":"unknown"})
    result=pra_lvm_standby.prepare(17,"DISPOSABLE-LAB-ONLY")
    assert result["target_stopped"] is True
    assert events[0][0]=="lvm_lab_prepare_standby"
    assert events[0][1]["protected_vm_id"]==17


def test_existing_running_target_is_rejected_without_remote_mutation(setup):
    vm, source, target, events, conn=setup
    target.append({"type":"qemu","vmid":4301,"node":"pve-backup","status":"running"})
    with pytest.raises(ValueError,match="en marche"):
        pra_lvm_standby.prepare(17,"DISPOSABLE-LAB-ONLY")
    assert events==[]
