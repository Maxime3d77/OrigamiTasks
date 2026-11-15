"""No live infrastructure is contacted in these integration tests."""
import json
from unittest.mock import Mock
import pytest
import httpx
from fastapi.testclient import TestClient
from app import integrations, config
from app.main import app


def test_hosts_must_be_allowlisted_and_https(monkeypatch):
    monkeypatch.setattr(config,'MATRIX_ALLOWED_HOSTS','matrix.example.org')
    assert integrations.authorized_origin('https://matrix.example.org','matrix.example.org')=='https://matrix.example.org'
    for address in ['http://matrix.example.org','https://evil.example.org','https://matrix.example.org@evil.example.org',
                    'https://matrix.example.org/evil','https://matrix.example.org?target=evil']:
        with pytest.raises(integrations.IntegrationError):
            integrations.authorized_origin(address,config.MATRIX_ALLOWED_HOSTS)


def test_matrix_uses_v3_api_and_vault_token(monkeypatch):
    monkeypatch.setattr(config,'MATRIX_ALLOWED_HOSTS','matrix.example.org')
    requests=[]
    class Fake:
        def __init__(self,**kw):
            assert kw['follow_redirects'] is False
            assert kw['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def put(self,url,headers,json):
            requests.append((url,headers,json))
            response=Mock()
            response.json.return_value={'event_id':'$ok:matrix.example.org'}
            return response
    monkeypatch.setattr(integrations.httpx,'Client',Fake)
    options={'homeserver':'https://matrix.example.org','room_id':'!ops:matrix.example.org','message':'Sauvegarde OK'}
    result=integrations.matrix_message(options,{'token':'top-secret-matrix'},21,'notify',15)
    assert '$ok:matrix.example.org' in result
    assert 'top-secret-matrix' not in result
    assert requests[0][0].endswith('/rooms/%21ops%3Amatrix.example.org/send/m.room.message/origami-21-notify')
    assert requests[0][1]['Authorization']=='Bearer top-secret-matrix'
    assert requests[0][2]['body']=='Sauvegarde OK'


def test_ansible_runner_command_is_quoted_and_limited():
    command=integrations.ansible_command({'playbook':'linux/update.yml','inventory':'prod/hosts.yml',
                 'limit':"web'; echo NOT_EXECUTED; #", 'check':True,'extra_vars':'{"patch":true}'})
    assert command.startswith('ansible-playbook -i ')
    assert '--check' in command
    assert "'web'\"'\"'; echo NOT_EXECUTED; #'" in command
    assert '/opt/origamitasks/ansible/playbooks/linux/update.yml' in command
    for pb in ['/etc/passwd.yml','../secrets.yml','demo;id.yml']:
        with pytest.raises(integrations.IntegrationError):
            integrations.ansible_command({'playbook':pb,'inventory':'prod.yml'})


def test_proxmox_auth_and_discovery(monkeypatch):
    monkeypatch.setattr(config,'PROXMOX_ALLOWED_HOSTS','pve.example.org:8006')
    called=[]
    class Fake:
        def __init__(self,**kw):
            assert kw['verify'] is True
            assert kw['follow_redirects'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,method,url,headers,data):
            called.append((method,url,headers))
            response=Mock()
            response.json.return_value={'data':[{'id':'qemu/106','type':'qemu','vmid':106,'name':'test','status':'running','private':'not-returned'}]}
            return response
    monkeypatch.setattr(integrations.httpx,'Client',Fake)
    dc={'name':'DC1','url':'https://pve.example.org:8006'}
    cred={'username':'bot@pve!origami','token':'sensitive-key'}
    data=integrations.discover_datacenter(dc,cred)
    assert data[0]['vmid']==106 and 'private' not in data[0]
    assert called[0][2]['Authorization']=='PVEAPIToken=bot@pve!origami=sensitive-key'


def test_datacenter_crud_and_task_catalog(monkeypatch):
    monkeypatch.setattr(config,'PROXMOX_ALLOWED_HOSTS','pve.example.org:8006')
    with TestClient(app) as c:
        assert c.get('/api/catalog').status_code==401
        assert c.post('/api/login',json={'username':'admin','password':'test-admin-password-over-12'}).status_code==200
        headers={'X-CSRF-Token':c.cookies['origami_csrf']}
        available=c.get('/api/catalog').json()['available']
        assert {'ansible','matrix','proxmox'}.issubset({item['type'] for item in available})
        secret=c.post('/api/credentials',headers=headers,json={'name':'pve-for-test','kind':'api','payload':{'username':'bot@pve!origami','token':'secret-pve-for-tests'}})
        assert secret.status_code==200
        cred_id=secret.json()['id']
        bad=c.post('/api/datacenters',headers=headers,json={'name':'invalid','url':'https://unapproved.local:8006','credential_id':cred_id})
        assert bad.status_code==422
        added=c.post('/api/datacenters',headers=headers,json={'name':'DC-AU','url':'https://pve.example.org:8006','credential_id':cred_id})
        assert added.status_code==200,added.text
        dcid=added.json()['id']
        listed=c.get('/api/datacenters')
        assert any(d['id']==dcid for d in listed.json()['items'])
        assert 'secret-pve-for-tests' not in listed.text
        monkeypatch.setattr(integrations,'discover_datacenter',lambda dc,cred,timeout=20:[{'type':'qemu','vmid':106,'node':'pve1'}])
        discovered=c.get(f'/api/datacenters/{dcid}/discover')
        assert discovered.status_code==200,discovered.text
        assert discovered.json()['resources'][0]['vmid']==106
        assert c.delete(f'/api/datacenters/{dcid}',headers=headers).status_code==200
        assert c.delete('/api/credentials/'+str(cred_id),headers=headers).status_code==200


def test_proxmox_power_fail_closed(monkeypatch):
    monkeypatch.setattr(config,'ALLOW_PROXMOX_ACTIONS',False)
    monkeypatch.setattr(integrations,'configured_datacenter',lambda _: {'name':'AU','url':'https://pve.example.org:8006','credential_id':1})
    with pytest.raises(integrations.IntegrationError,match='désactivées'):
        integrations.proxmox_task({'datacenter_id':1,'action':'start','node':'n1','vmid':106,'approved':True},lambda _: {'username':'test'},10)
    with pytest.raises(integrations.IntegrationError,match='non autorisée'):
        integrations.proxmox_task({'datacenter_id':1,'action':'replicate'},lambda _: {},10)
