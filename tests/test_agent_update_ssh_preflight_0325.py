from pathlib import Path
from app import agent_updates
from fastapi.testclient import TestClient
from app.main import app

def test_preflight_requires_explicit_vault_key():
    for ids,cred in (([],1),([1],0),(["1"],1)):
        try:
            agent_updates.preflight(ids,cred)
        except agent_updates.UpdateError:
            pass
        else:
            raise AssertionError("Invalid request allowed")

def test_preflight_needs_admin():
    with TestClient(app) as client:
        assert client.post('/api/agents/updates/preflight',json={
            'agent_ids':[1], 'credential_id':1
        }).status_code==401

def test_updater_uses_existing_vault_key_and_random_staging():
    src=(Path(__file__).resolve().parents[1]/'app/agent_updates.py').read_text()
    assert 'credential(job["credential_id"])' in src
    assert 'secrets.token_hex(12)' in src
    assert 'sftp.file(stage+"_"+name,"wb")' in src
    assert 'config.json' in src
