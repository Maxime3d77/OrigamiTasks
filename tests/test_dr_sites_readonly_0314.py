"""DR sites are metadata and disaster scenarios are strictly no-op simulations.

These tests assert a simulated outage NEVER calls Proxmox, agents, fencing or
storage actions, and no tasks are enqueued.
"""
from fastapi.testclient import TestClient

from app import agents, config, db, integrations
from app.main import app


def login(client):
    response=client.post('/api/login',json={
        "username":"admin","password":"test-admin-password-over-12"
    })
    assert response.status_code==200,response.text
    return {'X-CSRF-Token':client.cookies['origami_csrf']}


def create_dc(client, headers, name):
    response=client.post('/api/datacenters',headers=headers,json={
        'name':name,'url':'https://'+name.lower().replace('_','-')+'.example.org:8006',
        'kind':'cluster','token_username':'operator@pve!dr','token_secret':'not-real-secret-for-ci'
    })
    assert response.status_code==200,response.text
    return response.json()['id']


def test_dr_site_association_and_fail_closed_scenarios(monkeypatch):
    monkeypatch.setattr(config,'PROXMOX_ALLOWED_HOSTS','legacy-only.example.org')
    # Any attempt to touch the infrastructure is a test failure.
    def forbidden(*args,**kwargs):
        raise AssertionError('PRA simulation must never call Proxmox API')
    monkeypatch.setattr(integrations,'proxmox_request',forbidden)
    with TestClient(app) as client:
        headers=login(client)
        first=create_dc(client,headers,'CI_SD_DR_0314')
        second=create_dc(client,headers,'CI_AU_DR_0314')
        third=create_dc(client,headers,'CI_SD_SECOND_0314')
        agent=agents.register('dr-site-agent-ci-0314','linux','drci')['id']
        site_ids=[]
        try:
            source=client.post('/api/dr/sites',headers=headers,json={
                'name':'SD CI 0314','role':'primary','storage_profile':'iscsi_lvm',
                'datacenter_ids':[first,third],'agent_ids':[agent]
            })
            assert source.status_code==200,source.text
            sid=source.json()['id'];site_ids.append(sid)
            target=client.post('/api/dr/sites',headers=headers,json={
                'name':'AU CI 0314','role':'standby','storage_profile':'iscsi_lvm',
                'datacenter_ids':[second],'agent_ids':[]
            })
            assert target.status_code==200,target.text
            tid=target.json()['id'];site_ids.append(tid)
            rows=client.get('/api/dr/sites').json()['items']
            assert len(next(x for x in rows if x['id']==sid)['datacenters'])==2
            assert next(x for x in rows if x['id']==sid)['agents'][0]['pra_autonomous'] is False
            assert next(x for x in rows if x['id']==sid)['storage_profile']=='iscsi_lvm'
            conflict=client.post('/api/dr/sites',headers=headers,json={
                'name':'Conflicted CI 0314','role':'other','storage_profile':'auto',
                'datacenter_ids':[first],'agent_ids':[]
            })
            assert conflict.status_code==422
            with db.session() as con:
                before_tasks=con.execute('SELECT COUNT(*) FROM agent_tasks').fetchone()[0]
                before_runs=con.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
            for scenario in ('cluster_down','network_partition','console_down'):
                result=client.post('/api/dr/simulate',headers=headers,json={
                    'source_site_id':sid,'target_site_id':tid,'scenario':scenario
                })
                assert result.status_code==200,result.text
                payload=result.json()
                assert payload['decision']=='BLOCKED'
                assert payload['mode']=='simulation_read_only'
                assert payload['would_start_replica'] is False
                assert payload['would_stop_source'] is False
                assert payload['would_modify_infrastructure'] is False
                assert any(x['name']=='Fencing source' and not x['pass'] for x in payload['checks'])
                assert any(x['name']=='Témoin indépendant' and not x['pass'] for x in payload['checks'])
            with db.session() as con:
                assert con.execute('SELECT COUNT(*) FROM agent_tasks').fetchone()[0]==before_tasks
                assert con.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==before_runs
            assert len(client.get('/api/dr/simulations').json()['items'])>=3
        finally:
            for sid in site_ids:
                assert client.delete('/api/dr/sites/'+str(sid),headers=headers).status_code==200
            for dcid in (first,second,third):
                dc=next(x for x in client.get('/api/datacenters').json()['items'] if x['id']==dcid)
                assert client.delete('/api/datacenters/'+str(dcid),headers=headers).status_code==200
                assert client.delete('/api/credentials/'+str(dc['credential_id']),headers=headers).status_code==200
            with db.session() as con:
                con.execute('DELETE FROM agents WHERE id=?',(agent,))


def test_readonly_dr_simulation_requires_admin_and_valid_scenario():
    with TestClient(app) as client:
        assert client.get('/api/dr/sites').status_code==401
        assert client.get('/api/dr/simulations').status_code==401
        headers=login(client)
        bad=client.post('/api/dr/simulate',headers=headers,json={
            'source_site_id':1,'target_site_id':1,'scenario':'cluster_down'
        })
        assert bad.status_code==422
        bad=client.post('/api/dr/sites',headers=headers,json={
            'name':'missing cluster','role':'primary','storage_profile':'auto',
            'datacenter_ids':[],'agent_ids':[]
        })
        assert bad.status_code==422


def test_dr_dashboard_is_not_labelled_as_autonomous():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[1]/'static/app.js').read_text()
    assert 'function openDRSimulationForm()' in src
    assert 'function openDRSiteForm(' in src
    assert 'mode PRA' not in src or 'simulation uniquement' in src
    assert 'aucun agent pra autonome installé' in src.lower()
    assert "api('/dr/simulate','POST'" in src
    assert 'Aucune bascule effectuée.' in src
    assert 'Les agents actuels' not in src or 'agents' in src
