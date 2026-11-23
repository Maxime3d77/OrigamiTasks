"""Read-only DR network planning cannot authorize failover or NIC activation."""
import pytest

from app import pra_lvm_network_plan as net


def test_same_bridge_is_only_a_suggestion_not_network_readiness():
    preview=net.build_preview(
        {"nics":[{"slot":"net0","model":"virtio","source_bridge":"vmbr0",
                  "source_vlan":230}]},
        {"bridges":["vmbr0","vmbr1"]})
    assert preview["nics"][0]["bridge_exists"] is True
    assert preview["nics"][0]["suggested_target_vlan"]==230
    assert preview["nics"][0]["vlan_reachability_verified"] is False
    assert preview["nics"][0]["source_mac_reuse_authorized"] is False
    assert preview["network_ready"] is False
    assert preview["apply_allowed"] is False
    assert preview["production_failover_authorized"] is False


def test_missing_bridge_has_no_implicit_mapping():
    preview=net.build_preview(
        {"nics":[{"slot":"net0","model":"virtio","source_bridge":"vmbr230",
                  "source_vlan":230}]},
        {"bridges":["vmbr0"]})
    assert preview["nics"][0]["suggested_target_bridge"] is None
    assert preview["missing_bridges"]==["vmbr230"]


@pytest.mark.parametrize("source,target",[
    ({"nics":"invalid"},{"bridges":[]}),
    ({"nics":[{"slot":"net0","source_bridge":"vmbr0","source_vlan":9999}]},
      {"bridges":["vmbr0"]}),
    ({"nics":[{"slot":"net0","source_bridge":"vmbr0"},
              {"slot":"net0","source_bridge":"vmbr1"}]},
      {"bridges":["vmbr0","vmbr1"]}),
    ({"nics":[]},{"bridges":[123]}),
])
def test_invalid_mapping_fails_closed(source,target):
    with pytest.raises(ValueError):
        net.build_preview(source,target)


def test_network_preview_is_read_only_contract():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    helper=(root/"agents/linux/pra_host_worker.py").read_text()
    code=(root/"app/pra_lvm_network_plan.py").read_text()
    ui=(root/"static/app.js").read_text()
    assert "def source_network_inventory(data):" in helper
    assert "def destination_network_inventory(data):" in helper
    assert '"source_network_inventory"' in (root/"app/pra_live.py").read_text()
    assert "qm set" not in code
    assert "qm start" not in code
    assert "Prévisualiser le réseau de secours" in ui
