"""Network-free tests for read-only vault diagnostic protocols and API authorization."""
import ftplib

import paramiko
import pytest
from fastapi.testclient import TestClient

from app import connection_tests as t, config, db, security
from app.main import app


def _admin():
    client=TestClient(app)
    client.__enter__()
    response=client.post("/api/login", json={
        "username":"admin","password":"test-admin-password-over-12"
    })
    assert response.status_code == 200, response.text
    return client, {"X-CSRF-Token":client.cookies["origami_csrf"]}


def test_ssh_probe_validates_host_key_and_auth_without_command(monkeypatch):
    events=[]
    class FakeSSH:
        def load_system_host_keys(self): events.append("host-system")
        def load_host_keys(self,path): events.append("host-file")
        def set_missing_host_key_policy(self,policy):
            events.append(("pinned",type(policy).__name__))
        def connect(self,**kwargs):
            events.append(("login",kwargs["username"],kwargs["hostname"]))
            assert kwargs["look_for_keys"] is False and kwargs["allow_agent"] is False
        def exec_command(self,*args,**kwargs):
            raise AssertionError("SSH diagnostic must never execute a command")
        def close(self): events.append("close")
    monkeypatch.setattr(paramiko,"SSHClient",FakeSSH)
    result=t.run("ssh", {"host":"ssh.example.org","port":2222,
                          "username":"alice","password":"test-secret"})
    assert result["ok"] and len(result["checks"]) == 3
    assert ("pinned","PersistentAcceptNewPolicy") in events  # automatic first-use trust; changed host keys remain blocked
    assert ("login","alice","ssh.example.org") in events
    assert "close" in events
    class Failed(FakeSSH):
        def connect(self,**kwargs): raise paramiko.AuthenticationException("sensitive-secret")
    monkeypatch.setattr(paramiko,"SSHClient",Failed)
    with pytest.raises(t.ConnectionTestError,match="authentification refusée") as exc:
        t.run("ssh",{"host":"ssh.example.org","username":"alice","password":"sensitive-secret"})
    assert "sensitive-secret" not in str(exc.value)


def test_ftp_explicit_tls_auth_only_and_no_transfer(monkeypatch):
    ops=[]
    class Fake:
        def __init__(self,**kwargs): ops.append("init")
        def connect(self,host,port,timeout): ops.append("connect")
        def login(self,username,password): ops.append("login")
        def prot_p(self): ops.append("protected")
        def quit(self): ops.append("quit")
        def close(self): ops.append("close")
        def storbinary(self,*args): raise AssertionError("FTP test must never transfer data")
        def retrbinary(self,*args): raise AssertionError("FTP test must never transfer data")
    monkeypatch.setattr(ftplib,"FTP_TLS",Fake)
    result=t.run("ftp",{"host":"files.example.org","port":21,
                         "tls_mode":"explicit","username":"svc","password":"secret"})
    assert result["ok"]
    assert ops == ["init","connect","login","protected","quit"]
    ops.clear()
    monkeypatch.setattr(ftplib,"FTP",Fake)
    result=t.run("ftp",{"host":"files.example.org","port":21,"tls_mode":"none",
                         "username":"svc","password":"never-send-cleartext"})
    assert result["ok"]
    assert "login" not in ops
    assert result["checks"][1]["status"] == "not_tested"


def test_matrix_whoami_uses_token_only_for_allowlisted_https(monkeypatch):
    monkeypatch.setattr(config,"MATRIX_ALLOWED_HOSTS","matrix.example.org")
    calls=[]
    class Response:
        status_code=200
        def json(self): return {"user_id":"@origami:example.org"}
    class FakeHTTP:
        def __init__(self,**kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def get(self,url,headers):
            calls.append((url,headers["Authorization"]))
            return Response()
    monkeypatch.setattr(t.httpx,"Client",FakeHTTP)
    result=t.run("matrix",{"homeserver":"https://matrix.example.org","token":"secret-token"})
    assert result["ok"]
    assert calls == [("https://matrix.example.org/_matrix/client/v3/account/whoami",
                      "Bearer secret-token")]
    with pytest.raises(t.ConnectionTestError,match="autorisé"):
        t.run("matrix",{"homeserver":"https://elsewhere.example.org","token":"secret-token"})


def test_proxmox_uses_protected_read_endpoint_no_vm_actions(monkeypatch):
    monkeypatch.setattr(config,"PROXMOX_ALLOWED_HOSTS","pve.example.org:8006")
    seen=[]
    def fake(origin,cred,path,timeout):
        seen.append((origin,path))
        return {"/": ["Sys.Audit"]}
    monkeypatch.setattr(t.integrations,"proxmox_request",fake)
    result=t.run("proxmox",{
        "url":"https://pve.example.org:8006",
        "username":"user@pve!origami","token":"secret-token"
    })
    assert result["ok"]
    assert seen == [("https://pve.example.org:8006","/api2/json/access/permissions")]


def test_api_probe_get_only_and_warns_identity_not_proven(monkeypatch):
    monkeypatch.setattr(config,"CONNECTION_TEST_ALLOWED_HOSTS","api.example.org")
    calls=[]
    class Response:
        status_code=204
    class FakeHTTP:
        def __init__(self,**kwargs):
            assert kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def get(self,url,headers):
            calls.append((url,headers["Authorization"]))
            return Response()
        def post(self,*args,**kwargs):
            raise AssertionError("Diagnostic API must never POST")
    monkeypatch.setattr(t.httpx,"Client",FakeHTTP)
    result=t.run("api",{"base_url":"https://api.example.org",
                        "test_path":"/health","token":"token-123"})
    assert result["ok"]
    assert calls == [("https://api.example.org/health","Bearer token-123")]
    assert result["checks"][-1]["status"]=="not_tested"
    for path in ("https://evil.example.org","//evil.example.org","/../../escape","/?token=secret"):
        with pytest.raises(t.ConnectionTestError):
            t.run("api",{"base_url":"https://api.example.org","test_path":path,"token":"token"})
    monkeypatch.setattr(config,"CONNECTION_TEST_ALLOWED_HOSTS","")
    with pytest.raises(t.ConnectionTestError,match="CONNECTION_TEST_ALLOWED_HOSTS"):
        t.run("api",{"base_url":"https://api.example.org","token":"secret"})


def test_ldap_bind_uses_ldaps_certificate_and_base_search(monkeypatch):
    import ldap3
    events=[]
    class FakeConnection:
        def __init__(self,server,user,password,receive_timeout,auto_bind,check_names=False):
                assert check_names is False
            assert auto_bind and user=="cn=read,dc=example,dc=org"
            events.append("bind")
        def search(self,base,filter,scope,attributes,size_limit):
            assert base=="dc=example,dc=org"
            events.append("search")
            return True
        def unbind(self): events.append("unbind")
    monkeypatch.setattr(ldap3,"Server",lambda *a,**kw:object())
    monkeypatch.setattr(ldap3,"Tls",lambda *a,**kw:object())
    monkeypatch.setattr(ldap3,"Connection",FakeConnection)
    monkeypatch.setattr(t.ldap_settings,"current",lambda:{"ca_cert":""})
    result=t.run("ldap",{"uri":"ldaps://ldap.example.org:636",
                         "starttls":False,"base_dn":"dc=example,dc=org",
                         "username":"cn=read,dc=example,dc=org","password":"secret"})
    assert result["ok"]
    assert events==["bind","search","unbind"]


def test_admin_only_test_endpoint_and_audit(monkeypatch):
    c,h=_admin()
    try:
        created=c.post("/api/credentials",headers=h,json={
            "name":"CI diag SMTP","kind":"smtp",
            "payload":{"host":"smtp.example.org","port":587,"auth":False}
        })
        assert created.status_code == 200,created.text
        cid=created.json()["id"]
        called=[]
        monkeypatch.setattr(t,"run",lambda kind,values,**kwargs: (
            called.append((kind,values.get("host"))),
            {"ok":True,"protocol":kind,"message":"Testé","checks":[]}
        )[1])
        assert c.post(f"/api/credentials/{cid}/test").status_code==403
        res=c.post(f"/api/credentials/{cid}/test",headers=h)
        assert res.status_code==200,res.text
        assert called == [("smtp","smtp.example.org")]
        with db.session() as con:
            audit=con.execute("SELECT action FROM audit WHERE target=? AND action='vault.test'",
                              (str(cid),)).fetchone()
        assert audit is not None
        assert c.delete(f"/api/credentials/{cid}",headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_generic_vault_cannot_claim_authentication():
    with pytest.raises(t.ConnectionTestError,match="générique"):
        t.run("generic",{"username":"alice","password":"secret"})
