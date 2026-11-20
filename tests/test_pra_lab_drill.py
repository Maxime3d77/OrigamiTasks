"""Isolated boot drill is an independent FULL clone, never the original REP_.

All Proxmox mutations are mocked. A live non-production acceptance run is
still required before trusting cloned disks, QEMU boot or NIC removal.
"""
from fastapi.testclient import TestClient
from app import config, db, integrations, pra_restore
from app.main import app
from test_dr_protected_vms_0321 import (
    login, dc, plan, site, fake_pve, protected_payload, cleanup,
)


def _setup(client, monkeypatch, h):
    p=plan(client,h,"CI isolated PRA drill")
    src=dc(client,h,"lab-pra-source")
    dst=dc(client,h,"lab-pra-target")
    ss=site(client,h,"CI protected site",p,src,"primary")
    ds=site(client,h,"CI recovery site",p,dst,"standby")
    fake_pve(monkeypatch,src,dst)
    payload=protected_payload(p,src,dst)
    payload["engine"]="pbs_backup_restore"
    response=client.post("/api/dr/vms",headers=h,json=payload)
    assert response.status_code==200,response.text
    vm_id=response.json()["id"]
    with db.session() as con:
        backup=con.execute(
            "INSERT INTO pra_pbs_backups(protected_vm_id,storage,node,status,"
            "actor,created_at) VALUES (?,?,?,?,?,?)",
            (vm_id,"pbs-source","srcnode","completed","ci",db.now())
        ).lastrowid
        restored=con.execute(
            "INSERT INTO pra_pbs_restores(protected_vm_id,backup_job_id,pbs_storage,"
            "archive,node,target_vmid,target_storage,actor,status,created_at)"
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (vm_id,backup,"pbs-target","pbs-target:backup/vm/210/test",
             "dstnode",321,"target-lvm","ci","completed",db.now())
        ).lastrowid
    conf={"onboot":0,"scsi0":"target-lvm:vm-321-disk-0",
          "net0":"virtio=BC:24:11:11:11:11,bridge=vmbr0"}
    state={"resources":[{"type":"qemu","vmid":321,"node":"dstnode",
                          "status":"stopped","name":"REP_vm-ci-database"}],
           "base":conf,"clone":{},"calls":[]}
    def fake_pve_http(dc,cred,path,method="GET",data=None):
        state["calls"].append((method,path,data))
        if path.endswith("/qemu/321/config"):
            return dict(state["base"])
        if path.endswith("/qemu/9301/config"):
            return dict(state["clone"])
        if "/tasks/" in path and path.endswith("/status"):
            return {"status":"stopped","exitstatus":"OK"}
        if method=="POST" and path.endswith("/qemu/321/clone"):
            assert data["newid"]=="9301" and data["full"]=="1"
            state["clone"]={"onboot":0,"scsi0":"target-lvm:vm-9301-disk-0",
                            "net0":state["base"]["net0"]}
            state["resources"].append({
                "type":"qemu","vmid":9301,"node":"dstnode",
                "status":"stopped","name":data["name"],
            })
            return "UPID:lab:clone"
        if method=="POST" and path.endswith("/qemu/9301/config"):
            assert data["delete"]=="net0"
            del state["clone"]["net0"]
            return "UPID:lab:config"
        if method=="POST" and path.endswith("/qemu/9301/status/start"):
            assert not any(x.startswith("net") for x in state["clone"])
            state["resources"][-1]["status"]="running"
            return "UPID:lab:start"
        if method=="POST" and path.endswith("/qemu/9301/status/shutdown"):
            state["resources"][-1]["status"]="stopped"
            return "UPID:lab:shutdown"
        raise AssertionError(f"Unexpected Proxmox request {method} {path}")
    monkeypatch.setattr(pra_restore, "_target", lambda vm: ({"url":"https://pve.invalid"},{}))
    monkeypatch.setattr(pra_restore, "_pve", fake_pve_http)
    monkeypatch.setattr(integrations, "discover_datacenter",
                        lambda dc,cred,timeout: list(state["resources"]))
    monkeypatch.setattr(config,"ALLOW_PROXMOX_ACTIONS",True)
    return vm_id, restored, state, ([ss,ds],[src,dst],[p])


def _cleanup(client,h,vid,parts):
    with db.session() as con:
        con.execute("DELETE FROM pra_lab_drills WHERE protected_vm_id=?",(vid,))
        con.execute("DELETE FROM pra_pbs_restores WHERE protected_vm_id=?",(vid,))
        con.execute("DELETE FROM pra_pbs_backups WHERE protected_vm_id=?",(vid,))
    assert client.delete("/api/dr/vms/"+str(vid),headers=h).status_code==200
    cleanup(client,h,*parts)


def test_drill_clones_with_full_disk_copy_and_never_powers_on_standby(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vid, restored, state, parts=_setup(client,monkeypatch,h)
        try:
            path=f"/api/dr/vms/{vid}/drills"
            denied=client.post(path,headers=h,json={
                "restore_job_id":restored,"test_vmid":9301,"approved":False})
            assert denied.status_code==409
            assert not any(x[0]=="POST" for x in state["calls"])
            prepared=client.post(path,headers=h,json={
                "restore_job_id":restored,"test_vmid":9301,"approved":True})
            assert prepared.status_code==200,prepared.text
            idx=prepared.json()["drill_id"]
            assert prepared.json()["standby_vm_started"] is False
            assert prepared.json()["clone_started"] is False
            assert state["resources"][0]["status"]=="stopped"
            clone=client.get(path+"/"+str(idx),headers=h)
            assert clone.status_code==200,clone.text
            assert clone.json()["status"]=="needs_isolation"
            assert clone.json()["ready_for_boot"] is False
            unready=client.post(path+"/"+str(idx)+"/start",
                headers=h,json={"approved":True})
            assert unready.status_code==409
            isolated=client.post(path+"/"+str(idx)+"/isolate",
                headers=h,json={"approved":True})
            assert isolated.status_code==200,isolated.text
            assert isolated.json()["status"]=="ready"
            assert isolated.json()["network_isolated"] is True
            started=client.post(path+"/"+str(idx)+"/start",
                headers=h,json={"approved":True})
            assert started.status_code==200,started.text
            assert started.json()["started_vmid"]==9301
            assert state["resources"][0]["status"]=="stopped"
            running=client.get(path+"/"+str(idx),headers=h)
            assert running.status_code==200,running.text
            assert running.json()["status"]=="running"
            assert running.json()["can_promote"] is False
            stopped=client.post(path+"/"+str(idx)+"/shutdown",
                headers=h,json={"approved":True})
            assert stopped.status_code==200,stopped.text
            assert state["resources"][0]["status"]=="stopped"
            assert client.delete(f"/api/dr/vms/{vid}",headers=h).status_code==409
            assert any(x[0]=="POST" and x[1].endswith("/qemu/9301/status/start")
                           for x in state["calls"])
            assert not any(x[0]=="POST" and x[1].endswith("/qemu/321/status/start")
                           for x in state["calls"])
            assert not any(x[0]=="DELETE" for x in state["calls"])
        finally:
            _cleanup(client,h,vid,parts)


def test_refuses_cluster_wide_container_vmid_collision(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vid,restored,state,parts=_setup(client,monkeypatch,h)
        try:
            state["resources"].append({
                "type":"lxc","vmid":9301,"node":"dstnode","status":"stopped"})
            response=client.post(f"/api/dr/vms/{vid}/drills",headers=h,json={
                "restore_job_id":restored,"test_vmid":9301,"approved":True})
            assert response.status_code==409
            assert not any(x[0]=="POST" for x in state["calls"])
        finally:
            _cleanup(client,h,vid,parts)


def test_no_boot_with_shared_disk_or_untrusted_clone_identity(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vid,restored,state,parts=_setup(client,monkeypatch,h)
        try:
            prefix=f"/api/dr/vms/{vid}/drills"
            prepared=client.post(prefix,headers=h,json={
                "restore_job_id":restored,"test_vmid":9301,"approved":True})
            assert prepared.status_code==200
            drill=prepared.json()["drill_id"]
            state["clone"]["scsi0"]=state["base"]["scsi0"]
            response=client.get(prefix+"/"+str(drill),headers=h)
            assert response.status_code==409
            assert "Disque partagé" in response.json()["detail"]
            assert not any(x[1].endswith("/qemu/9301/status/start")
                           for x in state["calls"])
        finally:
            _cleanup(client,h,vid,parts)


def test_drill_endpoints_require_admin():
    with TestClient(app) as client:
        assert client.get("/api/dr/vms/17/drills").status_code in (401,403)
        assert client.post("/api/dr/vms/17/drills",json={}).status_code in (401,403)
        assert client.post("/api/dr/vms/17/drills/1/start",json={"approved":True}).status_code in (401,403)
