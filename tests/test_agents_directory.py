"""Preview agent API integration tests: no real remote infrastructure required."""
import threading
import time
from fastapi.testclient import TestClient
from app import agents, directory, db
from app.main import app


def test_agent_enroll_poll_report_and_revoke():
    with TestClient(app) as client:
        assert client.post('/api/login',json={'username':'admin','password':'test-admin-password-over-12'}).status_code == 200
        csrf={'X-CSRF-Token':client.cookies['origami_csrf']}
        assert client.post('/api/agents/enrollments',json={'name':'lab1','platform':'linux'}).status_code == 403
        reg=client.post('/api/agents/enrollments',json={'name':'lab1','platform':'linux','group':'lab'},headers=csrf)
        assert reg.status_code == 200,reg.text
        info=reg.json()
        enroll={'agent_id':info['id'],'enrollment_token':info['enrollment_token'],'platform':'linux','hostname':'ci-agent'}
        bad={**enroll,'enrollment_token':'x'*45}
        assert client.post('/api/agent/enroll',json=bad).status_code == 401
        new=client.post('/api/agent/enroll',json=enroll)
        assert new.status_code == 200,new.text
        token=new.json()['token']
        assert client.post('/api/agent/enroll',json=enroll).status_code == 401
        headers={'Authorization':'Bearer '+token,'X-Origami-Agent-ID':str(info['id'])}
        assert client.post('/api/agent/poll',json={'hostname':'ci-agent','version':'0.3.0'},headers=headers).json()['task'] is None
        with db.session() as con:
            run_id=con.execute("INSERT INTO runs(job_name,steps_snapshot,status,trigger,requested_by,queued_at) VALUES(?,?,?,?,?,?)",
                ('agent-test','[]','running','manual','pytest',db.now())).lastrowid
        result=[]
        def work():
            result.append(agents.execute({'agent_id':info['id'],'action':'inventory'},{'run_id':run_id,'step_id':'check'},5))
        thread=threading.Thread(target=work)
        thread.start()
        task=None
        for attempt in range(40):
            response=client.post('/api/agent/poll',json={'hostname':'ci-agent','version':'0.3.0'},headers=headers)
            assert response.status_code==200,response.text
            task=response.json()['task']
            if task:break
            time.sleep(.05)
        assert task and task['action']=='inventory'
        assert client.post('/api/agent/report',json={'task_id':task['id'],'lease':'invalid','exit_code':0,'output':'ok'},headers=headers).status_code == 401
        posted=client.post('/api/agent/report',json={'task_id':task['id'],'lease':task['lease'],'exit_code':0,'output':'inventory-test'},headers=headers)
        assert posted.status_code == 200,posted.text
        thread.join(7)
        assert not thread.is_alive() and result and result[0]['output']=='inventory-test'
        assert client.post('/api/agent/report',json={'task_id':task['id'],'lease':task['lease'],'exit_code':0,'output':'again'},headers=headers).status_code == 409
        assert client.post('/api/agents/'+str(info['id'])+'/revoke',headers=csrf).status_code == 200
        assert client.post('/api/agent/poll',json={'hostname':'ci-agent'},headers=headers).status_code == 401


def test_agent_script_service_and_deployment_disabled():
    from app import config
    assert not config.ALLOW_AGENT_SCRIPTS
    assert not config.ALLOW_AGENT_MUTATIONS
    assert not config.ALLOW_AGENT_REMOTE_DEPLOY
    for action,opts in [('script',{'script':'echo ok'}),('service_restart',{'service':'sshd','approved':True})]:
        try:
            agents.execute(dict(agent_id=999,action=action,**opts),{'run_id':123,'step_id':'step'},5)
        except ValueError:
            pass
        else:
            raise AssertionError(action+' must require explicit policy flag')
    with TestClient(app) as client:
        client.post('/api/login',json={'username':'admin','password':'test-admin-password-over-12'})
        headers={'X-CSRF-Token':client.cookies['origami_csrf']}
        r=client.post('/api/agents/deploy',headers=headers,json={'platform':'linux','host':'target.invalid','approved':True})
        assert r.status_code==403


def test_directory_dn_scope_and_write_gate():
    assert directory.dn_within('CN=Alice,OU=IT,DC=example,DC=org','DC=example,DC=org')
    assert not directory.dn_within('CN=Alice,DC=malicious,DC=org','DC=example,DC=org')
    try:
        directory.execute({'action':'disable_user','approved':True},{})
    except directory.DirectoryError as exc:
        assert 'ALLOW_DIRECTORY_WRITES' in str(exc)
    else:
        raise AssertionError('Directory write must be disabled by default')
