"""PRA VM selection and simulations: strict per-plan isolation and no VM writes."""
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app import dr_protected_vms, db, integrations, security
from app.main import app


def login(client):
    response=client.post('/api/login',json={
        'username':'admin','password':'test-admin-password-over-12'})
    assert response.status_code==200,response.text
    return {'X-CSRF-Token':client.cookies['origami_csrf']}


def dc(client,h,name):
    result=client.post('/api/datacenters',headers=h,json={
        'name':name,'kind':'cluster','url':'https://'+name.lower()+'.example.org:8006',
        'token_username':'tester@pve!ci','token_secret':'unprivileged-test-secret'
    })
    assert result.status_code==200,result.text
    return result.json()['id']


def plan(client,h,name):
    r=client.post('/api/dr/plans',headers=h,json={'name':name,'description':'Isolation'})
    assert r.status_code==200,r.text
    return r.json()['id']


def site(client,h,name,plan_id,dc_id,role):
    r=client.post('/api/dr/sites',headers=h,json={
        'name':name,'plan_id':plan_id,'role':role,'storage_profile':'iscsi_lvm',
        'datacenter_ids':[dc_id],'agent_ids':[]
    })
    assert r.status_code==200,r.text
    return r.json()['id']


def fake_pve(monkeypatch,source_id,target_id):
    src=[
        {'type':'qemu','vmid':210,'name':'vm-ci-database',
         'node':'srcnode','status':'stopped'},
        {'type':'qemu','vmid':211,'name':'vm-ci-live','node':'srcnode','status':'running'},
        {'type':'node','node':'srcnode','status':'online'}
    ]
    dst=[
        {'type':'node','node':'dstnode','status':'online'},
        {'type':'qemu','vmid':300,'name':'other-real-vm','node':'dstnode','status':'stopped'}
    ]
    def inventory(dc_id):
        if dc_id not in (source_id,target_id):
            raise AssertionError('Wrong cluster outside authorized DR group')
        return ({'id':dc_id,'name':'ci-source' if dc_id==source_id else 'ci-target',
                 'url':'https://mock.example.org:8006'},
                {'username':'ci@pve!test','token':'dummy'},
                src if dc_id==source_id else dst)
    monkeypatch.setattr(dr_protected_vms,'_inventory',inventory)
    monkeypatch.setattr(dr_protected_vms,'_storages',lambda dc,cred:[
        {'storage':'target-lvm','images':True,'content':'images,rootdir',
         'type':'lvm','shared':True}
    ])
    monkeypatch.setattr(integrations,'assess_vm_dr',lambda *args,**kwargs:{
        'disks':[{'slot':'scsi0','storage':'shared-lvm',
                  'storage_type':'lvm','shared':True},
                 {'slot':'scsi1','storage':'shared-lvm',
                  'storage_type':'lvm','shared':True}]
    })
    # The entire VM lifecycle is deliberately read-only: no PVE mutations.
    monkeypatch.setattr(integrations,'proxmox_request',lambda *args,**kwargs:
                        (_ for _ in ()).throw(AssertionError('No PVE writes or calls permitted')))


def protected_payload(plan_id,src,dst,vmid=210,target_vmid=321):
    return {'plan_id':plan_id,'source_dc_id':src,'target_dc_id':dst,
            'source_vmid':vmid,'target_vmid':target_vmid,
            'target_node':'dstnode','target_storage':'target-lvm',
            'engine':'offline_copy','interval_minutes':60}


def cleanup(client,h,sites,clusters,plans):
    for site_id in sites:
        r=client.delete('/api/dr/sites/'+str(site_id),headers=h)
        assert r.status_code==200,r.text
    for dcid in clusters:
        dc_obj=next(x for x in client.get('/api/datacenters').json()['items']
                    if x['id']==dcid)
        assert client.delete('/api/datacenters/'+str(dcid),headers=h).status_code==200
        assert client.delete('/api/credentials/'+str(dc_obj['credential_id']),headers=h).status_code==200
    for plan_id in plans:
        assert client.delete('/api/dr/plans/'+str(plan_id),headers=h).status_code==200


def test_protected_vm_create_dry_run_failover_no_vm_actions(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        pid=plan(client,h,'CI protection 0321')
        source=dc(client,h,'cisource0321')
        target=dc(client,h,'citarget0321')
        srcsite=site(client,h,'CI primary 0321',pid,source,'primary')
        dstsite=site(client,h,'CI standby 0321',pid,target,'standby')
        fake_pve(monkeypatch,source,target)
        vid=None
        try:
            options=client.get('/api/dr/vms/options',params={
                'plan_id':pid,'source_dc_id':source,'target_dc_id':target
            })
            assert options.status_code==200,options.text
            assert len(options.json()['vms'])==2
            assert options.json()['target_storages'][0]['images'] is True
            created=client.post('/api/dr/vms',headers=h,
                                json=protected_payload(pid,source,target))
            assert created.status_code==200,created.text
            payload=created.json();vid=payload['id']
            assert payload['replica_name']=='REP_vm-ci-database'
            assert payload['disk_copy_performed'] is False
            assert payload['vm_created'] is False
            items=client.get('/api/dr/vms').json()['items']
            vm=next(x for x in items if x['id']==vid)
            assert vm['source_storage']=='shared-lvm'
            assert vm['source_site_id']==srcsite
            assert vm['target_site_id']==dstsite
            assert vm['replication_status']=='not_configured'
            assert vm['last_successful_sync'] is None
            assert vm['failover_available'] is False
            assert client.post('/api/dr/vms',headers=h,
                               json=protected_payload(pid,source,target)).status_code==422
            assert client.post('/api/dr/vms',headers=h,
                               json=protected_payload(pid,source,target,
                                                      vmid=211,target_vmid=321)).status_code==422
            assert client.post('/api/dr/vms',headers=h,
                               json=protected_payload(pid,source,target,
                                                      vmid=211,target_vmid=300)).status_code==422
            # All operations remain within the DR plan and are audit logged.
            for mode in ('replication_preflight','failover_simulation',
                         'network_partition','console_down'):
                test=client.post('/api/dr/vms/'+str(vid)+'/simulate',
                                 headers=h,json={'scenario':mode})
                assert test.status_code==200,test.text
                result=test.json()
                assert result['decision']=='BLOCKED'
                assert result['mode']=='dry_run_no_infrastructure_writes'
                assert result['disk_copy_performed'] is False
                assert result['replica_created'] is False
                assert result['target_vm_started'] is False
                assert result['source_vm_stopped'] is False
                assert result['would_modify_infrastructure'] is False
                assert result['last_successful_sync'] is None
                assert any(c['name']=='Fencing source' and not c['pass']
                           for c in result['checks'])
            history=client.get('/api/dr/vms/'+str(vid)+'/tests').json()['items']
            assert len(history)==4
            whole_site=client.post('/api/dr/simulate',headers=h,json={
                'source_site_id':srcsite,'target_site_id':dstsite,
                'scenario':'cluster_down'
            })
            assert whole_site.status_code==200,whole_site.text
            site_result=whole_site.json()
            assert site_result['decision']=='BLOCKED'
            assert site_result['protected_vms'][0]['id']==vid
            assert site_result['protected_vms'][0]['replica_name']=='REP_vm-ci-database'
            assert site_result['protected_vms'][0]['last_successful_sync'] is None
            assert client.post('/api/dr/vms/'+str(vid)+'/replicate',
                               headers=h).status_code==409
            assert client.post('/api/dr/vms/'+str(vid)+'/state',
                               headers=h,json={'state':'paused'}).status_code==200
            paused=client.post('/api/dr/vms/'+str(vid)+'/simulate',
                headers=h,json={'scenario':'replication_preflight'})
            assert any(x['name']=='Protection active dans l\'inventaire' and not x['pass']
                       for x in paused.json()['checks'])
            # Can't delete or move a site with protected guests.
            assert client.delete('/api/dr/sites/'+str(dstsite),headers=h).status_code!=200
            invalid=client.put('/api/dr/sites/'+str(srcsite),headers=h,json={
                'name':'CI primary 0321','plan_id':pid,'role':'standby',
                'storage_profile':'auto','datacenter_ids':[source],'agent_ids':[]
            })
            assert invalid.status_code==422
            removed=client.delete('/api/dr/vms/'+str(vid),headers=h)
            assert removed.status_code==200,removed.text
            assert removed.json()['vm_deleted'] is False
            vid=None
            assert client.get('/api/dr/vms').json()['items']==[]
        finally:
            if vid is not None:
                client.delete('/api/dr/vms/'+str(vid),headers=h)
            cleanup(client,h,[srcsite,dstsite],[source,target],[pid])


def test_other_plan_target_rejected_before_pve_lookup(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        a=plan(client,h,'CI A 0321')
        b=plan(client,h,'CI B 0321')
        dc_a=dc(client,h,'cisourceextra0321')
        dc_b=dc(client,h,'citargetextra0321')
        a_site=site(client,h,'CI A source 0321',a,dc_a,'primary')
        b_site=site(client,h,'CI B standby 0321',b,dc_b,'standby')
        def forbidden(dc_id):
            raise AssertionError('Must reject cross-plan before making a PVE call')
        monkeypatch.setattr(dr_protected_vms,'_inventory',forbidden)
        try:
            response=client.post('/api/dr/vms',headers=h,
                                 json=protected_payload(a,dc_a,dc_b))
            assert response.status_code==422,response.text
            assert 'même groupe PRA' in response.text
            assert client.get('/api/dr/vms',params={'plan_id':b}).json()['items']==[]
        finally:
            cleanup(client,h,[a_site,b_site],[dc_a,dc_b],[a,b])


def test_vm_protection_api_requires_admin():
    with TestClient(app) as client:
        assert client.get('/api/dr/vms').status_code==401
        assert client.post('/api/dr/vms',json={}).status_code in (401,403)
        assert client.post('/api/dr/vms/1/simulate',
                           json={'scenario':'failover_simulation'}).status_code in (401,403)


def test_pra_group_rename_is_visible_after_commit():
    with TestClient(app) as client:
        h=login(client)
        pid=plan(client,h,'CI old group 0321')
        try:
            updated=client.put('/api/dr/plans/'+str(pid),headers=h,json={
                'name':'CI renamed group 0321','description':'New description'
            })
            assert updated.status_code==200,updated.text
            assert updated.json()['plan']['name']=='CI renamed group 0321'
            fresh=client.get('/api/dr/plans').json()['items']
            assert next(x for x in fresh if x['id']==pid)['name']=='CI renamed group 0321'
            assert next(x for x in fresh if x['id']==pid)['description']=='New description'
        finally:
            assert client.delete('/api/dr/plans/'+str(pid),headers=h).status_code==200


def test_frontend_has_manual_protection_state_colors_and_refresh_controls():
    root=Path(__file__).resolve().parents[1]
    src=(root/'static/app.js').read_text()
    css=(root/'static/styles.css').read_text()
    nav=(root/'static/index.html').read_text()
    for needle in (
        'function protectedVMsPage()', 'function openProtectedVMWizard(',
        'function openProtectedVMConfig(', 'function showProtectedVMTest(',
        'function openProtectedVMHistory(', "state.drProtectedVMs=(await api('/dr/vms')).items",
        'agent-status-refresh','agents-status-refresh',
        'data-page="protected-vms"', 'data-action="dr-vm-preflight"',
        'data-action="dr-vm-failover"','dr-plans-refresh',
        'cache:\'no-store\'',
    ):
        assert needle in src,(needle,'absent')
    for needle in ('.status.online','.status.offline','.status.running',
                   '.status.completed_with_errors','.status.BLOCKED'):
        assert needle in css
    assert 'data-page="protected-vms"' in nav
    assert 'mode PRA' not in src.lower() or 'préparation' in src.lower()
