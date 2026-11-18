"""PBS restore sends a real PVE restore request, never starts the standby.

All PVE calls here are mocked. Integration testing needs an actual PBS visible
from both clusters and an unused destination VMID.
"""
from fastapi.testclient import TestClient
from app import db, config, integrations, dr_protected_vms, pra_restore
from app.main import app
from test_dr_protected_vms_0321 import login,dc,plan,site,fake_pve,protected_payload,cleanup


def test_restore_creates_stopped_vm_without_overwrite(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        pid=plan(client,h,'CI restore group 0327')
        source=dc(client,h,'cipbsrestoresource0327')
        target=dc(client,h,'cipbsrestoretarget0327')
        srcsite=site(client,h,'CI restore primary 0327',pid,source,'primary')
        dstsite=site(client,h,'CI restore standby 0327',pid,target,'standby')
        fake_pve(monkeypatch,source,target)
        vm_id=None
        backup_id=None
        try:
            payload=protected_payload(pid,source,target)
            payload['engine']='pbs_backup_restore'
            created=client.post('/api/dr/vms',headers=h,json=payload)
            assert created.status_code==200,created.text
            vm_id=created.json()['id']
            with db.session() as con:
                backup_id=con.execute(
                    "INSERT INTO pra_pbs_backups(protected_vm_id,storage,node,status,actor,created_at,finished_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (vm_id,"pbs-sd","srcnode","completed","ci",db.now(),db.now())
                ).lastrowid
            monkeypatch.setattr(config,'ALLOW_PROXMOX_ACTIONS',True)
            target_resources=[{'type':'node','node':'dstnode','status':'online'}]
            monkeypatch.setattr(integrations,'discover_datacenter',
                                lambda dc,cred,timeout:target_resources)
            archive='pbs-au:backup/vm/210/2026-11-18T11:00:00Z'
            calls=[]
            def pve(origin,cred,path,timeout,method='GET',data=None):
                calls.append((method,path,data))
                if path=='/api2/json/storage':
                    return [
                        {'storage':'pbs-au','type':'pbs','content':'backup'},
                        {'storage':'target-lvm','type':'lvm','content':'images,rootdir'}
                    ]
                if path.endswith('/storage/pbs-au/content?content=backup'):
                    return [{'volid':archive,'ctime':1795000000,'size':1000}]
                if path=='/api2/json/nodes/dstnode/qemu' and method=='POST':
                    assert data['start']=='0'
                    assert data['onboot']=='0'
                    assert data['ha-managed']=='0'
                    assert data['unique']=='1'
                    assert data['vmid']=='321'
                    assert data['archive']==archive
                    target_resources.append({'type':'qemu','node':'dstnode','vmid':321,'status':'stopped'})
                    return 'UPID:dstnode:0001:0001:test:qmrestore:321:ci@pve:'
                if '/tasks/' in path and path.endswith('/status'):
                    return {'status':'stopped','exitstatus':'OK'}
                if path=='/api2/json/nodes/dstnode/qemu/321/config':
                    return {'onboot':0}
                raise AssertionError('Unexpected Proxmox call: '+path)
            monkeypatch.setattr(integrations,'proxmox_request',pve)
            endpoint='/api/dr/vms/'+str(vm_id)
            options=client.get(endpoint+'/pbs/restore-options',params={
                'backup_job_id':backup_id,'target_pbs_storage':'pbs-au'
            })
            assert options.status_code==200,options.text
            assert options.json()['archives'][0]['volid']==archive
            rejected=client.post(endpoint+'/pbs/restore',headers=h,json={
                'backup_job_id':backup_id,'target_pbs_storage':'pbs-au',
                'archive':archive,'approved':False
            })
            assert rejected.status_code==409
            restore=client.post(endpoint+'/pbs/restore',headers=h,json={
                'backup_job_id':backup_id,'target_pbs_storage':'pbs-au',
                'archive':archive,'approved':True
            })
            assert restore.status_code==200,restore.text
            assert restore.json()['will_start'] is False
            assert sum(x[0]=='POST' for x in calls)==1
            rid=restore.json()['restore_job_id']
            check=client.get(endpoint+'/pbs/restores/'+str(rid))
            assert check.status_code==200,check.text
            assert check.json()['restored'] is True
            assert check.json()['failover_ready'] is False
            assert client.delete(endpoint,headers=h).status_code==422
            repeat=client.post(endpoint+'/pbs/restore',headers=h,json={
                'backup_job_id':backup_id,'target_pbs_storage':'pbs-au',
                'archive':archive,'approved':True
            })
            assert repeat.status_code==409
            assert sum(x[0]=='POST' for x in calls)==1
        finally:
            # The test simulated an actual restored target and the delete guard
            # deliberately requires an operator clearance; only test DB cleanup.
            if vm_id is not None:
                with db.session() as con:
                    con.execute('DELETE FROM pra_pbs_restores WHERE protected_vm_id=?',(vm_id,))
                    con.execute('DELETE FROM dr_protected_vms WHERE id=?',(vm_id,))
            cleanup(client,h,[srcsite,dstsite],[source,target],[pid])


def test_restore_endpoints_require_admin():
    with TestClient(app) as client:
        assert client.post('/api/dr/vms/1/pbs/restore',json={}).status_code in (401,403)
        assert client.get('/api/dr/vms/1/pbs/restores').status_code==401
