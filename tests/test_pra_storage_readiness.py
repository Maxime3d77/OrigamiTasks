"""Read-only storage readiness for one protected VM, independent of array vendor."""
from app import dr_protected_vms as protected
from app import pra_storage_capabilities as caps


def setup(monkeypatch, target):
    vm={"id":17,"source_dc_id":1,"target_dc_id":2,"source_vmid":218,
        "target_vmid":318,"target_node":"target-node","target_storage":"target-storage"}
    monkeypatch.setattr(protected,"_one",lambda i: vm)
    seen=[]
    def inventory(dc_id):
        seen.append(("inventory",dc_id))
        return ({"id":dc_id,"url":"https://pve.example.invalid","name":"source" if dc_id==1 else "target"},
                {"token":"secret"},
                [{"type":"node","node":"target-node","status":"online"}] if dc_id==2 else [])
    monkeypatch.setattr(protected,"_inventory", inventory)
    monkeypatch.setattr(protected,"_storages",lambda dc,cred: [caps.describe_storage(target)])
    monkeypatch.setattr(protected,"_node_storage",lambda dc,cred,node,storage:
                        {"active":True,"available_bytes":8*1024**4})
    from app import integrations
    monkeypatch.setattr(integrations,"assess_vm_dr",lambda dc,cred,vmid,timeout=18:
                        {"disks":[{"slot":"scsi0","storage":"source-block",
                                   "storage_type":"lvm","snapshot_capability":"not_verified"}]})
    return seen


def test_lvm_snapshot_chain_preflight_not_replication_permission(monkeypatch):
    seen=setup(monkeypatch,{"storage":"target-storage","type":"lvm","content":"images",
                             "shared":1,"snapshot-as-volume-chain":1})
    result=protected.storage_readiness(17)
    assert result["backend"]=="lvm-qcow2-staging"
    assert result["target_storage"]["snapshot_as_volume_chain"] is True
    assert result["node_online"] is True
    assert result["node_storage_active"] is True
    assert result["available_bytes"]==8*1024**4
    assert result["target_replication_authorized"] is False
    assert result["target_failover_authorized"] is False
    assert result["source_disks"][0]["slot"]=="scsi0"
    assert seen==[("inventory",1),("inventory",2)]


def test_directory_receiver_also_needs_real_agent_probe(monkeypatch):
    setup(monkeypatch,{"storage":"target-storage","type":"dir","content":"images"})
    result=protected.storage_readiness(17)
    assert result["backend"]=="dir-qcow2-cow"
    assert result["target_replication_authorized"] is False
    assert any("agent" in x.lower() for x in result["checks"])


def test_lvm_without_chain_cannot_be_mistaken_for_ready(monkeypatch):
    setup(monkeypatch,{"storage":"target-storage","type":"lvm","content":"images",
                       "snapshot-as-volume-chain":0})
    result=protected.storage_readiness(17)
    assert result["backend"]=="lvm-qcow2-staging"
    assert any("désactivées" in x for x in result["checks"])
    assert result["target_replication_authorized"] is False


def test_readiness_cannot_mutate_pve(monkeypatch):
    seen=setup(monkeypatch,{"storage":"target-storage","type":"lvm",
                             "content":"images","snapshot-as-volume-chain":1})
    from app import integrations
    monkeypatch.setattr(integrations,"proxmox_request",lambda *args,**kwargs:
                        (_ for _ in ()).throw(AssertionError("Unexpected Proxmox mutation")))
    report=protected.storage_readiness(17)
    assert report["note"].startswith("Découverte")
    assert seen


def test_lvm_capacity_report_flags_insufficient_space_without_allocating(monkeypatch):
    setup(monkeypatch,{"storage":"target-storage","type":"lvm","content":"images",
                       "snapshot-as-volume-chain":1})
    from app import integrations
    monkeypatch.setattr(integrations, "assess_vm_dr",
        lambda *_args,**_kwargs: {"disks":[
            {"slot":"scsi0","storage":"source","storage_type":"lvm","size_bytes":20*1024**3},
            {"slot":"scsi1","storage":"source","storage_type":"lvm","size_bytes":10*1024**3},
        ]})
    monkeypatch.setattr(protected, "_node_storage",
        lambda *_args,**_kwargs: {"active":True,"available_bytes":25*1024**3})
    report=protected.storage_readiness(17)
    assert report["source_total_virtual_bytes"]==30*1024**3
    assert report["estimated_lvm_generation_bytes"]>30*1024**3
    assert report["available_bytes"]==25*1024**3
    assert any("insuffisant" in x for x in report["checks"])
    assert report["target_replication_authorized"] is False


def test_unknown_source_disk_size_never_claims_sufficient_lvm_capacity(monkeypatch):
    setup(monkeypatch,{"storage":"target-storage","type":"lvm","content":"images",
                       "snapshot-as-volume-chain":1})
    report=protected.storage_readiness(17)
    assert report["source_total_virtual_bytes"] is None
    assert report["estimated_lvm_generation_bytes"] is None
    assert any("non estimable" in x for x in report["checks"])
    assert report["target_replication_authorized"] is False
