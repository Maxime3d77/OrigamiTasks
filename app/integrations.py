"""Managed integrations. No shell fallback for API actions; all secrets come from vault.

V0.2 deliberately does not implement cross-cluster VM replication or remote migration:
those require dedicated replication agents, storage-aware validation and fencing.
"""
import json
import re
import shlex
from urllib.parse import quote, urlsplit
import httpx
from . import config, db


class IntegrationError(Exception):
    pass


def authorized_origin(raw_url, allowed_hosts):
    """Only HTTPS to an explicitly configured host; avoids credential SSRF.

    Exact DNS names or IP addresses, optionally with a port, are accepted in
    the environment allow-list. Never redirect with Authorization headers.
    """
    url = str(raw_url or '').rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise IntegrationError('URL HTTPS racine obligatoire (sans identifiants ni chemin)')
    if not allowed_hosts:
        raise IntegrationError('Connecteur désactivé : configurer la liste des hôtes autorisés dans .env')
    allowed = {item.strip().lower() for item in allowed_hosts.split(',') if item.strip()}
    host = parsed.hostname.lower()
    effective = (host+':'+str(parsed.port)) if parsed.port else host
    if host not in allowed and effective not in allowed:
        raise IntegrationError('Serveur absent de la liste des hôtes autorisés')
    return url


def matrix_message(opts, cred, run_id, step_id, timeout):
    base = authorized_origin(opts.get('homeserver'), config.MATRIX_ALLOWED_HOSTS)
    room_id = str(opts.get('room_id') or '')
    body = str(opts.get('message') or '')
    token = cred.get('token') or ''
    if not (room_id.startswith('!') or room_id.startswith('#')) or len(room_id) > 255:
        raise IntegrationError('Room ID Matrix invalide')
    if not body or len(body)>15000:
        raise IntegrationError('Message Matrix vide ou trop long')
    if not token:
        raise IntegrationError('Un token Matrix du coffre est nécessaire')
    # Deterministic transaction id enables idempotent delivery on retries.
    txn = re.sub(r'[^a-zA-Z0-9_.-]','-',f'origami-{run_id}-{step_id}')[:100]
    path='/_matrix/client/v3/rooms/'+quote(room_id,safe='')+'/send/m.room.message/'+txn
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response=client.put(base+path,headers={'Authorization':'Bearer '+token},json={'msgtype':'m.text','body':body})
    response.raise_for_status()
    result=response.json()
    return 'Matrix : événement envoyé '+str(result.get('event_id',''))[:200]


def ansible_command(opts):
    """Build only an allowlisted ansible-playbook invocation on the SSH runner."""
    playbook = str(opts.get('playbook') or '')
    inventory = str(opts.get('inventory') or '')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./-]{0,180}\.ya?ml',playbook) or '..' in playbook.split('/') or playbook.startswith('/'):
        raise IntegrationError('Playbook relatif invalide')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./-]{0,180}',inventory) or '..' in inventory.split('/') or inventory.startswith('/'):
        raise IntegrationError('Inventaire relatif invalide')
    root = '/opt/origamitasks/ansible'
    cmd=['ansible-playbook','-i',f'{root}/inventories/{inventory}',f'{root}/playbooks/{playbook}']
    if opts.get('limit'):
        limit=str(opts['limit'])
        if len(limit)>150 or any(c in limit for c in '\r\n\x00'):
            raise IntegrationError('Limite Ansible invalide')
        cmd += ['--limit',limit]
    if bool(opts.get('check')):
        cmd.append('--check')
    variables=opts.get('extra_vars') or '{}'
    if isinstance(variables,str):
        try: variables=json.loads(variables)
        except (ValueError,TypeError) as ex: raise IntegrationError('extra_vars doit être un objet JSON') from ex
    if not isinstance(variables,dict) or len(json.dumps(variables)) > 10000:
        raise IntegrationError('extra_vars JSON invalide')
    if variables:
        cmd += ['--extra-vars', json.dumps(variables,separators=(',',':'),ensure_ascii=True)]
    # shlex.quote ensures remote shell cannot interpret fields as shell fragments.
    return ' '.join(shlex.quote(x) for x in cmd)


def proxmox_request(origin, cred, path, timeout=20, method='GET', data=None):
    base=authorized_origin(origin,config.PROXMOX_ALLOWED_HOSTS)
    ident=str(cred.get('username') or '')
    token=str(cred.get('token') or '')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+![a-zA-Z0-9_.-]+', ident) or not token:
        raise IntegrationError('Coffre Proxmox : username=user@realm!tokenname et token=SECRET')
    if not path.startswith('/api2/json/') or '://' in path or '\\' in path:
        raise IntegrationError('Chemin Proxmox invalide')
    with httpx.Client(timeout=timeout,follow_redirects=False,trust_env=False,verify=config.PROXMOX_CA_CERT or True) as client:
        response=client.request(method,base+path,headers={'Authorization':f'PVEAPIToken={ident}={token}'},data=data)
    response.raise_for_status()
    result=response.json()
    if not isinstance(result,dict) or 'data' not in result:
        raise IntegrationError('Réponse Proxmox inattendue')
    return result['data']


def discover_datacenter(dc, cred, timeout=20):
    resources=proxmox_request(dc['url'],cred,'/api2/json/cluster/resources',timeout)
    if not isinstance(resources,list):
        raise IntegrationError('Inventaire Proxmox indisponible')
    result=[]
    for item in resources:
        if not isinstance(item,dict) or item.get('type') not in ('node','qemu','lxc','storage'):
            continue
        # Intentionally expose a restricted view of Proxmox properties.
        result.append({k:item.get(k) for k in ('id','type','node','vmid','name','status','maxmem','mem','maxdisk','disk','storage','cpu','maxcpu') if k in item})
    return result[:10000]


def configured_datacenter(dc_id):
    try: id_=int(dc_id)
    except (ValueError,TypeError): raise IntegrationError('Datacenter ID invalide')
    with db.session() as con:
        item=con.execute('SELECT id,name,url,credential_id FROM datacenters WHERE id=?',(id_,)).fetchone()
    if not item: raise IntegrationError('Datacenter introuvable')
    return dict(item)


def proxmox_task(opts, load_credential, timeout):
    dc=configured_datacenter(opts.get('datacenter_id'))
    cred=load_credential(dc['credential_id'])
    action=opts.get('action','inventory')
    if action=='inventory':
        resources=discover_datacenter(dc,cred,timeout)
        counts={kind:sum(r.get('type')==kind for r in resources) for kind in ('node','qemu','lxc','storage')}
        return json.dumps({'datacenter':dc['name'],'count':counts,'resources':resources[:100]},ensure_ascii=False)
    if action not in ('start','shutdown','reboot'):
        raise IntegrationError('Action Proxmox non autorisée (réplication/migration non implémentées)')
    if not config.ALLOW_PROXMOX_ACTIONS or not opts.get('approved'):
        raise IntegrationError('Actions Proxmox désactivées : ALLOW_PROXMOX_ACTIONS=true et validation explicite requises')
    node=str(opts.get('node',''))
    try:vmid=int(opts.get('vmid'))
    except (TypeError,ValueError):raise IntegrationError('VMID invalide')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',node) or not 100 <= vmid <= 999999999:
        raise IntegrationError('Nœud ou VMID invalide')
    # Fail closed: confirm target is the expected QEMU VM before a power action.
    resources=discover_datacenter(dc,cred,timeout)
    if not any(r.get('type')=='qemu' and r.get('vmid')==vmid and r.get('node')==node for r in resources):
        raise IntegrationError('VM non trouvée sur ce nœud : action annulée')
    path=f'/api2/json/nodes/{quote(node)}/qemu/{vmid}/status/{action}'
    upid=proxmox_request(dc['url'],cred,path,timeout,method='POST')
    return json.dumps({'datacenter':dc['name'],'vmid':vmid,'action':action,'upid':upid},ensure_ascii=False)
