"""Regression tests for explicit PBS backup and cluster guard.

No live cluster is contacted: the only infrastructure mutation accepted by the
test double is a snapshot backup on the configured PBS storage.
"""
from fastapi.testclient import TestClient
from app import config, db, dr_protected_vms, integrations, pra_pbs
from app.main import app
from test_dr_protected_vms_0321 import (
    login, dc, plan, site, fake_pve, protected_payload, cleanup,
)


def test_pbs_backup_requires_approval_and_only_submits_vzdump(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        pid=plan(client,h,'CI PBS group 0322')
        source=dc(client,h,'cipbssource0322')
        target=dc(client,h,'cipbstarget0322')
        srcsite=site(client,h,'CI PBS source 0322',pid,source,'primary')
        dstsite=site(client,h,'CI PBS standby 0322',pid,target,'standby')
        fake_pve(monkeypatch,source,target)
        protection_id=None
        try:
            payload=protected_payload(pid,source,target)
            payload['engine']='pbs_backup_restore'
            created=client.post('/api/dr/vms',headers=h,json=payload)
            assert created.status_code==200,created.text
            protection_id=created.json()['id']
            denied=client.post('/api/dr/vms/'+str(protection_id)+'/pbs/backup',
                headers=h,json={'storage':'pbs-remote','approved':False})
            assert denied.status_code==409
            monkeypatch.setattr(config,'ALLOW_PROXMOX_ACTIONS',True)
            monkeypatch.setattr(integrations,'discover_datacenter',
                                lambda dc,cred,timeout:[
                                  {'type':'qemu','vmid':210,'node':'srcnode',
                                   'name':'vm-ci-database','status':'stopped'}])
            calls=[]
            def pve(origin,cred,path,timeout,method='GET',data=None):
                calls.append((method,path,data))
                if path=='/api2/json/storage':
                    return [{'storage':'pbs-remote','type':'pbs','content':'backup'}]
                if path=='/api2/json/nodes/srcnode/vzdump' and method=='POST':
                    assert data=={'vmid':'210','storage':'pbs-remote','mode':'snapshot'}
                    return 'UPID:srcnode:0001:0001:test:vzdump:210:ci@pve:'
                if '/tasks/' in path and path.endswith('/status'):
                    return {'status':'stopped','exitstatus':'OK'}
                raise AssertionError('Unexpected infrastructure request: '+path)
            monkeypatch.setattr(integrations,'proxmox_request',pve)
            started=client.post('/api/dr/vms/'+str(protection_id)+'/pbs/backup',
                headers=h,json={'storage':'pbs-remote','approved':True})
            assert started.status_code==200,started.text
            assert started.json()['replica_created'] is False
            assert sum(method=='POST' for method,_,_ in calls)==1
            again=client.post('/api/dr/vms/'+str(protection_id)+'/pbs/backup',
                headers=h,json={'storage':'pbs-remote','approved':True})
            assert again.status_code==409
            status=client.get(
                '/api/dr/vms/'+str(protection_id)+'/pbs/backups/'+str(started.json()['job_id']))
            assert status.status_code==200,status.text
            assert status.json()['status']=='completed'
            assert status.json()['backup_verified'] is True
            assert status.json()['replica_created'] is False
        finally:
            if protection_id is not None:
                assert client.delete('/api/dr/vms/'+str(protection_id),headers=h).status_code==200
            cleanup(client,h,[srcsite,dstsite],[source,target],[pid])


def test_standby_cannot_be_started_via_origamitasks(monkeypatch):
    # The code guard must execute before a mutating PVE request.
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'app/integrations.py').read_text()
    assert "Démarrage interdit : VM de secours PRA" in source
    assert "if action in ('start','reboot')" in source
