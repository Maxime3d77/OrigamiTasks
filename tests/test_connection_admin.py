"""Typed connection and LDAP administration regression tests (no real network)."""
from fastapi.testclient import TestClient
from app.main import app
from app import connections, ldap_settings, db, security, executor
from unittest.mock import Mock


def admin():
    client=TestClient(app)
    client.__enter__()
    result=client.post('/api/login',json={'username':'admin','password':'test-admin-password-over-12'})
    assert result.status_code==200, result.text
    return client, {'X-CSRF-Token':client.cookies['origami_csrf']}


def test_typed_smtp_fields_and_encrypted_api(monkeypatch):
    client,headers=admin()
    try:
        profile={'host':'smtp.example.org','port':465,'tls_mode':'ssl',
                 'auth':True,'username':'tester@example.org','password':'sensitive-smtp-password',
                 'from_address':'noreply@example.org'}
        created=client.post('/api/credentials',headers=headers,json={
            'name':'CI SMTP profilé','kind':'smtp','payload':profile})
        assert created.status_code==200, created.text
        cid=created.json()['id']
        listing=client.get('/api/credentials').json()['items']
        row=next(r for r in listing if r['id']==cid)
        assert row['kind']=='smtp'
        assert row['public']['host']=='smtp.example.org'
        assert row['public']['port']==465
        assert row['public']['auth'] is True
        assert 'password' not in row['public']
        assert 'sensitive-smtp-password' not in client.get('/api/credentials').text
        with db.session() as con:
            cipher=con.execute('SELECT encrypted_payload FROM credentials WHERE id=?',(cid,)).fetchone()['encrypted_payload']
        assert 'sensitive-smtp-password' not in cipher
        assert security.decrypt(cipher)['password']=='sensitive-smtp-password'

        updated=client.put(f'/api/credentials/{cid}',headers=headers,json={
            'name':'CI SMTP profilé','kind':'smtp','payload':{'port':587,'tls_mode':'starttls','from_address':'new@example.org'}})
        assert updated.status_code==200, updated.text
        with db.session() as con:
            stored=security.decrypt(con.execute('SELECT encrypted_payload FROM credentials WHERE id=?',(cid,)).fetchone()['encrypted_payload'])
        assert stored['password']=='sensitive-smtp-password'
        assert stored['port']==587 and stored['tls_mode']=='starttls'
        monkeypatch.setattr(connections,'test_smtp',lambda values:{'ok':True,'message':'SMTP OK','authenticated':True})
        tested=client.post(f'/api/credentials/{cid}/test',headers=headers)
        assert tested.status_code==200 and tested.json()['ok']
        assert client.delete(f'/api/credentials/{cid}',headers=headers).status_code==200
    finally:
        client.__exit__(None,None,None)


def test_unauthed_smtp_relay_and_plaintext_auth_rejected(monkeypatch):
    c,h=admin()
    try:
        noauth=c.post('/api/credentials',headers=h,json={'name':'CI relais sans auth',
            'kind':'smtp','payload':{'host':'relay.example.org','port':25,
             'tls_mode':'none','auth':False,'from_address':'bot@example.org'}})
        assert noauth.status_code==200,noauth.text
        invalid=c.post('/api/credentials',headers=h,json={'name':'CI SMTP invalide','kind':'smtp',
            'payload':{'host':'relay.example.org','tls_mode':'none','auth':True,
                       'username':'bot','password':'secret'}})
        assert invalid.status_code==422
        assert 'sans TLS' in invalid.json()['detail']
        assert c.delete('/api/credentials/'+str(noauth.json()['id']),headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_smtp_connection_handshake_respects_noauth_and_starttls(monkeypatch):
    calls=[]
    class Fake:
        def __init__(self,host,port,timeout,**kwargs):
            calls.append(('open',host,port,timeout,'context' in kwargs))
        def ehlo(self):calls.append(('ehlo',))
        def starttls(self,context):calls.append(('starttls',bool(context)))
        def login(self,username,password):calls.append(('login',username))
        def quit(self):calls.append(('quit',))
        def close(self):calls.append(('close',))
    monkeypatch.setattr(connections.smtplib,'SMTP',Fake)
    monkeypatch.setattr(connections.smtplib,'SMTP_SSL',Fake)
    plain=connections.open_smtp({'host':'relay.example.org','port':25,'tls_mode':'none','auth':False})
    plain.quit()
    assert not any(c[0]=='login' or c[0]=='starttls' for c in calls)
    calls.clear()
    tls=connections.open_smtp({'host':'smtp.example.org','port':587,'tls_mode':'starttls',
        'auth':True,'username':'smtp-user','password':'secret'})
    tls.quit()
    assert any(c[0]=='starttls' for c in calls)
    assert any(c[0]=='login' for c in calls)
    calls.clear()
    ssl_conn=connections.open_smtp({'host':'smtp.example.org','port':465,'tls_mode':'ssl','auth':False})
    ssl_conn.quit()
    assert any(c[0]=='open' and c[-1] for c in calls)


def test_task_uses_vault_smtp_host_tls_and_from(monkeypatch):
    profile={'host':'smtp.example.org','port':465,'tls_mode':'ssl','auth':False,
             'from_address':'noreply@example.org'}
    sent=[]
    class Fake:
        def send_message(self,message):sent.append(message)
        def quit(self):pass
        def close(self):pass
    calls=[]
    monkeypatch.setattr(executor,'credential',lambda _id:profile)
    monkeypatch.setattr(executor.connections,'open_smtp',lambda values,timeout: (calls.append(values),Fake())[1])
    result=executor.execute({'id':'mail','name':'Send','type':'email',
        'config':{'credential_id':99,'to':'recipient@example.org','subject':'Test','body':'Bonjour'}},
        {'run_id':1,'variables':{},'steps':{}})
    assert result['status']=='success'
    assert calls[0]['host']=='smtp.example.org' and calls[0]['port']==465 and calls[0]['tls_mode']=='ssl'
    assert sent[0]['From']=='noreply@example.org'
    assert sent[0]['To']=='recipient@example.org'


def test_ldap_admin_config_is_encrypted_and_test_is_readonly(monkeypatch):
    client,headers=admin()
    try:
        cfg={'enabled':True,'uri':'ldaps://ldap.example.org:636','starttls':False,
             'bind_dn':'cn=reader,ou=Applications,dc=example,dc=org',
             'bind_password':'new-secret-bind-password','base_dn':'dc=example,dc=org',
             'group_base_dn':'ou=Groups,dc=example,dc=org','user_attribute':'uid',
             'admin_group':'ops-admin','operator_group':'ops','viewer_group':'ops-read',
             'nested_groups':False,'ca_cert':''}
        assert client.get('/api/admin/ldap').status_code==200
        assert client.put('/api/admin/ldap',json=cfg).status_code==403
        updated=client.put('/api/admin/ldap',json=cfg,headers=headers)
        assert updated.status_code==200,updated.text
        assert 'new-secret-bind-password' not in updated.text
        assert updated.json()['has_bind_password'] is True
        fetched=client.get('/api/admin/ldap')
        assert fetched.json()['source']=='administration'
        assert fetched.json()['user_attribute']=='uid'
        assert 'new-secret-bind-password' not in fetched.text
        with db.session() as con:
            encrypted=con.execute('SELECT encrypted_config FROM ldap_settings WHERE id=1').fetchone()['encrypted_config']
        assert 'new-secret-bind-password' not in encrypted
        assert security.decrypt(encrypted)['bind_password']=='new-secret-bind-password'
        monkeypatch.setattr(ldap_settings,'test',lambda values,user:{
            'ok':True,'message':'LDAP OK','group_sample_count':2,
            'user_tested':bool(user),'user_found':True})
        result=client.post('/api/admin/ldap/test',json={'settings':{},'test_user':'alice'},headers=headers)
        assert result.status_code==200,result.text
        assert result.json()['group_sample_count']==2
        # An empty secret edit never wipes the bound password.
        updated=client.put('/api/admin/ldap',json={'bind_password':'','admin_group':'ops-admin'},headers=headers)
        assert updated.status_code==200,updated.text
        assert ldap_settings.current()['bind_password']=='new-secret-bind-password'
    finally:
        client.__exit__(None,None,None)


def test_ldap_admin_rejects_plaintext_and_bad_attributes():
    c,h=admin()
    try:
        assert c.put('/api/admin/ldap',headers=h,json={
            'enabled':True,'uri':'ldap://ldap.example.org:389','starttls':False}).status_code==422
        assert c.put('/api/admin/ldap',headers=h,json={'user_attribute':'uid)(|(uid=*)'}).status_code==422
    finally:
        c.__exit__(None,None,None)
