"""OrigamiTasks — single-node Linux control plane / API / scheduler."""
import json
import re
import secrets
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import config, db, engine, scheduler, security


@asynccontextmanager
async def lifespan(app):
    if not config.VAULT_KEY:
        raise RuntimeError('VAULT_KEY manquant — utiliser ops/bootstrap.sh')
    security.cipher()  # validation before accepting requests
    db.initialize()
    security.initialize_admin()
    engine.start()
    scheduler.start()
    yield
    scheduler.STOP.set()
    engine.STOP.set()


app = FastAPI(title='OrigamiTasks', version=config.APP_VERSION, docs_url=None, redoc_url=None, lifespan=lifespan)
STATIC = Path(__file__).resolve().parent.parent / 'static'
app.mount('/assets', StaticFiles(directory=STATIC), name='assets')


@app.middleware('http')
async def browser_security(request, call_next):
    if request.headers.get('content-length') and int(request.headers['content-length']) > 2_000_000:
        return JSONResponse({'detail':'Corps de requête trop volumineux'},status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api') else 'private, max-age=0'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.get('/')
def frontend():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/health')
def health():
    return {'status':'ok','name':'OrigamiTasks','version':config.APP_VERSION}


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000)


FAILED_LOGINS = {}


@app.post('/api/login')
def login(payload: Login, request: Request, response: Response):
    import time
    ip = request.client.host if request.client else 'unknown'
    times = [x for x in FAILED_LOGINS.get(ip,[]) if time.monotonic()-x < 600]
    if len(times) >= 7:
        raise HTTPException(429, 'Trop de tentatives. Réessayez dans 10 minutes')
    user = security.authenticate(payload.username,payload.password)
    if not user:
        times.append(time.monotonic())
        FAILED_LOGINS[ip] = times
        db.audit(payload.username,'auth.failed','login')
        raise HTTPException(401,'Identifiants incorrects ou groupe non autorisé')
    FAILED_LOGINS.pop(ip,None)
    sid,csrf = security.create_session(user)
    response.set_cookie('origami_session',sid,max_age=config.SESSION_HOURS*3600,httponly=True,
                        secure=config.COOKIE_SECURE,samesite='strict',path='/')
    response.set_cookie('origami_csrf',csrf,max_age=config.SESSION_HOURS*3600,httponly=False,
                        secure=config.COOKIE_SECURE,samesite='strict',path='/')
    db.audit(user['username'],'auth.login','login')
    return {'user':security.present_user(user)}


@app.get('/api/me')
def me(request: Request):
    return {'user':security.present_user(security.require(request))}


@app.post('/api/logout')
def logout(request: Request, response: Response):
    user = security.require(request,mutate=True)
    sid = request.cookies.get('origami_session','')
    with db.session() as con:
        con.execute('DELETE FROM sessions WHERE sid_hash=?',(security.sha(sid),))
    response.delete_cookie('origami_session',path='/')
    response.delete_cookie('origami_csrf',path='/')
    db.audit(user['username'],'auth.logout','logout')
    return {'ok':True}


VALID_TYPES = {'shell','powershell','ssh','sftp','ftp','email','http','delay','set_variable'}


def validate_job(payload):
    name = str(payload.get('name','')).strip()
    if not 1 <= len(name) <= 120:
        raise HTTPException(422,'Nom obligatoire (120 caractères maximum)')
    steps = payload.get('steps_json',[])
    if not isinstance(steps,list) or len(steps)>80:
        raise HTTPException(422,'Étapes invalides (80 maximum)')
    ids = set()
    for step in steps:
        if not isinstance(step,dict):
            raise HTTPException(422,'Étape invalide')
        sid = str(step.get('id',''))
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}',sid) or sid in ids:
            raise HTTPException(422,'ID d’étape invalide ou dupliqué')
        ids.add(sid)
        if step.get('type') not in VALID_TYPES:
            raise HTTPException(422,'Type d’étape inconnu')
        if not isinstance(step.get('config'),dict) or len(json.dumps(step['config']))>50_000:
            raise HTTPException(422,'Configuration d’étape invalide')
        if not 1 <= len(str(step.get('name',''))) <= 120:
            raise HTTPException(422,'Nom d’étape invalide')
        if int(step.get('retries',0)) not in range(0,6):
            raise HTTPException(422,'5 tentatives de reprise maximum')
    for step in steps:
        for edge in ('on_success','on_failure'):
            value=step.get(edge)
            if value and value not in ids|{'END','FAIL'}:
                raise HTTPException(422,f'Branche {edge} : étape inconnue {value}')
    acl = payload.get('acl_json',{})
    if not isinstance(acl,dict) or any(not isinstance(acl.get(x,[]),list) or len(acl.get(x,[]))>50 for x in ('view','run','edit')):
        raise HTTPException(422,'ACL invalides')
    schedule = payload.get('schedule_json',{})
    if not isinstance(schedule,dict):
        raise HTTPException(422,'Planification invalide')
    try:
        next_run = scheduler.check_schedule(schedule) if payload.get('enabled',True) else None
    except (ValueError,KeyError) as exc:
        raise HTTPException(422,'Planification: '+str(exc))
    return {
        'name':name,
        'description':str(payload.get('description',''))[:2000],
        'folder':str(payload.get('folder','Général')).strip()[:100] or 'Général',
        'tags_json':json.dumps([str(x).strip()[:40] for x in payload.get('tags_json',[]) if str(x).strip()][:20],ensure_ascii=False),
        'acl_json':json.dumps({k:[str(g).strip().lower()[:120] for g in acl.get(k,[]) if str(g).strip()] for k in ('view','run','edit')},ensure_ascii=False),
        'steps_json':json.dumps(steps,ensure_ascii=False),
        'schedule_json':json.dumps(schedule,ensure_ascii=False),
        'enabled':int(bool(payload.get('enabled',True))),
        'next_run_at':next_run,
    }


def get_job(con,job_id):
    row=con.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row: raise HTTPException(404,'Workflow introuvable')
    return db.obj(row,'tags_json','acl_json','steps_json','schedule_json')


@app.get('/api/jobs')
def list_jobs(request: Request):
    user=security.require(request)
    with db.session() as con:
        rows=con.execute('SELECT * FROM jobs ORDER BY updated_at DESC,id DESC').fetchall()
    jobs=[db.obj(row,'tags_json','acl_json','steps_json','schedule_json') for row in rows]
    return {'items':[dict(j,permissions={'view':True,'run':security.can_job(user,j,'run'),'edit':security.can_job(user,j,'edit')}) for j in jobs if security.can_job(user,j,'view')]}


@app.get('/api/jobs/{job_id}')
def read_job(job_id: int, request: Request):
    user=security.require(request)
    with db.session() as con:
        job=get_job(con,job_id)
    if not security.can_job(user,job): raise HTTPException(403,'Accès refusé')
    return job


@app.post('/api/jobs')
def create_job(payload: dict[str,Any], request: Request):
    user=security.require(request,role='admin',mutate=True)
    fields=validate_job(payload)
    with db.session() as con:
        cur=con.execute('INSERT INTO jobs (name,description,folder,tags_json,acl_json,steps_json,schedule_json,enabled,next_run_at,created_by,created_at,updated_at) '
                        'VALUES (:name,:description,:folder,:tags_json,:acl_json,:steps_json,:schedule_json,:enabled,:next_run_at,:created_by,:created_at,:updated_at)',
                        dict(fields,created_by=user['username'],created_at=db.now(),updated_at=db.now()))
        id_=cur.lastrowid
    db.audit(user['username'],'job.create',id_,fields['name'])
    return {'id':id_}


@app.put('/api/jobs/{job_id}')
def update_job(job_id: int,payload:dict[str,Any],request: Request):
    user=security.require(request,role='operator',mutate=True)
    fields=validate_job(payload)
    with db.session() as con:
        old=get_job(con,job_id)
        if not security.can_job(user,old,'edit'): raise HTTPException(403,'Modification interdite')
        # Operators cannot grant new ACLs or edit existing ACLs, only admins.
        if user['role'] != 'admin':
            fields['acl_json']=json.dumps(old['acl_json'],ensure_ascii=False)
            existing_creds={step['id']:str(step.get('config',{}).get('credential_id') or '') for step in old['steps_json']}
            for step in payload['steps_json']:
                current=str(step.get('config',{}).get('credential_id') or '')
                if current != existing_creds.get(step['id'],''):
                    raise HTTPException(403,'Seul un administrateur peut attribuer ou changer un identifiant de coffre')
        con.execute('UPDATE jobs SET name=:name,description=:description,folder=:folder,tags_json=:tags_json,acl_json=:acl_json,steps_json=:steps_json,schedule_json=:schedule_json,enabled=:enabled,next_run_at=:next_run_at,updated_at=:updated_at WHERE id=:id',
                    dict(fields,id=job_id,updated_at=db.now()))
    db.audit(user['username'],'job.update',job_id,fields['name'])
    return {'ok':True}


@app.delete('/api/jobs/{job_id}')
def delete_job(job_id: int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    with db.session() as con:
        job=get_job(con,job_id)
        con.execute('DELETE FROM jobs WHERE id=?',(job_id,))
    db.audit(user['username'],'job.delete',job_id,job['name'])
    return {'ok':True}


@app.post('/api/jobs/{job_id}/run')
def run_job(job_id:int,request:Request):
    user=security.require(request,role='operator',mutate=True)
    with db.session() as con: job=get_job(con,job_id)
    if not security.can_job(user,job,'run'): raise HTTPException(403,'Exécution interdite')
    run_id=engine.enqueue(job,'manual',user['username'])
    db.audit(user['username'],'job.run',job_id,'Run #'+str(run_id))
    return {'run_id':run_id}


@app.get('/api/jobs/{job_id}/webhook')
def webhook_info(job_id:int, request:Request):
    user=security.require(request,role='admin')
    with db.session() as con:
        get_job(con,job_id)
        row=con.execute('SELECT created_at FROM webhooks WHERE job_id=?',(job_id,)).fetchone()
    return {'enabled':bool(row),'created_at':row['created_at'] if row else None}


@app.post('/api/jobs/{job_id}/webhook')
def webhook_rotate(job_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    token=secrets.token_urlsafe(40)
    with db.session() as con:
        get_job(con,job_id)
        con.execute('INSERT INTO webhooks(job_id,token_hash,created_at) VALUES(?,?,?) '
                    'ON CONFLICT(job_id) DO UPDATE SET token_hash=excluded.token_hash,created_at=excluded.created_at',
                    (job_id,security.sha(token),db.now()))
    db.audit(user['username'],'webhook.rotate',job_id)
    return {'token':token,'path':f'/api/hooks/{job_id}'}  # shown only once


@app.delete('/api/jobs/{job_id}/webhook')
def webhook_disable(job_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    with db.session() as con:
        get_job(con,job_id)
        con.execute('DELETE FROM webhooks WHERE job_id=?',(job_id,))
    db.audit(user['username'],'webhook.disable',job_id)
    return {'ok':True}


@app.post('/api/hooks/{job_id}')
async def webhook_trigger(job_id:int,request:Request):
    token=request.headers.get('X-Origami-Webhook-Token','')
    if len(token)<24 or len(token)>200:
        raise HTTPException(401,'Token webhook manquant')
    with db.session() as con:
        row=con.execute('SELECT token_hash FROM webhooks WHERE job_id=?',(job_id,)).fetchone()
        if not row or not hmac.compare_digest(security.sha(token),row['token_hash']):
            raise HTTPException(401,'Token webhook invalide')
        job=get_job(con,job_id)
    if not job['enabled']:
        raise HTTPException(409,'Workflow désactivé')
    variables={}
    if request.headers.get('content-type','').split(';')[0]=='application/json':
        try:
            raw=await request.json()
            if isinstance(raw,dict):
                variables={str(k)[:64]:str(v)[:2000] for k,v in raw.items() if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',str(k))}
                if len(variables)>50:raise HTTPException(422,'50 variables webhook maximum')
        except ValueError:
            raise HTTPException(422,'JSON invalide')
    run_id=engine.enqueue(job,'webhook','webhook',variables)
    db.audit('webhook','job.trigger',job_id,str(run_id))
    return {'run_id':run_id,'status':'queued'}


@app.get('/api/runs')
def list_runs(request:Request):
    user=security.require(request)
    with db.session() as con:
        jobs={r['id']:db.obj(r,'acl_json') for r in con.execute('SELECT id,acl_json FROM jobs')}
        rows=con.execute('SELECT id,job_id,job_name,status,trigger,requested_by,queued_at,started_at,finished_at,error FROM runs ORDER BY id DESC LIMIT 200').fetchall()
    return {'items':[dict(r) for r in rows if user['role']=='admin' or (r['job_id'] in jobs and security.can_job(user,jobs[r['job_id']],'view'))]}


@app.get('/api/runs/{run_id}')
def read_run(run_id:int,request:Request):
    user=security.require(request)
    with db.session() as con:
        run=con.execute('SELECT id,job_id,job_name,status,trigger,requested_by,queued_at,started_at,finished_at,error FROM runs WHERE id=?',(run_id,)).fetchone()
        if not run: raise HTTPException(404,'Exécution introuvable')
        job=con.execute('SELECT id,acl_json FROM jobs WHERE id=?',(run['job_id'],)).fetchone()
        if user['role']!='admin' and (not job or not security.can_job(user,db.obj(job,'acl_json'),'view')):
            raise HTTPException(403,'Accès refusé')
        steps=con.execute('SELECT id,step_id,name,type,status,attempt,exit_code,output,started_at,finished_at FROM step_runs WHERE run_id=? ORDER BY id',(run_id,)).fetchall()
    return dict(run,steps=[dict(s) for s in steps])


@app.get('/api/summary')
def summary(request:Request):
    user=security.require(request)
    with db.session() as con:
        jobs=[db.obj(x,'acl_json') for x in con.execute('SELECT id,acl_json,enabled FROM jobs')]
        visible={x['id'] for x in jobs if security.can_job(user,x,'view')}
        r=con.execute('SELECT job_id,status FROM runs ORDER BY id DESC LIMIT 500').fetchall()
    records=[x for x in r if x['job_id'] in visible]
    return {'jobs':len(visible),'enabled':sum(1 for j in jobs if j['id'] in visible and j['enabled']),
            'running':sum(x['status'] in ('running','queued') for x in records),
            'success':sum(x['status']=='success' for x in records[:100]),
            'failed':sum(x['status'] in ('failed','interrupted') for x in records[:100])}


@app.get('/api/credentials')
def list_credentials(request:Request):
    security.require(request,role='admin')
    with db.session() as con:
        items=con.execute('SELECT id,name,kind,created_at,updated_at FROM credentials ORDER BY name').fetchall()
    return {'items':[dict(r) for r in items]}


@app.post('/api/credentials')
def create_credential(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    name=str(payload.get('name','')).strip()
    fields=payload.get('payload',{})
    if not name or len(name)>120 or not isinstance(fields,dict) or len(json.dumps(fields))>30000:
        raise HTTPException(422,'Coffre invalide')
    if not fields or not any(str(x) for x in fields.values()):
        raise HTTPException(422,'Au moins une valeur de secret requise')
    encrypted=security.encrypt({str(k):str(v) for k,v in fields.items() if v is not None})
    with db.session() as con:
        try:
            cur=con.execute('INSERT INTO credentials (name,kind,encrypted_payload,created_at,updated_at) VALUES (?,?,?,?,?)',
                            (name,str(payload.get('kind','generic'))[:30],encrypted,db.now(),db.now()))
        except db.sqlite3.IntegrityError:
            raise HTTPException(409,'Ce nom de coffre existe déjà')
    db.audit(user['username'],'vault.create',cur.lastrowid,name)
    return {'id':cur.lastrowid}


@app.delete('/api/credentials/{credential_id}')
def delete_credential(credential_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    with db.session() as con:
        exists=con.execute('SELECT name FROM credentials WHERE id=?',(credential_id,)).fetchone()
        if not exists: raise HTTPException(404,'Coffre introuvable')
        con.execute('DELETE FROM credentials WHERE id=?',(credential_id,))
    db.audit(user['username'],'vault.delete',credential_id,exists['name'])
    return {'ok':True}


@app.get('/api/users')
def list_users(request:Request):
    security.require(request,role='admin')
    with db.session() as con:
        users=con.execute('SELECT id,username,display_name,source,role,groups_json,created_at FROM users ORDER BY username').fetchall()
    return {'items':[db.obj(x,'groups_json') for x in users]}


@app.get('/api/audit')
def audit(request:Request):
    security.require(request,role='admin')
    with db.session() as con:
        records=con.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200').fetchall()
    return {'items':[dict(r) for r in records]}


@app.get('/api/updates')
def updates(request:Request):
    security.require(request,role='admin')
    with db.session() as con:
        rows=con.execute('SELECT * FROM updates ORDER BY id DESC LIMIT 15').fetchall()
    state=config.DATA/'update-state.json'
    info={}
    if state.exists():
        try: info=json.loads(state.read_text())
        except (OSError,ValueError): pass
    return {'version':config.APP_VERSION,'git_branch':config.GIT_BRANCH,'worker':info,'requests':[dict(x) for x in rows]}


@app.post('/api/updates/request')
def request_update(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    action=payload.get('action')
    if action not in ('check','apply'):
        raise HTTPException(422,'Action inconnue')
    with db.session() as con:
        queued=con.execute("SELECT COUNT(*) FROM updates WHERE status IN ('queued','running')").fetchone()[0]
        if queued: raise HTTPException(409,'Une opération Git est déjà en cours')
        cur=con.execute('INSERT INTO updates (action,actor,created_at,status) VALUES (?,?,?,?)',
                        (action,user['username'],db.now(),'queued'))
    db.audit(user['username'],'update.'+action,cur.lastrowid)
    return {'request_id':cur.lastrowid}
