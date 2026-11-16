"""Persistent, bounded bulk agent provisioning queue.

Bulk requests never execute a privileged SSH session inside the HTTP request.
A single background worker processes an explicitly approved list, one host at a
time, and records successes/errors independently. Queued/running deployments
are interrupted on control-plane restart, never silently replayed.
"""
import json
import threading
import time
from . import agents, config, db, ssh_inventory

STOP = threading.Event()
_WORKER = None
MAX_HOSTS_PER_BATCH = 100


class DeploymentError(ValueError):
    pass


def _target(raw):
    if not isinstance(raw, dict) or set(raw) - {'server_id', 'host'}:
        raise DeploymentError("Cible invalide : server_id ou host")
    if raw.get('server_id') is not None:
        server = ssh_inventory.server(raw['server_id'])
        return server['fqdn'], server['port'], server['id']
    if raw.get('host'):
        text = str(raw['host']).strip()
        if ':' in text:
            host, port_text = text.rsplit(':', 1)
            port = ssh_inventory.port(port_text)
        else:
            host, port = text, 22
        return ssh_inventory.fqdn(host), port, None
    raise DeploymentError("Serveur sans FQDN")


def queue(payload, actor):
    if not config.ALLOW_AGENT_REMOTE_DEPLOY:
        raise DeploymentError("Déploiement distant désactivé (ALLOW_AGENT_REMOTE_DEPLOY)")
    if payload.get('approved') is not True:
        raise DeploymentError("Confirmation explicite du déploiement obligatoire")
    platform = str(payload.get('platform') or '')
    if platform not in ('linux', 'windows'):
        raise DeploymentError("Sélectionner Linux ou Windows pour ce lot")
    try:
        credential_id = int(payload.get('credential_id'))
    except (TypeError, ValueError):
        raise DeploymentError("Compte SSH obligatoire") from None
    with db.session() as con:
        cred = con.execute("SELECT kind FROM credentials WHERE id=?", (credential_id,)).fetchone()
    if not cred or cred['kind'] != 'ssh':
        raise DeploymentError("Le compte sélectionné doit être une identité SSH du coffre")
    group_name = str(payload.get('agent_group') or 'default').strip()
    if not 1 <= len(group_name) <= 80:
        raise DeploymentError("Groupe d'agents invalide")
    source = payload.get('targets')
    if not isinstance(source, list) or not 1 <= len(source) <= MAX_HOSTS_PER_BATCH:
        raise DeploymentError("Sélectionner entre 1 et 100 serveurs")
    targets = [_target(item) for item in source]
    if len(set(host for host, port, sid in targets)) != len(targets):
        raise DeploymentError("FQDN en double dans le lot")
    allowed = {h.strip().lower() for h in config.AGENT_DEPLOY_ALLOWED_HOSTS.split(',') if h.strip()}
    if not allowed or any(host not in allowed for host, _port, _sid in targets):
        raise DeploymentError("Un serveur n'est pas autorisé dans AGENT_DEPLOY_ALLOWED_HOSTS")
    if not config.AGENT_SERVER_URL.lower().startswith('https://'):
        raise DeploymentError("AGENT_SERVER_URL doit être une URL HTTPS valide")
    with db.session() as con:
        batch_id = con.execute(
            "INSERT INTO agent_deploy_batches(actor,platform,agent_group,total,status,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (actor, platform, group_name, len(targets), 'queued', db.now())
        ).lastrowid
        for host,port,sid in targets:
            con.execute(
                "INSERT INTO agent_deploy_targets(batch_id,host,port,server_id,credential_id,"
                "status,created_at) VALUES (?,?,?,?,?,?,?)",
                (batch_id, host, port, sid, credential_id, 'queued', db.now())
            )
    return {'batch_id': batch_id, 'total': len(targets), 'status': 'queued'}


def list_batches():
    with db.session() as con:
        groups = [dict(x) for x in con.execute(
            "SELECT * FROM agent_deploy_batches ORDER BY id DESC LIMIT 25")]
        targets = [dict(x) for x in con.execute(
            "SELECT id,batch_id,host,port,status,agent_id,error,started_at,finished_at "
            "FROM agent_deploy_targets ORDER BY id DESC LIMIT 2500")]
    for group in groups:
        group['targets'] = [x for x in targets if x['batch_id']==group['id']]
        group['done'] = sum(x['status'] in ('success','failed','interrupted') for x in group['targets'])
        group['success'] = sum(x['status']=='success' for x in group['targets'])
        group['failed'] = sum(x['status'] in ('failed','interrupted') for x in group['targets'])
    return {'items': groups}


def process_one():
    if not config.ALLOW_AGENT_REMOTE_DEPLOY:
        return False
    with db.session() as con:
        item = con.execute(
            "SELECT t.id,t.batch_id,t.host,t.port,t.server_id,t.credential_id,"
            "b.platform,b.agent_group FROM agent_deploy_targets t "
            "JOIN agent_deploy_batches b ON b.id=t.batch_id "
            "WHERE t.status='queued' ORDER BY t.id LIMIT 1"
        ).fetchone()
        if not item:
            return False
        target = dict(item)
        con.execute("UPDATE agent_deploy_targets SET status='running',started_at=? WHERE id=?",
                    (db.now(),target['id']))
        con.execute("UPDATE agent_deploy_batches SET status='running' WHERE id=?",
                    (target['batch_id'],))
    result = None
    error = ''
    try:
        # Do not silently redirect to a changed inventory server.
        if target['server_id'] is not None:
            current = ssh_inventory.server(target['server_id'])
            if current['fqdn'] != target['host'] or current['port'] != target['port']:
                raise DeploymentError("FQDN/port inventaire modifié depuis la demande")
        result = agents.deploy({
            'name': target['host'],
            'host': target['host'],
            'port': target['port'],
            'platform': target['platform'],
            'group': target['agent_group'],
            'credential_id': target['credential_id'],
            'approved': True
        })
    except Exception as exc:
        # Never return SSH failure messages (they may contain secrets).
        error = 'Installation échouée ('+type(exc).__name__+') ; vérifier SSH, sudo et journaux'
    with db.session() as con:
        con.execute("UPDATE agent_deploy_targets SET status=?,agent_id=?,error=?,finished_at=? "
                    "WHERE id=?", ('success' if result else 'failed', result['id'] if result else None,
                                   error, db.now(), target['id']))
        remaining = con.execute(
            "SELECT COUNT(*) FROM agent_deploy_targets WHERE batch_id=? "
            "AND status IN ('queued','running')",(target['batch_id'],)
        ).fetchone()[0]
        if not remaining:
            failed = con.execute(
                "SELECT COUNT(*) FROM agent_deploy_targets WHERE batch_id=? AND status<>'success'",
                (target['batch_id'],)
            ).fetchone()[0]
            con.execute("UPDATE agent_deploy_batches SET status=?,finished_at=? WHERE id=?",
                        ('completed_with_errors' if failed else 'success', db.now(),
                         target['batch_id']))
    return True


def loop():
    while not STOP.is_set():
        try:
            if process_one():
                continue
        except Exception:
            pass
        STOP.wait(2)


def start():
    global _WORKER
    STOP.clear()
    with db.session() as con:
        con.execute("UPDATE agent_deploy_targets SET status='interrupted',finished_at=?,"
                    "error='Service redémarré ; relancer explicitement si nécessaire' "
                    "WHERE status IN ('queued','running')",(db.now(),))
        con.execute(
            "UPDATE agent_deploy_batches SET status='interrupted',finished_at=? "
            "WHERE status IN ('queued','running')",(db.now(),)
        )
    _WORKER = threading.Thread(target=loop, name='OrigamiAgentBulkDeploy', daemon=True)
    _WORKER.start()
