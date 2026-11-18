"""End-to-end controller queue and per-VM audit with PVE/SSH fully mocked.

No hypervisor commands or credentials are sent by these tests.
"""
import time
from fastapi.testclient import TestClient
from app import db, pra_direct_jobs, integrations
from app.main import app
from test_dr_protected_vms_0321 import login,dc,plan,site,fake_pve,protected_payload,cleanup


def test_gui_direct_seed_job_requires_admin_and_tracks_vm(monkeypatch):
    with TestClient(app) as client:
        headers=login(client)
        plan_id=plan(client,headers,"CI integrated PRA 0330")
        source=dc(client,headers,"ci0330source")
        target=dc(client,headers,"ci0330target")
        ss=site(client,headers,"CI integrated primary 0330",plan_id,source,"primary")
        ds=site(client,headers,"CI integrated target 0330",plan_id,target,"standby")
        fake_pve(monkeypatch,source,target)
        vm_id=None
        creds=[]
        try:
            payload=protected_payload(plan_id,source,target)
            payload["engine"]="offline_copy"
            created=client.post("/api/dr/vms",json=payload,headers=headers)
            assert created.status_code==200,created.text
            vm_id=created.json()["id"]
            with db.session() as con:
                for name in ("ci0330_source_ssh","ci0330_target_ssh"):
                    creds.append(con.execute(
                        "INSERT INTO credentials(name,kind,encrypted_payload,created_at,updated_at) "
                        "VALUES (?,?,?,?,?)",
                        (name,"ssh","test-do-not-use",db.now(),db.now())
                    ).lastrowid)
            monkeypatch.setattr(pra_direct_jobs,"_vm_owner",lambda vm:"srcnode")
            monkeypatch.setattr(pra_direct_jobs,"_host",lambda con,dcid,node:{
                "fqdn":"source.example.org" if dcid==source else "target.example.org",
                "port":22
            })
            monkeypatch.setattr(pra_direct_jobs,"_agent",lambda con,vm,node:{"id":1})
            calls=[]
            monkeypatch.setattr(pra_direct_jobs,"_execute",
                                lambda job,vm,src,dst:calls.append(job["kind"]) or {"output":"ok"})
            endpoint="/api/dr/vms/"+str(vm_id)
            rejected=client.post(endpoint+"/direct-jobs",headers=headers,json={
                "kind":"seed","source_credential_id":creds[0],
                "target_credential_id":creds[1],"approved":False})
            assert rejected.status_code==409
            started=client.post(endpoint+"/direct-jobs",headers=headers,json={
                "kind":"seed","source_credential_id":creds[0],
                "target_credential_id":creds[1],"approved":True})
            assert started.status_code==200,started.text
            job_id=started.json()["job_id"]
            finish=None
            for _ in range(50):
                status=client.get(endpoint+"/direct-jobs",headers=headers)
                assert status.status_code==200,status.text
                finish=next(x for x in status.json()["items"] if x["id"]==job_id)
                if finish["status"]=="success":break
                time.sleep(.1)
            assert finish["status"]=="success",finish
            assert calls==["seed"]
            listed=client.get("/api/dr/vms",headers=headers).json()["items"]
            entry=next(x for x in listed if x["id"]==vm_id)
            assert entry["direct_job"]["status"]=="success"
            assert entry["last_direct_seed"]
            assert entry["failover_available"] is False
            assert client.delete(endpoint,headers=headers).status_code!=200
            monkeypatch.setattr(integrations,"discover_datacenter",
                                lambda dc,cred,timeout:[])
            released=client.post(endpoint+"/direct-release",headers=headers,json={"approved":True})
            assert released.status_code==200,released.text
            assert released.json()["remote_vm_deleted"] is False
            assert client.delete(endpoint,headers=headers).status_code==200
            vm_id=None
        finally:
            if vm_id is not None:
                with db.session() as con:
                    con.execute("DELETE FROM pra_direct_jobs WHERE protected_vm_id=?",(vm_id,))
                    con.execute("DELETE FROM dr_protected_vms WHERE id=?",(vm_id,))
            with db.session() as con:
                for cid in creds:
                    con.execute("DELETE FROM credentials WHERE id=?",(cid,))
            cleanup(client,headers,[ss,ds],[source,target],[plan_id])
