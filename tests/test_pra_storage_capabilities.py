"""Read-only inventory guards: snapshot-as-volume-chain is not PRA replication."""
from app import dr_protected_vms
from app import pra_storage_capabilities as caps


def test_shared_lvm_chain_is_a_capability_not_an_available_replication_adapter():
    discovered=caps.describe_storage({
        "storage":"example-lvm", "type":"lvm", "shared":1,
        "vgname":"any-vg", "content":"rootdir,images",
        "snapshot-as-volume-chain":1,
    })
    assert discovered["shared"] is True
    assert discovered["snapshot_as_volume_chain"] is True
    assert discovered["snapshot_chain_preview"] is True
    assert discovered["snapshot_chain_existing_raw_supported"] is False
    assert discovered["per_disk_format_probe_required"] is True
    assert discovered["qmp_receiver_available"] is False
    assert discovered["qmp_receiver_adapter"] is None


def test_lvm_chain_is_not_inferred_from_storage_name_or_vendor():
    for name in ("SD-Prod-Proxmox","PureStorage","pool42","lvmData"):
        item=caps.describe_storage({
            "storage":name, "type":"lvm", "content":"images",
            "snapshot-as-volume-chain":0
        })
        assert item["snapshot_as_volume_chain"] is False
        assert item["qmp_receiver_available"] is False


def test_invalid_and_disabled_storage_cannot_be_receiver():
    assert caps.describe_storage({"storage":"disabled","type":"dir",
        "content":"images","disable":1})["qmp_receiver_available"] is False
    assert caps.describe_storage({"storage":"pbs-archive","type":"pbs",
        "content":"backup"})["qmp_receiver_available"] is False
    assert caps.describe_storage({"storage":"no-image","type":"dir",
        "content":"iso,vztmpl"})["qmp_receiver_available"] is False


def test_dir_is_only_current_receiver_in_v0324():
    item=caps.describe_storage({"storage":"generic-dir","type":"dir",
                               "content":"images,iso"})
    assert item["qmp_receiver_adapter"]=="dir-qcow2-cow"
    assert item["qmp_receiver_available"] is True
    assert item["per_disk_format_probe_required"] is True


def test_storages_inventory_preserves_vendor_neutral_capabilities(monkeypatch):
    from app import integrations
    monkeypatch.setattr(integrations,"proxmox_request",
        lambda *args: [{"storage":"a","type":"lvm","shared":1,
                        "content":"images","snapshot-as-volume-chain":1},
                       {"storage":"b","type":"dir","content":"images"}])
    items=dr_protected_vms._storages({"url":"https://pve.example.invalid"},
                                    {"type":"proxmox"})
    assert len(items)==2
    assert items[0]["snapshot_as_volume_chain"] is True
    assert items[0]["qmp_receiver_available"] is False
    assert items[1]["qmp_receiver_available"] is True
