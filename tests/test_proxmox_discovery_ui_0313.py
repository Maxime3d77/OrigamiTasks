"""Proxmox discovery screen: filtering, sorting and paging remain read-only.

Source-level regression coverage complements Node.js --check in CI; all rows
are populated from the existing read-only cluster/resources endpoint.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_proxmox_discovery_has_every_requested_type_and_sort():
    js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    assert "const PVE_TYPES={node:'Nœuds',qemu:'VM',lxc:'LXC',storage:'Stockages'}" in js
    assert "id=\"proxmoxResourceSearch\"" in js
    assert "id=\"proxmoxResourceType\"" in js
    assert "id=\"proxmoxResourceSort\"" in js
    assert "id=\"proxmoxResourceDirection\"" in js
    assert "valueFor=item=>" in js
    assert "localeCompare(str(y),'fr',{numeric:true" in js
    assert "pageSize:100" in js
    assert "renderProxmoxDiscoveryResults()" in js
    assert "$$('[data-dc-search]')" in js
    assert "data-action=\"proxmox-discovery-refresh\"" in js
    assert "data-action=\"assess-dr\"" in js


def test_proxmox_discovery_view_does_not_issue_guest_mutations():
    js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    start = js.index("function openProxmoxDiscovery(")
    end = js.index("function datacenters()", start)
    section = js[start:end]
    assert "api('/datacenters/'+targetId+'/discover')" not in section  # action handler elsewhere
    assert "modal(" in section
    assert "proxmoxDiscoveryTable" in section
    assert "document.getElementById('proxmoxDiscoveryTable')" in section
    assert "run_id" not in section
    for path in (ROOT / "docs/ARCHITECTURE-PRA-INTERSITES.fr.md",
                 ROOT / "docs/ARCHITECTURE-PRA-INTERSITES.md"):
        text = path.read_text()
        assert "fencing" in text.lower()
        assert "witness" in text.lower() or "témoin" in text.lower()
        assert "REP_" in text
        assert "failback" in text.lower()


def test_proxmox_discovery_stays_on_read_only_route():
    from app.main import app
    routes = {getattr(route, "path", ""): getattr(route, "methods", set())
              for route in app.routes}
    assert "GET" in routes["/api/datacenters/{dc_id}/discover"]
    assert "POST" not in routes["/api/datacenters/{dc_id}/discover"]
