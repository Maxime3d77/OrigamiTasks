"""Agent upgrade endpoints are administrative and never re-register identities."""
from pathlib import Path
from app import agent_updates
from fastapi.testclient import TestClient
from app.main import app


def test_version_comparison():
    assert agent_updates.available_versions()=={"linux":"0.3.33","windows":"0.3.33"}
    assert agent_updates.outdated("0.3.2","0.3.23")
    assert not agent_updates.outdated("0.3.23","0.3.23")
    assert not agent_updates.outdated("0.4.0","0.3.23")
    assert not agent_updates.outdated("unknown","0.3.23")


def test_update_rejects_missing_approval():
    try:
        agent_updates.queue([1],1,False,"ci")
    except agent_updates.UpdateError as exc:
        assert "Validation" in str(exc)
    else:
        raise AssertionError("Update must reject missing confirmation")


def test_upgrade_routes_are_admin_only():
    with TestClient(app) as client:
        assert client.get('/api/agents/updates').status_code==401
        assert client.post('/api/agents/updates',json={}).status_code in (401,403)


def test_ui_provides_single_and_bulk_upgrade():
    content=(Path(__file__).resolve().parents[1]/'static/app.js').read_text()
    for needle in ("agent-update-all","agent-update","agentUpdateForm","agentVersionOutdated",
                   "Mise à jour en cours","/agents/updates"):
        assert needle in content


def test_upgrade_does_not_replace_identity_or_modules():
    source=(Path(__file__).resolve().parents[1]/'app/agent_updates.py').read_text()
    assert "config.json" in source
    assert "modules.json" not in source
    assert "agent_id" in source
    assert "installed_controller_url" not in source
