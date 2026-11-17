"""Cluster discovery → SSH group → secure, approved multi-node agent deployment.

No real SSH or PVE access in tests. The worker must re-check pinned host
identities before privileged installation, even with a linked cluster group.
"""
from fastapi.testclient import TestClient
import pytest
from app import (agents, agent_deployments, config, db, host_keys,
                 proxmox_fleet, ssh_inventory)
from app.main import app


def login(client):
    response=client.post('/api/login',json={'username':'admin',
                                            'password':'test-admin-password-over-12'})
    assert response.status_code==200,response.text
    return {'X-CSRF-Token':client.cookies['origami_csrf']}


def setup_dc(client,headers):
    result=client.post('/api/datacenters',headers=headers,json={
        'name':'SD-PROD-FLEET-0317','kind':'cluster',
        'url':'https://sd-cardano.infra.aday.fr:8006',
        'token_username':'svc@pve!fleet','token_secret':'not-real-token-for-test-0317'
    })
    assert result.status_code==200,result.text
    return result.json()['id']


def setup_ssh_cred():
    from app import security
    with db.session() as con:
        return con.execute(
            "INSERT INTO credentials(name,kind,encrypted_payload,created_at,updated_at) "
            "VALUES(?,?,?,?,?)",
            ('ci-ssh-fleet-0317','ssh',security.encrypt({'username':'root'}),
             db.now(),db.now())
        ).lastrowid


def cleanup(client,headers,dc_id,cred_id,group_id,agent_ids=()):
    with db.session() as con:
        con.execute('DELETE FROM agent_deploy_targets WHERE batch_id IN '
                    '(SELECT id FROM agent_deploy_batches WHERE proxmox_datacenter_id=?)',
                    (dc_id,))
        con.execute('DELETE FROM agent_deploy_batches WHERE proxmox_datacenter_id=?',(dc_id,))
        for agent_id in agent_ids:
            con.execute('DELETE FROM dr_site_agents WHERE agent_id=?',(agent_id,))
            con.execute('DELETE FROM agent_tasks WHERE agent_id=?',(agent_id,))
            con.execute('DELETE FROM agents WHERE id=?',(agent_id,))
        con.execute('DELETE FROM dr_sites WHERE name=?',('SD fleet ci 0317',))
        if group_id:
            con.execute('DELETE FROM ssh_server_group_members WHERE group_id=?',(group_id,))
            con.execute('DELETE FROM proxmox_ssh_groups WHERE datacenter_id=?',(dc_id,))
            con.execute('DELETE FROM proxmox_ssh_nodes WHERE datacenter_id=?',(dc_id,))
            con.execute('DELETE FROM ssh_server_groups WHERE id=?',(group_id,))
            for fqdn in ('sd-cardano.infra.aday.fr','sd-rubis.infra.aday.fr'):
                con.execute('DELETE FROM ssh_servers WHERE fqdn=?',(fqdn,))
    rows=client.get('/api/datacenters').json()['items']
    dc=next(x for x in rows if x['id']==dc_id)
    assert client.delete('/api/datacenters/'+str(dc_id),headers=headers).status_code==200
    assert client.delete('/api/credentials/'+str(dc['credential_id']),headers=headers).status_code==200
    with db.session() as con:
        con.execute('DELETE FROM credentials WHERE id=?',(cred_id,))


def test_cluster_group_preview_and_agent_batch_fail_closed(monkeypatch):
    monkeypatch.setattr(config,'ALLOW_AGENT_REMOTE_DEPLOY',False)
    monkeypatch.setattr(config,'AGENT_DEPLOY_ALLOWED_HOSTS','')
    monkeypatch.setattr(agent_deployments,'start',lambda:None)
    monkeypatch.setattr(proxmox_fleet,'_discover',
                        lambda dc:['sd-cardano','sd-rubis'])
    deployed=[]
    def fake_deploy(body, *, cluster_authorized=False):
        assert cluster_authorized is True
        assert body['host']=='sd-rubis.infra.aday.fr'
        agent=agents.register('test-'+body['host'],'linux',body['group'])
        deployed.append(agent['id'])
        return {'id':agent['id'],'name':body['name']}
    monkeypatch.setattr(agents,'deploy',fake_deploy)
    with TestClient(app) as client:
        headers=login(client)
        dc_id=setup_dc(client,headers)
        cred=setup_ssh_cred()
        group_id=None
        try:
            preview=client.get('/api/datacenters/'+str(dc_id)+'/agent-nodes')
            assert preview.status_code==200,preview.text
            data=preview.json()
            assert data['domain_suffix']=='infra.aday.fr'
            assert len(data['nodes'])==2
            assert all(x['fqdn'].endswith('.infra.aday.fr') for x in data['nodes'])
            bad=client.post('/api/datacenters/'+str(dc_id)+'/ssh-group',headers=headers,
                            json={'nodes':['untrusted-pve-node'],
                                  'domain_suffix':'infra.aday.fr'})
            assert bad.status_code==422
            group=client.post('/api/datacenters/'+str(dc_id)+'/ssh-group',headers=headers,
                              json={'nodes':['sd-cardano','sd-rubis'],
                                    'domain_suffix':'infra.aday.fr'})
            assert group.status_code==200,group.text
            group_id=group.json()['group_id']
            assert group.json()['count']==2
            found=client.get('/api/ssh/inventory').json()
            ssh_group=next(x for x in found['groups'] if x['id']==group_id)
            assert ssh_group['server_count']==2
            invalid_edit=client.put('/api/ssh/groups/'+str(group_id),headers=headers,
                                    json={'name':'Tampered SSH group'})
            assert invalid_edit.status_code==422
            source=client.post('/api/dr/sites',headers=headers,json={
                'name':'SD fleet ci 0317','role':'primary',
                'storage_profile':'iscsi_lvm',
                'datacenter_ids':[dc_id],'agent_ids':[]})
            assert source.status_code==200,source.text
            sid=source.json()['id']
            ids={x['fqdn']:x['id'] for x in found['servers']}
            c=ids['sd-cardano.infra.aday.fr']
            d=ids['sd-rubis.infra.aday.fr']
            # An untrusted SSH host is blocked at worker time.
            monkeypatch.setattr(host_keys,'inspect',
                                lambda host,port:{'status':'unknown'})
            queued=client.post('/api/datacenters/'+str(dc_id)+'/agents/deploy',headers=headers,
                               json={'group_id':group_id,'server_ids':[c],
                                     'credential_id':cred,'approved':True})
            assert queued.status_code==200,queued.text
            assert agent_deployments.process_one()
            with db.session() as con:
                entry=con.execute(
                    'SELECT status FROM agent_deploy_targets WHERE batch_id=?',
                    (queued.json()['batch_id'],)
                ).fetchone()
                assert entry['status']=='failed'
            assert deployed==[]
            # A pinned and verified host key allows just the selected node.
            monkeypatch.setattr(host_keys,'inspect',
                                lambda host,port:{'status':'trusted'})
            queued=client.post('/api/datacenters/'+str(dc_id)+'/agents/deploy',headers=headers,
                               json={'group_id':group_id,'server_ids':[d],
                                     'credential_id':cred,'approved':True})
            assert queued.status_code==200,queued.text
            assert agent_deployments.process_one()
            assert len(deployed)==1
            with db.session() as con:
                assert con.execute(
                    'SELECT COUNT(*) FROM dr_site_agents WHERE site_id=? AND agent_id=?',
                    (sid,deployed[0])
                ).fetchone()[0]==1
                assert con.execute(
                    'SELECT status FROM agent_deploy_targets WHERE batch_id=?',
                    (queued.json()['batch_id'],)
                ).fetchone()['status']=='success'
            assert client.post('/api/datacenters/'+str(dc_id)+'/agents/deploy',headers=headers,
                json={'group_id':group_id,'server_ids':[d],'credential_id':cred,
                      'approved':True}).status_code==422  # duplicate agent
        finally:
            cleanup(client,headers,dc_id,cred,group_id,deployed)


def test_invalid_cluster_mapping_does_not_authorize_legacy_host(monkeypatch):
    monkeypatch.setattr(config,'ALLOW_AGENT_REMOTE_DEPLOY',False)
    with pytest.raises(agent_deployments.DeploymentError,match='désactivé'):
        agent_deployments.queue({
            'platform':'linux','credential_id':1000,'agent_group':'default',
            'targets':[{'host':'unrelated.example.org'}],'approved':True
        },'ci')
    from app import integrations
    assert 'agent-nodes' in [r.path for r in app.routes
                             if 'agent-nodes' in getattr(r,'path','')]
    assert 'ssh-group' in [r.path for r in app.routes
                           if 'ssh-group' in getattr(r,'path','')]


def test_cluster_node_fqdn_does_not_escape_approved_suffix():
    from app import proxmox_fleet
    assert proxmox_fleet._node('sd-cardano','infra.aday.fr')=='sd-cardano.infra.aday.fr'
    with pytest.raises(proxmox_fleet.FleetError,match='hors du domaine'):
        proxmox_fleet._node('evil.other.example.org','infra.aday.fr')
    with pytest.raises(proxmox_fleet.FleetError):
        proxmox_fleet._node('pve;reboot','infra.aday.fr')


def test_cluster_ui_buttons_from_site_and_cluster():
    from pathlib import Path
    js=(Path(__file__).resolve().parents[1]/'static/app.js').read_text()
    assert 'Agents du cluster' in js
    assert 'Déployer sur les nœuds' in js
    assert 'function openClusterAgentWizard(' in js
    assert 'function openPRAClusterSelector(' in js
    assert 'fleet-ssh-key' in js
    assert 'proxmoxFleetGroupForm' in js
    assert 'proxmoxFleetDeployForm' in js
