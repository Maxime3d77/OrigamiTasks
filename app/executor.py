"""Task adapters. All scripts are trusted privileged automation, never user sandboxing."""
import base64
import mimetypes
import shutil
import ftplib
import io
import os
import re
import shlex
import smtplib
import subprocess
import time
from email.message import EmailMessage
from pathlib import Path
import httpx
from . import db, config, security, integrations, agents, directory, connections, ldap_settings, ssh_inventory


class StepError(Exception):
    def __init__(self, message, output='', code=1):
        super().__init__(message)
        self.output = output or message
        self.code = code


def credential(id_):
    if not id_:
        return {}
    with db.session() as con:
        row = con.execute('SELECT encrypted_payload FROM credentials WHERE id=?', (int(id_),)).fetchone()
    if not row:
        raise StepError('Identifiant de coffre introuvable')
    return security.decrypt(row['encrypted_payload'])


def scrub(text, secrets):
    result = str(text)
    for v in secrets.values():
        if isinstance(v, str) and len(v) >= 4:
            result = result.replace(v, '[SECRET]')
    return result[:config.MAX_LOG_BYTES]


def resolve_local(path):
    """All transfer paths stay in mounted transfer storage, not system configuration."""
    if not path:
        raise StepError('Chemin local manquant')
    root = config.TRANSFER_ROOT.resolve()
    p = (root / path.lstrip('/')).resolve()
    if not p.is_relative_to(root) or p == root:
        raise StepError('Chemin hors de l’espace transferts')
    return p


def interpolate(text, ctx):
    """Limited template syntax, never calls eval or exposes secrets."""
    if not isinstance(text, str):
        return text
    def repl(match):
        key = match.group(1).strip()
        if key == 'run.id':
            return str(ctx['run_id'])
        if key.startswith('var.'):
            return str(ctx['variables'].get(key[4:], ''))
        if key.startswith('steps.'):
            pieces = key.split('.')
            if len(pieces) == 3 and pieces[2] in ('output', 'status', 'exit_code'):
                return str(ctx['steps'].get(pieces[1], {}).get(pieces[2], ''))
        return match.group(0)
    return re.sub(r'\{\{\s*([^{}]+)\s*\}\}', repl, text)


def expand(value, ctx):
    if isinstance(value, str):
        return interpolate(value, ctx)
    if isinstance(value, dict):
        return {k: expand(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, ctx) for v in value]
    return value


def subprocess_step(command, env, timeout):
    try:
        p = subprocess.run(command, capture_output=True, text=True, errors='replace',
                           env=env, cwd=str(config.TRANSFER_ROOT), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise StepError(f'Délai dépassé ({timeout}s)', (exc.stdout or b'').decode(errors='replace') if isinstance(exc.stdout,bytes) else str(exc.stdout or ''), 124)
    except FileNotFoundError as exc:
        raise StepError('Exécutable non disponible sur cet hôte : '+str(exc))
    output = ((p.stdout or '') + ('\n[STDERR]\n'+p.stderr if p.stderr else '')).strip()
    return p.returncode, output


def _ssh(command, opts, cred, timeout):
    try:
        import paramiko
    except ImportError:
        raise StepError('Installer paramiko pour les tâches SSH et SFTP')
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if config.KNOWN_HOSTS.exists():
        client.load_host_keys(str(config.KNOWN_HOSTS))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    user = cred.get('username') or opts.get('username')
    if not user:
        raise StepError('Utilisateur SSH manquant')
    # A selected inventory target takes precedence over the optional legacy host
    # embedded in old vault profiles; never log private key/password material.
    if opts.get('ssh_server_id'):
        try:
            target=ssh_inventory.server(opts['ssh_server_id'])
        except ssh_inventory.InventoryError as exc:
            raise StepError(str(exc)) from None
        target_host, target_port=target['fqdn'],target['port']
    else:
        target_host=opts.get('host') or cred.get('host')
        target_port=opts.get('port') or cred.get('port') or 22
    if not target_host:
        raise StepError('Serveur SSH manquant : sélectionner un serveur dans l’inventaire')
    kwargs = dict(hostname=target_host, port=int(target_port), username=user,
                  timeout=min(timeout, 30), auth_timeout=min(timeout, 30), look_for_keys=False, allow_agent=False)
    if cred.get('private_key'):
        key = cred['private_key']
        for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                kwargs['pkey'] = cls.from_private_key(io.StringIO(key), password=cred.get('passphrase') or None)
                break
            except (paramiko.SSHException, ValueError):
                continue
        if 'pkey' not in kwargs:
            raise StepError('Clé privée SSH invalide')
    else:
        kwargs['password'] = cred.get('password')
    try:
        client.connect(**kwargs)
        if command is None:
            return client, None
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        # Paramiko ChannelFile.read may block when output is huge; use a polling loop.
        ch = stdout.channel
        chunks, errors = [], []
        start = time.monotonic()
        budget = config.MAX_LOG_BYTES * 4
        while not ch.exit_status_ready() or ch.recv_ready() or ch.recv_stderr_ready():
            if time.monotonic() - start > timeout:
                ch.close()
                raise StepError(f'SSH : délai dépassé ({timeout}s)', code=124)
            if ch.recv_ready():
                part = ch.recv(65536)
                if sum(map(len,chunks)) < budget: chunks.append(part)
            if ch.recv_stderr_ready():
                part = ch.recv_stderr(65536)
                if sum(map(len,errors)) < budget: errors.append(part)
            if not ch.recv_ready() and not ch.recv_stderr_ready():
                time.sleep(0.05)
        code = ch.recv_exit_status()
        out = b''.join(chunks).decode(errors='replace')
        err = b''.join(errors).decode(errors='replace')
        return code, (out + ('\n[STDERR]\n' + err if err else '')).strip()
    finally:
        if command is not None:
            client.close()


def execute(step, ctx):
    typ = step.get('type')
    opts = expand(step.get('config') or {}, ctx)
    cred = credential(opts.get('credential_id'))
    timeout = min(max(int(opts.get('timeout') or 60), 1), config.MAX_STEP_SECONDS)
    if typ in ('shell', 'powershell'):
        if not config.ALLOW_LOCAL_EXECUTION:
            raise StepError('Exécution locale désactivée. Utiliser SSH ou activer explicitement ALLOW_LOCAL_EXECUTION=true après évaluation des risques.')
        script = opts.get('script', '')
        if not script:
            raise StepError('Script vide')
        env = {k:v for k,v in os.environ.items() if k not in ('VAULT_KEY','ADMIN_PASSWORD','LDAP_BIND_PASSWORD','LDAP_CA_CERT') and not k.startswith('ORIGAMI_SECRET_')}
        # no arbitrary environment variable names from credentials
        if cred:
            for key in ('username', 'password', 'token'):
                if cred.get(key):
                    env['ORIGAMI_' + key.upper()] = cred[key]
        if typ == 'shell':
            code, output = subprocess_step(['/bin/bash', '-c', script], env, timeout)
        else:
            code, output = subprocess_step(['pwsh', '-NoProfile', '-NonInteractive', '-Command', script], env, timeout)
    elif typ == 'ssh':
        script = opts.get('script', '')
        if opts.get('platform') == 'windows':
            cmd = f'powershell.exe -NoLogo -NonInteractive -NoProfile -EncodedCommand {base64.b64encode(script.encode("utf-16le")).decode()}'
        else:
            cmd = 'bash -lc ' + shlex.quote(script)
        code, output = _ssh(cmd, opts, cred, timeout)
    elif typ == 'agent':
        try:
            # Keep the engine's current graph step ID for durable remote audit.
            return agents.execute(opts, dict(ctx,step_id=step['id']), timeout)
        except (ValueError,TypeError) as exc:
            raise StepError('Agent : '+str(exc))
    elif typ == 'directory':
        if not opts.get('credential_id') and not ldap_settings.current()['enabled']:
            raise StepError('LDAP/AD : choisir une connexion de coffre ou configurer Administration → LDAP')
        try:
            output = directory.execute(opts,cred)
        except (directory.DirectoryError,ValueError) as exc:
            raise StepError('LDAP/AD : '+str(exc))
        code = 0
    elif typ == 'matrix':
        try:
            output = integrations.matrix_message(dict(opts,homeserver=opts.get('homeserver') or cred.get('homeserver')),cred,ctx['run_id'],step['id'],timeout)
        except (integrations.IntegrationError,httpx.HTTPError,ValueError) as exc:
            raise StepError('Matrix : '+type(exc).__name__+' (vérifier URL, accès room et coffre)')
        code = 0
    elif typ == 'ansible':
        try:
            cmd = integrations.ansible_command(opts)
        except integrations.IntegrationError as exc:
            raise StepError('Ansible : '+str(exc))
        code,output = _ssh(cmd,opts,cred,timeout)
    elif typ == 'proxmox':
        try:
            output=integrations.proxmox_task(opts,credential,timeout)
        except (integrations.IntegrationError,httpx.HTTPError,ValueError) as exc:
            raise StepError('Proxmox : '+str(exc) if isinstance(exc,integrations.IntegrationError) else 'Proxmox : échec API '+type(exc).__name__)
        code = 0
    elif typ == 'set_variable':
        name = str(opts.get('name','')).strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name):
            raise StepError('Nom de variable invalide')
        code, output = 0, f'Variable {name} définie'
    elif typ == 'delay':
        seconds = min(max(int(opts.get('seconds', 1)), 0), 300)
        time.sleep(seconds)
        code, output = 0, f'Pause {seconds}s terminée'
    elif typ == 'http':
        headers = dict(opts.get('headers') or {})
        if cred.get('token'):
            headers['Authorization'] = 'Bearer ' + cred['token']
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                r = client.request(opts.get('method', 'GET').upper(), opts.get('url') or cred.get('base_url') or '',
                                   headers=headers, content=opts.get('body') or None)
            code = 0 if 200 <= r.status_code < 400 else 1
            output = f'HTTP {r.status_code}\n{r.text[:config.MAX_LOG_BYTES]}'
        except httpx.RequestError as exc:
            raise StepError('HTTP : '+str(exc))
    elif typ == 'email':
        if not (opts.get('host') or cred.get('host')):
            raise StepError('SMTP : serveur manquant dans la tâche et le coffre')
        if not (opts.get('from') or cred.get('from_address')):
            raise StepError('SMTP : adresse expéditeur manquante')
        if not opts.get('to'):
            raise StepError('SMTP : destinataires manquants')
        msg = EmailMessage()
        msg['From'] = opts.get('from') or cred.get('from_address') or ''
        msg['To'] = opts.get('to') or ''
        msg['Subject'] = opts.get('subject', 'OrigamiTasks')
        msg.set_content(opts.get('body', ''))
        if opts.get('html_body'):
            msg.add_alternative(str(opts['html_body']), subtype='html')
        attachments = opts.get('attachments') or []
        if isinstance(attachments, str):
            attachments = [x.strip() for x in attachments.split(',') if x.strip()]
        if not isinstance(attachments,list) or len(attachments)>10:
            raise StepError('Liste de pièces jointes invalide (10 maximum)')
        total = 0
        for item in attachments:
            file = resolve_local(str(item))
            if not file.is_file():
                raise StepError('Pièce jointe introuvable dans transfers/')
            length = file.stat().st_size
            total += length
            if total > 10_000_000:
                raise StepError('Pièces jointes limitées à 10 Mo au total')
            media = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            main_type,sub_type = media.split('/',1)
            msg.add_attachment(file.read_bytes(), maintype=main_type, subtype=sub_type, filename=file.name)
        mode = 'ssl' if opts.get('ssl') else cred.get('tls_mode') or 'starttls'
        profile = dict(cred, host=opts.get('host') or cred.get('host'),
                       port=opts.get('port') or cred.get('port') or (465 if mode=='ssl' else 587),
                       tls_mode=mode)
        try:
            conn=connections.open_smtp(profile,timeout=timeout)
        except Exception as exc:
            raise StepError('SMTP : échec de connexion/authentification ('+type(exc).__name__+')') from None
        try:
            conn.send_message(msg)
        except Exception as exc:
            raise StepError('SMTP : échec d’envoi ('+type(exc).__name__+')') from None
        finally:
            try:
                conn.quit()
            except (OSError, smtplib.SMTPException):
                conn.close()
        code, output = 0, 'Message envoyé à ' + opts['to']
    elif typ == 'file':
        action = str(opts.get('action') or '')
        path = resolve_local(str(opts.get('path') or ''))
        if action == 'list':
            if not path.is_dir():
                raise StepError('Répertoire introuvable')
            items = sorted(x.name for x in path.iterdir())[:1000]
            code, output = 0, '\n'.join(items)
        elif action == 'read':
            if not path.is_file():
                raise StepError('Fichier introuvable')
            with path.open('rb') as f:
                contents = f.read(config.MAX_LOG_BYTES + 1)
            if len(contents) > config.MAX_LOG_BYTES:
                raise StepError('Fichier trop volumineux pour une lecture en sortie de tâche')
            code, output = 0, contents.decode('utf-8',errors='replace')
        elif action == 'write':
            data = str(opts.get('content') or '').encode('utf-8')
            if len(data) > 100_000:
                raise StepError('Écriture de fichier limitée à 100 Ko')
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data)
            code, output = 0, f'Écriture de {len(data)} octets dans le dossier de transferts'
        elif action in ('copy','move'):
            source = resolve_local(str(opts.get('source') or ''))
            if not source.is_file():
                raise StepError('Fichier source introuvable')
            if source == path:
                raise StepError('La source et la destination sont identiques')
            path.parent.mkdir(parents=True,exist_ok=True)
            if action == 'copy':
                shutil.copy2(source,path)
            else:
                shutil.move(str(source),str(path))
            code, output = 0, f'Fichier {action} réussi dans le dossier de transferts'
        elif action == 'mkdir':
            path.mkdir(parents=True,exist_ok=True)
            code, output = 0, 'Répertoire créé dans le dossier de transferts'
        elif action == 'delete':
            if opts.get('approved') is not True:
                raise StepError('Suppression de fichier soumise à confirmation explicite')
            if not path.is_file():
                raise StepError('Fichier introuvable')
            path.unlink()
            code, output = 0, 'Fichier supprimé du dossier de transferts'
        else:
            raise StepError('Action fichier inconnue')
    elif typ == 'ftp':
        # No plaintext FTP by default. Require explicit opt-in for legacy servers.
        plain = bool(opts.get('allow_plaintext', False) or cred.get('tls_mode')=='none')
        client = ftplib.FTP() if plain else ftplib.FTP_TLS()
        try:
            client.connect(opts.get('host') or cred.get('host'), int(opts.get('port') or cred.get('port') or 21), timeout=timeout)
            client.login(cred.get('username') or opts.get('username', 'anonymous'), cred.get('password') or '')
            if not plain: client.prot_p()
            local = resolve_local(opts['local_path'])
            remote = opts['remote_path']
            if opts.get('direction', 'get') == 'put':
                with local.open('rb') as f: client.storbinary('STOR ' + remote, f)
            else:
                local.parent.mkdir(parents=True, exist_ok=True)
                with local.open('wb') as f: client.retrbinary('RETR ' + remote, f.write)
            code, output = 0, f'FTP {opts.get("direction", "get")} {remote} réussi'
        finally:
            try: client.quit()
            except Exception: client.close()
    elif typ == 'sftp':
        client, _ = _ssh(None, opts, cred, timeout)
        try:
            sftp = client.open_sftp()
            try:
                local = resolve_local(opts['local_path'])
                remote = opts['remote_path']
                if opts.get('direction', 'get') == 'put':
                    sftp.put(str(local), remote)
                else:
                    local.parent.mkdir(parents=True, exist_ok=True)
                    sftp.get(remote, str(local))
                code, output = 0, f'SFTP {opts.get("direction", "get")} {remote} réussi'
            finally: sftp.close()
        finally: client.close()
    else:
        raise StepError('Type de tâche inconnu : '+str(typ))
    return {'exit_code': code, 'output': scrub(output, cred), 'status': 'success' if code == 0 else 'failed'}
