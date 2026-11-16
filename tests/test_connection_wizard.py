"""Extended credential test wizard: simulated services, no real network calls."""
import ftplib
import re
from unittest.mock import Mock

import pytest

from app import connection_tests as t, config, connections


def test_real_smtp_message_is_submitted_once_and_not_claimed_delivered(monkeypatch):
    submitted=[]
    class SMTP:
        def send_message(self,message,from_addr,to_addrs):
            submitted.append((message,from_addr,to_addrs))
            return {}
        def quit(self): pass
        def close(self): pass
    monkeypatch.setattr(connections,"open_smtp",lambda values,timeout: SMTP())
    profile={"host":"smtp.example.org","from_address":"noreply@example.org",
             "port":587,"tls_mode":"starttls","auth":False}
    opts={"send_email":True,"confirm_send":True,"recipient":"alice@example.org",
          "subject":"Essai OrigamiTasks","message":"Ceci est un test."}
    cid=198456
    try:
        result=t.run("smtp",profile,opts,credential_id=cid)
        assert result["ok"]
        assert "réception" not in result["message"].lower() or "vérifier" in result["message"].lower()
        assert result["reference"].startswith("<")
        assert any(x["name"]=="Réception en boîte" and x["status"]=="not_tested" for x in result["checks"])
        assert len(submitted)==1
        message,from_addr,to_addrs=submitted[0]
        assert from_addr=="noreply@example.org"
        assert to_addrs==["alice@example.org"]
        assert message["To"]=="alice@example.org"
        assert message["Subject"]=="Essai OrigamiTasks"
        assert message["Message-ID"]==result["reference"]
        assert "Ceci est un test." in message.get_content()
        with pytest.raises(t.ConnectionTestError,match="30 secondes"):
            t.run("smtp",profile,opts,credential_id=cid)
        assert len(submitted)==1
    finally:
        t._LAST_MAIL_SEND.pop(cid,None)


def test_smtp_send_requires_explicit_confirmation_and_valid_address(monkeypatch):
    monkeypatch.setattr(connections,"open_smtp",lambda *a,**k: (_ for _ in ()).throw(
        AssertionError("Must not send without valid parameters")))
    profile={"host":"smtp.example.org","tls_mode":"starttls",
             "from_address":"noreply@example.org","auth":False}
    for options, match in [
        ({"send_email":True,"recipient":"a@example.org"}, "Confirmer"),
        ({"send_email":True,"confirm_send":True,
          "recipient":"a@example.org,b@example.org"}, "un seul destinataire"),
        ({"send_email":True,"confirm_send":True,
          "recipient":"alice@example.org","subject":"Sujet\nInjecté"}, "Retour à la ligne"),
    ]:
        with pytest.raises(t.ConnectionTestError,match=match):
            t.run("smtp",profile,options)


def test_smtp_refused_recipient_does_not_claim_success(monkeypatch):
    class Refused:
        def send_message(self,message,from_addr,to_addrs):
            return {"alice@example.org":(550,b"rejected")}
        def quit(self): pass
        def close(self): pass
    monkeypatch.setattr(connections,"open_smtp",lambda values,timeout:Refused())
    with pytest.raises(t.ConnectionTestError,match="refusé"):
        t.run("smtp",{"from_address":"noreply@example.org","tls_mode":"starttls"},
              {"send_email":True,"confirm_send":True,"recipient":"alice@example.org"},
              credential_id=18724)


def test_ssh_optin_sftp_and_whoami_without_arbitrary_commands(monkeypatch):
    import paramiko
    actions=[]
    class Channel:
        def exit_status_ready(self):return True
        def recv_exit_status(self):return 0
    class Stdout: channel=Channel()
    class Sftp:
        def stat(self,path):
            assert path=="."
            actions.append("metadata")
        def close(self):actions.append("sftpclose")
    class Client:
        def load_system_host_keys(self):pass
        def set_missing_host_key_policy(self,policy):pass
        def connect(self,**kwargs):actions.append("connect")
        def open_sftp(self):return Sftp()
        def exec_command(self,command,timeout):
            assert command=="whoami"
            actions.append("whoami")
            return (None,Stdout(),None)
        def close(self):actions.append("close")
    monkeypatch.setattr(paramiko,"SSHClient",Client)
    result=t.run("ssh",{"host":"ssh.example.org","username":"test",
                        "password":"secret"},{"check_sftp":True,"check_whoami":True})
    assert result["ok"]
    assert actions == ["connect","metadata","sftpclose","whoami","close"]
    assert len(result["checks"])==5
    with pytest.raises(t.ConnectionTestError,match="Paramètre non autorisé"):
        t.run("ssh",{"host":"ssh.example.org","username":"test","password":"secret"},
              {"command":"rm -rf /"})


def test_ftp_optin_directory_only(monkeypatch):
    actions=[]
    class FakeFTPS:
        def __init__(self,**kwargs):pass
        def connect(self,host,port,timeout):actions.append("connect")
        def login(self,username,password):actions.append("auth")
        def prot_p(self):actions.append("tls")
        def pwd(self):actions.append("pwd");return "/home/hidden"
        def quit(self):pass
        def close(self):pass
    monkeypatch.setattr(ftplib,"FTP_TLS",FakeFTPS)
    result=t.run("ftp",{"host":"ftp.example.org","username":"tester",
                        "password":"secret","tls_mode":"explicit"},{"check_directory":True})
    assert result["ok"]
    assert actions==["connect","auth","tls","pwd"]
    assert "/home/hidden" not in str(result)


def test_ldap_user_and_group_filter_escaping(monkeypatch):
    import ldap3
    filters=[]
    class Connection:
        def __init__(self,*a,**kw):pass
        def search(self,base,filter,scope,attributes,size_limit):
            filters.append(filter)
            return True
        def unbind(self):pass
    monkeypatch.setattr(ldap3,"Connection",Connection)
    monkeypatch.setattr(ldap3,"Server",lambda *a,**k:object())
    monkeypatch.setattr(ldap3,"Tls",lambda *a,**k:object())
    monkeypatch.setattr(t.ldap_settings,"current",lambda:{"ca_cert":"","user_attribute":"uid"})
    result=t.run("ldap",{"uri":"ldaps://ldap.example.org:636",
                         "username":"cn=service,dc=example,dc=org","password":"secret",
                         "base_dn":"dc=example,dc=org","group_base_dn":"ou=groups,dc=example,dc=org"},
                 {"test_user":"ali*ce","test_group":"ops"})
    assert result["ok"]
    assert len(filters)==3
    assert "ali*" not in filters[1]  # LDAP filter escaped
    assert "\\2a" in filters[1]
    assert filters[2]=="(cn=ops)"


def test_matrix_optin_room_and_explicit_send(monkeypatch):
    monkeypatch.setattr(config,"MATRIX_ALLOWED_HOSTS","matrix.example.org")
    sent=[]
    class Response:
        def __init__(self,data):self.status_code=200;self.data=data
        def json(self):return self.data
    class HTTP:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def get(self,url,headers):
            if "whoami" in url:return Response({"user_id":"@bot:example.org"})
            assert "m.room.member" in url
            return Response({"membership":"join"})
    monkeypatch.setattr(t.httpx,"Client",HTTP)
    monkeypatch.setattr(t.integrations,"matrix_message",lambda opts,cred,run_id,step_id,timeout:
                        sent.append((opts,run_id)))
    profile={"homeserver":"https://matrix.example.org","token":"secret"}
    options={"check_room":True,"room_id":"!room:example.org",
             "send_message":True,"confirm_send":True,"message":"Essai du bot"}
    cid=198456
    try:
        result=t.run("matrix",profile,options,credential_id=cid)
        assert result["ok"] and len(sent)==1
        assert sent[0][0]["message"]=="Essai du bot"
        assert any(x["name"]=="Envoi message" for x in result["checks"])
        with pytest.raises(t.ConnectionTestError,match="30 secondes"):
            t.run("matrix",profile,options,credential_id=cid)
        assert len(sent)==1
    finally:
        t._LAST_MATRIX_SEND.pop(cid,None)
    with pytest.raises(t.ConnectionTestError,match="Confirmer"):
        t.run("matrix",profile,{"send_message":True,"room_id":"!room:example.org"})


def test_proxmox_optional_inventory_and_node_status(monkeypatch):
    monkeypatch.setattr(config,"PROXMOX_ALLOWED_HOSTS","pve.example.org:8006")
    paths=[]
    def get(origin,cred,path,timeout):
        paths.append(path)
        if path.endswith("/nodes"):return [{"node":"pve1"}]
        return {}
    monkeypatch.setattr(t.integrations,"proxmox_request",get)
    result=t.run("proxmox",{"url":"https://pve.example.org:8006"},
                 {"check_nodes":True,"node":"pve1"})
    assert result["ok"] and len(result["checks"])==4
    assert paths==["/api2/json/access/permissions","/api2/json/nodes",
                   "/api2/json/nodes/pve1/status"]
    with pytest.raises(t.ConnectionTestError,match="invalide"):
        t.run("proxmox",{"url":"https://pve.example.org:8006"},{"node":"../evil"})


def test_api_head_and_expected_status(monkeypatch):
    monkeypatch.setattr(config,"CONNECTION_TEST_ALLOWED_HOSTS","api.example.org")
    called=[]
    class Resp:
        status_code=204
    class Client:
        def __init__(self,**kwargs):
            assert not kwargs["follow_redirects"]
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def head(self,url,headers):called.append(("HEAD",url));return Resp()
        def get(self,url,headers):raise AssertionError("GET was not requested")
    monkeypatch.setattr(t.httpx,"Client",Client)
    result=t.run("api",{"base_url":"https://api.example.org","token":"token"},
                 {"method":"HEAD","path":"/ready","expected_status":"204"})
    assert result["ok"] and called == [("HEAD","https://api.example.org/ready")]
    with pytest.raises(t.ConnectionTestError,match="attendu"):
        t.run("api",{"base_url":"https://api.example.org","token":"token"},
              {"method":"HEAD","path":"/ready","expected_status":"200"})
    with pytest.raises(t.ConnectionTestError,match="GET et HEAD"):
        t.run("api",{"base_url":"https://api.example.org"},{"method":"DELETE"})


def test_test_option_validation_never_accepts_unknown_keys_or_unconfirmed_side_effect():
    with pytest.raises(t.ConnectionTestError,match="Paramètre non autorisé"):
        t.run("smtp",{"host":"smtp.example.org"},{"password":"leak"})
    with pytest.raises(t.ConnectionTestError,match="invalide"):
        t.run("smtp",{"host":"smtp.example.org"},{"send_email":"true"})


def test_api_smtp_wizard_send_receipt_reference_and_audit(monkeypatch):
    """The real HTTP flow must send via the vault, require CSRF/confirmation and avoid logging PII."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app import db

    captured=[]
    class FakeSMTP:
        def send_message(self,msg,from_addr,to_addrs):
            captured.append((msg,to_addrs))
            return {}
        def quit(self):pass
        def close(self):pass
    monkeypatch.setattr(connections,"open_smtp",lambda values,timeout:FakeSMTP())
    with TestClient(app) as c:
        login=c.post("/api/login",json={
            "username":"admin","password":"test-admin-password-over-12"})
        assert login.status_code==200
        headers={"X-CSRF-Token":c.cookies["origami_csrf"]}
        created=c.post("/api/credentials",headers=headers,json={
            "name":"CI wizard mail delivery","kind":"smtp","payload":{
                "host":"smtp.example.org","port":587,"tls_mode":"starttls",
                "auth":False,"from_address":"noreply@example.org"
            }})
        assert created.status_code==200,created.text
        cid=created.json()["id"]
        path=f"/api/credentials/{cid}/test"
        options={"send_email":True,"confirm_send":True,
                 "recipient":"alice@example.org","subject":"Test","message":"Bonjour"}
        try:
            no_csrf=c.post(path,json={"options":options})
            assert no_csrf.status_code==403
            no_confirmation=c.post(path,headers=headers,json={
                "options":dict(options,confirm_send=False)})
            assert no_confirmation.status_code==422
            assert captured==[]
            response=c.post(path,headers=headers,json={"options":options})
            assert response.status_code==200,response.text
            result=response.json()
            assert result["reference"].startswith("<")
            assert result["elapsed_ms"] >= 0
            assert len(captured)==1
            assert captured[0][1]==["alice@example.org"]
            assert any(x["status"]=="not_tested" and x["name"]=="Réception en boîte"
                       for x in result["checks"])
            with db.session() as con:
                rows=con.execute(
                    "SELECT action,target,detail FROM audit WHERE target=? AND action LIKE 'vault.test.send%'",
                    (str(cid),)).fetchall()
            assert any(x["action"]=="vault.test.send.attempt" for x in rows)
            assert any(x["action"]=="vault.test.send" for x in rows)
            for row in rows:
                assert "alice@example.org" not in str(dict(row))
                assert "Bonjour" not in str(dict(row))
        finally:
            t._LAST_MAIL_SEND.pop(cid,None)
            assert c.delete(f"/api/credentials/{cid}",headers=headers).status_code==200
