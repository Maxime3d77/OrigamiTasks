"""Persistent, bounded bulk agent provisioning queue.

Bulk requests never execute a privileged SSH session inside the HTTP request.
A single background worker processes an explicitly approved list, one host at a
time, and records successes/errors independently. Queued/running deployments
are interrupted on control-plane restart, never silently replayed.
"""
import json
import threading
import time
from . import agents, config, db, ssh_inventory, host_keys, agent_settings

STOP = threading.Event()
_WORKER = None
MAX_HOSTS_PER_BATCH = 100


class DeploymentError(ValueError):
    pass


def public_failure(exc):
    """Categorize errors without exposing SSH stderr, credentials or tokens."""
    from fastapi import HTTPException
    if isinstance(exc,DeploymentError):
        # All DeploymentError messages are authored locally (never SSH output).
        return str(exc)[:250]
    if isinstance(exc,HTTPException):
        if exc.status_code==403:
            return "Autorisation refusée : vérifier la stratégie de déploiement ou les droits de l'identité SSH"
        if exc.status_code==422:
            return "Paramètres d'installation invalides (FQDN, port, coffre ou URL HTTPS)"
    kind=type(exc).__name__
    if kind in ('AuthenticationException','BadAuthenticationType','PasswordRequiredException'):
        return "Authentification SSH refusée : vérifier la clé et l'utilisateur du coffre"
    if kind in ('BadHostKeyException','SSHException'):
        return "Vérification SSH échouée : clé d'hôte ou négociation SSH à contrôler"
    if isinstance(exc,(TimeoutError,ConnectionError,OSError)):
        return "Connexion SSH impossible : vérifier DNS, port 22, réseau et pare-feu"
    if isinstance(exc,RuntimeError):
        return "Commande d'installation distante en échec : vérifier droits sudo -n, systemd et Python 3"
    return "Installation interrompue au stade SSH ou installation ("+kind[:45]+") : consulter les journaux serveur"


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
    # Managed cluster batches are a separate scoped, admin-approved pathway.
    # The legacy global toggle still governs all other arbitrary SSH deployments.
    if not config.ALLOW_AGENT_REMOTE_DEPLOY and payload.get('proxmox_datacenter_id') is None:
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
    cluster_id=payload.get("proxmox_datacenter_id")
    cluster_group=payload.get("proxmox_group_id")
    enable_proxmox=payload.get("enable_proxmox",False)
    enable_pra=payload.get("enable_pra",False)
    if type(enable_proxmox) is not bool or type(enable_pra) is not bool:
        raise DeploymentError("Options des modules agents invalides")
    if enable_pra and not enable_proxmox:
        raise DeploymentError("Le rôle PRA nécessite le rôle Proxmox")
    if cluster_id is None and (enable_proxmox or enable_pra):
        raise DeploymentError("Les rôles de site nécessitent un cluster Proxmox lié")
    if cluster_id is not None or cluster_group is not None:
        if (type(cluster_id) is not int or type(cluster_group) is not int or
                platform != "linux" or any(sid is None for _host,_port,sid in targets)):
            raise DeploymentError("Déploiement cluster : choisir des serveurs SSH associés au cluster")
        with db.session() as con:
            mapping=con.execute(
                "SELECT 1 FROM proxmox_ssh_groups WHERE datacenter_id=? AND group_id=?",
                (cluster_id,cluster_group)
            ).fetchone()
            if not mapping:
                raise DeploymentError("Groupe SSH non lié à ce cluster Proxmox")
            if enable_proxmox or enable_pra:
                site=con.execute(
                    "SELECT s.role FROM dr_site_datacenters l "
                    "JOIN dr_sites s ON s.id=l.site_id WHERE l.datacenter_id=?",
                    (cluster_id,)
                ).fetchone()
                if not site or (enable_pra and site["role"] not in ("primary","standby")):
                    raise DeploymentError("Associer un site PRA valide avant d'activer ces modules")
            for host,ssh_port,sid in targets:
                row=con.execute(
                    "SELECT 1 FROM proxmox_ssh_nodes n "
                    "JOIN ssh_servers s ON s.id=n.server_id "
                    "JOIN ssh_server_group_members m ON m.server_id=s.id "
                    "WHERE n.datacenter_id=? AND n.server_id=? AND m.group_id=? "
                    "AND s.fqdn=? AND s.port=?",
                    (cluster_id,sid,cluster_group,host,ssh_port)
                ).fetchone()
                if not row:
                    raise DeploymentError("Nœud SSH non découvert pour ce cluster : synchroniser")
    else:
        allowed = {h.strip().lower() for h in config.AGENT_DEPLOY_ALLOWED_HOSTS.split(',') if h.strip()}
        if not allowed or any(host not in allowed for host, _port, _sid in targets):
            raise DeploymentError("Un serveur n'est pas autorisé dans AGENT_DEPLOY_ALLOWED_HOSTS")
    try:
        agent_settings.effective_url()
    except ValueError as exc:
        raise DeploymentError(str(exc)) from None
    with db.session() as con:
        batch_id = con.execute(
            "INSERT INTO agent_deploy_batches(actor,platform,agent_group,total,status,created_at,"
            "proxmox_datacenter_id,proxmox_group_id,enable_proxmox,enable_pra) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (actor, platform, group_name, len(targets), 'queued', db.now(),
             cluster_id,cluster_group,int(enable_proxmox),int(enable_pra))
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
    with db.session() as con:
        # Serialize the claim across any duplicate worker thread or process.
        # SQLite BEGIN IMMEDIATE prevents two processes choosing the same host.
        con.execute("BEGIN IMMEDIATE")
        item = con.execute(
            "SELECT t.id,t.batch_id,t.host,t.port,t.server_id,t.credential_id,"
            "b.platform,b.agent_group,b.proxmox_datacenter_id,b.proxmox_group_id,"
            "b.enable_proxmox,b.enable_pra "
            "FROM agent_deploy_targets t "
            "JOIN agent_deploy_batches b ON b.id=t.batch_id "
            "WHERE t.status='queued' AND (? OR b.proxmox_datacenter_id IS NOT NULL) "
            "ORDER BY t.id LIMIT 1",(1 if config.ALLOW_AGENT_REMOTE_DEPLOY else 0,)
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
        cluster_authorized=False
        if target.get("proxmox_datacenter_id") is not None:
            # Recheck every membership just before the privileged SSH action.
            with db.session() as con:
                found=con.execute(
                    "SELECT 1 FROM proxmox_ssh_nodes n "
                    "JOIN proxmox_ssh_groups g ON g.datacenter_id=n.datacenter_id "
                    "JOIN ssh_server_group_members m ON m.server_id=n.server_id "
                    "JOIN ssh_servers s ON s.id=n.server_id "
                    "WHERE n.datacenter_id=? AND g.group_id=? AND "
                    "m.group_id=g.group_id AND n.server_id=? AND "
                    "s.fqdn=? AND s.port=?",
                    (target['proxmox_datacenter_id'],target['proxmox_group_id'],
                     target['server_id'],target['host'],target['port'])
                ).fetchone()
            if not found:
                raise DeploymentError("Nœud Proxmox retiré du groupe depuis la validation")
            # A group is never enough to grant host trust. Reject TOFU here,
            # including when legacy policy elsewhere allows accept-new.
            try:
                inspected=host_keys.inspect(target['host'],target['port'])
            except host_keys.HostKeyError:
                raise DeploymentError("Empreinte SSH non vérifiable depuis OrigamiTasks") from None
            if inspected['status']!='trusted':
                raise DeploymentError("Clé hôte SSH inconnue ou modifiée : approbation nécessaire")
            # A site deleted after approval blocks module-related rollout
            # BEFORE the privileged SSH installation.
            if target["enable_proxmox"] or target["enable_pra"]:
                with db.session() as con:
                    site=con.execute(
                        "SELECT s.role FROM dr_site_datacenters l JOIN dr_sites s "
                        "ON s.id=l.site_id WHERE l.datacenter_id=?",
                        (target["proxmox_datacenter_id"],)
                    ).fetchone()
                if not site or (target["enable_pra"] and site["role"] not in ("primary","standby")):
                    raise DeploymentError("Site PRA devenu invalide avant installation")
            cluster_authorized=True
        deployment={
            'name': target['host'],
            'host': target['host'],
            'port': target['port'],
            'platform': target['platform'],
            'group': target['agent_group'],
            'credential_id': target['credential_id'],
            'approved': True
        }
        # Preserve the legacy installer call shape for existing bulk workflows.
        result=(agents.deploy(deployment,cluster_authorized=True) if cluster_authorized
                else agents.deploy(deployment))
        if cluster_authorized:
            with db.session() as con:
                site=con.execute(
                    "SELECT site_id FROM dr_site_datacenters WHERE datacenter_id=?",
                    (target['proxmox_datacenter_id'],)
                ).fetchone()
                if site:
                    con.execute(
                        "INSERT OR IGNORE INTO dr_site_agents(site_id,agent_id) VALUES(?,?)",
                        (site['site_id'],result['id'])
                    )
                    if target["enable_proxmox"] or target["enable_pra"]:
                        # The first authenticated poll (possibly after enrollment)
                        # receives the desired safe roles automatically.
                        roles={"standard":True,
                               "proxmox":bool(target["enable_proxmox"]),
                               "pra":bool(target["enable_pra"])}
                        con.execute(
                            "UPDATE agents SET modules_json=?,modules_revision=modules_revision+1 "
                            "WHERE id=? AND modules_revision=1 AND revoked=0",
                            (json.dumps(roles,separators=(',',':'),sort_keys=True),result['id'])
                        )
    except Exception as exc:
        # Never reveal exception strings from SSH, SFTP or remote stderr:
        # they can contain credentials or attacker-controlled text.
        error = public_failure(exc)
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
    if _WORKER is not None and _WORKER.is_alive():
        # Reuse the existing worker when tests recreate TestClient lifespan.
        STOP.clear()
        return
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
