"""OrigamiTasks 0.3.5: typed selectors, safe CA import, bulk agents, SMTP and branches."""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import app
from app import config, db, security, executor, agent_deployments, engine


def logged_admin():
    c=TestClient(app)
    c.__enter__()
    r=c.post("/api/login",json={"username":"admin","password":"test-admin-password-over-12"})
    assert r.status_code==200,r.text
    return c,{"X-CSRF-Token":c.cookies["origami_csrf"]}


def test_bulk_agent_deploy_queue_is_ssh_only_and_reports_each_server(monkeypatch):
    monkeypatch.setattr(config,"ALLOW_AGENT_REMOTE_DEPLOY",True)
    monkeypatch.setattr(config,"AGENT_SERVER_URL","https://tasks.example.org")
    monkeypatch.setattr(config,"AGENT_DEPLOY_ALLOWED_HOSTS",
                        "ci-bulk-a.infra.example.org,ci-bulk-b.infra.example.org")
    calls=[]
    def fake_deploy(body):
        calls.append(dict(body))
        if body["host"].startswith("ci-bulk-b"):
            raise RuntimeError("simulated fail without credential details")
        return {"id":8881,"name":body["name"],"status":"installed_pending_enrollment"}
    monkeypatch.setattr(agent_deployments.agents,"deploy",fake_deploy)
    c,h=logged_admin()
    try:
        bad_cred=c.post("/api/credentials",headers=h,json={
            "name":"CI-BULK-SMTP","kind":"smtp",
            "payload":{"host":"relay.example.org","port":25,"auth":False,"tls_mode":"none"}})
        assert bad_cred.status_code==200,bad_cred.text
        smtp=bad_cred.json()["id"]
        ssh_cred=c.post("/api/credentials",headers=h,json={
            "name":"CI-BULK-SSH","kind":"ssh","payload":{
                "username":"svc-deploy","password":"safe-test-secret"}})
        assert ssh_cred.status_code==200,ssh_cred.text
        ssh=ssh_cred.json()["id"]
        path="/api/agents/deploy/batch"
        targets=[{"host":"ci-bulk-a.infra.example.org"},
                 {"host":"ci-bulk-b.infra.example.org:2222"}]
        assert c.post(path,headers=h,json={"platform":"linux",
                        "credential_id":smtp,"targets":targets,"approved":True}).status_code==422
        assert c.post(path,json={"platform":"linux",
                        "credential_id":ssh,"targets":targets,"approved":True}).status_code==403
        assert c.post(path,headers=h,json={"platform":"linux",
                        "credential_id":ssh,"targets":targets,"approved":False}).status_code==422
        response=c.post(path,headers=h,json={"platform":"linux","agent_group":"ops-linux",
                          "credential_id":ssh,"targets":targets,"approved":True})
        assert response.status_code==200,response.text
        batch_id=response.json()["batch_id"]
        for _ in range(80):
            status=c.get("/api/agents/deploy/batches").json()["items"]
            batch=next(x for x in status if x["id"]==batch_id)
            if batch["status"] in ("success","completed_with_errors"):
                break
            time.sleep(0.05)
        assert batch["status"]=="completed_with_errors"
        assert batch["total"]==2 and batch["done"]==2
        assert batch["success"]==1 and batch["failed"]==1
        assert len(calls)==2
        assert calls[0]["credential_id"]==ssh
        assert calls[1]["port"]==2222
        failed=[x for x in batch["targets"] if x["status"]=="failed"]
        assert len(failed)==1
        assert "simulated fail" not in failed[0]["error"]
        assert "credential_id" not in json.dumps(batch["targets"])
        assert c.delete("/api/credentials/"+str(smtp),headers=h).status_code==200
        assert c.delete("/api/credentials/"+str(ssh),headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_bulk_agent_hosts_must_be_allowlisted_and_unique(monkeypatch):
    monkeypatch.setattr(config,"ALLOW_AGENT_REMOTE_DEPLOY",True)
    monkeypatch.setattr(config,"AGENT_SERVER_URL","https://tasks.example.org")
    monkeypatch.setattr(config,"AGENT_DEPLOY_ALLOWED_HOSTS","ci-authorized.infra.example.org")
    c,h=logged_admin()
    try:
        c_response=c.post("/api/credentials",headers=h,json={
            "name":"CI-BULK-SINGLE","kind":"ssh",
            "payload":{"username":"deploy","password":"not-a-real-secret"}})
        assert c_response.status_code==200
        cid=c_response.json()["id"]
        base={"platform":"linux","credential_id":cid,"approved":True}
        for targets in [
            [{"host":"ci-not-allowlisted.infra.example.org"}],
            [{"host":"ci-authorized.infra.example.org"},
             {"host":"ci-authorized.infra.example.org"}],
            [{"host":"invalid host"}],
            [{}]
        ]:
            res=c.post("/api/agents/deploy/batch",headers=h,
                       json=dict(base,targets=targets))
            assert res.status_code==422,res.text
        assert c.delete(f"/api/credentials/{cid}",headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_import_public_ldap_ca_without_skipping_tls(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import NameOID
    key=Ed25519PrivateKey.generate()
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"Origami LDAP Test CA")])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
          .public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(days=1))
          .not_valid_after(now+timedelta(days=90))
          .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
          .sign(key,algorithm=None))
    pem=cert.public_bytes(serialization.Encoding.PEM).decode()
    c,h=logged_admin()
    try:
        assert c.post("/api/admin/ldap/ca",json={"pem":pem}).status_code==403
        imported=c.post("/api/admin/ldap/ca",headers=h,json={"pem":pem})
        assert imported.status_code==200,imported.text
        result=imported.json()
        path=Path(result["path"])
        assert path.is_file()
        assert path.parent.is_relative_to(config.DATA)
        assert path.stat().st_mode & 0o777==0o600
        assert path.read_text()==pem
        assert result["certificate_count"]==1
        assert c.post("/api/admin/ldap/ca",headers=h,json={
            "pem":pem+key.private_bytes(serialization.Encoding.PEM,
                 serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
        }).status_code==422
        assert c.post("/api/admin/ldap/ca",headers=h,
                      json={"pem":"bad certificate data"*50}).status_code==422
        path.unlink()
    finally:
        c.__exit__(None,None,None)


def test_manual_and_vault_smtp_workflow_use_the_correct_source(monkeypatch):
    calls=[]
    class SMTP:
        def send_message(self,message):
            calls.append(("message",message["From"],message["To"]))
        def quit(self):pass
        def close(self):pass
    def fake_open(profile,timeout):
        calls.append(("profile",dict(profile)))
        return SMTP()
    monkeypatch.setattr(executor.connections,"open_smtp",fake_open)
    c,h=logged_admin()
    try:
        created=c.post("/api/credentials",headers=h,json={
            "name":"CI-UX-SMTP","kind":"smtp",
            "payload":{"host":"smtp-vault.example.org","port":465,"tls_mode":"ssl",
                       "auth":False,"from_address":"vault@example.org"}})
        assert created.status_code==200,created.text
        cid=created.json()["id"]
        context={"run_id":1,"variables":{},"steps":{}}
        vault_step={"id":"mail","name":"Vault mail","type":"email",
                    "config":{"connection_mode":"vault","credential_id":cid,
                              "host":"obsolete.example.org","port":2525,"ssl":True,
                              "from":"obsolete@example.org","to":"recipient@example.org",
                              "subject":"Test","body":"Bonjour"}}
        result=executor.execute(vault_step,context)
        assert result["status"]=="success"
        profile=next(x[1] for x in calls if x[0]=="profile")
        assert profile["host"]=="smtp-vault.example.org"
        assert profile["port"]==465 and profile["tls_mode"]=="ssl"
        assert ("message","vault@example.org","recipient@example.org") in calls
        calls.clear()
        manual_step={"id":"mail","name":"Manual relay","type":"email",
                     "config":{"connection_mode":"manual","credential_id":cid,
                               "host":"relay.example.org","port":25,"tls_mode":"none",
                               "from":"manual@example.org","to":"recipient@example.org",
                               "subject":"Test","body":"Bonjour"}}
        result=executor.execute(manual_step,context)
        assert result["status"]=="success"
        profile=next(x[1] for x in calls if x[0]=="profile")
        assert profile["host"]=="relay.example.org"
        assert profile["port"]==25 and profile["tls_mode"]=="none"
        assert profile.get("auth") is None
        assert ("message","manual@example.org","recipient@example.org") in calls
        assert c.delete(f"/api/credentials/{cid}",headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_validation_rejects_mismatched_vault_task_credentials():
    c,h=logged_admin()
    try:
        smtp=c.post("/api/credentials",headers=h,json={
            "name":"CI-VALID-SMTP","kind":"smtp",
            "payload":{"host":"relay.example.org","port":25,"tls_mode":"none","auth":False}})
        assert smtp.status_code==200
        cid=smtp.json()["id"]
        base={"name":"CI mode testing","enabled":False}
        cfg={"connection_mode":"vault","credential_id":cid,
             "to":"user@example.org","subject":"Hi"}
        task={"id":"mail1","name":"Email step","type":"email","config":cfg}
        ok=c.post("/api/jobs",headers=h,json=dict(base,steps_json=[task]))
        assert ok.status_code==200,ok.text
        job=ok.json()["id"]
        assert c.delete(f"/api/jobs/{job}",headers=h).status_code==200
        bad=c.post("/api/jobs",headers=h,json=dict(base,steps_json=[
            dict(task,config=dict(cfg,credential_id=""))
        ]))
        assert bad.status_code==422
        manual=c.post("/api/jobs",headers=h,json=dict(base,steps_json=[
            dict(task,config={"connection_mode":"manual","host":"relay.example.org",
                              "from":"noreply@example.org","to":"user@example.org"})
        ]))
        assert manual.status_code==200,manual.text
        assert c.delete(f"/api/jobs/{manual.json()['id']}",headers=h).status_code==200
        assert c.delete(f"/api/credentials/{cid}",headers=h).status_code==200
    finally:
        c.__exit__(None,None,None)


def test_failure_branch_runs_recovery_steps_without_breaking_normal_path(monkeypatch):
    monkeypatch.setattr(engine,"record_step",lambda *args,**kwargs:None)
    jobs=[
        {"id":"step1","name":"Source","type":"http","on_failure":"recover"},
        {"id":"recover","name":"Alerter","type":"email","on_success":"END"}
    ]
    visited=[]
    def fail_then_recover(step,ctx):
        visited.append(step["id"])
        if step["id"]=="step1":
            return {"status":"failed","exit_code":1,"output":"network failure"}
        return {"status":"success","exit_code":0,"output":"alert sent"}
    result=engine.run_graph(jobs,123,step_exec=fail_then_recover)
    assert result[0]=="success" and visited==["step1","recover"]


def test_schedule_mode_and_connection_ui_regression():
    source=Path("static/app.js").read_text()
    styles=Path("static/styles.css").read_text()
    assert "function showScheduleMode()" in source
    assert "$$('[data-schedule-mode]',root).forEach" in source
    assert "Une seule exécution programmée" in source
    assert "Assistant de répétition" in source
    assert "Cron avancé" in source
    assert ".schedule-detail[hidden]" in styles
    assert "cfg_connection_mode" in source
    assert "add-failure-step" in source
    assert "state.credentials.filter(c=>c.kind==='ssh')" in source
    assert "AgentBulkDeployForm" not in source or "agentBulkDeployForm" in source
