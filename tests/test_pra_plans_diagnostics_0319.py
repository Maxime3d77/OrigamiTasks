"""PRA environment isolation and safe agent fleet diagnostics."""
import pytest
from fastapi.testclient import TestClient

from app import agent_deployments, db
from app.main import app


def login(client):
    r=client.post('/api/login',json={'username':'admin','password':'test-admin-password-over-12'})
    assert r.status_code==200,r.text
    return {'X-CSRF-Token':client.cookies['origami_csrf']}


def create_dc(client,h,name):
    r=client.post('/api/datacenters',headers=h,json={
        'name':name,'kind':'cluster','url':'https://'+name.lower().replace('_','-')+'.example.org:8006',
        'token_username':'tester@pve!sandbox','token_secret':'not-a-real-secret-for-test'
    })
    assert r.status_code==200,r.text
    return r.json()['id']


def create_site(client,h,name,dc,role,plan_id):
    r=client.post('/api/dr/sites',headers=h,json={
        'name':name,'plan_id':plan_id,'role':role,'storage_profile':'auto',
        'datacenter_ids':[dc],'agent_ids':[]
    })
    assert r.status_code==200,r.text
    return r.json()['id']


def test_pp_prod_isolation_crud_and_safe_simulation():
    with TestClient(app) as client:
        h=login(client)
        pp=client.post('/api/dr/plans',headers=h,json={
            'name':'Preproduction 0319','description':'Tests isolated from production'
        })
        assert pp.status_code==200,pp.text
        pp=pp.json()['id']
        prod=client.post('/api/dr/plans',headers=h,json={
            'name':'Production 0319','description':'Critical workloads'
        })
        assert prod.status_code==200,prod.text
        prod=prod.json()['id']
        dcs=[];sites=[]
        try:
            for name,role,group in (
                ('ci_pp_source_0319','primary',pp),
                ('ci_pp_backup_0319','standby',pp),
                ('ci_prod_source_0319','primary',prod),
                ('ci_prod_backup_0319','standby',prod),
            ):
                dc=create_dc(client,h,name);dcs.append(dc)
                sites.append(create_site(client,h,'site-'+name,dc,role,group))
            plans=client.get('/api/dr/plans').json()['items']
            assert next(p for p in plans if p['id']==pp)['sites_count']==2
            assert next(p for p in plans if p['id']==prod)['sites_count']==2
            records=client.get('/api/dr/sites').json()['items']
            assert {x['plan_id'] for x in records if x['id'] in sites}=={pp,prod}
            # Cross-group simulation must never touch the infrastructure.
            blocked=client.post('/api/dr/simulate',headers=h,json={
                'source_site_id':sites[0],'target_site_id':sites[3],
                'scenario':'cluster_down'
            })
            assert blocked.status_code==422,blocked.text
            assert 'groupes PRA différents' in blocked.text
            valid=client.post('/api/dr/simulate',headers=h,json={
                'source_site_id':sites[0],'target_site_id':sites[1],
                'scenario':'cluster_down'
            })
            assert valid.status_code==200,valid.text
            assert valid.json()['decision']=='BLOCKED'
            assert valid.json()['would_modify_infrastructure'] is False
            attempt=client.delete('/api/dr/plans/'+str(pp),headers=h)
            assert attempt.status_code==422
            # Can't silently attach one DC to sites from PP and PROD.
            conflict=client.post('/api/dr/sites',headers=h,json={
                'name':'duplicate-cluster-0319','plan_id':prod,'role':'standby',
                'storage_profile':'auto','datacenter_ids':[dcs[0]],'agent_ids':[]
            })
            assert conflict.status_code==422
        finally:
            for site in sites:
                assert client.delete('/api/dr/sites/'+str(site),headers=h).status_code==200
            for dc in dcs:
                row=next(x for x in client.get('/api/datacenters').json()['items'] if x['id']==dc)
                assert client.delete('/api/datacenters/'+str(dc),headers=h).status_code==200
                assert client.delete('/api/credentials/'+str(row['credential_id']),headers=h).status_code==200
            for plan in (pp,prod):
                assert client.delete('/api/dr/plans/'+str(plan),headers=h).status_code==200


def test_plan_admin_auth_and_bad_input():
    with TestClient(app) as client:
        assert client.get('/api/dr/plans').status_code==401
        assert client.post('/api/dr/plans',json={'name':'Production'}).status_code in (401,403)
        h=login(client)
        for value in ('','X','\n','A'*81):
            r=client.post('/api/dr/plans',headers=h,json={'name':value})
            assert r.status_code==422


def test_agent_error_categories_do_not_reveal_secrets():
    from fastapi import HTTPException
    from app.agent_deployments import DeploymentError,public_failure
    assert 'empreinte SSH' in public_failure(DeploymentError('Clé hôte SSH inconnue : approbation nécessaire')) or 'Clé hôte' in public_failure(DeploymentError('Clé hôte SSH inconnue : approbation nécessaire'))
    assert 'Connexion SSH impossible' in public_failure(ConnectionError('token=sensitive'))
    assert "Commande d'installation" in public_failure(RuntimeError('sudo password secret=private-key'))
    assert 'Autorisation refusée' in public_failure(HTTPException(403,'userpassword'))
    for exc in (ConnectionError('SECRET_0319'),RuntimeError('SECRET_0319'),
                HTTPException(403,'SECRET_0319')):
        assert 'SECRET_0319' not in public_failure(exc)


def test_pra_groups_visual_and_generic_defaults():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    js=(root/'static/app.js').read_text(encoding='utf-8')
    assert 'function openDRPlanForm(' in js
    assert 'Groupe PRA (isolation PP / Production)' in js
    assert 'dr_sim_plan' in js
    assert "state.drPlans=(await api('/dr/plans')).items" in js
    assert "groupes PRA différents" in js
    assert 'sd-cardano' not in js
    assert 'origamitasks.infra.aday.fr' not in js
    assert 'const url=settings.controller_url||' in js
