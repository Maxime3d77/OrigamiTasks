"""Agent enrollment, dispatch, leases and optional SSH provisioning."""
import io
import json
import hmac
import math
import re
import secrets
import shlex
import time
import base64
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse
from fastapi import HTTPException
from . import config, db, security, agent_modules, agent_settings, agent_updates

ACTIONS = {'inventory', 'script', 'processes', 'disk', 'service_status',
           'service_start', 'service_stop', 'service_restart'}
MUTATIONS = {'service_start', 'service_stop', 'service_restart'}
MAX_PAYLOAD = 20000


def utc_plus(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='seconds')


def ensure_https(url):
    parsed = urlparse(str(url))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Agent endpoint must be an HTTPS origin without query or credentials')
    if parsed.path not in ('', '/'):
        raise ValueError('Agent endpoint must be an HTTPS origin')
    return str(url).rstrip('/')


def register(name, platform, group='default'):
    if platform not in ('linux', 'windows'):
        raise HTTPException(422, 'Système agent inconnu')
    if not re.fullmatch(r'[A-Za-z0-9_. -]{1,100}', str(name)) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', str(group)):
        raise HTTPException(422, 'Nom ou groupe agent invalide')
    token = secrets.token_urlsafe(40)
    with db.session() as con:
        cur = con.execute(
            'INSERT INTO agents (name,platform,group_name,enroll_hash,enroll_expires,enrolled,created_at) VALUES (?,?,?,?,?,0,?)',
            (name, platform, group, security.sha(token), utc_plus(900), db.now()))
        agent_id = cur.lastrowid
    return {'id': agent_id, 'enrollment_token': token, 'expires_in': 900}


def enroll(body):
    try:
        id_ = int(body['agent_id'])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(401, 'Enrollment denied')
    token = str(body.get('enrollment_token') or '')
    if not 24 <= len(token) <= 200:
        raise HTTPException(401, 'Enrollment denied')
    platform = str(body.get('platform') or '')
    hostname = str(body.get('hostname') or '')[:120]
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', hostname):
        raise HTTPException(422, 'Hostname invalide')
    new_token = secrets.token_urlsafe(48)
    with db.session() as con:
        row = con.execute('SELECT * FROM agents WHERE id=?', (id_,)).fetchone()
        if not row or row['revoked'] or row['enrolled'] or row['platform'] != platform or not row['enroll_hash'] or row['enroll_expires'] <= db.now() or not hmac.compare_digest(row['enroll_hash'], security.sha(token)):
            raise HTTPException(401, 'Enrollment denied')
        changed = con.execute(
            'UPDATE agents SET enrolled=1,token_hash=?,enroll_hash=NULL,enroll_expires=NULL,hostname=?,last_seen=? WHERE id=? AND enrolled=0 AND revoked=0',
            (security.sha(new_token), hostname, db.now(), id_)).rowcount
        if changed != 1:
            raise HTTPException(409, 'Agent already enrolled')
    db.audit('agent', 'agent.enroll', id_, hostname)
    return {'agent_id': id_, 'token': new_token}


def authenticate(request):
    try:
        id_ = int(request.headers.get('X-Origami-Agent-ID', ''))
    except ValueError:
        raise HTTPException(401, 'Agent authorization required')
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer ') or not 24 <= len(auth[7:]) <= 200:
        raise HTTPException(401, 'Agent authorization required')
    with db.session() as con:
        agent = con.execute('SELECT * FROM agents WHERE id=? AND enrolled=1 AND revoked=0', (id_,)).fetchone()
    if not agent or not agent['token_hash'] or not hmac.compare_digest(agent['token_hash'], security.sha(auth[7:])):
        raise HTTPException(401, 'Agent authorization required')
    return dict(agent)


def poll(agent, body):
    hostname = str(body.get('hostname') or '')[:120]
    version = str(body.get('version') or '')[:40]
    if hostname and not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', hostname):
        raise HTTPException(422, 'Hostname invalide')
    cpu = body.get('cpu_percent')
    memory = body.get('memory_mb')
    def metric(value, maximum):
        if value is None:
            return None
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=maximum:
            raise HTTPException(422, 'Métrique agent invalide')
        return round(float(value),2)
    cpu = metric(cpu, 1000)
    memory = metric(memory, 262144)
    with db.session() as con:
        con.execute("UPDATE agents SET last_seen=?,hostname=CASE WHEN ?<>'' THEN ? ELSE hostname END,"
                    "version=?,cpu_percent=?,memory_mb=? WHERE id=?",
                    (db.now(),hostname,hostname,version,cpu,memory,agent['id']))
        agent_modules.report(agent['id'],body,con)
        agent_updates.reconcile(agent['id'],version,con)
        modules_bundle=agent_modules.bundle(agent['id'],con)
        task = con.execute(
            "SELECT * FROM agent_tasks WHERE agent_id=? AND status='queued' ORDER BY id LIMIT 1", (agent['id'],)).fetchone()
        if not task:
            return {'task': None, **modules_bundle}
        lease = secrets.token_urlsafe(32)
        updated = con.execute(
            "UPDATE agent_tasks SET status='claimed', lease_hash=?, claimed_at=? WHERE id=? AND status='queued'",
            (security.sha(lease), db.now(), task['id'])).rowcount
        if not updated:
            return {'task': None, **modules_bundle}
    return {'task': {'id':task['id'], 'lease':lease, 'action':task['action'],
                     'payload':json.loads(task['payload_json']), 'timeout':task['timeout_seconds']},
            **modules_bundle}


def report(agent, body):
    try:
        task_id = int(body['task_id'])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, 'Identifiant de tâche invalide')
    lease = str(body.get('lease') or '')
    if not 24 <= len(lease) <= 200:
        raise HTTPException(401, 'Invalid lease')
    output = str(body.get('output') or '')[:config.MAX_LOG_BYTES]
    code = int(body.get('exit_code', 1))
    if code < -500 or code > 100000:
        raise HTTPException(422, 'Code retour invalide')
    with db.session() as con:
        task = con.execute('SELECT * FROM agent_tasks WHERE id=? AND agent_id=?',
                           (task_id, agent['id'])).fetchone()
        if not task or task['status'] != 'claimed' or not task['lease_hash'] or not hmac.compare_digest(task['lease_hash'], security.sha(lease)):
            raise HTTPException(409, 'Lease revoked or task not claimed')
        con.execute('UPDATE agent_tasks SET status=?, exit_code=?, output=?, completed_at=?, lease_hash=NULL WHERE id=?',
                    ('success' if code == 0 else 'failed', code, output, db.now(), task_id))
        con.execute('UPDATE agents SET last_seen=? WHERE id=?', (db.now(),agent['id']))
    return {'ok': True}


def execute(opts, ctx, timeout):
    try:
        agent_id = int(opts.get('agent_id'))
    except (TypeError, ValueError):
        raise ValueError('Select a valid agent')
    action = str(opts.get('action') or 'inventory')
    if action not in ACTIONS:
        raise ValueError('Unknown agent action')
    if action == 'script' and not config.ALLOW_AGENT_SCRIPTS:
        raise ValueError('Agent scripts disabled (ALLOW_AGENT_SCRIPTS=false)')
    if action in MUTATIONS and (not config.ALLOW_AGENT_MUTATIONS or opts.get('approved') is not True):
        raise ValueError('Service mutations require ALLOW_AGENT_MUTATIONS=true and approved=true')
    payload = {}
    if action == 'script':
        script = str(opts.get('script') or '')
        if not script.strip() or len(script) > 16000:
            raise ValueError('Script required (max 16 KB)')
        payload['script'] = script
    if action.startswith('service_'):
        service = str(opts.get('service') or '')
        if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,120}', service):
            raise ValueError('Invalid service name')
        payload['service'] = service
    timeout = min(max(1, int(timeout)), config.MAX_STEP_SECONDS)
    with db.session() as con:
        agent = con.execute('SELECT id,name,platform,last_seen FROM agents WHERE id=? AND enrolled=1 AND revoked=0',
                            (agent_id,)).fetchone()
        if not agent:
            raise ValueError('Agent not enrolled or revoked')
        if not agent['last_seen'] or agent['last_seen'] < utc_plus(-90):
            raise ValueError('Agent offline (no heartbeat in 90 seconds)')
        cur = con.execute(
            'INSERT INTO agent_tasks(agent_id,run_id,step_id,action,payload_json,status,timeout_seconds,queued_at) VALUES(?,?,?,?,?,?,?,?)',
            (agent_id, ctx['run_id'], ctx['step_id'], action, json.dumps(payload), 'queued', timeout, db.now()))
        task_id = cur.lastrowid
    limit = time.monotonic() + timeout + 10
    while time.monotonic() < limit:
        with db.session() as con:
            row = con.execute('SELECT status,exit_code,output FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
        if row['status'] in ('success','failed'):
            return {'exit_code':row['exit_code'], 'output':row['output'],
                    'status':row['status']}
        if row['status'] == 'interrupted':
            raise ValueError('Agent task interrupted or agent revoked')
        time.sleep(0.4)
    with db.session() as con:
        con.execute("UPDATE agent_tasks SET status='expired', lease_hash=NULL WHERE id=? AND status IN ('queued','claimed')", (task_id,))
    raise ValueError('Agent task timed out. No automatic replay; check the target before retrying.')


def revoke(agent_id):
    with db.session() as con:
        changed = con.execute('UPDATE agents SET revoked=1,token_hash=NULL,enroll_hash=NULL WHERE id=? AND revoked=0', (agent_id,)).rowcount
        con.execute("UPDATE agent_tasks SET status='interrupted',lease_hash=NULL WHERE agent_id=? AND status IN ('queued','claimed')", (agent_id,))
    if not changed:
        raise HTTPException(404, 'Agent not found or already revoked')


def deploy(body, *, cluster_authorized=False):
    if not config.ALLOW_AGENT_REMOTE_DEPLOY and not cluster_authorized:
        raise HTTPException(403, 'Enable ALLOW_AGENT_REMOTE_DEPLOY=true to provision from the UI')
    if body.get('approved') is not True:
        raise HTTPException(422, 'Explicit installation confirmation required')
    try:
        endpoint = agent_settings.effective_url()
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    host = str(body.get('host') or '').strip()
    allowed = {x.strip().lower() for x in config.AGENT_DEPLOY_ALLOWED_HOSTS.split(',') if x.strip()}
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', host):
        raise HTTPException(422, 'FQDN de la cible SSH invalide')
    # A cluster group is an alternate per-target authorization set only for
    # the *internal* vetted bulk worker, never for the public deploy endpoint.
    if not cluster_authorized and (not allowed or host.lower() not in allowed):
        raise HTTPException(403, 'Target host not in AGENT_DEPLOY_ALLOWED_HOSTS')
    try:
        port = int(body.get('port') or 22)
        cred_id = int(body.get('credential_id'))
        if not 1 <= port <= 65535:
            raise ValueError()
    except (ValueError,TypeError):
        raise HTTPException(422, 'Invalid SSH port or credential ID')
    platform = str(body.get('platform') or '')
    name = str(body.get('name') or host)
    # Create enrollment only after all validation, but before provisioning.
    # Failed installs remain pending for 15 minutes; revoke if necessary.
    from .executor import _ssh, credential
    cred = credential(cred_id)
    client, _ = _ssh(None, {'host':host,'port':port},cred,120)
    result = None
    try:
        result = register(name, platform, str(body.get('group') or 'default'))
        config_data = {'server':endpoint, 'agent_id':result['id'],
                       'enrollment_token':result['enrollment_token']}
        sftp = client.open_sftp()
        try:
            base = sftp.normalize('.').rstrip('/')
            marker = 'origami_' + secrets.token_hex(8)
            is_windows = platform == 'windows'
            local_name = 'agent.ps1' if is_windows else 'agent.py'
            source = Path(__file__).resolve().parent.parent / 'agents' / ('windows/agent.ps1' if is_windows else 'linux/agent.py')
            files = {marker+'_'+local_name: source.read_bytes(),
                     marker+'_config.json': json.dumps(config_data).encode()}
            # One deployment bundle. Linux runs a second, isolated *passive*
            # PRA process from the same installation directory (no VM actions).
            if not is_windows:
                worker=Path(__file__).resolve().parent.parent / 'agents/linux/pra_worker.py'
                files[marker+'_pra_worker.py']=worker.read_bytes()
                # Root-only, bounded PRA helper libraries: shipped as part of the agent.
                for helper in ("pra_qmp_incremental.py","pra_checkpoint.py","pra_host_worker.py","pra_lvm_target.py"):
                    files[marker+"_"+helper]=(worker.parent/helper).read_bytes()
            for name_, data in files.items():
                with sftp.file(base+'/'+name_, 'wb') as f:
                    f.write(data)
                if not is_windows:
                    sftp.chmod(base+'/'+name_, 0o600)
        finally:
            sftp.close()
        if is_windows:
            def winpath(path):
                path = re.sub(r'^/([A-Za-z]:/)', r'\1', path)
                return path.replace('/', '\\')
            stage_script = winpath(base+'/'+marker+'_agent.ps1')
            stage_config = winpath(base+'/'+marker+'_config.json')
            # Use -EncodedCommand so no host-controlled text is parsed as PowerShell.
            ps = (
                "$ErrorActionPreference='Stop'; "
                "$dest=Join-Path $env:ProgramData 'OrigamiTasks\\Agent'; "
                "New-Item -ItemType Directory -Force -Path $dest | Out-Null; "
                "Copy-Item -LiteralPath '"+stage_script+"' -Destination (Join-Path $dest 'agent.ps1') -Force; "
                "Copy-Item -LiteralPath '"+stage_config+"' -Destination (Join-Path $dest 'config.json') -Force; "
                "$acl=Get-Acl $dest; $acl.SetAccessRuleProtection($true,$false); "
                "$acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }; "
                "foreach($id in @('SYSTEM','BUILTIN\\Administrators')) { "
                "$rule=New-Object System.Security.AccessControl.FileSystemAccessRule($id,'FullControl','ContainerInherit,ObjectInherit','None','Allow'); "
                "$acl.AddAccessRule($rule) }; Set-Acl -Path $dest -AclObject $acl; "
                "$act=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + [char]34 + (Join-Path $dest 'agent.ps1') + [char]34); "
                "$trig=New-ScheduledTaskTrigger -AtStartup; "
                "Register-ScheduledTask -TaskName 'OrigamiTasksAgent' -Action $act -Trigger $trig -User 'SYSTEM' -RunLevel Highest -Force | Out-Null; "
                "Start-ScheduledTask -TaskName 'OrigamiTasksAgent'; "
                "Remove-Item -LiteralPath '"+stage_script+"','"+stage_config+"' -Force; "
                "Write-Output 'Agent Windows scheduled task installed'"
            )
            command = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + base64.b64encode(ps.encode('utf-16le')).decode()
        else:
            stage_script = shlex.quote(base+'/'+marker+'_agent.py')
            stage_worker = shlex.quote(base+'/'+marker+'_pra_worker.py')
            stage_config = shlex.quote(base+'/'+marker+'_config.json')
            stage_privileged = {name:shlex.quote(base+'/'+marker+'_'+name) for name in
                                ('pra_qmp_incremental.py','pra_checkpoint.py','pra_host_worker.py','pra_lvm_target.py')}
            root_install = 'install -d -m 700 -o root -g root /usr/local/lib/origamitasks-pra; ' + ''.join(
                'install -m 500 -o root -g root '+source+' /usr/local/lib/origamitasks-pra/'+name+'; '
                for name,source in stage_privileged.items())
            install_script = (
                'set -eu; command -v python3 >/dev/null; command -v systemctl >/dev/null; '
                "id origamitasks-agent >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin origamitasks-agent; "
                'install -d -m 700 -o origamitasks-agent -g origamitasks-agent /opt/origamitasks-agent; '
                'install -m 500 -o origamitasks-agent -g origamitasks-agent '+stage_script+' /opt/origamitasks-agent/agent.py; '
                'install -m 500 -o origamitasks-agent -g origamitasks-agent '+stage_worker+' /opt/origamitasks-agent/pra_worker.py; '
                'install -m 600 -o origamitasks-agent -g origamitasks-agent '+stage_config+' /opt/origamitasks-agent/config.json; '
                +root_install+
                'printf "%s\\n" "[Unit]" "Description=OrigamiTasks outbound agent" "After=network-online.target" "Wants=network-online.target" '
                '"[Service]" "Type=simple" "User=origamitasks-agent" "Group=origamitasks-agent" '
                '"ExecStart=/usr/bin/env python3 /opt/origamitasks-agent/agent.py" "Restart=always" "RestartSec=10" '
                '"NoNewPrivileges=true" "ProtectSystem=strict" "ProtectHome=true" "ReadWritePaths=/opt/origamitasks-agent" "MemoryMax=192M" "CPUQuota=20%" "TasksMax=48" '
                '"[Install]" "WantedBy=multi-user.target" > /etc/systemd/system/origamitasks-agent.service; '
                'printf "%s\\n" "[Unit]" "Description=OrigamiTasks passive PRA module" "After=network-online.target" "Wants=network-online.target" '
                '"[Service]" "Type=simple" "User=origamitasks-agent" "Group=origamitasks-agent" '
                '"ExecStart=/usr/bin/env python3 /opt/origamitasks-agent/pra_worker.py" "Restart=always" "RestartSec=15" '
                '"NoNewPrivileges=true" "ProtectSystem=strict" "ProtectHome=true" "ReadWritePaths=/opt/origamitasks-agent" '
                '"MemoryMax=64M" "CPUQuota=10%" "TasksMax=24" '
                '"[Install]" "WantedBy=multi-user.target" > /etc/systemd/system/origamitasks-agent-pra.service; '
                'systemctl daemon-reload; systemctl enable --now origamitasks-agent.service; '
                'systemctl enable --now origamitasks-agent-pra.service; '
                'rm -f '+stage_script+' '+stage_config+' '+stage_worker+' '+ ' '.join(stage_privileged.values())
            )
            command = 'sh -c '+shlex.quote("if [ \"$(id -u)\" -eq 0 ]; then exec sh -c "+shlex.quote(install_script)+"; else exec sudo -n sh -c "+shlex.quote(install_script)+"; fi")
        _, stdout, stderr = client.exec_command(command, timeout=120)
        code = stdout.channel.recv_exit_status()
        if code:
            raise RuntimeError('Remote install exited with code '+str(code)+' (verify SSH privileges, PowerShell or systemd prerequisites)')
        # Record exactly what went into this machine's config.json. Changing
        # the control-plane URL later must not break verified remote removal.
        with db.session() as con:
            con.execute("UPDATE agents SET installed_controller_url=? WHERE id=?",
                        (endpoint,result['id']))
        return {'id':result['id'], 'name':name, 'platform':platform,
                'status':'installed_pending_enrollment'}
    except Exception:
        if result:
            revoke(result['id'])
        raise
    finally:
        client.close()
