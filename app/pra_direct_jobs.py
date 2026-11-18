"""Admin-approved, vault-backed direct PVE seed and OFFLINE incremental jobs.

SSH keys are kept in the vault, transported over the encrypted controller ->
source SSH session, and held in anonymous RAM file descriptors on the current
source node for the lifetime of the transfer. Payload flows source -> target
directly. No PBS, no permanent SSH private key or extra .env setting.
"""
import base64
import json
import re
import shlex
import threading
import time
from pathlib import Path
from . import db, integrations, dr_protected_vms, host_keys, agent_updates

STOP=threading.Event()
WORKER=None
BIN="/usr/local/lib/origamitasks/pra"
MAX_SECONDS=6*3600


class PRAJobError(ValueError):
    pass


def _host(con,dc_id,node):
    rows=con.execute(
        "SELECT s.fqdn,s.port FROM proxmox_ssh_nodes p "
        "JOIN ssh_servers s ON s.id=p.server_id "
        "WHERE p.datacenter_id=? AND p.node_name=?",(dc_id,node)
    ).fetchall()
    if len(rows)!=1:
        raise PRAJobError("Nœud "+node+" non lié à un serveur de l'inventaire SSH du cluster")
    return dict(rows[0])


def _vm_owner(vm):
    from .executor import credential
    dc=integrations.configured_datacenter(vm["source_dc_id"])
    secret=credential(dc["credential_id"])
    auth=dict(secret,ca_cert=dc.get("ca_cert") or secret.get("ca_cert") or "",
              skip_tls_verification=bool(dc.get("skip_tls_verification",False)))
    entries=integrations.discover_datacenter(dc,auth,20)
    guests=[x for x in entries if x.get("type")=="qemu" and x.get("vmid")==vm["source_vmid"]]
    if len(guests)!=1:
        raise PRAJobError("VM source introuvable ou inventaire ambigu")
    return str(guests[0].get("node") or "")


def _agent(con,vm,node):
    from .agent_updates import outdated
    result=con.execute(
        "SELECT a.id,a.version,a.enrolled,a.revoked,a.last_seen,a.hostname FROM agents a "
        "JOIN dr_site_agents da ON da.agent_id=a.id "
        "WHERE da.site_id=?",(vm["source_site_id"],)
    ).fetchall()
    found=[x for x in result if str(x["hostname"] or "").split(".")[0].lower()==node.lower()]
    if len(found)!=1:
        raise PRAJobError("Agent PRA absent ou ambigu sur le nœud source "+node)
    agent=found[0]
    from .agents import utc_plus
    if not agent["enrolled"] or agent["revoked"] or not agent["last_seen"] or agent["last_seen"]<utc_plus(-90):
        raise PRAJobError("Agent source hors ligne")
    if outdated(agent["version"],"0.3.30") or not re.fullmatch(r"0\.(?:3[0-9]|[4-9][0-9])\.\d+",str(agent["version"] or "")):
        raise PRAJobError("Mettre à jour l'agent source en v0.3.30 avant le transfert")
    return dict(agent)


def _validate(vm_id,kind,source_ssh_id,target_ssh_id):
    if kind not in ("seed","offline_incremental"):
        raise PRAJobError("Mode PRA invalide")
    if any(type(x) is not int or x<1 for x in (vm_id,source_ssh_id,target_ssh_id)):
        raise PRAJobError("Identités SSH et VM invalides")
    vm=dr_protected_vms._one(vm_id)
    if vm["state"]!="planned" or vm["engine"]!="offline_copy":
        raise PRAJobError("Protection active avec moteur copie directe requise")
    node=_vm_owner(vm)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}",node):
        raise PRAJobError("Nœud source invalide")
    with db.session() as con:
        source=_host(con,vm["source_dc_id"],node)
        target=_host(con,vm["target_dc_id"],vm["target_node"])
        agent=_agent(con,vm,node)
        creds=con.execute(
            "SELECT id,kind FROM credentials WHERE id IN (?,?)",(source_ssh_id,target_ssh_id)
        ).fetchall()
    if {r["id"] for r in creds if r["kind"]=="ssh"}!={source_ssh_id,target_ssh_id}:
        raise PRAJobError("Choisir deux identités SSH existantes du coffre-fort")
    if source["fqdn"]==target["fqdn"]:
        raise PRAJobError("Source et secours sur le même nœud")
    if target["port"]!=22 and kind=="seed":
        raise PRAJobError("Copie initiale : port SSH cible 22 requis par ce pilote")
    return vm,node,source,target,agent


def queue(vm_id,kind,source_ssh_id,target_ssh_id,approved,actor):
    if approved is not True:
        raise PRAJobError("Confirmation explicite requise")
    vm,node,source,target,agent=_validate(vm_id,kind,source_ssh_id,target_ssh_id)
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM pra_direct_jobs WHERE protected_vm_id=? "
                       "AND status IN ('queued','running','uncertain')",(vm_id,)).fetchone():
            raise PRAJobError("Copie précédente encore active ou état incertain")
        record=con.execute(
            "INSERT INTO pra_direct_jobs(protected_vm_id,kind,source_node,source_host,"
            "target_host,source_credential_id,target_credential_id,status,actor,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (vm_id,kind,node,source["fqdn"],target["fqdn"],
             source_ssh_id,target_ssh_id,"queued",actor,db.now())
        ).lastrowid
    db.audit(actor,"pra.direct.queue",vm_id,"job="+str(record)+" mode="+kind)
    return {"job_id":record,"status":"queued","kind":kind,
            "source_node":node,"target_node":vm["target_node"]}


def list_jobs(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        rows=con.execute(
            "SELECT id,kind,status,source_node,source_host,target_host,error,"
            "created_at,started_at,finished_at,elapsed_seconds "
            "FROM pra_direct_jobs WHERE protected_vm_id=? ORDER BY id DESC LIMIT 30",
            (vm_id,)
        ).fetchall()
    return {"items":[dict(r) for r in rows]}


def _known_host_line(host,port):
    import paramiko
    if host_keys.inspect(host,port)["status"]!="trusted":
        raise PRAJobError("Clé d'hôte SSH cible non approuvée ou modifiée")
    name=host if port==22 else "["+host+"]:"+str(port)
    known=paramiko.HostKeys()
    known.load(str(host_keys._trusted_path()))
    entry=known.lookup(name)
    if not entry:
        raise PRAJobError("Clé hôte absente du registre de confiance")
    # The host-key inspection above matched the currently offered key.
    lines=[name+" "+key.get_name()+" "+key.get_base64() for key in entry.values()]
    return "\n".join(lines)+"\n"


# This script runs as root through an SSH command on the SOURCE node.
# No shell text is generated using user-controlled strings. Secrets only
# cross the already authenticated SSH channel in stdin, never in argv/logs.
_REMOTE_PY=r'''import json,os,sys,subprocess
data=json.loads(sys.stdin.read(65000))
if sys.stdin.read(1): raise SystemExit(55)
if os.geteuid()!=0: raise SystemExit(56)
if data["kind"] not in ("seed","offline_incremental"): raise SystemExit(57)
k=os.memfd_create("origami-ephemeral-ssh-key",0)
h=os.memfd_create("origami-pinned-ssh-host",0)
try:
 for fd,blob in ((k,data["key"]),(h,data["known_hosts"])):
  os.fchmod(fd,0o600); os.write(fd,blob.encode()); os.lseek(fd,0,0)
 args=[str(x) for x in data["args"]]
 script="/usr/local/lib/origamitasks/pra/"+("pra-direct-seed.sh" if data["kind"]=="seed" else "pra-incremental-cold.sh")
 if not os.path.isfile(script) or os.path.islink(script):
  raise SystemExit(58)
 env=os.environ.copy()
 env["PRA_PINNED_KNOWN_HOSTS"]="/proc/self/fd/"+str(h)
 if data["kind"]=="seed":
  args.extend(["root","/proc/self/fd/"+str(k)])
 else:
  args.insert(4,"/proc/self/fd/"+str(k))
 child=subprocess.Popen(["bash",script,*args],env=env,pass_fds=(k,h))
 raise SystemExit(child.wait())
finally:
 os.close(k);os.close(h)
'''
