"""OrigamiTasks — single-node Linux control plane / API / scheduler."""
import json
import re
import secrets
import ssl
import hmac
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import config, db, engine, scheduler, security, integrations, agents, connections, ldap_settings, connection_tests, ssh_inventory, agent_deployments, host_keys, proxmox_cas, dr_sites, agent_uninstall, agent_modules, proxmox_fleet, agent_settings, dr_plans, dr_protected_vms, pra_pbs, pra_live, pra_lvm_jobs, pra_lvm_activation, pra_lvm_schedule, agent_updates


@asynccontextmanager
async def lifespan(app):
    if not config.VAULT_KEY:
        raise RuntimeError('VAULT_KEY manquant — utiliser ops/bootstrap.sh')
    security.cipher()  # validation before accepting requests
    db.initialize()
    security.initialize_admin()
    engine.start()
    scheduler.start()
    agent_deployments.start()
    agent_updates.start()
    pra_live.initialize()
    pra_lvm_jobs.initialize()
    pra_lvm_activation.initialize()
    pra_lvm_schedule.initialize()
    yield
    scheduler.STOP.set()
    engine.STOP.set()
    agent_deployments.STOP.set()
    agent_updates.STOP.set()
    pra_live.STOP.set()


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
    # Always serve the effective backend version in the UI and asset URLs.
    # Keep app version in config.APP_VERSION as the single source of truth.
    template=(STATIC / 'index.html').read_text(encoding='utf-8')
    return HTMLResponse(template.replace('__APP_VERSION__', config.APP_VERSION))


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
@app.get('/api/admin/agent-settings')
def read_agent_settings(request:Request):
    security.require(request,role='admin')
    return agent_settings.current()


@app.put('/api/admin/agent-settings')
def save_agent_settings(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    value=agent_settings.update(payload)
    db.audit(user['username'],'agents.controller-url.update','agents',
             'URL HTTPS de contact des agents modifiée')
    return value


@app.get('/api/agents')
def list_agents(request:Request):
    security.require(request,role='admin')
    with db.session() as con:
        rows=con.execute('SELECT id,name,platform,group_name,hostname,version,enrolled,revoked,last_seen,cpu_percent,memory_mb,uninstalled_at,installed_controller_url,modules_json,modules_revision,modules_applied_revision,modules_reported_json,created_at FROM agents ORDER BY id DESC').fetchall()
    cutoff=agents.utc_plus(-90)
    return {'items':[agent_modules.describe(dict(r,status=('uninstalled' if r['uninstalled_at'] else 'revoked' if r['revoked'] else 'pending' if not r['enrolled'] else
        'online' if r['last_seen'] and r['last_seen']>=cutoff else 'offline'))) for r in rows]}


@app.put('/api/agents/{agent_id}/modules')
def configure_agent_modules(agent_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    result=agent_modules.set_modules(agent_id,payload)
    db.audit(user['username'],'agent.modules.configure',agent_id,
             'revision='+str(result['revision'])+' roles='+','.join(
                 k for k,v in result['modules'].items() if v))
    return result


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
    if payload.get('credential_id'):
        with db.session() as con:
            credential_kind=con.execute("SELECT kind FROM credentials WHERE id=?",
                (payload['credential_id'],)).fetchone()
        if not credential_kind or credential_kind['kind']!='ssh':
            raise HTTPException(422,'Une identité SSH du coffre est obligatoire')
    try:
        result=agents.deploy(payload)
    except HTTPException:
        raise
    except Exception as exc:
        # Do not leak paths, SSH passwords or remote command output to browsers.
        raise HTTPException(502,'Installation distante échouée : '+type(exc).__name__)
    db.audit(user['username'],'agent.deploy',result['id'],result['name'])
    return result



@app.post('/api/agents/deploy/batch')
def batch_deploy_agents(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        result=agent_deployments.queue(payload,user['username'])
    except (agent_deployments.DeploymentError,ssh_inventory.InventoryError) as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'agent.deploy.batch',result['batch_id'],
             str(result['total'])+' serveur(s)')
    return result


@app.get('/api/agents/deploy/batches')
def agent_deploy_batch_status(request:Request):
    security.require(request,role='admin')
    return agent_deployments.list_batches()


@app.post('/api/agents/deploy/batches/cleanup')
def agent_deploy_batch_cleanup(request:Request):
    user=security.require(request,role='admin',mutate=True)
    result=agent_deployments.cleanup_completed(keep=20,older_than_days=30)
    db.audit(user['username'],'agent.deploy.batch.cleanup','history',
             str(result['removed'])+' lots terminés purgés')
    return result


@app.get('/api/agents/updates')
def list_agent_updates(request:Request):
    security.require(request,role='admin')
    return agent_updates.jobs()


@app.post('/api/agents/updates')
def queue_agent_updates(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict) or set(payload)!={'agent_ids','credential_id','approved'}:
        raise HTTPException(422,'Sélection, identité SSH et confirmation obligatoires')
    try:
        return agent_updates.queue(payload['agent_ids'],payload['credential_id'],
                                   payload['approved'],user['username'])
    except agent_updates.UpdateError as exc:
        raise HTTPException(422,str(exc)) from None


@app.post('/api/agents/{agent_id}/revoke')
def revoke_agent(agent_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    agents.revoke(agent_id)
    db.audit(user['username'],'agent.revoke',agent_id)
    return {'ok':True}


@app.post('/api/agents/{agent_id}/uninstall')
def uninstall_agent(agent_id:int,request:Request,payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    # Preserve an audit trail even when SSH or the host identity check fails.
    db.audit(user['username'],'agent.uninstall.attempt',agent_id,'SSH host removal requested')
    try:
        result=agent_uninstall.uninstall(agent_id,payload)
    except agent_uninstall.AgentUninstallError as exc:
        db.audit(user['username'],'agent.uninstall.failed',agent_id,type(exc).__name__)
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'agent.uninstall.success',agent_id,'Retiré par SSH et jeton révoqué')
    return result


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
        connection_mode=step['config'].get('connection_mode')
        if connection_mode is not None:
            if connection_mode not in ('manual','vault'):
                raise HTTPException(422,'Mode de connexion invalide')
            typ=step.get('type')
            kind={'email':'smtp','http':'api','ssh':'ssh','sftp':'ssh',
                  'ansible':'ssh','ftp':'ftp','matrix':'matrix','directory':'ldap'}.get(typ)
            if not kind:
                raise HTTPException(422,'Mode coffre/manuel non applicable à cette tâche')
            cid=step['config'].get('credential_id')
            need_cred=(connection_mode=='vault' and typ not in ('directory',)) or (
                typ in ('ssh','sftp','ansible','matrix'))
            if need_cred and not cid:
                raise HTTPException(422,'Sélectionner un identifiant '+kind.upper()+' du coffre')
            if cid:
                with db.session() as check_con:
                    cred=check_con.execute("SELECT kind FROM credentials WHERE id=?",(cid,)).fetchone()
                if not cred or cred['kind']!=kind:
                    raise HTTPException(422,'Le compte sélectionné doit être une connexion '+kind.upper())
            if connection_mode=='vault' and typ in ('ssh','sftp','ansible') and not step['config'].get('ssh_server_id'):
                raise HTTPException(422,'Sélectionner un serveur SSH de l’inventaire')
            if connection_mode=='manual' and typ=='email':
                if not step['config'].get('host') or not step['config'].get('from'):
                    raise HTTPException(422,'SMTP manuel : serveur et expéditeur obligatoires')
            if connection_mode=='manual' and typ=='http' and not step['config'].get('url'):
                raise HTTPException(422,'API manuelle : URL obligatoire')
        if step.get('type') in ('ssh','sftp','ansible') and step['config'].get('ssh_server_id'):
            try:
                ssh_inventory.server(step['config']['ssh_server_id'])
            except ssh_inventory.InventoryError as exc:
                raise HTTPException(422,str(exc)) from None
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
    notice=payload.get('notification_json',{})
    if not isinstance(notice,dict) or set(notice)-{'enabled','credential_id','recipients','on_success','on_failure'}:
        raise HTTPException(422,'Configuration des notifications invalide')
    if notice.get('enabled'):
        if type(notice.get('on_success',False)) is not bool or type(notice.get('on_failure',False)) is not bool:
            raise HTTPException(422,'Choix du statut à notifier invalide')
        if not (notice.get('on_success') or notice.get('on_failure')):
            raise HTTPException(422,'Sélectionner réussite et/ou échec pour les notifications')
        recipients=str(notice.get('recipients') or '').strip()
        emails=[v.strip() for v in recipients.split(',')]
        if not 1<=len(emails)<=5 or any(
            not re.fullmatch(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}', email)
            for email in emails
        ):
            raise HTTPException(422,'Notifications : 1 à 5 adresses e-mail valides, séparées par des virgules')
        try:
            smtp_id=int(notice.get('credential_id'))
        except (TypeError,ValueError):
            raise HTTPException(422,'Notifications : choisir une connexion SMTP du coffre') from None
        with db.session() as test_con:
            smtp=test_con.execute('SELECT kind FROM credentials WHERE id=?',(smtp_id,)).fetchone()
        if not smtp or smtp['kind']!='smtp':
            raise HTTPException(422,'Notifications : connexion SMTP introuvable')
        notice={'enabled':True,'credential_id':smtp_id,'recipients':','.join(emails),
                'on_success':notice['on_success'],'on_failure':notice['on_failure']}
    else:
        notice={}
    folder=folder_path(payload.get('folder','Général'))
    return {
        'name':name,
        'description':str(payload.get('description',''))[:2000],
        'folder':folder,
        'tags_json':json.dumps([str(x).strip()[:40] for x in payload.get('tags_json',[]) if str(x).strip()][:20],ensure_ascii=False),
        'acl_json':json.dumps({k:[str(g).strip().lower()[:120] for g in acl.get(k,[]) if str(g).strip()] for k in ('view','run','edit')},ensure_ascii=False),
        'steps_json':json.dumps(steps,ensure_ascii=False),
        'schedule_json':json.dumps(schedule,ensure_ascii=False),
        'notification_json':json.dumps(notice,ensure_ascii=False),
        'enabled':int(bool(payload.get('enabled',True))),
        'next_run_at':next_run,
    }


def get_job(con,job_id):
    row=con.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row: raise HTTPException(404,'Workflow introuvable')
    return db.obj(row,'tags_json','acl_json','steps_json','schedule_json','notification_json')


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


@app.get('/api/proxmox/cas')
def list_proxmox_cas(request: Request):
    security.require(request,role='admin')
    return {'items':proxmox_cas.list_public()}


@app.post('/api/proxmox/cas')
def upload_proxmox_ca(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict) or set(payload)-{'pem_text','der_base64'}:
        raise HTTPException(422,'Certificat CA Proxmox : données invalides')
    try:
        result=proxmox_cas.import_public(
            pem_text=payload.get('pem_text'),der_base64=payload.get('der_base64')
        )
    except proxmox_cas.ProxmoxCAError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'proxmox.ca.import',result['id'],str(result['certificate_count'])+' CA')
    return result


@app.get('/api/datacenters')
def list_datacenters(request:Request):
    security.require(request,role='admin')
    with db.session() as con:
        rows=con.execute(
            'SELECT id,name,url,credential_id,kind,ca_cert,skip_tls_verification,created_at FROM datacenters ORDER BY name'
        ).fetchall()
    return {'items':[dict(row) for row in rows]}


def _datacenter_fields(payload, con, *, existing=None):
    name=str(payload.get('name','')).strip()
    kind=str(payload.get('kind',existing['kind'] if existing else 'cluster'))
    if not 1<=len(name)<=100 or kind not in ('cluster','node'):
        raise HTTPException(422,'Nom (1 à 100 caractères) ou type de cible Proxmox invalide')
    try:
        origin=integrations.authorized_origin(
            payload.get('url'),config.PROXMOX_ALLOWED_HOSTS,admin_proxmox=True
        )
    except integrations.IntegrationError as exc:
        raise HTTPException(422,str(exc)) from None
    skip_tls=payload.get('skip_tls_verification',False)
    if type(skip_tls) is not bool:
        raise HTTPException(422,'Ignorer la vérification TLS : valeur booléenne attendue')
    ca_id=payload.get('ca_cert','')
    if not isinstance(ca_id,str):
        raise HTTPException(422,'Identifiant de CA Proxmox invalide')
    if ca_id:
        try:
            proxmox_cas.filename(ca_id)
        except proxmox_cas.ProxmoxCAError as exc:
            raise HTTPException(422,str(exc)) from None
    username=payload.get('token_username')
    token=payload.get('token_secret')
    if username is not None or token is not None:
        username=str(username or '').strip()
        token=str(token or '')
        if (not re.fullmatch(r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+![a-zA-Z0-9_.-]+',username)
                or not 8<=len(token)<=1024 or '\n' in token or '\r' in token):
            raise HTTPException(
                422,'Token Proxmox : renseigner user@realm!tokenname et son secret (8 à 1024 caractères)'
            )
        payload_credentials=connections.normalize('proxmox',{
            'url':origin,'username':username,'token':token,'ca_cert':ca_id,
            'skip_tls_verification':skip_tls
        },is_new=True)
        # The credentials remain in the encrypted vault; never return the secret.
        vault_name='Proxmox - '+name[:80]+' - '+secrets.token_hex(4)
        cursor=con.execute(
            'INSERT INTO credentials (name,kind,encrypted_payload,created_at,updated_at) VALUES (?,?,?,?,?)',
            (vault_name,'proxmox',security.encrypt(payload_credentials),db.now(),db.now())
        )
        credential_id=cursor.lastrowid
    else:
        try:
            credential_id=int(payload.get('credential_id',existing['credential_id'] if existing else None))
        except (TypeError,ValueError):
            raise HTTPException(422,'Choisir un token du coffre ou saisir un nouveau token') from None
        row=con.execute('SELECT kind,encrypted_payload FROM credentials WHERE id=?',(credential_id,)).fetchone()
        if not row or row['kind'] not in ('proxmox','api'):
            raise HTTPException(422,'Connexion Proxmox du coffre introuvable')
        values=security.decrypt(row['encrypted_payload'])
        saved_url=values.get('url') or values.get('base_url')
        if not saved_url:
            raise HTTPException(422,'Le profil du token doit contenir une URL HTTPS Proxmox')
        try:
            saved=integrations.authorized_origin(saved_url,config.PROXMOX_ALLOWED_HOSTS,
                                                  admin_proxmox=True)
        except integrations.IntegrationError as exc:
            raise HTTPException(422,str(exc)) from None
        if saved.lower().rstrip('/') != origin.lower().rstrip('/'):
            raise HTTPException(422,
                'L’URL du datacenter et celle du token du coffre doivent correspondre. '
                'Choisir un autre token ou en créer un pour ce serveur.')
    return name,kind,origin,credential_id,ca_id,skip_tls


@app.post('/api/datacenters')
def add_datacenter(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    with db.session() as con:
        name,kind,origin,credential_id,ca_id,skip_tls=_datacenter_fields(payload,con)
        try:
            cur=con.execute(
                'INSERT INTO datacenters(name,url,credential_id,kind,ca_cert,skip_tls_verification,created_at) '
                'VALUES (?,?,?,?,?,?,?)',
                (name,origin,credential_id,kind,ca_id,int(skip_tls),db.now())
            )
        except db.sqlite3.IntegrityError:
            raise HTTPException(409,'Nom de datacenter déjà enregistré') from None
        dc_id=cur.lastrowid
    db.audit(user['username'],'datacenter.create',dc_id,name+' ('+kind+')')
    return {'id':dc_id}


@app.put('/api/datacenters/{dc_id}')
def edit_datacenter(dc_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    with db.session() as con:
        old=con.execute('SELECT * FROM datacenters WHERE id=?',(dc_id,)).fetchone()
        if not old:
            raise HTTPException(404,'Serveur/cluster Proxmox introuvable')
        name,kind,origin,credential_id,ca_id,skip_tls=_datacenter_fields(payload,con,existing=old)
        try:
            con.execute('UPDATE datacenters SET name=?,url=?,credential_id=?,kind=?,ca_cert=?,skip_tls_verification=? WHERE id=?',
                        (name,origin,credential_id,kind,ca_id,int(skip_tls),dc_id))
        except db.sqlite3.IntegrityError:
            raise HTTPException(409,'Nom de datacenter déjà enregistré') from None
    db.audit(user['username'],'datacenter.update',dc_id,name+' ('+kind+')')
    return {'ok':True,'id':dc_id}


@app.get('/api/datacenters/{dc_id}/test')
def test_datacenter(dc_id:int,request:Request):
    security.require(request,role='admin')
    dc=integrations.configured_datacenter(dc_id)
    values=engine.executor.credential(dc['credential_id'])
    values=dict(values,url=dc['url'])
    if dc.get('ca_cert'):
        values['ca_cert']=dc['ca_cert']
    values['skip_tls_verification']=bool(dc.get('skip_tls_verification',False))
    try:
        return connection_tests.proxmox(values,options={'check_nodes':True})
    except (connection_tests.ConnectionTestError,integrations.IntegrationError,
            proxmox_cas.ProxmoxCAError) as exc:
        raise HTTPException(422,str(exc)) from None


@app.get('/api/dr/vms')
def list_protected_vms(request:Request,plan_id:int|None=None):
    security.require(request,role='admin')
    return {'items':dr_protected_vms.list_vms(plan_id)}


@app.get('/api/dr/vms/options')
def protected_vm_options(request:Request,plan_id:int,source_dc_id:int,target_dc_id:int):
    security.require(request,role='admin')
    try:
        return dr_protected_vms.options(plan_id,source_dc_id,target_dc_id)
    except (dr_protected_vms.ProtectedVMError,integrations.IntegrationError) as exc:
        raise HTTPException(422,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Découverte source ou destination indisponible : vérifier API et TLS') from None


@app.post('/api/dr/vms')
def create_protected_vm(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict):
        raise HTTPException(422,'Paramètres invalides')
    activate=payload.get('activate_lab') is True
    if activate and payload.get('lab_confirmation')!='DISPOSABLE-LAB-ONLY':
        raise HTTPException(422,'Confirmer explicitement la recette LVM')
    if activate and payload.get('engine')!='qmp_live_incremental':
        raise HTTPException(422,'Activation de recette réservée au moteur QMP')
    clean={k:v for k,v in payload.items() if k not in ('activate_lab','lab_confirmation')}
    try:
        result=dr_protected_vms.save(clean,user['username'])
    except (dr_protected_vms.ProtectedVMError,integrations.IntegrationError) as exc:
        raise HTTPException(422,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Préparation VM impossible : vérifier les deux API Proxmox') from None
    if payload.get("activate_lab") is True:
        try:
            activation=pra_lvm_activation.start(
                result['id'],user['username'],payload.get('lab_confirmation'))
        except ValueError as exc:
            db.audit(user['username'],'pra.lvm.lab.activation.blocked',result['id'],str(exc)[:450])
            # Keep the protection so an admin can inspect and retry safely.
            raise HTTPException(409,'Protection enregistrée, mais activation refusée : '+str(exc)) from None
        result['activation']=activation
    db.audit(user['username'],'dr.vm.plan.create',result['id'],
             'Protection enregistrée ; activation de recette demandée' if payload.get("activate_lab") else
             'Configuration uniquement ; aucune copie ni VM créée')
    return result


@app.put('/api/dr/vms/{vm_id}')
def update_protected_vm(vm_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        result=dr_protected_vms.save(payload,user['username'],vm_id)
    except (dr_protected_vms.ProtectedVMError,integrations.IntegrationError) as exc:
        raise HTTPException(422,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Mise à jour impossible : vérifier API et stockage') from None
    db.audit(user['username'],'dr.vm.plan.update',vm_id,
             'Configuration seulement ; aucun disque modifié')
    return result


@app.post('/api/dr/vms/{vm_id}/state')
def change_protected_vm_state(vm_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        if not isinstance(payload,dict) or set(payload)!={'state'}:
            raise dr_protected_vms.ProtectedVMError('État invalide')
        result=dr_protected_vms.set_state(vm_id,payload['state'])
    except dr_protected_vms.ProtectedVMError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'dr.vm.plan.state',vm_id,result['state'])
    return result


@app.delete('/api/dr/vms/{vm_id}')
def remove_protected_vm(vm_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        result=dr_protected_vms.delete(vm_id)
    except dr_protected_vms.ProtectedVMError as exc:
        raise HTTPException(404,str(exc)) from None
    db.audit(user['username'],'dr.vm.plan.delete',vm_id,
             'Configuration supprimée ; aucun disque ni VM supprimé')
    return result


@app.get('/api/dr/vms/{vm_id}/tests')
def protected_vm_test_history(vm_id:int,request:Request):
    security.require(request,role='admin')
    try:
        return {'items':dr_protected_vms.history(vm_id)}
    except dr_protected_vms.ProtectedVMError as exc:
        raise HTTPException(404,str(exc)) from None


@app.post('/api/dr/vms/{vm_id}/simulate')
def simulate_protected_vm(vm_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict) or set(payload)!={'scenario'}:
        raise HTTPException(422,'Scénario invalide')
    try:
        result=dr_protected_vms.run_readonly_test(vm_id,payload.get('scenario'),
                                                   user['username'])
    except dr_protected_vms.ProtectedVMError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'dr.vm.simulate',vm_id,
             str(payload['scenario'])+' : BLOCKED, aucune action infrastructure')
    return result


@app.get('/api/dr/vms/{vm_id}/replications')
def get_live_replication_history(vm_id:int,request:Request):
    security.require(request,role='admin')
    try:
        return {'items':pra_live.list_runs(vm_id)}
    except dr_protected_vms.ProtectedVMError as exc:
        raise HTTPException(404,str(exc)) from None


@app.get('/api/dr/vms/{vm_id}/recovery-readiness')
def get_vm_recovery_readiness(vm_id:int, request:Request):
    security.require(request,role='admin')
    from . import pra_recovery_readiness
    try:
        return pra_recovery_readiness.assess(vm_id)
    except dr_protected_vms.ProtectedVMError as exc:
        raise HTTPException(404,str(exc)) from None


@app.get('/api/dr/vms/{vm_id}/storage-readiness')
def get_vm_storage_readiness(vm_id:int,request:Request):
    security.require(request,role='admin')
    try:
        return dr_protected_vms.storage_readiness(vm_id)
    except dr_protected_vms.ProtectedVMError as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Découverte du stockage : API Proxmox indisponible ou non autorisée') from None


@app.post('/api/dr/vms/{vm_id}/lvm-lab/prepare-standby')
def prepare_lvm_lab_standby(vm_id:int, request:Request, payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict) or set(payload)!={'confirmation'}:
        raise HTTPException(422,'Confirmation de recette requise')
    from . import pra_lvm_standby
    try:
        result=pra_lvm_standby.prepare(vm_id,payload['confirmation'])
    except (ValueError,dr_protected_vms.ProtectedVMError) as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Préparation PRA non confirmée : vérifier agent, SSH et Proxmox') from None
    db.audit(user['username'],'pra.lvm.lab.standby.prepare',vm_id,
             'VMID='+str(result['vmid'])+' created='+str(result['created']))
    return result


@app.get('/api/dr/vms/{vm_id}/lvm-lab/schedule-events')
def get_lvm_lab_schedule_events(vm_id:int,request:Request):
    security.require(request,role='admin')
    try:
        return {'items':pra_lvm_schedule.history(vm_id)}
    except dr_protected_vms.ProtectedVMError as exc:
        raise HTTPException(404,str(exc)) from None


@app.get('/api/dr/vms/{vm_id}/lvm-lab/activations')
def list_lvm_lab_activations(vm_id:int,request:Request):
    security.require(request,role='admin')
    try:
        return {'items':pra_lvm_activation.status(vm_id)}
    except dr_protected_vms.ProtectedVMError as exc:
        raise HTTPException(404,str(exc)) from None


@app.get('/api/dr/vms/{vm_id}/lvm-lab/preflight')
def get_lvm_lab_preflight(vm_id:int, request:Request):
    security.require(request,role='admin')
    try:
        return pra_lvm_jobs.preflight(vm_id)
    except (pra_lvm_jobs.LabJobError,dr_protected_vms.ProtectedVMError) as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Prévol LVM de recette : vérifier les agents SSH/PVE et la VM marquée LAB-ONLY') from None


@app.get('/api/dr/vms/{vm_id}/lvm-lab/runs')
def list_lvm_lab_runs(vm_id:int, request:Request):
    security.require(request,role='admin')
    try:
        return {'items':pra_lvm_jobs.list_runs(vm_id)}
    except (pra_lvm_jobs.LabJobError,dr_protected_vms.ProtectedVMError) as exc:
        raise HTTPException(404,str(exc)) from None


@app.post('/api/dr/vms/{vm_id}/lvm-lab/replicate')
def start_lvm_lab_run(vm_id:int, request:Request, payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict) or set(payload)!={'mode','confirmation'}:
        raise HTTPException(422,'Préciser mode et confirmation de recette')
    try:
        return pra_lvm_jobs.start(
            vm_id,user['username'],
            mode=payload['mode'],confirmation=payload['confirmation'])
    except (pra_lvm_jobs.LabJobError,dr_protected_vms.ProtectedVMError) as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Copie LVM non lancée : contrôler la recette Proxmox et les agents') from None


@app.post('/api/dr/vms/{vm_id}/lvm-lab/restore-clone')
def create_lvm_lab_restore_clone(vm_id:int, request:Request, payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict) or set(payload)!={'test_vmid','confirmation'}:
        raise HTTPException(422,'VMID de test et confirmation explicite requis')
    try:
        return pra_lvm_jobs.start_restore(
            vm_id,user['username'],test_vmid=payload['test_vmid'],
            confirmation=payload['confirmation'])
    except (pra_lvm_jobs.LabJobError,dr_protected_vms.ProtectedVMError) as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Préparation du clone de recette non confirmée : vérifier les LV/QEMU') from None


@app.get('/api/dr/vms/{vm_id}/replication-preflight')
def live_replication_preflight(vm_id:int,request:Request):
    security.require(request,role='admin')
    try:
        return pra_live.preflight(vm_id)
    except (pra_live.LiveReplicationError,dr_protected_vms.ProtectedVMError) as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Prévol de réplication : API ou agent inaccessible') from None


@app.post('/api/dr/vms/{vm_id}/replications/{run_id}/reconcile')
def reconcile_live_replication(vm_id:int,run_id:int,request:Request,payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict) or set(payload)!={'approved'} or payload['approved'] is not True:
        raise HTTPException(422,'Validation explicite requise')
    try:
        return pra_live.reconcile(vm_id,run_id,user['username'],approved=True)
    except (pra_live.LiveReplicationError,dr_protected_vms.ProtectedVMError) as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Réconciliation non confirmée : vérifier les jobs QMP et NBD sur les deux nœuds') from None


@app.post('/api/dr/vms/{vm_id}/replicate')
def replicate_protected_vm(vm_id:int,request:Request,payload:dict[str,Any]|None=None):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict) or set(payload)!={'approved'} or payload.get('approved') is not True:
        raise HTTPException(409,'Répliquer requiert une validation explicite')
    try:
        return pra_live.start(vm_id,user['username'],approved=True)
    except (pra_live.LiveReplicationError,dr_protected_vms.ProtectedVMError) as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Lancement PRA impossible : vérifier les agents, stockages et API') from None


@app.post('/api/dr/vms/{vm_id}/pbs/backup')
def start_protected_vm_pbs_backup(vm_id:int, payload:dict[str,Any], request:Request):
    user=security.require(request,role='admin',mutate=True)
    if not isinstance(payload,dict) or set(payload)!={'storage','approved'}:
        raise HTTPException(422,'Stockage PBS et approbation explicite requis')
    try:
        return pra_pbs.start_backup(vm_id,payload.get('storage'),payload.get('approved'),
                                    user['username'])
    except pra_pbs.PBSBackupError as exc:
        raise HTTPException(409,str(exc)) from None


@app.get('/api/dr/vms/{vm_id}/pbs/backups/{job_id}')
def get_protected_vm_pbs_backup(vm_id:int,job_id:int,request:Request):
    security.require(request,role='admin')
    try:
        return pra_pbs.status(vm_id,job_id)
    except pra_pbs.PBSBackupError as exc:
        raise HTTPException(409,str(exc)) from None


@app.get('/api/dr/plans')
def get_dr_plans(request:Request):
    security.require(request,role='admin')
    return {'items':dr_plans.list_plans()}


@app.post('/api/dr/plans')
def create_dr_plan(request:Request,payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    try:
        plan_id=dr_plans.save(payload)
    except dr_plans.DRPlanError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'dr.plan.create',plan_id,'Groupe PRA créé')
    return {'id':plan_id}


@app.put('/api/dr/plans/{plan_id}')
def update_dr_plan(plan_id:int,request:Request,payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    try:
        dr_plans.save(payload,plan_id)
    except dr_plans.DRPlanError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'dr.plan.update',plan_id,'Groupe PRA modifié : '+str(payload.get('name',''))[:80])
    plan=next((x for x in dr_plans.list_plans() if x['id']==plan_id),None)
    return {'ok':True,'plan':plan}


@app.delete('/api/dr/plans/{plan_id}')
def delete_dr_plan(plan_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        name=dr_plans.delete(plan_id)
    except dr_plans.DRPlanError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'dr.plan.delete',plan_id,name)
    return {'ok':True}


@app.get('/api/dr/sites')
def get_dr_sites(request:Request):
    security.require(request,role='admin')
    return {'items':dr_sites.list_sites()}


@app.post('/api/dr/sites')
def post_dr_site(request:Request,payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    try:
        site_id=dr_sites.save_site(payload)
    except dr_sites.DRSiteError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'dr.site.create',site_id,'Enregistrement du site / lecture seule')
    return {'id':site_id}


@app.put('/api/dr/sites/{site_id}')
def put_dr_site(site_id:int,request:Request,payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    try:
        dr_sites.save_site(payload,site_id)
    except dr_sites.DRSiteError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'dr.site.update',site_id,'Mise à jour des associations')
    return {'ok':True}


@app.delete('/api/dr/sites/{site_id}')
def delete_dr_site(site_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        name=dr_sites.delete_site(site_id)
    except dr_sites.DRSiteError as exc:
        raise HTTPException(404,str(exc)) from None
    db.audit(user['username'],'dr.site.delete',site_id,name)
    return {'ok':True}


@app.post('/api/dr/simulate')
def simulate_dr_failure(request:Request,payload:dict[str,Any]):
    user=security.require(request,role='admin',mutate=True)
    try:
        result=dr_sites.simulate(payload,user['username'])
    except dr_sites.DRSiteError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'dr.simulation',result['simulation_id'],
             result['scenario']+' : '+result['decision']+' (AUCUNE ACTION SUR INFRASTRUCTURE)')
    return result


@app.get('/api/dr/simulations')
def list_dr_simulations(request:Request):
    security.require(request,role='admin')
    return {'items':dr_sites.history()}


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


@app.get('/api/datacenters/{dc_id}/agent-nodes')
def preview_cluster_agent_nodes(dc_id:int,request:Request,domain_suffix:str=''):
    security.require(request,role='admin')
    try:
        return proxmox_fleet.preview(dc_id,domain_suffix)
    except (proxmox_fleet.FleetError,integrations.IntegrationError) as exc:
        raise HTTPException(422,str(exc)) from None


@app.post('/api/datacenters/{dc_id}/ssh-group')
def sync_proxmox_ssh_group(dc_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        result=proxmox_fleet.sync_group(dc_id,payload)
    except (proxmox_fleet.FleetError,integrations.IntegrationError,
            db.sqlite3.IntegrityError) as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'proxmox.ssh-group.sync',dc_id,
             'groupe='+str(result['group_id'])+' noeuds='+str(result['count']))
    return result


@app.post('/api/datacenters/{dc_id}/agents/deploy')
def deploy_proxmox_cluster_agents(dc_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        result=proxmox_fleet.deploy(dc_id,payload,user['username'])
    except (proxmox_fleet.FleetError,integrations.IntegrationError,
            ssh_inventory.InventoryError) as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'proxmox.agent-batch.queued',dc_id,
             'lot='+str(result['batch_id'])+' noeuds='+str(result['total']))
    return result


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
        allowed=[db.obj(row,'tags_json','acl_json','steps_json','schedule_json','notification_json') for row in rows]
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
    return dict(ldap_settings.public(), source='administration' if saved else '.env', insecure_tls_allowed=config.ALLOW_INSECURE_LDAP_TLS)


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


@app.post('/api/admin/ldap/ca')
def upload_ldap_ca(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        if set(payload)-{'pem','der_base64'}:
            raise ldap_settings.LDAPSettingsError('Champs du certificat CA non reconnus')
        result=ldap_settings.import_ca(pem_text=payload.get('pem'),
                                       der_base64=payload.get('der_base64'))
    except ldap_settings.LDAPSettingsError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'ldap.ca.import',result['fingerprint_sha256'][:16],
             str(result['certificate_count'])+' certificat(s) publics')
    return result


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
            from ldap3 import Server, Connection, SUBTREE, NONE
            from ldap3.utils.conv import escape_filter_chars
            if not (settings['uri'].startswith('ldaps://') or settings['starttls']):
                raise ValueError('LDAPS ou STARTTLS obligatoire')
            server=Server(settings['uri'],tls=ldap_settings.tls_for(settings),connect_timeout=6,get_info=NONE)
            conn=Connection(server,user=settings['bind_dn'],password=settings['bind_password'],
                            auto_bind=not settings['starttls'],receive_timeout=8,check_names=False)
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
    jobs=[db.obj(row,'tags_json','acl_json','steps_json','schedule_json','notification_json') for row in rows]
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
        cur=con.execute('INSERT INTO jobs (name,description,folder,tags_json,acl_json,steps_json,schedule_json,notification_json,enabled,next_run_at,created_by,created_at,updated_at) '
                        'VALUES (:name,:description,:folder,:tags_json,:acl_json,:steps_json,:schedule_json,:notification_json,:enabled,:next_run_at,:created_by,:created_at,:updated_at)',
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
            fields['notification_json']=json.dumps(old.get('notification_json') or {},ensure_ascii=False)
            existing_creds={step['id']:str(step.get('config',{}).get('credential_id') or '') for step in old['steps_json']}
            for step in payload['steps_json']:
                current=str(step.get('config',{}).get('credential_id') or '')
                if current != existing_creds.get(step['id'],''):
                    raise HTTPException(403,'Seul un administrateur peut attribuer ou changer un identifiant de coffre')
        con.execute('UPDATE jobs SET name=:name,description=:description,folder=:folder,tags_json=:tags_json,acl_json=:acl_json,steps_json=:steps_json,schedule_json=:schedule_json,notification_json=:notification_json,enabled=:enabled,next_run_at=:next_run_at,updated_at=:updated_at WHERE id=:id',
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
        rows=con.execute('SELECT id,job_id,job_name,status,trigger,requested_by,queued_at,started_at,finished_at,error,notification_status,notification_error FROM runs ORDER BY id DESC LIMIT 200').fetchall()
    return {'items':[dict(r) for r in rows if user['role']=='admin' or (r['job_id'] in jobs and security.can_job(user,jobs[r['job_id']],'view'))]}


@app.get('/api/runs/{run_id}')
def read_run(run_id:int,request:Request):
    user=security.require(request)
    with db.session() as con:
        run=con.execute('SELECT id,job_id,job_name,status,trigger,requested_by,queued_at,started_at,finished_at,error,notification_status,notification_error FROM runs WHERE id=?',(run_id,)).fetchone()
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



# SSH inventory is separate from the encrypted login/key vault.
# Manual host-key onboarding: admin-only, no private credentials sent.
@app.post('/api/ssh/host-key/inspect')
def inspect_ssh_host_key(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    if set(payload)-{'host','port'}:
        raise HTTPException(422,'Paramètres SSH inconnus')
    try:
        result=host_keys.inspect(payload.get('host'),payload.get('port',22))
    except host_keys.HostKeyError as exc:
        db.audit(user['username'],'ssh.hostkey.inspect.failed','ssh','lecture seule')
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'ssh.hostkey.inspect',result['host'],'empreinte publique consultée')
    return result


@app.post('/api/ssh/host-key/approve')
def approve_ssh_host_key(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    if set(payload)-{'host','port','fingerprint','verified_out_of_band'}:
        raise HTTPException(422,'Paramètres de confiance SSH inconnus')
    db.audit(user['username'],'ssh.hostkey.approve.attempt','ssh','confirmation requise')
    try:
        result=host_keys.approve(
            payload.get('host'),payload.get('port',22),
            payload.get('fingerprint'),payload.get('verified_out_of_band',False))
    except host_keys.HostKeyError as exc:
        db.audit(user['username'],'ssh.hostkey.approve.failed','ssh','empreinte non approuvée')
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'ssh.hostkey.approve',result['host'],
             'empreinte publique '+result['fingerprint'])
    return result


@app.get('/api/ssh/inventory')
def get_ssh_inventory(request:Request):
    security.require(request,role='admin')
    return ssh_inventory.inventory()


@app.post('/api/ssh/groups')
def add_ssh_group(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        gid=ssh_inventory.create_group(payload)
    except ssh_inventory.InventoryError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'ssh.group.create',gid,str(payload.get('name',''))[:80])
    return {'id':gid}


@app.put('/api/ssh/groups/{group_id}')
def edit_ssh_group(group_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        ssh_inventory.update_group(group_id,payload)
    except ssh_inventory.InventoryError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'ssh.group.update',group_id,str(payload.get('name',''))[:80])
    return {'ok':True}


@app.delete('/api/ssh/groups/{group_id}')
def remove_ssh_group(group_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        ssh_inventory.delete_group(group_id)
    except ssh_inventory.InventoryError as exc:
        raise HTTPException(404,str(exc)) from None
    db.audit(user['username'],'ssh.group.delete',group_id,'Serveurs conservés')
    return {'ok':True}


@app.post('/api/ssh/servers')
def add_ssh_server(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        sid=ssh_inventory.create_server(payload)
    except ssh_inventory.InventoryError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'ssh.server.create',sid,str(payload.get('fqdn',''))[:150])
    return {'id':sid}


@app.put('/api/ssh/servers/{server_id}')
def edit_ssh_server(server_id:int,payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        ssh_inventory.update_server(server_id,payload)
    except ssh_inventory.InventoryError as exc:
        raise HTTPException(422,str(exc)) from None
    db.audit(user['username'],'ssh.server.update',server_id,'Configuration serveur')
    return {'ok':True}


@app.delete('/api/ssh/servers/{server_id}')
def remove_ssh_server(server_id:int,request:Request):
    user=security.require(request,role='admin',mutate=True)
    try:
        ssh_inventory.delete_server(server_id)
    except ssh_inventory.InventoryError as exc:
        raise HTTPException(409,str(exc)) from None
    db.audit(user['username'],'ssh.server.delete',server_id,'Entrée retirée de l’inventaire')
    return {'ok':True}


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
def test_credential(credential_id:int,request:Request,payload:dict[str,Any]|None=None):
    user=security.require(request,role='admin',mutate=True)
    params=payload or {}
    options=params.get('options',{})
    if (set(params)-{'options'} or not isinstance(options,dict) or
        len(json.dumps(options))>5000):
        raise HTTPException(422,'Options de diagnostic invalides')
    with db.session() as con:
        row=con.execute('SELECT kind,encrypted_payload FROM credentials WHERE id=?',(credential_id,)).fetchone()
    if not row: raise HTTPException(404,'Connexion introuvable')
    sending=options.get('send_email') is True or options.get('send_message') is True
    action='vault.test.send' if sending else 'vault.test'
    if sending:
        # Log the action before attempting network I/O; never log recipient or message body.
        db.audit(user['username'],'vault.test.send.attempt',credential_id,row['kind'])
    started=time.monotonic()
    try:
        values=security.decrypt(row['encrypted_payload'])
        if row['kind']=='ssh':
            # An SSH identity is reusable: test against a one-off FQDN without
            # persisting that host in the vault or server inventory.
            target_id=options.get('server_id')
            manual_host=options.get('manual_host')
            manual_port=options.get('manual_port')
            if target_id is not None and (manual_host is not None or manual_port is not None):
                raise connection_tests.ConnectionTestError('Choisir une cible SSH : inventaire ou FQDN manuel, pas les deux')
            if manual_host is not None or manual_port is not None:
                if not isinstance(manual_host,str):
                    raise connection_tests.ConnectionTestError('Saisir un FQDN SSH pour le test manuel')
                if manual_port is None:
                    manual_port=22
                if not isinstance(manual_port,(int,str)) or isinstance(manual_port,bool):
                    raise connection_tests.ConnectionTestError('Port SSH manuel invalide')
                values.update(host=ssh_inventory.fqdn(manual_host),port=ssh_inventory.port(manual_port))
            elif target_id is not None:
                target=ssh_inventory.server(target_id)
                values.update(host=target['fqdn'],port=target['port'])
            elif not values.get('host'):
                raise connection_tests.ConnectionTestError('Saisir un FQDN SSH ou choisir un serveur de l’inventaire')
            options={k:v for k,v in options.items()
                     if k not in ('server_id','manual_host','manual_port')}
        result=connection_tests.run(row['kind'],values,
                                    options=options,credential_id=credential_id)
    except ssh_inventory.InventoryError as exc:
        db.audit(user['username'],action+'.failed',credential_id,'ssh target')
        raise HTTPException(422,str(exc)) from None
    except connection_tests.ConnectionTestError as exc:
        db.audit(user['username'],action+'.failed',credential_id,row['kind'])
        raise HTTPException(422,str(exc)) from None
    except Exception as exc:
        db.audit(user['username'],action+'.failed',credential_id,row['kind'])
        # Never return raw exceptions, URLs with tokens, or decrypted credentials.
        raise HTTPException(422,'Test '+str(row['kind'])+' impossible ('+type(exc).__name__+')') from None
    db.audit(user['username'],action,credential_id,row['kind'])
    result['elapsed_ms']=round((time.monotonic()-started)*1000)
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
    heartbeat_path=config.DATA/'update-worker-heartbeat.json'
    worker_online=False
    if heartbeat_path.is_file():
        try:
            worker_online=0<=time.time()-heartbeat_path.stat().st_mtime<25
        except OSError:
            pass
    return {'version':config.APP_VERSION,'git_branch':config.GIT_BRANCH,
            'worker':info,'worker_online':worker_online,
            'requests':[dict(x) for x in rows]}


@app.post('/api/updates/request')
def request_update(payload:dict[str,Any],request:Request):
    user=security.require(request,role='admin',mutate=True)
    action=payload.get('action')
    if action not in ('check','preflight','apply'):
        raise HTTPException(422,'Action inconnue')
    with db.session() as con:
        queued=con.execute("SELECT COUNT(*) FROM updates WHERE status IN ('queued','running')").fetchone()[0]
        if queued: raise HTTPException(409,'Une opération Git est déjà en cours')
        cur=con.execute('INSERT INTO updates (action,actor,created_at,status) VALUES (?,?,?,?)',
                        (action,user['username'],db.now(),'queued'))
    db.audit(user['username'],'update.'+action,cur.lastrowid)
    return {'request_id':cur.lastrowid}
