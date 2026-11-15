import time
from fastapi.testclient import TestClient
from app.main import app


def test_login_create_run_and_view_logs():
    with TestClient(app) as client:
        r=client.post('/api/login',json={'username':'admin','password':'test-admin-password-over-12'})
        assert r.status_code==200,r.text
        csrf=client.cookies['origami_csrf']
        headers={'X-CSRF-Token':csrf}
        assert client.get('/api/me').status_code==200
        assert client.get('/api/summary').json()['jobs']==0
        no_csrf=client.post('/api/jobs',json={'name':'Fail'})
        assert no_csrf.status_code==403
        job={'name':'Workflow test','description':'Test','folder':'Intégration',
            'tags_json':['pytest','linux'],'acl_json':{'view':[],'run':[],'edit':[]},
            'steps_json':[{'id':'hello','name':'Hello','type':'shell','config':{'script':'echo hello-origami','timeout':5},'retries':0,'on_success':'END','on_failure':'FAIL'}],
            'schedule_json':{},'enabled':True}
        created=client.post('/api/jobs',json=job,headers=headers)
        assert created.status_code==200,created.text
        job_id=created.json()['id']
        assert client.get('/api/jobs').json()['items'][0]['tags_json']==['pytest','linux']
        started=client.post(f'/api/jobs/{job_id}/run',headers=headers)
        assert started.status_code==200,started.text
        run_id=started.json()['run_id']
        for i in range(30):
            info=client.get(f'/api/runs/{run_id}').json()
            if info['status'] not in ('running','queued'):break
            time.sleep(.2)
        assert info['status']=='success',info
        assert info['steps'][0]['output']=='hello-origami'
        delete=client.delete(f'/api/jobs/{job_id}',headers=headers)
        assert delete.status_code==200
        assert client.get(f'/api/runs/{run_id}').status_code==200


def test_secret_never_returned():
    with TestClient(app) as client:
        assert client.post('/api/login',json={'username':'admin','password':'test-admin-password-over-12'}).status_code==200
        csrf=client.cookies['origami_csrf']
        r=client.post('/api/credentials',json={'name':'testing-api-secret','kind':'api','payload':{'token':'top-secret-string'}},headers={'X-CSRF-Token':csrf})
        assert r.status_code==200
        listing=client.get('/api/credentials').text
        assert 'top-secret-string' not in listing
        assert client.delete('/api/credentials/'+str(r.json()['id']),headers={'X-CSRF-Token':csrf}).status_code==200


def test_webhook_token_variables_and_revocation():
    with TestClient(app) as client:
        assert client.post('/api/login',json={'username':'admin','password':'test-admin-password-over-12'}).status_code==200
        headers={'X-CSRF-Token':client.cookies['origami_csrf']}
        job={'name':'Webhook pipeline','folder':'Events','tags_json':['webhook'],
            'steps_json':[
              {'id':'save','name':'Set','type':'set_variable','config':{'name':'input','value':'{{ var.ticket_id }}'},'on_success':'print'},
              {'id':'print','name':'Print','type':'shell','config':{'script':'printf "{{ var.input }}"'},'on_success':'END'}],
            'schedule_json':{},'enabled':True,'acl_json':{'view':[],'run':[],'edit':[]}}
        created=client.post('/api/jobs',json=job,headers=headers)
        assert created.status_code==200,created.text
        job_id=created.json()['id']
        endpoint=f'/api/hooks/{job_id}'
        assert client.post(endpoint).status_code==401
        rotated=client.post(f'/api/jobs/{job_id}/webhook',headers=headers)
        assert rotated.status_code==200
        token=rotated.json()['token']
        assert 'token' not in client.get(f'/api/jobs/{job_id}/webhook').text
        assert client.post(endpoint,headers={'X-Origami-Webhook-Token':'bad-token'}).status_code==401
        triggered=client.post(endpoint,headers={'X-Origami-Webhook-Token':token},json={'ticket_id':'42'})
        assert triggered.status_code==200,triggered.text
        run_id=triggered.json()['run_id']
        for i in range(40):
            info=client.get(f'/api/runs/{run_id}').json()
            if info['status'] not in ('running','queued'):break
            time.sleep(.2)
        assert info['status']=='success',info
        assert info['steps'][-1]['output']=='42'
        assert client.delete(f'/api/jobs/{job_id}/webhook',headers=headers).status_code==200
        assert client.post(endpoint,headers={'X-Origami-Webhook-Token':token}).status_code==401


def test_operator_cannot_grant_credential_or_modify_acl():
    from app import db, security
    with TestClient(app) as admin:
        assert admin.post('/api/login',json={'username':'admin','password':'test-admin-password-over-12'}).status_code==200
        aheaders={'X-CSRF-Token':admin.cookies['origami_csrf']}
        with db.session() as con:
            con.execute("INSERT OR IGNORE INTO users(username,display_name,password_hash,source,role,groups_json,created_at) VALUES (?,?,?,?,?,?,?)",
                        ('operator-ci','Opérateur test',security.ph.hash('strong-pass-for-operator-123'),'local','operator','["teamops"]',db.now()))
        cred=admin.post('/api/credentials',json={'name':'ops-ssh-demo','kind':'ssh','payload':{'username':'machine','password':'secret-example'}},headers=aheaders)
        assert cred.status_code==200
        cred_id=cred.json()['id']
        j={'name':'ACL test','description':'','folder':'Ops','tags_json':[],
           'acl_json':{'view':[],'run':[],'edit':['teamops']},
           'schedule_json':{},'enabled':True,
           'steps_json':[{'id':'a','name':'SSH','type':'ssh','config':{'host':'host.local','script':'echo ok','credential_id':cred_id}}]}
        created=admin.post('/api/jobs',json=j,headers=aheaders)
        assert created.status_code==200,created.text
        job_id=created.json()['id']
        with TestClient(app) as operator:
            assert operator.post('/api/login',json={'username':'operator-ci','password':'strong-pass-for-operator-123'}).status_code==200
            h={'X-CSRF-Token':operator.cookies['origami_csrf']}
            assert any(item['id']==job_id for item in operator.get('/api/jobs').json()['items'])
            assert operator.get('/api/credentials').status_code==403
            j['acl_json']={'view':['everyone'],'run':[],'edit':['teamops']}
            j['steps_json'][0]['config']['credential_id']=99999
            r=operator.put(f'/api/jobs/{job_id}',json=j,headers=h)
            assert r.status_code==403
            j['steps_json'][0]['config']['credential_id']=cred_id
            assert operator.put(f'/api/jobs/{job_id}',json=j,headers=h).status_code==200
            actual=admin.get(f'/api/jobs/{job_id}').json()
            assert actual['acl_json']['view']==[]
