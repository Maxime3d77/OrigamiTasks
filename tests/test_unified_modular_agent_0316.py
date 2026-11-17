"""A single modular agent installation with fail-closed, passive Linux PRA runtime."""
import importlib.util
import json
from pathlib import Path
import stat

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import agents, db, agent_modules, security
from app.main import app

ROOT=Path(__file__).resolve().parents[1]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,str(ROOT/path))
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def create_agent(platform='linux'):
    result=agents.register('ci-unified-agent-'+platform,platform,'modular')
    creds=agents.enroll({
        'agent_id':result['id'],'enrollment_token':result['enrollment_token'],
        'platform':platform,'hostname':'ci-node-'+platform
    })
    return result['id'],creds['token']


def site_for_agent(agent_id):
    with db.session() as con:
        credential_id=con.execute(
            "INSERT INTO credentials(name,kind,encrypted_payload,created_at,updated_at) "
            "VALUES (?,?,?,?,?)",
            ('ci-modular-cred-'+str(agent_id),'proxmox',
             security.encrypt({'url':'https://pve-modular.example.org:8006',
                               'username':'tester@pve!agent','token':'synthetic-test-token'}),
             db.now(),db.now())
        ).lastrowid
        dc_id=con.execute(
            "INSERT INTO datacenters(name,url,credential_id,created_at) VALUES (?,?,?,?)",
            ('ci-modular-pve-'+str(agent_id),'https://pve-modular.example.org:8006',
             credential_id,db.now())
        ).lastrowid
        site_id=con.execute(
            "INSERT INTO dr_sites(name,role,storage_profile,created_at,updated_at) VALUES (?,?,?,?,?)",
            ('ci-modular-site-'+str(agent_id),'primary','iscsi_lvm',db.now(),db.now())
        ).lastrowid
        con.execute("INSERT INTO dr_site_datacenters(site_id,datacenter_id) VALUES (?,?)",
                    (site_id,dc_id))
        con.execute("INSERT INTO dr_site_agents(site_id,agent_id) VALUES (?,?)",
                    (site_id,agent_id))
    return site_id,dc_id,credential_id


def cleanup(agent_id,refs=None):
    with db.session() as con:
        if refs:
            site,dc,credential=refs
            con.execute("DELETE FROM dr_site_agents WHERE site_id=?",(site,))
            con.execute("DELETE FROM dr_site_datacenters WHERE site_id=?",(site,))
            con.execute("DELETE FROM dr_sites WHERE id=?",(site,))
            con.execute("DELETE FROM datacenters WHERE id=?",(dc,))
            con.execute("DELETE FROM credentials WHERE id=?",(credential,))
        con.execute("DELETE FROM agent_tasks WHERE agent_id=?",(agent_id,))
        con.execute("DELETE FROM agents WHERE id=?",(agent_id,))


def test_modules_require_associated_site_and_linux_for_pra():
    agent_id,_=create_agent()
    win_id,_=create_agent('windows')
    refs=None
    refs_win=None
    try:
        for flag in ({'standard':False,'proxmox':False,'pra':False},
                     {'standard':True,'proxmox':False,'pra':'true'}):
            with pytest.raises(HTTPException):
                agent_modules.set_modules(agent_id,flag)
        with pytest.raises(HTTPException,match='Associer'):
            agent_modules.set_modules(agent_id,{
                'standard':True,'proxmox':True,'pra':False})
        refs=site_for_agent(agent_id)
        result=agent_modules.set_modules(agent_id,{
            'standard':True,'proxmox':True,'pra':True})
        assert result['revision']==2
        assert result['status']=='pending_agent_ack'
        assert agent_modules.bundle(agent_id)['site']['name'].startswith('ci-modular-site-')
        assert agent_modules.bundle(agent_id)['pra_policy']['promotion_enabled'] is False
        # Same exact roles are idempotent, no revision churn.
        result=agent_modules.set_modules(agent_id,{
            'standard':True,'proxmox':True,'pra':True})
        assert result['revision']==2
        refs_win=site_for_agent(win_id)
        with pytest.raises(HTTPException,match='Linux'):
            agent_modules.set_modules(win_id,{
                'standard':True,'proxmox':True,'pra':True})
        assert agent_modules.set_modules(win_id,{
            'standard':True,'proxmox':True,'pra':False})['revision']==2
    finally:
        cleanup(agent_id,refs)
        cleanup(win_id,refs_win)


def test_poll_delivers_policy_and_records_ack_without_replaying_tasks():
    agent_id,_=create_agent()
    refs=site_for_agent(agent_id)
    try:
        agent_modules.set_modules(agent_id,{'standard':True,'proxmox':True,'pra':True})
        with db.session() as con:
            agent=dict(con.execute('SELECT * FROM agents WHERE id=?',(agent_id,)).fetchone())
        result=agents.poll(agent,{'hostname':'ci-node-linux','version':'0.3.2',
                                  'modules_applied_revision':0,
                                  'modules_status':{'standard':'ready','pra':'not_installed'}})
        assert result['task'] is None
        assert result['modules_revision']==2
        assert result['modules']['pra'] is True
        assert len(result['site']['clusters'])==1
        assert not result['pra_policy']['promotion_enabled']
        ack=agents.poll(agent,{'hostname':'ci-node-linux','version':'0.3.2',
                               'modules_applied_revision':2,
                               'modules_status':{'standard':'ready','proxmox':'configured',
                                                 'pra':'passive'}})
        assert ack['task'] is None
        with db.session() as con:
            row=dict(con.execute('SELECT * FROM agents WHERE id=?',(agent_id,)).fetchone())
        assert row['modules_applied_revision']==2
        assert agent_modules.describe(row)['modules_status']['pra']=='passive'
        with pytest.raises(HTTPException,match='Version'):
            agents.poll(agent,{'modules_applied_revision':500,
                               'modules_status':{'pra':'ready'}})
        with pytest.raises(HTTPException,match='Statut'):
            agents.poll(agent,{'modules_applied_revision':2,
                               'modules_status':{'pra':'ACTIVE_AU'}})
    finally:
        cleanup(agent_id,refs)


def test_linux_module_policy_is_local_atomic_and_pra_worker_fail_closed(tmp_path,monkeypatch):
    std=load('modular_std_ci',Path('agents/linux/agent.py'))
    pra=load('modular_pra_ci',Path('agents/linux/pra_worker.py'))
    monkeypatch.setattr(std,'MODULES_FILE',tmp_path/'modules.json')
    monkeypatch.setattr(std,'PRA_HEALTH',tmp_path/'pra-state.json')
    monkeypatch.setattr(pra,'POLICY',tmp_path/'modules.json')
    monkeypatch.setattr(pra,'STATUS',tmp_path/'pra-state.json')
    response={
        'modules':{'standard':True,'proxmox':True,'pra':True},
        'modules_revision':2,
        'site':{'id':1,'name':'SD','role':'primary','storage_profile':'iscsi_lvm',
                'clusters':[{'id':7,'name':'SD-PROD','url':'https://pve.example.org:8006'}]},
        'pra_policy':{
            'replication_enabled':False,'promotion_enabled':False,
            'fencing_ready':False,'witness_ready':False
        }
    }
    std.apply_modules(response,123)
    assert stat.S_IMODE(std.MODULES_FILE.stat().st_mode)==0o600
    assert json.loads(std.MODULES_FILE.read_text())['site']['name']=='SD'
    assert std.module_report(123)['modules_status']['pra']=='not_installed'
    status=pra.step()
    assert status['status']=='passive'
    assert status['promotion_permitted'] is False
    assert status['replication_active'] is False
    assert std.module_report(123)['modules_status']['pra']=='passive'
    # A malformed or unexpectedly enabling central message is rejected.
    enabling=json.loads(json.dumps(response))
    enabling['pra_policy']['promotion_enabled']=True
    std.apply_modules(enabling,123)
    assert std.module_report(123)['modules_status']['pra']=='passive'
    # Even a locally altered, unsafe policy fails closed.
    modules=json.loads(std.MODULES_FILE.read_text())
    modules['pra_policy']['promotion_enabled']=True
    std.MODULES_FILE.write_text(json.dumps(modules))
    assert pra.step()['status']=='disabled'


def test_full_bundle_installer_and_uninstaller_manage_one_agent_identity():
    linux=(ROOT/'app/agents.py').read_text()
    cleanup_code=(ROOT/'app/agent_uninstall.py').read_text()
    win=(ROOT/'agents/windows/agent.ps1').read_text()
    app=(ROOT/'static/app.js').read_text()
    assert "files[marker+'_pra_worker.py']" in linux
    assert 'origamitasks-agent-pra.service' in linux
    assert 'origamitasks-agent.service' in linux
    assert 'MemoryMax=64M' in linux
    assert 'origamitasks-agent-pra.service' in cleanup_code
    assert '/opt/origamitasks-agent/modules.json' in cleanup_code
    assert 'Save-AgentModules' in win
    assert "Get-AgentModuleReport" in win
    assert "data-action=\"agent-modules\"" in app
    assert "function openAgentModulesForm(" in app
    assert "f.has('pra')" in app


def test_module_endpoint_admin_only_and_safe_default():
    with TestClient(app) as client:
        response=client.put('/api/agents/1/modules',json={
            'standard':True,'proxmox':True,'pra':False})
        assert response.status_code in (401,403)
    new_id,_=create_agent()
    try:
        with db.session() as con:
            row=dict(con.execute('SELECT * FROM agents WHERE id=?',(new_id,)).fetchone())
        desc=agent_modules.describe(row)
        assert desc['modules']=={'standard':True,'proxmox':False,'pra':False}
        assert desc['modules_in_sync'] is False
        assert desc['modules_status']=={}
    finally:
        cleanup(new_id)
