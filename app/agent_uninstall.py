"""Administrator-initiated, identity-checked remote agent removal over trusted SSH.

This removes only the known OrigamiTasks service/task and its own agent folder.
It never deletes agent history, SSH keys or other software. Revocation is
performed only after a successful remote removal.
"""
import base64
import shlex

from . import config, db, host_keys, ssh_inventory, agents


class AgentUninstallError(ValueError):
    pass


def _linux_command(agent_id, encoded_origin):
    script = (
        "set -eu; "
        "test -f /opt/origamitasks-agent/config.json; "
        "python3 -c " + shlex.quote(
            "import base64,json,sys;"
            "p=json.load(open('/opt/origamitasks-agent/config.json'));"
            "expected=base64.b64decode("+repr(encoded_origin)+").decode();"
            "sys.exit(0 if p.get('agent_id')=="+str(agent_id)+
            " and str(p.get('server','')).rstrip('/')==expected else 31)"
        ) + "; "
        "systemctl disable --now origamitasks-agent.service; "
        "if systemctl list-unit-files origamitasks-agent-pra.service --no-legend 2>/dev/null | grep -q origamitasks-agent-pra; then "
        "  systemctl disable --now origamitasks-agent-pra.service; fi; "
        "rm -f /etc/systemd/system/origamitasks-agent.service "
        "/etc/systemd/system/origamitasks-agent-pra.service; "
        "systemctl daemon-reload; "
        "rm -f /opt/origamitasks-agent/agent.py /opt/origamitasks-agent/pra_worker.py "
        "/opt/origamitasks-agent/modules.json /opt/origamitasks-agent/pra-state.json "
        "/opt/origamitasks-agent/config.json; "
        "rmdir /opt/origamitasks-agent; "
        "printf 'ORIGAMI_AGENT_REMOVED\\n'"
    )
    return 'sh -c '+shlex.quote(
        'if [ "$(id -u)" -eq 0 ]; then exec sh -c '+shlex.quote(script)+
        '; else exec sudo -n sh -c '+shlex.quote(script)+'; fi'
    )


def _windows_command(agent_id, encoded_origin):
    ps = (
        "$ErrorActionPreference='Stop'; "
        "$expected=[Text.Encoding]::UTF8.GetString("
        "[Convert]::FromBase64String('"+encoded_origin+"')); "
        "$dest=Join-Path $env:ProgramData 'OrigamiTasks\\Agent'; "
        "$path=Join-Path $dest 'config.json'; "
        "if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw 'No agent installed'}; "
        "$cfg=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json; "
        "if([int]$cfg.agent_id -ne "+str(agent_id)+" -or "
        "([string]$cfg.server).TrimEnd('/') -cne $expected){throw 'Agent identity mismatch'}; "
        "$task=Get-ScheduledTask -TaskName 'OrigamiTasksAgent' -ErrorAction SilentlyContinue; "
        "if($task){ "
        "Stop-ScheduledTask -TaskName 'OrigamiTasksAgent' -ErrorAction SilentlyContinue; "
        "Unregister-ScheduledTask -TaskName 'OrigamiTasksAgent' -Confirm:$false -ErrorAction Stop }; "
        "Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction Stop; "
        "Write-Output 'ORIGAMI_AGENT_REMOVED'"
    )
    return ('powershell.exe -NoProfile -NonInteractive -EncodedCommand '+
            base64.b64encode(ps.encode('utf-16le')).decode('ascii'))


def uninstall(agent_id, body):
    if not isinstance(body,dict) or set(body)-{'host','port','credential_id','approved'}:
        raise AgentUninstallError("Paramètres de désinstallation invalides")
    if body.get('approved') is not True:
        raise AgentUninstallError("Confirmation explicite de la désinstallation requise")
    try:
        agent_id=int(agent_id)
        host=ssh_inventory.fqdn(body['host'])
        port=ssh_inventory.port(body.get('port',22))
        credential_id=body['credential_id']
        if type(credential_id) is not int or credential_id<=0:
            raise ValueError()
    except (ValueError,TypeError,KeyError,ssh_inventory.InventoryError):
        raise AgentUninstallError("Hôte SSH, port ou identité du coffre invalide") from None

    with db.session() as con:
        agent=con.execute(
            'SELECT id,hostname,platform,enrolled,revoked,uninstalled_at FROM agents WHERE id=?',
            (agent_id,)
        ).fetchone()
        cred=con.execute(
            'SELECT kind FROM credentials WHERE id=?',(credential_id,)
        ).fetchone()
    if not agent:
        raise AgentUninstallError("Agent introuvable")
    if agent['uninstalled_at']:
        raise AgentUninstallError("Agent déjà désinstallé")
    if not agent['enrolled'] or agent['platform'] not in ('linux','windows'):
        raise AgentUninstallError("Agent non enrôlé ou plateforme non prise en charge")
    if not cred or cred['kind']!='ssh':
        raise AgentUninstallError("Sélectionner une identité SSH du coffre")
    # A self-reported hostname alone does not grant uninstall rights:
    # the public SSH host key must also be previously pinned and unchanged.
    actual=str(agent['hostname'] or '').lower().rstrip('.')
    if not actual or not (host==actual or host.split('.')[0]==actual):
        raise AgentUninstallError(
            "Le FQDN SSH ne correspond pas au nom d'hôte du dernier enrôlement. "
            "Vérifier la machine cible avant de continuer."
        )
    try:
        origin=agents.ensure_https(config.AGENT_SERVER_URL)
    except ValueError:
        raise AgentUninstallError("Configurer une URL de contrôle HTTPS valide") from None
    try:
        inspected=host_keys.inspect(host,port)
    except host_keys.HostKeyError:
        raise AgentUninstallError("Impossible de vérifier la clé d'hôte SSH de la cible") from None
    if inspected['status']!='trusted':
        raise AgentUninstallError(
            "Clé d'hôte SSH non approuvée ou modifiée. "
            "Vérifier et enregistrer l'empreinte du serveur avant la désinstallation."
        )
    from .executor import _ssh,credential
    client=None
    try:
        cred_values=credential(credential_id)
        client,_=_ssh(None,{'host':host,'port':port},cred_values,80)
        encoded=base64.b64encode(origin.encode('utf-8')).decode('ascii')
        command=(_linux_command(agent_id,encoded) if agent['platform']=='linux'
                 else _windows_command(agent_id,encoded))
        _,stdout,_=client.exec_command(command,timeout=80)
        result=stdout.read(4096).decode('utf-8',errors='replace')
        exit_code=stdout.channel.recv_exit_status()
        if exit_code!=0 or 'ORIGAMI_AGENT_REMOVED' not in result:
            raise AgentUninstallError(
                "La suppression distante a échoué ou son identité n'a pas été confirmée. "
                "Vérifier les privilèges SSH/sudo, le service et son config.json. "
                "Le jeton de l'agent n'a pas été révoqué automatiquement."
            )
    except AgentUninstallError:
        raise
    except Exception:
        raise AgentUninstallError(
            "Connexion ou exécution SSH impossible : agent non désinstallé de façon confirmée. "
            "Vérifier la cible et le compte SSH."
        ) from None
    finally:
        if client is not None:
            client.close()

    with db.session() as con:
        con.execute(
            'UPDATE agents SET uninstalled_at=?,revoked=1,token_hash=NULL,'
            'enroll_hash=NULL WHERE id=?',(db.now(),agent_id)
        )
        con.execute(
            "UPDATE agent_tasks SET status='interrupted',lease_hash=NULL "
            "WHERE agent_id=? AND status IN ('queued','claimed')",(agent_id,)
        )
    return {'ok':True,'agent_id':agent_id,'host':host,'status':'uninstalled'}
