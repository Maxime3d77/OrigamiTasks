"""Security regression tests for the remote-uninstall and agent metrics UI.

Remote removal must be restricted to an existing enrolled agent, a matching
host, a pre-trusted SSH host key and confirmation of the agent installation.
"""
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from app import agents, config, db, host_keys, agent_uninstall, executor, security
from app.main import app


class FakeChannel:
    def __init__(self, exit_code=0):
        self.exit_code=exit_code
    def recv_exit_status(self):
        return self.exit_code


class FakeStdout:
    def __init__(self, text=b'ORIGAMI_AGENT_REMOVED\n', exit_code=0):
        self.text=text
        self.channel=FakeChannel(exit_code)
    def read(self, maximum):
        return self.text[:maximum]


class FakeSSH:
    def __init__(self, *, exit_code=0):
        self.commands=[]
        self.exit_code=exit_code
        self.closed=False
    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        return None,FakeStdout(exit_code=self.exit_code),None
    def close(self):
        self.closed=True


def new_agent(platform='linux',hostname='node-ci'):
    response=agents.register('agent-uninstall-test',platform,'ci')
    enrolled=agents.enroll({
        'agent_id':response['id'],'enrollment_token':response['enrollment_token'],
        'platform':platform,'hostname':hostname
    })
    with db.session() as con:
        cid=con.execute(
            "INSERT INTO credentials (name,kind,encrypted_payload,created_at,updated_at) "
            "VALUES(?,?,?,?,?)",
            ('ssh-remove-test-'+str(response['id']),'ssh',security.encrypt({'username':'operator'}),
             db.now(),db.now())
        ).lastrowid
    return response['id'],cid,enrolled['token']


def cleanup(aid,cid):
    with db.session() as con:
        con.execute('DELETE FROM agent_tasks WHERE agent_id=?',(aid,))
        con.execute('DELETE FROM agents WHERE id=?',(aid,))
        con.execute('DELETE FROM credentials WHERE id=?',(cid,))


def test_uninstall_linux_uses_trusted_ssh_and_revokes_token_only_after_success(monkeypatch):
    monkeypatch.setattr(config,'AGENT_SERVER_URL','https://origami.example.org')
    monkeypatch.setattr(host_keys,'inspect',lambda host,port:{
        'status':'trusted','fingerprint':'SHA256:trusted'
    })
    monkeypatch.setattr(executor,'credential',lambda credential_id:{'username':'operator'})
    ssh=FakeSSH()
    monkeypatch.setattr(executor,'_ssh',lambda *args:(ssh,None))
    aid,cid,token=new_agent()
    try:
        result=agent_uninstall.uninstall(aid,{
            'host':'node-ci.example.org','port':22,'credential_id':cid,'approved':True
        })
        assert result['status']=='uninstalled'
        assert ssh.closed
        assert len(ssh.commands)==1
        assert '/opt/origamitasks-agent' in ssh.commands[0]
        assert 'systemctl' in ssh.commands[0]
        assert 'AGENT_SERVER_URL' not in ssh.commands[0]
        with db.session() as con:
            row=con.execute('SELECT revoked,token_hash,uninstalled_at FROM agents WHERE id=?',(aid,)).fetchone()
        assert row['revoked']==1 and row['token_hash'] is None and row['uninstalled_at']
    finally:
        cleanup(aid,cid)


def test_uninstall_blocks_unknown_host_key_and_mismatched_hostname(monkeypatch):
    monkeypatch.setattr(config,'AGENT_SERVER_URL','https://origami.example.org')
    monkeypatch.setattr(executor,'_ssh',lambda *args: (_ for _ in ()).throw(AssertionError('no SSH')))
    aid,cid,_=new_agent()
    try:
        with pytest.raises(agent_uninstall.AgentUninstallError,match='nom d.hôte'):
            agent_uninstall.uninstall(aid,{
                'host':'other.example.org','port':22,'credential_id':cid,'approved':True
            })
        monkeypatch.setattr(host_keys,'inspect',lambda host,port:{'status':'unknown'})
        with pytest.raises(agent_uninstall.AgentUninstallError,match='Clé d.hôte'):
            agent_uninstall.uninstall(aid,{
                'host':'node-ci.example.org','port':22,'credential_id':cid,'approved':True
            })
        with db.session() as con:
            row=con.execute('SELECT revoked,uninstalled_at FROM agents WHERE id=?',(aid,)).fetchone()
        assert row['revoked']==0 and row['uninstalled_at'] is None
    finally:
        cleanup(aid,cid)


def test_uninstall_failed_remote_command_does_not_revoke(monkeypatch):
    monkeypatch.setattr(config,'AGENT_SERVER_URL','https://origami.example.org')
    monkeypatch.setattr(host_keys,'inspect',lambda host,port:{'status':'trusted'})
    monkeypatch.setattr(executor,'credential',lambda cid:{'username':'operator'})
    ssh=FakeSSH(exit_code=31)
    monkeypatch.setattr(executor,'_ssh',lambda *args:(ssh,None))
    aid,cid,_=new_agent()
    try:
        with pytest.raises(agent_uninstall.AgentUninstallError,match='suppression distante'):
            agent_uninstall.uninstall(aid,{
                'host':'node-ci.example.org','port':22,'credential_id':cid,'approved':True
            })
        with db.session() as con:
            row=con.execute('SELECT revoked,uninstalled_at FROM agents WHERE id=?',(aid,)).fetchone()
        assert row['revoked']==0 and row['uninstalled_at'] is None
    finally:
        cleanup(aid,cid)


def test_windows_uninstall_is_fixed_power_shell_script():
    cmd=agent_uninstall._windows_command(17,'dGVzdA==')
    assert cmd.startswith('powershell.exe -NoProfile -NonInteractive -EncodedCommand ')
    import base64
    data=base64.b64decode(cmd.split()[-1]).decode('utf-16le')
    assert "OrigamiTasks\\Agent" in data
    assert "Unregister-ScheduledTask" in data
    assert "if([int]$cfg.agent_id -ne 17" in data


def test_poll_metrics_are_validated_and_returned(monkeypatch):
    aid,cid,_=new_agent()
    try:
        with db.session() as con:
            agent=dict(con.execute('SELECT * FROM agents WHERE id=?',(aid,)).fetchone())
        assert agents.poll(agent,{
            'hostname':'node-ci','version':'0.3.1',
            'cpu_percent':1.23,'memory_mb':37.4
        })['task'] is None
        with db.session() as con:
            row=con.execute('SELECT cpu_percent,memory_mb FROM agents WHERE id=?',(aid,)).fetchone()
        assert row['cpu_percent']==1.23 and row['memory_mb']==37.4
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            agents.poll(agent,{'cpu_percent':True})
        with pytest.raises(HTTPException):
            agents.poll(agent,{'cpu_percent':float('nan')})
    finally:
        cleanup(aid,cid)


def test_overview_tabs_and_agent_security_notices():
    base=Path(__file__).resolve().parents[1]
    js=(base/'static/app.js').read_text()
    css=(base/'static/styles.css').read_text()
    linux=(base/'agents/linux/agent.py').read_text()
    ps=(base/'agents/windows/agent.ps1').read_text()
    installer=(base/'app/agents.py').read_text()
    for part in ('function dashboardTasks(', 'function dashboardAgents(',
                 'function dashboardProxmox(', "data-action=\"overview-tab\"",
                 "function openAgentUninstallForm(", "agent_uninstall"):
        assert part in js or (part=='agent_uninstall' and '/uninstall' in js)
    assert "moteur PRA absent" in js
    assert "CPUQuota=20%" in installer and "MemoryMax=192M" in installer
    assert "poll_interval_seconds" in linux and "poll_interval_seconds" in ps
    assert ".overview-tabs button.active" in css


def test_agent_uninstall_api_requires_admin():
    with TestClient(app) as client:
        assert client.post('/api/agents/1/uninstall',json={
            'host':'node-ci.example.org','port':22,'credential_id':1,'approved':True
        }).status_code in (401,403)
