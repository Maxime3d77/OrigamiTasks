"""Managed integrations. No shell fallback for API actions; all secrets come from vault.

V0.2 deliberately does not implement cross-cluster VM replication or remote migration:
those require dedicated replication agents, storage-aware validation and fencing.
"""
import json
import ipaddress
import re
import shlex
from urllib.parse import quote, urlsplit
import httpx
from . import config, db, proxmox_cas


class IntegrationError(Exception):
    pass


def authorized_origin(raw_url, allowed_hosts, *, admin_proxmox=False):
    """Validate an HTTPS API origin; never follow redirects with tokens.

    For Proxmox only, an administrator-created vault connection or datacenter
    is sufficient to authorize a constrained HTTPS endpoint when no explicit
    PROXMOX_ALLOWED_HOSTS is configured. An existing allow-list wins.
    """
    url = str(raw_url or '').strip().rstrip('/')
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError):
        raise IntegrationError("Port de l'URL Proxmox invalide") from None
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ('', '/')):
        raise IntegrationError('URL HTTPS racine obligatoire (sans identifiants ni chemin)')
    host = parsed.hostname.lower().rstrip('.')
    if admin_proxmox:
        # This origin is already stored in an admin-controlled Proxmox vault
        # connection or datacenter. The global env allow-list is not consulted.
        # Auto-authorization is scoped to the expected HTTPS API ports.
        if (port not in (None, 443, 8006) or host == 'localhost' or
                host.endswith('.localhost') or host in ('metadata.google.internal',)):
            raise IntegrationError('Proxmox : hôte ou port interdit en autorisation automatique')
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
                raise IntegrationError('Proxmox : adresse locale ou spéciale interdite')
        return url
    if not allowed_hosts:
        raise IntegrationError("Connecteur désactivé : configurer la liste des hôtes autorisés dans .env")
    allowed = {item.strip().lower() for item in allowed_hosts.split(',') if item.strip()}
    effective = (host+':'+str(port)) if port else host
    if host not in allowed and effective not in allowed:
        raise IntegrationError(
            'Hôte Proxmox absent de PROXMOX_ALLOWED_HOSTS (FQDN ou FQDN:8006 à ajouter)'
        )
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
    base=authorized_origin(origin,config.PROXMOX_ALLOWED_HOSTS,admin_proxmox=True)
    ident=str(cred.get('username') or '')
    token=str(cred.get('token') or '')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+![a-zA-Z0-9_.-]+', ident) or not token:
        raise IntegrationError('Coffre Proxmox : username=user@realm!tokenname et token=SECRET')
    if not path.startswith('/api2/json/') or '://' in path or '\\' in path:
        raise IntegrationError('Chemin Proxmox invalide')
    # A CA chosen in the web UI takes priority over the legacy env fallback.
    tls_verify=proxmox_cas.verify_value(cred.get('ca_cert'))
    with httpx.Client(timeout=timeout,follow_redirects=False,trust_env=False,verify=tls_verify) as client:
        response=client.request(method,base+path,headers={'Authorization':f'PVEAPIToken={ident}={token}'},data=data)
    response.raise_for_status()
    result=response.json()
    if not isinstance(result,dict) or 'data' not in result:
        raise IntegrationError('Réponse Proxmox inattendue')
    return result['data']


def discover_datacenter(dc, cred, timeout=20):
    if dc.get('ca_cert'):
        cred=dict(cred,ca_cert=dc['ca_cert'])
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
        item=con.execute('SELECT id,name,url,credential_id,kind,ca_cert FROM datacenters WHERE id=?',(id_,)).fetchone()
    if not item: raise IntegrationError('Datacenter introuvable')
    return dict(item)


def proxmox_task(opts, load_credential, timeout):
    dc=configured_datacenter(opts.get('datacenter_id'))
    cred=load_credential(dc['credential_id'])
    if dc.get('ca_cert'):
        cred=dict(cred,ca_cert=dc['ca_cert'])
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


def assess_vm_dr(dc, cred, vmid, timeout=20):
    """Read-only storage-aware assessment, never a replication plan or failover approval.

    Storage type is derived from PVE /storage metadata, not guessed from disk names.
    Results deliberately avoid claiming operational readiness or an RPO.
    """
    try:
        vmid = int(vmid)
    except (TypeError, ValueError):
        raise IntegrationError('VMID invalide')
    if not 100 <= vmid <= 999999999:
        raise IntegrationError('VMID invalide')
    if dc.get('ca_cert'):
        cred=dict(cred,ca_cert=dc['ca_cert'])
    inventory = discover_datacenter(dc, cred, timeout)
    matches = [r for r in inventory if r.get('type') == 'qemu' and r.get('vmid') == vmid]
    if len(matches) != 1:
        raise IntegrationError('VM QEMU introuvable dans l’inventaire')
    vm = matches[0]
    node = str(vm.get('node') or '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', node):
        raise IntegrationError('Nœud Proxmox invalide')
    conf = proxmox_request(dc['url'], cred,
                           f'/api2/json/nodes/{quote(node)}/qemu/{vmid}/config', timeout)
    storage_items = proxmox_request(dc['url'], cred, '/api2/json/storage', timeout)
    if not isinstance(conf, dict) or not isinstance(storage_items, list):
        raise IntegrationError('Configuration VM ou stockages indisponibles')
    types = {str(x.get('storage')): {'type': x.get('type', 'unknown'),
             'shared': bool(x.get('shared', 0))} for x in storage_items
             if isinstance(x, dict) and isinstance(x.get('storage'), str)}
    drives = []
    for key, value in sorted(conf.items()):
        if not re.fullmatch(r'(?:scsi|virtio|sata|ide)\d+', key) or not isinstance(value,str):
            continue
        volume = value.split(',', 1)[0]
        # Disk entries may reference a storage pool or special values like cdrom/none.
        pool = volume.split(':', 1)[0] if ':' in volume else ''
        meta = types.get(pool, {'type': 'unknown', 'shared': False})
        drives.append({'slot': key, 'storage': pool or None,
                       'storage_type': meta['type'], 'shared': meta['shared'],
                       'snapshot_capability': 'not_verified'})
    unique_types = sorted({d['storage_type'] for d in drives})
    risks = ['No source fencing check, destination disk mapping, write-order guarantee or DR validation has been performed.',
             'Never boot a standby clone while the source may still be writing.']
    if any(t in ('iscsi','iscsidirect','lvm','lvmthin') for t in unique_types):
        risks.append('Block/iSCSI storage: evaluate vendor array snapshots, replication, consistency groups and LUN ownership. rsync of live block devices is unsafe.')
    options = [
      {'engine': 'pbs_backup_sync_restore', 'status': 'requires_design',
       'purpose': 'Periodic recovery points; a backup requires restore before boot.'},
      {'engine': 'storage_array_replication', 'status': 'requires_vendor_validation',
       'purpose': 'Potentially low-lag mirror depending on the array, consistency and fencing.'},
      {'engine': 'remote_migration', 'status': 'requires_pdm_and_mapping_validation',
       'purpose': 'Moves an active VM; not a continuously maintained standby.'},
    ]
    if drives and all(t == 'zfspool' for t in unique_types):
        options.append({'engine':'pvesr', 'status':'intra_cluster_only',
                        'purpose':'Native PVE local ZFS replication within one cluster, not generic cross-cluster DR.'})
    return {'datacenter':dc['name'], 'vmid':vmid, 'name':vm.get('name'),
            'node':node, 'source_status':vm.get('status'), 'disks':drives,
            'storage_types':unique_types,'options':options, 'risks':risks,
            'ready_to_failover':False, 'replication_active':False,
            'note':'Read-only exploratory assessment; no snapshot, copy, migration, or replication was executed.'}
