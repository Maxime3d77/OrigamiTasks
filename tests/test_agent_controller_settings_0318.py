"""Regression tests: HTTPS controller URL from UI, behind Nginx, no .env edits.

Also ensure that changing the public URL does not prevent verified removal
of agents installed using the old address, and Windows stays workflows-only.
"""
from fastapi.testclient import TestClient
import pytest

from app import (agent_deployments, agent_settings, agent_uninstall, agents,
                 config, db, executor, host_keys, security)
from app.main import app


def admin(client):
    response=client.post('/api/login',json={
        'username':'admin','password':'test-admin-password-over-12'})
    assert response.status_code==200,response.text
    return {'X-CSRF-Token':client.cookies['origami_csrf']}


def _settings():
    with db.session() as con:
        r=con.execute("SELECT controller_url,updated_at FROM agent_settings WHERE id=1").fetchone()
        return dict(r) if r else None


def _restore(before):
    with db.session() as con:
        con.execute('DELETE FROM agent_settings WHERE id=1')
        if before:
            con.execute(
                "INSERT INTO agent_settings(id,controller_url,updated_at) VALUES(1,?,?)",
                (before['controller_url'],before['updated_at']))


def test_settings_https_origin_and_admin_only(monkeypatch):
    monkeypatch.setattr(config,'AGENT_SERVER_URL','')
    before=_settings()
    monkeypatch.setattr(agent_deployments,'start',lambda:None)
    try:
        with db.session() as con:
            con.execute('DELETE FROM agent_settings WHERE id=1')
        with TestClient(app) as client:
            assert client.get('/api/admin/agent-settings').status_code==401
            assert client.put('/api/admin/agent-settings',json={
                'controller_url':'https://origami.example.org'
            }).status_code in (401,403)
            headers=admin(client)
            response=client.get('/api/admin/agent-settings')
            assert response.status_code==200
            assert response.json()['configured'] is False
            assert response.json()['source']=='non configuré'
            assert not response.json()['controller_url']
            for invalid in (
                'http://origami.example.org',
                'http://127.0.0.1:8091',
                'https://127.0.0.1:8091',
                'https://localhost:8091',
                'https://origami.example.org/api',
                'https://operator:secret@origami.example.org',
                'https://origami.example.org?token=hidden',
                'https://origami.example.org#fragment',
                '',
            ):
                bad=client.put('/api/admin/agent-settings',headers=headers,json={
                    'controller_url':invalid})
                assert bad.status_code==422,(invalid,bad.text)
            saved=client.put('/api/admin/agent-settings',headers=headers,json={
                'controller_url':'https://origami.example.org/'
            })
            assert saved.status_code==200,saved.text
            assert saved.json()['controller_url']=='https://origami.example.org'
            assert saved.json()['source']=='interface'
            assert agent_settings.effective_url()=='https://origami.example.org'
            assert _settings()['controller_url']=='https://origami.example.org'
    finally:
        _restore(before)


def test_saved_ui_url_overrides_legacy_env_and_no_domain_magic(monkeypatch):
    monkeypatch.setattr(config,'AGENT_SERVER_URL','https://legacy.example.org')
    before=_settings()
    try:
        with db.session() as con:
            con.execute('DELETE FROM agent_settings WHERE id=1')
        assert agent_settings.effective_url()=='https://legacy.example.org'
        agent_settings.update({'controller_url':'https://new-controller.example.org'})
        assert agent_settings.effective_url()=='https://new-controller.example.org'
        assert agent_settings.current()['source']=='interface'
        monkeypatch.setattr(config,'AGENT_SERVER_URL','')
        assert agent_settings.effective_url()=='https://new-controller.example.org'
    finally:
        _restore(before)


class FakeChannel:
    def recv_exit_status(self):
        return 0


class FakeStdout:
    channel=FakeChannel()
    def read(self, count):
        return b'ORIGAMI_AGENT_REMOVED\n'


class FakeSSH:
    def __init__(self):
        self.commands=[]
    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        return None,FakeStdout(),None
    def close(self):
        pass


def test_uninstall_retains_original_installed_endpoint(monkeypatch):
    monkeypatch.setattr(config,'AGENT_SERVER_URL','')
    before=_settings()
    a=agents.register('ci-install-origin','linux','ci')
    agents.enroll({
        'agent_id':a['id'],
        'enrollment_token':a['enrollment_token'],
        'platform':'linux','hostname':'pve-fake'
    })
    with db.session() as con:
        con.execute('UPDATE agents SET installed_controller_url=? WHERE id=?',
                    ('https://old.example.org',a['id']))
        cid=con.execute(
            "INSERT INTO credentials(name,kind,encrypted_payload,created_at,updated_at) "
            "VALUES(?,?,?,?,?)",
            ('ci-ssh-old-origin-'+str(a['id']),'ssh',
             security.encrypt({'username':'root'}),
             db.now(),db.now())
        ).lastrowid
    try:
        agent_settings.update({'controller_url':'https://new.example.org'})
        fake=FakeSSH()
        monkeypatch.setattr(host_keys,'inspect',
                            lambda hostname,port:{'status':'trusted'})
        monkeypatch.setattr(executor,'_ssh',lambda *args:(fake,None))
        monkeypatch.setattr(executor,'credential',lambda cid:{'username':'root'})
        result=agent_uninstall.uninstall(a['id'],{
            'host':'pve-fake.example.org','port':22,
            'credential_id':cid,'approved':True
        })
        assert result['status']=='uninstalled'
        assert len(fake.commands)==1
        assert 'old.example.org' not in fake.commands[0]  # encoded on SSH command line
        with db.session() as con:
            row=con.execute(
                'SELECT revoked,uninstalled_at FROM agents WHERE id=?',
                (a['id'],)
            ).fetchone()
        assert row['revoked']==1 and row['uninstalled_at']
    finally:
        with db.session() as con:
            con.execute('DELETE FROM agent_tasks WHERE agent_id=?',(a['id'],))
            con.execute('DELETE FROM agents WHERE id=?',(a['id'],))
            con.execute('DELETE FROM credentials WHERE id=?',(cid,))
        _restore(before)


def test_windows_user_interface_is_workflows_only():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    js=(root/'static/app.js').read_text(encoding='utf-8')
    windows=(root/'agents/windows/agent.ps1').read_text(encoding='utf-8')
    assert "Agent Windows : uniquement le module Standard" in js
    assert "proxmox=$false" in windows
    assert "proxmox='unsupported'" in windows
    assert 'agentConnectionSettingsForm' in js
    assert "enable_pra" in js
    assert 'PRA préparatoire (facultatif)' in js
    assert 'agent-settings-open' in js
    assert 'install' in (root/'app/agent_uninstall.py').read_text(encoding='utf-8').lower()
