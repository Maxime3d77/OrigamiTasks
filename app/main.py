"""OrigamiTasks — single-node Linux control plane / API / scheduler."""
import json
import re
import secrets
import ssl
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import config, db, engine, scheduler, security, integrations, agents, connections, ldap_settings


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



# Administration endpoints require session + CSRF; agent endpoints are bearer-only.
@app.get('/api/agents')
def list_agents(request:Request):
    security.require(request,role='admin')
    with db.session() as con:
        rows=con.execute('SELECT id,name,platform,group_name,hostname,version,enrolled,revoked,last_seen,created_at FROM agents ORDER BY id DESC').fetchall()
    cutoff=agents.utc_plus(-90)
    return {'items':[dict(r,status=('revoked' if r['revoked'] else 'pending' if not r['enrolled'] else
        'online' if r['last_seen'] and r['last_seen']>=cutoff else 'offline')) for r in rows]}


@app.post('/api/agents/enrollments')
def create_agent_enrollment(payload:dict[str,Any], request:Request):
    user=security.require(request,role='admin',mutate=True)
    result=agents.register(str(payload.get('name') or ''),str(payload.get('platform') or ''),
                           str(payload.get('group') or 'default'))
    db.audit(user['username'],'agent.registration',result['id'])
    return result  # token visible exactly once


@app.post('/api/agents/deploy')
def deploy_agent(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        result=agents.deploy(payload)
    except HTTPException:
        raise
    except Exception as exc:
        # Do not leak paths, SSH passwords or remote command output to browsers.
        raise HTTPException(502,'Installation distante échouée : '+type(exc).__name__)
    db.audit(user['username'],'agent.deploy',result['id'],result['name'])
    return result


@app.post('/api/agents/{agent_id}/revoke')
def revoke_agent(agent_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    agents.revoke(agent_id)
    db.audit(user['username'],'agent.revoke',agent_id)
    return {'ok':True}


@app.post('/api/agent/enroll')
def agent_enroll(payload:dict[str,Any]):
    return agents.enroll(payload)


@app.post('/api/agent/poll')
def agent_poll(payload:dict[str,Any],request:Request):
    return agents.poll(agents.authenticate(request),payload)


@app.post('/api/agent/report')
def agent_report(payload:dict[str,Any],request:Request):
    return agents.report(agents.authenticate(request),payload)


VALID_TYPES = {'shell','powershell','ssh','sftp','ftp','email','http','delay','set_variable','matrix','ansible','proxmox','file','agent','directory'}


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
    folder=folder_path(payload.get('folder','Général'))
    return {
        'name':name,
        'description':str(payload.get('description',''))[:2000],
        'folder':folder,
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


@app.get('/api/catalog')
def task_catalog(request: Request):
    security.require(request)
    return {'available':[
      {'type':'shell','category':'Scripts','name':'Bash local'},
      {'type':'powershell','category':'Scripts','name':'PowerShell local'},
      {'type':'ssh','category':'Scripts','name':'SSH / Windows distant'},
      {'type':'ansible','category':'Infrastructure','name':'Ansible Playbook sur runner SSH'},
      {'type':'matrix','category':'Messagerie','name':'Message Matrix'},
      {'type':'proxmox','category':'Infrastructure','name':'Proxmox inventaire et actions encadrées'},
      {'type':'agent','category':'Agents','name':'Agent Linux/Windows : scripts, services, inventaire, disques, processus'},
      {'type':'directory','category':'Annuaire','name':'LDAP / Active Directory : lecture, utilisateurs, groupes et comptes'},
      {'type':'email','category':'Messagerie','name':'SMTP'},
      {'type':'sftp','category':'Transferts','name':'SFTP'},
      {'type':'file','category':'Fichiers','name':'Opérations fichiers dans transfers/'},
      {'type':'ftp','category':'Transferts','name':'FTP/FTPS'},
      {'type':'http','category':'API','name':'HTTP / REST'},
      {'type':'set_variable','category':'Logique','name':'Variable'},
      {'type':'delay','category':'Logique','name':'Attente'}
    ],'roadmap_document':'docs/FEATURE-CATALOG.md'}


@app.get('/api/datacenters')
def list_datacenters(request:Request):
    security.require(request,role='admin')
    with db.session() as con:
        rows=con.execute('SELECT id,name,url,credential_id,created_at FROM datacenters ORDER BY name').fetchall()
    return {'items':[dict(row) for row in rows]}


@app.post('/api/datacenters')
def add_datacenter(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    name=str(payload.get('name','')).strip()
    if not 1<=len(name)<=100: raise HTTPException(422,'Nom du datacenter invalide')
    try:
        origin=integrations.authorized_origin(payload.get('url'),config.PROXMOX_ALLOWED_HOSTS)
        credential_id=int(payload.get('credential_id'))
    except (integrations.IntegrationError,ValueError,TypeError) as exc:
        raise HTTPException(422,str(exc))
    with db.session() as con:
        credential_exists=con.execute('SELECT 1 FROM credentials WHERE id=?',(credential_id,)).fetchone()
        if not credential_exists: raise HTTPException(422,'Credential introuvable')
        try:
            cur=con.execute('INSERT INTO datacenters(name,url,credential_id,created_at) VALUES (?,?,?,?)',
                            (name,origin,credential_id,db.now()))
        except __import__('sqlite3').IntegrityError:
            raise HTTPException(409,'Datacenter déjà enregistré')
        dc_id=cur.lastrowid
    db.audit(user['username'],'datacenter.create',dc_id,name)
    return {'id':dc_id}


@app.get('/api/datacenters/{dc_id}/discover')
def discover_datacenter(dc_id:int,request:Request):
    security.require(request,role='admin')
    try:
        dc=integrations.configured_datacenter(dc_id)
        cred=engine.executor.credential(dc['credential_id'])
        result=integrations.discover_datacenter(dc,cred)
    except integrations.IntegrationError as exc:
        raise HTTPException(422,str(exc))
    except Exception as exc:
        raise HTTPException(502,'Proxmox API indisponible : '+type(exc).__name__)
    return {'datacenter':dc['name'],'resources':result}


@app.get('/api/datacenters/{dc_id}/vms/{vmid}/dr-assessment')
def vm_dr_assessment(dc_id:int, vmid:int, request:Request):
    user = security.require(request, role='admin')
    try:
        dc = integrations.configured_datacenter(dc_id)
        cred = engine.executor.credential(dc['credential_id'])
        return integrations.assess_vm_dr(dc, cred, vmid)
    except integrations.IntegrationError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(502, 'Évaluation Proxmox indisponible : '+type(exc).__name__)


@app.delete('/api/datacenters/{dc_id}')
def delete_datacenter(dc_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    with db.session() as con:
        row=con.execute('SELECT name FROM datacenters WHERE id=?',(dc_id,)).fetchone()
        if not row:raise HTTPException(404,'Datacenter introuvable')
        con.execute('DELETE FROM datacenters WHERE id=?',(dc_id,))
    db.audit(user['username'],'datacenter.delete',dc_id,row['name'])
    return {'ok':True}



# Folders use stable slash-delimited paths; deleting a folder never deletes workflows.
def folder_path(value):
    path = str(value or '').strip()
    parts = path.split('/')
    if not path or len(path)>150 or len(parts)>12 or any(
        not part or part in ('.','..') or len(part)>70 or any(ord(c)<32 for c in part)
        for part in parts
    ):
        raise HTTPException(422,'Chemin de dossier invalide')
    return path


def known_folders(con):
    paths={'Général'}
    for row in con.execute('SELECT path FROM workflow_folders UNION SELECT folder AS path FROM jobs'):
        raw=row['path']
        if not raw: continue
        parts=raw.split('/')
        for i in range(1,len(parts)+1):
            paths.add('/'.join(parts[:i]))
    return paths


@app.get('/api/folders')
def list_folders(request:Request):
    user=security.require(request)
    with db.session() as con:
        rows=con.execute('SELECT * FROM jobs').fetchall()
        allowed=[db.obj(row,'tags_json','acl_json','steps_json','schedule_json') for row in rows]
        allowed=[job for job in allowed if security.can_job(user,job,'view')]
        if user['role']=='admin':
            paths=known_folders(con)
        else:
            paths={'Général'}
            for job in allowed:
                parts=job['folder'].split('/')
                paths.update('/'.join(parts[:i]) for i in range(1,len(parts)+1))
        counts={}
        for job in allowed:
            counts[job['folder']]=counts.get(job['folder'],0)+1
    return {'items':[{'path':p,'name':p.rsplit('/',1)[-1],
            'parent':p.rsplit('/',1)[0] if '/' in p else '',
            'count':counts.get(p,0),'depth':p.count('/')} for p in sorted(paths,key=lambda x:(x.count('/'),x.casefold()))]}


@app.post('/api/folders')
def create_folder(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    parent=str(payload.get('parent') or '').strip()
    name=folder_path(payload.get('name',''))
    if '/' in name: raise HTTPException(422,'Un nom de dossier ne peut pas contenir /')
    with db.session() as con:
        if parent:
            parent=folder_path(parent)
            if parent not in known_folders(con): raise HTTPException(404,'Dossier parent introuvable')
        path=folder_path(parent+'/'+name if parent else name)
        if path in known_folders(con): raise HTTPException(409,'Ce dossier existe déjà')
        con.execute('INSERT INTO workflow_folders (path,created_at) VALUES (?,?)',(path,db.now()))
    db.audit(user['username'],'folder.create',path)
    return {'path':path}


@app.put('/api/folders')
def rename_folder(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    old=folder_path(payload.get('path',''))
    name=folder_path(payload.get('name',''))
    if '/' in name or old=='Général': raise HTTPException(422,'Renommage de dossier interdit')
    prefix=old.rsplit('/',1)[0]+'/' if '/' in old else ''
    new=folder_path(prefix+name)
    with db.session() as con:
        paths=known_folders(con)
        if old not in paths: raise HTTPException(404,'Dossier introuvable')
        subtree={p for p in paths if p==old or p.startswith(old+'/')}
        destinations={new+p[len(old):] for p in subtree}
        if (paths-subtree)&destinations: raise HTTPException(409,'Collision avec un dossier existant')
        rows=con.execute('SELECT path FROM workflow_folders').fetchall()
        for row in rows:
            src=row['path']
            if src in subtree:
                con.execute('UPDATE workflow_folders SET path=? WHERE path=?',(new+src[len(old):],src))
        con.execute("UPDATE jobs SET folder=?||substr(folder, length(?)+1) WHERE folder=? OR substr(folder,1,length(?)+1)=?||'/'",
                    (new,old,old,old,old))
    db.audit(user['username'],'folder.rename',old,new)
    return {'path':new}


@app.delete('/api/folders')
def remove_folder(request:Request,path:str):
    user=security.require(request,role='admin',mutate=True)
    old=folder_path(path)
    if old=='Général': raise HTTPException(422,'Dossier Général protégé')
    with db.session() as con:
        if old not in known_folders(con): raise HTTPException(404,'Dossier introuvable')
        parent=old.rsplit('/',1)[0] if '/' in old else 'Général'
        con.execute("UPDATE jobs SET folder=? WHERE folder=? OR substr(folder,1,length(?)+1)=?||'/'",(parent,old,old,old))
        con.execute("DELETE FROM workflow_folders WHERE path=? OR substr(path,1,length(?)+1)=?||'/'",(old,old,old))
    db.audit(user['username'],'folder.delete',old,'workflows déplacés vers '+parent)
    return {'ok':True,'moved_to':parent}



@app.get('/api/admin/ldap')
def get_admin_ldap(request:Request):
    security.require(request,role='admin')
    with db.session() as con:
        saved=con.execute('SELECT 1 FROM ldap_settings WHERE id=1').fetchone() is not None
    return dict(ldap_settings.public(), source='administration' if saved else '.env')


@app.put('/api/admin/ldap')
def put_admin_ldap(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        values=ldap_settings.merge(payload)
    except ldap_settings.LDAPSettingsError as exc:
        raise HTTPException(422,str(exc)) from None
    ldap_settings.save(values)
    db.audit(user['username'],'ldap.settings.update','ldap','configuration protégée')
    return ldap_settings.public(values)


@app.post('/api/admin/ldap/test')
def test_admin_ldap(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        values=ldap_settings.merge(payload.get('settings',{}))
        result=ldap_settings.test(values,payload.get('test_user',''))
    except ldap_settings.LDAPSettingsError as exc:
        db.audit(user['username'],'ldap.settings.test.failed','ldap','lecture seule')
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'ldap.settings.test','ldap','lecture seule')
    return result


@app.get('/api/directory/groups')
def lookup_directory_groups(request:Request,q:str=''):
    security.require(request,role='admin')
    q=q.strip()[:80]
    with db.session() as con:
        found={name.lower():{'name':name,'source':'Utilisateurs connus','dn':''}
            for row in con.execute('SELECT groups_json FROM users')
            for name in json.loads(row['groups_json'] or '[]') if name}
    warning=''
    settings=ldap_settings.current()
    if settings['enabled'] and settings['uri'] and settings['group_base_dn']:
        try:
            from ldap3 import Server, Connection, SUBTREE, Tls
            from ldap3.utils.conv import escape_filter_chars
            if not (settings['uri'].startswith('ldaps://') or settings['starttls']):
                raise ValueError('LDAPS ou STARTTLS obligatoire')
            server=Server(settings['uri'],tls=Tls(validate=ssl.CERT_REQUIRED,ca_certs_file=settings['ca_cert'] or None),connect_timeout=6)
            conn=Connection(server,user=settings['bind_dn'],password=settings['bind_password'],
                            auto_bind=not settings['starttls'],receive_timeout=8)
            if settings['starttls']:
                conn.open()
                if not conn.start_tls() or not conn.bind(): raise ValueError('Bind LDAP refusé')
            filt='(|(objectClass=group)(objectClass=groupOfNames)(objectClass=groupOfUniqueNames)(objectClass=posixGroup))'
            if q:
                filt='(&'+filt+'(cn=*'+escape_filter_chars(q)+'*))'
            conn.search(settings['group_base_dn'],filt,SUBTREE,attributes=['cn','description'],size_limit=100)
            for item in conn.entries:
                name=str(item.cn.value or '') if 'cn' in item else ''
                if name: found[name.lower()]={'name':name,'dn':item.entry_dn,'source':'LDAP'}
            conn.unbind()
        except Exception as exc:
            warning='Recherche LDAP indisponible : '+type(exc).__name__
    items=sorted((v for v in found.values() if not q or q.casefold() in v['name'].casefold()),
                 key=lambda x:x['name'].casefold())[:100]
    return {'items':items,'warning':warning}


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
            if any(s.get('type') in ('proxmox','agent','directory') for s in old['steps_json']+payload['steps_json']):
                raise HTTPException(403,'Seul un administrateur peut modifier les étapes Proxmox')
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
        items=con.execute('SELECT * FROM credentials ORDER BY name').fetchall()
    result=[]
    for row in items:
        values=security.decrypt(row['encrypted_payload'])
        result.append({'id':row['id'],'name':row['name'],'kind':row['kind'],
                       'created_at':row['created_at'],'updated_at':row['updated_at'],
                       'fields':list(values),'public':connections.public(values)})
    return {'items':result}


@app.post('/api/credentials')
def create_credential(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    name=str(payload.get('name','')).strip()
    fields=payload.get('payload',{})
    if not name or len(name)>120 or not isinstance(fields,dict) or len(json.dumps(fields))>30000:
        raise HTTPException(422,'Coffre invalide')
    kind=str(payload.get('kind','generic'))
    try:
        values=connections.normalize(kind,fields,is_new=True)
    except connections.ConnectionError as exc:
        raise HTTPException(422,str(exc)) from None
    with db.session() as con:
        try:
            cur=con.execute('INSERT INTO credentials (name,kind,encrypted_payload,created_at,updated_at) VALUES (?,?,?,?,?)',
                            (name,kind,security.encrypt(values),db.now(),db.now()))
        except db.sqlite3.IntegrityError:
            raise HTTPException(409,'Ce nom de coffre existe déjà')
        credential_id=cur.lastrowid
    db.audit(user['username'],'vault.create',credential_id,name)
    return {'id':credential_id}


@app.put('/api/credentials/{credential_id}')
def update_credential(credential_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    name=str(payload.get('name','')).strip()
    updates=payload.get('payload',{})
    if not name or len(name)>120 or not isinstance(updates,dict) or len(json.dumps(updates))>30000:
        raise HTTPException(422,'Champs du coffre invalides')
    with db.session() as con:
        row=con.execute('SELECT * FROM credentials WHERE id=?',(credential_id,)).fetchone()
        if not row: raise HTTPException(404,'Identifiant introuvable')
        kind=str(payload.get('kind') or row['kind'])
        if kind!=row['kind']:
            raise HTTPException(422,'Type de connexion non modifiable : cloner ou créer une nouvelle connexion')
        values=security.decrypt(row['encrypted_payload'])
        remove=payload.get('remove_fields',[])
        if not isinstance(remove,list):
            raise HTTPException(422,'Liste de champs à supprimer invalide')
        for key in remove:
            if key in connections.SECRET_KEYS: values.pop(key,None)
        try:
            values=connections.normalize(kind,updates,values,is_new=False)
        except connections.ConnectionError as exc:
            raise HTTPException(422,str(exc)) from None
        try:
            con.execute('UPDATE credentials SET name=?,kind=?,encrypted_payload=?,updated_at=? WHERE id=?',
                        (name,kind,security.encrypt(values),db.now(),credential_id))
        except db.sqlite3.IntegrityError:
            raise HTTPException(409,'Ce nom de coffre existe déjà')
    db.audit(user['username'],'vault.update',credential_id,name)
    return {'ok':True}


@app.post('/api/credentials/{credential_id}/test')
def test_credential(credential_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    with db.session() as con:
        row=con.execute('SELECT kind,encrypted_payload FROM credentials WHERE id=?',(credential_id,)).fetchone()
    if not row: raise HTTPException(404,'Connexion introuvable')
    if row['kind']!='smtp':
        raise HTTPException(422,'Test disponible pour SMTP ; utiliser Administration / LDAP pour tester LDAP')
    try:
        result=connections.test_smtp(security.decrypt(row['encrypted_payload']))
    except connections.ConnectionError as exc:
        db.audit(user['username'],'vault.test.failed',credential_id,'smtp')
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'vault.test',credential_id,'smtp')
    return result


@app.post('/api/credentials/{credential_id}/clone')
def clone_credential(credential_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    name=str(payload.get('name','')).strip()
    if not name or len(name)>120: raise HTTPException(422,'Nom de copie invalide')
    with db.session() as con:
        row=con.execute('SELECT * FROM credentials WHERE id=?',(credential_id,)).fetchone()
        if not row: raise HTTPException(404,'Identifiant introuvable')
        try:
            cur=con.execute('INSERT INTO credentials (name,kind,encrypted_payload,created_at,updated_at) VALUES (?,?,?,?,?)',
                            (name,row['kind'],security.encrypt(security.decrypt(row['encrypted_payload'])),db.now(),db.now()))
        except db.sqlite3.IntegrityError:
            raise HTTPException(409,'Ce nom de coffre existe déjà')
        new_id=cur.lastrowid
    db.audit(user['username'],'vault.clone',new_id,'source #'+str(credential_id))
    return {'id':new_id}


@app.delete('/api/credentials/{credential_id}')
def delete_credential(credential_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    with db.session() as con:
        exists=con.execute('SELECT name FROM credentials WHERE id=?',(credential_id,)).fetchone()
        if not exists: raise HTTPException(404,'Coffre introuvable')
        if con.execute('SELECT 1 FROM datacenters WHERE credential_id=? LIMIT 1',(credential_id,)).fetchone():
            raise HTTPException(409,'Identifiant utilisé par un datacenter')
        jobs=con.execute('SELECT steps_json FROM jobs').fetchall()
        if any(str(step.get('config',{}).get(k,''))==str(credential_id)
               for row in jobs for step in json.loads(row['steps_json'])
               for k in ('credential_id','password_credential_id')):
            raise HTTPException(409,'Identifiant utilisé par un workflow')
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
