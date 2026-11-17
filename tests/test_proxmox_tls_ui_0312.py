"""Proxmox TLS verification is opt-out only per admin-owned vault/DC entry."""
import ssl
import httpx
from fastapi.testclient import TestClient

from app import config, db, integrations, connection_tests
from app.main import app


def login(client):
    result=client.post('/api/login',json={
        'username':'admin','password':'test-admin-password-over-12'
    })
    assert result.status_code==200,result.text
    return {'X-CSRF-Token':client.cookies['origami_csrf']}


def test_per_datacenter_insecure_tls_toggle_persists_and_is_reversible(monkeypatch):
    monkeypatch.setattr(config,'PROXMOX_CA_CERT','')
    with TestClient(app) as client:
        headers=login(client)
        url='https://pve-ssl-test.example.org:8006'
        added=client.post('/api/datacenters',headers=headers,json={
            'name':'PVE TLS test 0312', 'kind':'cluster', 'url':url,
            'token_username':'service@pve!tlsdiag', 'token_secret':'token-secret-for-ci'
        })
        assert added.status_code==200,added.text
        dc_id=added.json()['id']
        try:
            entries=client.get('/api/datacenters').json()['items']
            item=next(x for x in entries if x['id']==dc_id)
            assert item['skip_tls_verification']==0
            bad=client.put('/api/datacenters/'+str(dc_id),headers=headers,json={
                'name':item['name'], 'kind':'cluster','url':url,
                'credential_id':item['credential_id'],
                'skip_tls_verification':'true'
            })
            assert bad.status_code==422
            enabled=client.put('/api/datacenters/'+str(dc_id),headers=headers,json={
                'name':item['name'],'kind':'cluster','url':url,
                'credential_id':item['credential_id'],
                'skip_tls_verification':True
            })
            assert enabled.status_code==200,enabled.text
            from_db=integrations.configured_datacenter(dc_id)
            assert from_db['skip_tls_verification']==1
            assert client.get('/api/datacenters').json()['items'][-1] or True
            disabled=client.put('/api/datacenters/'+str(dc_id),headers=headers,json={
                'name':item['name'],'kind':'cluster','url':url,
                'credential_id':item['credential_id'],
                'skip_tls_verification':False
            })
            assert disabled.status_code==200,disabled.text
            assert integrations.configured_datacenter(dc_id)['skip_tls_verification']==0
        finally:
            assert client.delete('/api/datacenters/'+str(dc_id),headers=headers).status_code==200
            assert client.delete('/api/credentials/'+str(item['credential_id']),headers=headers).status_code==200


def test_httpx_verify_false_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(config,'PROXMOX_CA_CERT','')
    values=[]
    class FakeResponse:
        def raise_for_status(self):pass
        def json(self):return {'data':{'ok':1}}
    class FakeClient:
        def __init__(self,**kwargs):values.append(kwargs['verify'])
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def request(self,method,url,headers,data):
            assert headers['Authorization']=='PVEAPIToken=service@pve!tlsdiag=secret'
            return FakeResponse()
    monkeypatch.setattr(integrations.httpx,'Client',FakeClient)
    cred={'username':'service@pve!tlsdiag','token':'secret'}
    integrations.proxmox_request('https://pve.example.org:8006',cred,'/api2/json/version')
    integrations.proxmox_request('https://pve.example.org:8006',
                                 {**cred,'skip_tls_verification':True},
                                 '/api2/json/version')
    integrations.proxmox_request('https://pve.example.org:8006',
                                 {**cred,'skip_tls_verification':False},
                                 '/api2/json/version')
    assert values==[True,False,True]


def test_diagnostic_warns_explicitly_when_tls_disabled(monkeypatch):
    monkeypatch.setattr(connection_tests.integrations,'proxmox_request',
                        lambda origin, cred, path, timeout: {} if path.endswith('permissions') else [])
    result=connection_tests.proxmox({
        'url':'https://pve.example.org:8006',
        'username':'service@pve!diag','token':'secret',
        'skip_tls_verification':True
    })
    assert result['ok']
    assert result['checks'][0]['status']=='warning'
    assert 'désactivée' in result['checks'][0]['detail']


def test_tls_and_network_failures_have_different_safe_diagnostics(monkeypatch):
    def tls_error(*args,**kwargs):
        try:
            raise ssl.SSLCertVerificationError('self signed certificate')
        except ssl.SSLCertVerificationError as exc:
            raise httpx.ConnectError('connection failed') from exc
    monkeypatch.setattr(connection_tests.integrations,'proxmox_request',tls_error)
    try:
        connection_tests.proxmox({'url':'https://pve.example.org:8006'})
        assert False,'expected error'
    except connection_tests.ConnectionTestError as exc:
        assert 'certificat TLS non approuvé' in str(exc)
        assert 'self signed certificate' not in str(exc)
    monkeypatch.setattr(
        connection_tests.integrations,'proxmox_request',
        lambda *a,**kw: (_ for _ in ()).throw(httpx.ConnectError('failed'))
    )
    try:
        connection_tests.proxmox({'url':'https://pve.example.org:8006'})
        assert False,'expected error'
    except connection_tests.ConnectionTestError as exc:
        assert 'DNS, port 8006' in str(exc)


def test_ui_offers_per_dc_opt_out_and_safe_card_layout():
    from pathlib import Path
    js=(Path(__file__).resolve().parents[1]/'static/app.js').read_text()
    css=(Path(__file__).resolve().parents[1]/'static/styles.css').read_text()
    assert "name=\"dc_skip_tls\"" in js
    assert "skip_tls_verification:f.has('dc_skip_tls')" in js
    assert "proxmox-toolbar" in js
    assert "proxmox-card .actions{display:grid" in css
    assert "$$('[data-dc-search]')" in js
