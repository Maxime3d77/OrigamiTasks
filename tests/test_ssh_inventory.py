"""SSH identities are vault-only; FQDNs live in a reusable inventory and groups.

No remote SSH network calls. API permissions, inventory CRUD, and test target
selection are checked with a simulated SSH diagnostic.
"""
import io
import json
import paramiko
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import db, security, executor, ssh_inventory, connection_tests


def admin():
    c=TestClient(app)
    c.__enter__()
    r=c.post("/api/login",json={"username":"admin",
                                "password":"test-admin-password-over-12"})
    assert r.status_code==200, r.text
    return c, {"X-CSRF-Token":c.cookies["origami_csrf"]}


def test_ssh_server_groups_fqdns_ports_and_membership_lifecycle():
    c,h=admin()
    try:
        assert c.get("/api/ssh/inventory").status_code==200
        assert c.post("/api/ssh/groups",json={"name":"No CSRF"}).status_code==403
        data={"name":"CI SSH Groupe Linux","description":"Serveurs de recette",
              "servers":["ci-server1.infra.example.org",
                         "ci-server2.infra.example.org:2222"]}
        created=c.post("/api/ssh/groups",headers=h,json=data)
        assert created.status_code==200,created.text
        gid=created.json()["id"]
        inv=c.get("/api/ssh/inventory").json()
        group=next(x for x in inv["groups"] if x["id"]==gid)
        assert group["server_count"]==2
        s1=next(x for x in inv["servers"] if x["fqdn"]=="ci-server1.infra.example.org")
        s2=next(x for x in inv["servers"] if x["fqdn"]=="ci-server2.infra.example.org")
        assert s1["port"]==22 and s2["port"]==2222
        assert gid in s1["group_ids"] and gid in s2["group_ids"]
        second=c.post("/api/ssh/groups",headers=h,json={
            "name":"CI SSH Groupe secondaire",
            "servers":["ci-server1.infra.example.org"]})
        assert second.status_code==200,second.text
        second_gid=second.json()["id"]
        inv=c.get("/api/ssh/inventory").json()
        server=next(x for x in inv["servers"] if x["id"]==s1["id"])
        assert sorted(server["group_ids"])==sorted([gid,second_gid])
        update=c.put(f"/api/ssh/groups/{gid}",headers=h,json={
            "name":"CI SSH Groupe Linux (renommé)",
            "description":"Recette",
            "servers":["ci-server2.infra.example.org:2222"]})
        assert update.status_code==200,update.text
        inv=c.get("/api/ssh/inventory").json()
        assert next(x for x in inv["servers"] if x["id"]==s1["id"])["group_ids"]==[second_gid]
        assert c.delete(f"/api/ssh/groups/{gid}",headers=h).status_code==200
        assert c.delete(f"/api/ssh/groups/{second_gid}",headers=h).status_code==200
        inv=c.get("/api/ssh/inventory").json()
        assert s1["id"] in [x["id"] for x in inv["servers"]]
        assert c.delete(f"/api/ssh/servers/{s1['id']}",headers=h).status_code==200
        assert c.delete(f"/api/ssh/servers/{s2['id']}",headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_ssh_fqdn_validation_and_immutable_server_identity():
    c,h=admin()
    try:
        for bad in ["localhost","https://ci.example.org","bad_host.example.org",
                    "-bad.example.org","ci.evil.org/../../etc/passwd","bad host.example.org"]:
            r=c.post("/api/ssh/servers",headers=h,json={"fqdn":bad,"port":22})
            assert r.status_code==422,(bad,r.text)
        created=c.post("/api/ssh/servers",headers=h,json={
            "fqdn":"ci-immutable.infra.example.org","port":2022,
            "label":"CI recette"})
        assert created.status_code==200,created.text
        sid=created.json()["id"]
        assert c.put(f"/api/ssh/servers/{sid}",headers=h,json={
            "fqdn":"ci-moved.infra.example.org"}).status_code==422
        assert c.put(f"/api/ssh/servers/{sid}",headers=h,json={
            "fqdn":"ci-immutable.infra.example.org","port":2222,
            "label":"CI Updated"}).status_code==200
        inv=c.get("/api/ssh/inventory").json()
        changed=next(x for x in inv["servers"] if x["id"]==sid)
        assert changed["fqdn"]=="ci-immutable.infra.example.org"
        assert changed["port"]==2222
        assert c.delete(f"/api/ssh/servers/{sid}",headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_hostless_ssh_identity_and_on_demand_server_test(monkeypatch):
    c,h=admin()
    try:
        created=c.post("/api/credentials",headers=h,json={
            "name":"CI SSH identité déploiement","kind":"ssh",
            "payload":{"username":"deploy","private_key":"-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----"}})
        assert created.status_code==200,created.text
        cid=created.json()["id"]
        entry=next(x for x in c.get("/api/credentials").json()["items"] if x["id"]==cid)
        assert entry["public"]["username"]=="deploy"
        assert "host" not in entry["public"]
        assert "port" not in entry["public"]
        assert "private_key" not in entry["public"]
        server=c.post("/api/ssh/servers",headers=h,json={
            "fqdn":"ci-ssh-target.infra.example.org","port":2022})
        assert server.status_code==200,server.text
        sid=server.json()["id"]
        seen=[]
        def fake_run(kind,values,**kwargs):
            seen.append((kind,dict(values),kwargs["options"]))
            return {"ok":True,"protocol":"ssh","message":"SSH authentifié","checks":[]}
        monkeypatch.setattr(connection_tests,"run",fake_run)
        assert c.post(f"/api/credentials/{cid}/test",headers=h,
                      json={"options":{}}).status_code==422
        assert not seen
        result=c.post(f"/api/credentials/{cid}/test",headers=h,json={
            "options":{"server_id":sid,"check_sftp":True}})
        assert result.status_code==200,result.text
        assert result.json()["ok"]
        assert seen[0][0]=="ssh"
        assert seen[0][1]["host"]=="ci-ssh-target.infra.example.org"
        assert seen[0][1]["port"]==2022
        assert seen[0][1]["username"]=="deploy"
        assert seen[0][2]=={"check_sftp":True}
        assert c.post(f"/api/credentials/{cid}/test",headers=h,json={
            "options":{"server_id":10000000}}).status_code==422
        assert len(seen)==1
        # The test target must never be saved back into the credential vault.
        entry=next(x for x in c.get("/api/credentials").json()["items"] if x["id"]==cid)
        assert "host" not in entry["public"]
        assert c.delete(f"/api/ssh/servers/{sid}",headers=h).status_code==200
        assert c.delete(f"/api/credentials/{cid}",headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_ssh_inventory_target_for_workflow_and_delete_guard(monkeypatch):
    c,h=admin()
    try:
        server=c.post("/api/ssh/servers",headers=h,json={
            "fqdn":"ci-ssh-workflow.infra.example.org","port":2023})
        assert server.status_code==200,server.text
        sid=server.json()["id"]
        step={"id":"ssh1","name":"Exécuter commande sur inventaire","type":"ssh",
              "config":{"ssh_server_id":sid,"host":"legacy.example.org",
                        "credential_id":"","script":"echo ci"}}
        job=c.post("/api/jobs",headers=h,json={"name":"CI SSH inventory job",
            "steps_json":[step],"enabled":False})
        assert job.status_code==200,job.text
        jid=job.json()["id"]
        assert c.delete(f"/api/ssh/servers/{sid}",headers=h).status_code==409
        # The saved job's target ID is validated during creation.
        invalid=c.post("/api/jobs",headers=h,json={
            "name":"CI SSH invalid server","enabled":False,
            "steps_json":[dict(step,config=dict(step["config"],ssh_server_id=99999999))]})
        assert invalid.status_code==422
        class FakeSSH:
            def load_system_host_keys(self):pass
            def set_missing_host_key_policy(self,policy):pass
            def connect(self,**kwargs):
                assert kwargs["hostname"]=="ci-ssh-workflow.infra.example.org"
                assert kwargs["port"]==2023
                assert kwargs["username"]=="deploy"
                seen.append(True)
            def close(self):pass
        seen=[]
        monkeypatch.setattr(paramiko,"SSHClient",FakeSSH)
        client,result=executor._ssh(None,
            {"ssh_server_id":sid,"host":"legacy.example.org","port":9999},
            {"username":"deploy","password":"secret","host":"other.example.org"},
            5)
        client.close()
        assert result is None and seen==[True]
        assert c.delete(f"/api/jobs/{jid}",headers=h).status_code==200
        assert c.delete(f"/api/ssh/servers/{sid}",headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_legacy_ssh_credentials_with_host_still_work(monkeypatch):
    from app import connections
    legacy=connections.normalize("ssh",{
        "host":"old-ssh.infra.example.org","port":2222,
        "username":"legacy","password":"legacy-secret"},is_new=True)
    assert legacy["host"]=="old-ssh.infra.example.org" and legacy["port"]==2222
    portable=connections.normalize("ssh",{
        "username":"deploy","password":"secret"},is_new=True)
    assert "host" not in portable
    assert "port" not in portable
    with pytest.raises(connections.ConnectionError,match="login et mot de passe"):
        connections.normalize("ssh",{"username":"deploy"},is_new=True)
