"""Direct sync run status transitions for a real protection record (PVE mocked)."""
import uuid
import pytest
from fastapi.testclient import TestClient
from app import db, pra_direct_sync, dr_protected_vms
from app.main import app
from test_dr_protected_vms_0321 import login,dc,plan,site,fake_pve,protected_payload,cleanup


def test_per_vm_offline_sync_progress_and_completion(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        pid=plan(client,h,'CI offline sync 0329')
        src=dc(client,h,'cisyncsource0329')
        dst=dc(client,h,'cisynctarget0329')
        ss=site(client,h,'CI sync primary 0329',pid,src,'primary')
        ds=site(client,h,'CI sync standby 0329',pid,dst,'standby')
        fake_pve(monkeypatch,src,dst)
        protected=None
        agent_id=None
        try:
            created=client.post('/api/dr/vms',headers=h,json=protected_payload(pid,src,dst))
            assert created.status_code==200,created.text
            protected=created.json()['id']
            with db.session() as con:
                agent_id=con.execute(
                    "INSERT INTO agents(name,platform,group_name,enrolled,hostname,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    ("ci-sync-node-0329","linux","test",1,"srcnode",db.now())
                ).lastrowid
            monkeypatch.setattr(pra_direct_sync,"_node_authorized",lambda ag,vm:None)
            agent={"id":agent_id,"hostname":"srcnode"}
            run=str(uuid.uuid4())
            payload={"protected_vm_id":protected,"source_vmid":210,"target_vmid":321,
                     "run_uuid":run,"status":"running","disks_total":2,"disks_done":0,
                     "scanned_bytes":0,"transferred_bytes":0,"elapsed_seconds":0,"error":""}
            started=pra_direct_sync.report(agent,payload)
            assert started["status"]=="running"
            assert not started["failover_available"]
            payload.update(disks_done=1,scanned_bytes=1024,transferred_bytes=80,
                           elapsed_seconds=12)
            pra_direct_sync.report(agent,payload)
            payload.update(disks_done=2,scanned_bytes=2048,transferred_bytes=180,
                           elapsed_seconds=21,status="success")
            pra_direct_sync.report(agent,payload)
            runs=client.get('/api/dr/vms/'+str(protected)+'/direct-sync',headers=h)
            assert runs.status_code==200,runs.text
            row=runs.json()['items'][0]
            assert row['disks_done']==2
            assert row['transferred_bytes']==180
            assert row['status']=='success'
            inventory=client.get('/api/dr/vms',headers=h).json()['items']
            match=next(x for x in inventory if x['id']==protected)
            assert match['incremental_sync']['status']=='success'
            assert match['last_incremental_success']
            assert match['failover_available'] is False
            with pytest.raises(pra_direct_sync.DirectSyncError):
                pra_direct_sync.report(agent,payload)
            assert client.delete('/api/dr/vms/'+str(protected),headers=h).status_code!=200
        finally:
            if protected is not None:
                with db.session() as con:
                    con.execute('DELETE FROM pra_direct_sync_runs WHERE protected_vm_id=?',(protected,))
                assert client.delete('/api/dr/vms/'+str(protected),headers=h).status_code==200
            if agent_id is not None:
                with db.session() as con:
                    con.execute('DELETE FROM agents WHERE id=?',(agent_id,))
            cleanup(client,h,[ss,ds],[src,dst],[pid])
