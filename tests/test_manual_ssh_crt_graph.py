"""Regression tests for manual SSH target, LDAP .crt DER upload, and graph zoom UI.

External SSH/LDAP connections are simulated. No privileged network access.
"""
import base64
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app import connection_tests, db, security
from app.main import app


def session_admin():
    client=TestClient(app)
    client.__enter__()
    login=client.post('/api/login', json={
        'username':'admin','password':'test-admin-password-over-12'
    })
    assert login.status_code==200, login.text
    return client, {'X-CSRF-Token':client.cookies['origami_csrf']}


def test_vault_ssh_identity_can_be_tested_against_manual_fqdn(monkeypatch):
    client,headers=session_admin()
    try:
        created=client.post('/api/credentials', headers=headers, json={
            'name':'CI Portable SSH User','kind':'ssh',
            'payload':{'username':'deploy','password':'private-test-secret'}
        })
        assert created.status_code==200,created.text
        cid=created.json()['id']
        called=[]
        def simulate(kind,values,**kwargs):
            assert kind=='ssh'
            called.append((dict(values),kwargs['options']))
            return {'ok':True,'protocol':'ssh','message':'Simulation SSH',
                    'checks':[{'name':'Authentification','status':'success',
                               'detail':'Compte accepté'}]}
        monkeypatch.setattr(connection_tests,'run',simulate)
        path=f'/api/credentials/{cid}/test'
        assert client.post(path,headers=headers,json={'options':{}}).status_code==422
        assert client.post(path,json={'options':{'manual_host':'ci-test.infra.example.org',
                                                 'manual_port':2222}}).status_code==403
        valid=client.post(path,headers=headers,json={
            'options':{'manual_host':'CI-Test.infra.example.org',
                       'manual_port':2222,'check_sftp':True}})
        assert valid.status_code==200,valid.text
        assert called[0][0]['host']=='ci-test.infra.example.org'
        assert called[0][0]['port']==2222
        assert called[0][0]['username']=='deploy'
        assert called[0][1]=={'check_sftp':True}
        nohost=client.post(path,headers=headers,json={
            'options':{'manual_host':'','manual_port':22}})
        assert nohost.status_code==422
        badhost=client.post(path,headers=headers,json={
            'options':{'manual_host':'https://localhost/','manual_port':22}})
        assert badhost.status_code==422
        badport=client.post(path,headers=headers,json={
            'options':{'manual_host':'ci-test.infra.example.org','manual_port':70000}})
        assert badport.status_code==422
        conflict=client.post(path,headers=headers,json={
            'options':{'server_id':1,'manual_host':'ci-test.infra.example.org','manual_port':22}})
        assert conflict.status_code==422
        assert len(called)==1

        # A manual target must not be persisted to vault or SSH inventory.
        credential=next(x for x in client.get('/api/credentials').json()['items']
                        if x['id']==cid)
        assert 'host' not in credential['public']
        assert 'port' not in credential['public']
        inventory=client.get('/api/ssh/inventory').json()['servers']
        assert not any(x['fqdn']=='ci-test.infra.example.org' for x in inventory)
        assert client.delete(f'/api/credentials/{cid}',headers=headers).status_code==200
    finally:
        client.__exit__(None,None,None)


def make_ca_certificate():
    private_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'CI LDAP internal CA')])
    start=datetime.now(timezone.utc)-timedelta(days=1)
    cert=(x509.CertificateBuilder()
          .subject_name(name).issuer_name(name).public_key(private_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(start)
          .not_valid_after(start+timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
          .sign(private_key,hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            cert.public_bytes(serialization.Encoding.DER))


def test_ldap_ca_import_supports_pem_and_binary_der_crt_with_tls_verification():
    client,headers=session_admin()
    paths=[]
    try:
        pem,der=make_ca_certificate()
        for payload in ({'pem':pem},{'der_base64':base64.b64encode(der).decode()}):
            missing_csrf=client.post('/api/admin/ldap/ca',json=payload)
            assert missing_csrf.status_code==403
            res=client.post('/api/admin/ldap/ca',headers=headers,json=payload)
            assert res.status_code==200,res.text
            result=res.json()
            path=Path(result['path'])
            paths.append(path)
            assert path.suffix=='.pem'
            assert path.is_file()
            assert '-----BEGIN CERTIFICATE-----' in path.read_text()
            assert result['certificate_count']==1
            # OpenSSL must be able to load the saved trust anchor.
            import ssl
            ssl.create_default_context(cafile=str(path))
        # DER and PEM represent the same CA and are normalized to the same bytes.
        assert paths[0].read_bytes()==paths[1].read_bytes()
        assert paths[0]==paths[1]
        # Reject invalid DER and non-certificate/private-key material.
        for payload in ({'der_base64':'not-base64'},
                        {'pem':'-----BEGIN PRIVATE KEY-----\n...'},
                        {'pem':pem,'der_base64':base64.b64encode(der).decode()}):
            res=client.post('/api/admin/ldap/ca',headers=headers,json=payload)
            assert res.status_code==422,res.text
    finally:
        for path in set(paths):
            path.unlink(missing_ok=True)
        client.__exit__(None,None,None)


def test_workflow_graph_has_zoom_and_pan_controls_with_bounded_scroll_area():
    root=Path(__file__).resolve().parents[1]
    js=(root/'static/app.js').read_text()
    css=(root/'static/styles.css').read_text()
    for feature in ("graph-zoom-in","graph-zoom-out","graph-zoom-reset",
                    "graph-fit-width","graph-fit-all","graphZoomTo",
                    "graphPanStart","graphPanMove","graphPanStop",
                    "data-base-height","data-base-width","restoreGraphViewport"):
        assert feature in js
    assert "if(!ev.ctrlKey||!ev.target.closest('#workflowGraph'))return" in js
    assert "Math.min(2.5,Math.max(.08" in js
    assert "clientWidth-28" in js
    assert "overflow:auto" in css
    assert ".graph-surface svg.workflow-svg" in css
    assert "min-width:0!important" in css
    assert ".graph-surface.graph-panning" in css
    assert "data-ssh-test-mode=\"manual\"" in js
    assert "options.manual_host=" in js
    assert "file.arrayBuffer()" in js
    assert "der_base64:btoa(binary)" in js
