from app import config, engine, executor, security, db


def test_conditions_and_branches():
    steps=[
      {'id':'one','name':'Start','type':'shell','config':{},'on_success':'second','on_failure':'FAIL'},
      {'id':'second','name':'Check','type':'shell','config':{},'on_success':'END','on_failure':'recovery'},
      {'id':'recovery','name':'Recovery','type':'shell','config':{},'on_success':'END','on_failure':'FAIL'},
    ]
    results=[]
    def runner(step,ctx):
        results.append(step['id'])
        code=1 if step['id']=='second' else 0
        return {'status':'failed' if code else 'success','exit_code':code,'output':step['id']}
    db.initialize()
    with db.session() as con:
        con.execute("INSERT INTO runs (id,job_id,job_name,steps_snapshot,status,trigger,requested_by,queued_at) VALUES (?,?,?,?,?,?,?,?)",(981,None,'Test','[]','running','manual','test',db.now()))
    status,error=engine.run_graph(steps,981,runner)
    assert status=='success'
    assert results==['one','second','recovery']
    with db.session() as con:
        assert con.execute('SELECT COUNT(*) FROM step_runs WHERE run_id=981').fetchone()[0]==3


def test_skip_condition():
    steps=[
      {'id':'a','name':'A','type':'shell','config':{},'on_success':'b'},
      {'id':'b','name':'B','type':'shell','config':{},'condition':{'source':'last_exit_code','operator':'equals','value':3},'on_success':'END'}]
    visited=[]
    def runner(s,ctx):visited.append(s['id']);return {'status':'success','exit_code':0,'output':'ok'}
    with db.session() as con:
        con.execute("INSERT INTO runs (id,job_name,steps_snapshot,status,trigger,requested_by,queued_at) VALUES (?,?,?,?,?,?,?)",(982,'Test','[]','running','manual','test',db.now()))
    result,_=engine.run_graph(steps,982,runner)
    assert result=='success' and visited==['a']


def test_vault_roundtrip_and_no_leak():
    original={'username':'service','password':'password123456','token':'abcdef123456'}
    encrypted=security.encrypt(original)
    assert 'password123456' not in encrypted
    assert security.decrypt(encrypted)==original
    assert 'password123456' not in executor.scrub('secret password123456 shown',original)


def test_job_permission_groups():
    job={'acl_json':{'view':['dev'], 'run':['ops'],'edit':['platform']}}
    user={'role':'operator','groups_json':['ops']}
    assert security.can_job(user,job,'view')
    assert security.can_job(user,job,'run')
    assert not security.can_job(user,job,'edit')
    assert security.can_job({'role':'admin','groups_json':[]},job,'edit')
