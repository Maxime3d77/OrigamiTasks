"""PRA hot generations: source always running, immutable AU candidates, no failover."""
import time
from fastapi.testclient import TestClient
from app import db, integrations, pra_hot_jobs, pra_direct_jobs
from app.main import app
from test_dr_protected_vms_0321 import login,dc,plan,site,fake_pve,protected_payload,cleanup


def test_hot_generation_lifecycle_mocked(monkeypatch):
    with TestClient(app) as client:
        headers=login(client)
        plan_id=plan(client,headers,"CI online differential 0332")
        src=dc(client,headers,"ci0332-src")
        dst=dc(client,headers,"ci0332-dst")
        ss=site(client,headers,"CI live 0332",plan_id,src,"primary")
        ds=site(client,headers,"CI off 0332",plan_id,dst,"standby")
        fake_pve(monkeypatch,src,dst)
        vmid=None
        creds=[]
        try:
            payload=protected_payload(plan_id,src,dst)
            payload["engine"]="offline_copy"
            r=client.post("/api/dr/vms",headers=headers,json=payload)
            assert r.status_code==200,r.text
            vmid=r.json()["id"]
            with db.session() as con:
                for name in ("hot0332src","hot0332dst"):
                    creds.append(con.execute(
                        "INSERT INTO credentials(name,kind,encrypted_payload,created_at,updated_at)"
                        " VALUES(?,?,?,?,?)",
                        (name,"ssh","test-never-use",db.now(),db.now())
                    ).lastrowid)
                con.execute(
                    "INSERT INTO pra_direct_jobs(protected_vm_id,kind,source_node,source_host,"
                    "target_host,source_credential_id,target_credential_id,status,actor,created_at,finished_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (vmid,"seed","srcnode","src.example.org","dst.example.org",
                     creds[0],creds[1],"success","ci",db.now(),db.now())
                )
            monkeypatch.setattr(pra_hot_jobs,"_scope",lambda vid,a,b:(
                {"id":vid,"source_vmid":210,"target_vmid":321,
                 "target_dc_id":dst,"target_node":"dstnode","source_dc_id":src,
                 "engine":"offline_copy","state":"planned","target_storage":"target-lvm"},
                "srcnode",{"fqdn":"src.example.org","port":22},
                {"fqdn":"dst.example.org","port":22},{"id":1}))
            monkeypatch.setattr(pra_hot_jobs,"_target_verified_base",lambda vm,base:None)
            monkeypatch.setattr(pra_hot_jobs,"_target_verified",lambda vm,new,base:None)
            seen=[]
            def perform(job,vm,source,target):
                seen.append((job["kind"],job["base_vmid"],job["scratch_path"]))
                return {"candidate_vmid":900221,"disks":2,"scanned_bytes":10000,"transferred_bytes":100}
            monkeypatch.setattr(pra_direct_jobs,"_execute",perform)
            path=f"/api/dr/vms/{vmid}/hot-generations"
            bad=client.post(path,json={"source_credential_id":creds[0],
                "target_credential_id":creds[1],"scratch_path":"/","approved":True},
                headers=headers)
            assert bad.status_code==409,bad.text
            ok=client.post(path,json={"source_credential_id":creds[0],
                "target_credential_id":creds[1],"scratch_path":"/mnt/pra-scratch",
                "approved":True},headers=headers)
            assert ok.status_code==200,ok.text
            # Drive the worker synchronously so the test does not depend on
            # a background polling interval (normally four seconds).
            assert pra_hot_jobs.process_one() is True
            status=None
            for _ in range(60):
                r=client.get(path,headers=headers)
                assert r.status_code==200,r.text
                status=r.json()["items"][0]
                if status["status"]=="success":break
                time.sleep(.05)
            assert status["status"]=="success",status
            assert status["candidate_vmid"]==900221
            assert status["transferred_bytes"]==100
            assert seen==[("hot_differential",321,"/mnt/pra-scratch")]
            inv=client.get("/api/dr/vms",headers=headers).json()["items"]
            vm=next(x for x in inv if x["id"]==vmid)
            assert vm["ready_hot_replica"]["candidate_vmid"]==900221
            assert vm["failover_available"] is False
            assert client.delete(f"/api/dr/vms/{vmid}",headers=headers).status_code!=200
            # An interrupted next generation remains UNCERTAIN; its candidate
            # never becomes the current verified AU standby.
            def interrupted(job,vm,source,target):
                err=pra_direct_jobs.PRAJobError("Lien SSH interrompu")
                err.candidate_vmid=900222
                raise err
            monkeypatch.setattr(pra_direct_jobs,"_execute",interrupted)
            again=client.post(path,json={"source_credential_id":creds[0],
                "target_credential_id":creds[1],"scratch_path":"/mnt/pra-scratch",
                "approved":True},headers=headers)
            assert again.status_code==200,again.text
            assert pra_hot_jobs.process_one() is True
            history=client.get(path,headers=headers).json()["items"]
            assert history[0]["status"]=="uncertain"
            assert history[0]["candidate_vmid"]==900222
            assert client.post(path,json={"source_credential_id":creds[0],
                "target_credential_id":creds[1],"scratch_path":"/mnt/pra-scratch",
                "approved":True},headers=headers).status_code==409
            monkeypatch.setattr(integrations,"configured_datacenter",
                                lambda dc_id:{"credential_id":creds[0]})
            monkeypatch.setattr(pra_hot_jobs,"credential",lambda cred_id:{})
            monkeypatch.setattr(integrations,"discover_datacenter",
                                lambda dc,auth,timeout:[])
            resolved=client.post(path+"/"+str(history[0]["id"])+"/resolve",
                                 json={"approved":True},headers=headers)
            assert resolved.status_code==200,resolved.text
            assert resolved.json()["remote_deleted"] is False
        finally:
            if vmid is not None:
                with db.session() as con:
                    con.execute(
                        "DELETE FROM pra_hot_resolutions WHERE job_id IN "
                        "(SELECT id FROM pra_hot_jobs WHERE protected_vm_id=?)",(vmid,)
                    )
                    con.execute("DELETE FROM pra_hot_jobs WHERE protected_vm_id=?",(vmid,))
                    con.execute("DELETE FROM pra_direct_jobs WHERE protected_vm_id=?",(vmid,))
                assert client.delete(f"/api/dr/vms/{vmid}",headers=headers).status_code==200
            with db.session() as con:
                for c in creds:
                    con.execute("DELETE FROM credentials WHERE id=?",(c,))
            cleanup(client,headers,[ss,ds],[src,dst],[plan_id])


def test_hot_job_requires_admin():
    with TestClient(app) as c:
        assert c.get("/api/dr/vms/1/hot-generations").status_code==401
        assert c.post("/api/dr/vms/1/hot-generations",json={}).status_code in (401,403)
