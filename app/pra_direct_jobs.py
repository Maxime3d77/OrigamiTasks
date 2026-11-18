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
    if outdated(agent["version"],"0.3.30") or not re.fullmatch(r"\d+\.\d+\.\d+",str(agent["version"] or "")):
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



def _plain_key(cred):
    """Export a vault key in memory for temporary transfer over the SSH tunnel."""
    from cryptography.hazmat.primitives import serialization
    raw=str(cred.get("private_key") or "").encode()
    if not raw or len(raw)>25000:
        raise PRAJobError("Identité SSH destination : clé privée requise")
    pw=cred.get("passphrase")
    password=(pw.encode() if pw else None)
    try:
        try:
            key=serialization.load_ssh_private_key(raw,password=password)
        except ValueError:
            key=serialization.load_pem_private_key(raw,password=password)
        return key.private_bytes(
            serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption()
        ).decode()
    except Exception:
        raise PRAJobError("Clé SSH destination invalide ou phrase secrète incorrecte") from None


def _check_remote(client,command):
    _,stdout,stderr=client.exec_command(command,timeout=25)
    ch=stdout.channel
    started=time.monotonic()
    while not ch.exit_status_ready():
        if time.monotonic()-started>30:
            ch.close()
            raise PRAJobError("Prévol SSH distant expiré")
        time.sleep(.1)
    status=ch.recv_exit_status()
    if status:
        raise PRAJobError("Prévol des droits/VM/stockage impossible (code "+str(status)+")")


def _execute(job,vm,source,target):
    from .executor import _ssh,credential
    from . import host_keys
    source_cred=credential(job["source_credential_id"])
    target_cred=credential(job["target_credential_id"])
    if str(target_cred.get("username") or "")!="root":
        raise PRAJobError("Le pilote actuel requiert une identité SSH root sur le nœud cible")
    target_key=_plain_key(target_cred)
    pinned=_known_host_line(target["fqdn"],target["port"])
    # Validate the vault identity independently on BOTH endpoints before
    # granting the source process a temporary key in RAM.
    client_target=None
    client_source=None
    try:
        try:
            client_target,_=_ssh(None,{"host":target["fqdn"],"port":target["port"]},target_cred,40)
            dest_check="test \"$(id -u)\" -eq 0; command -v qm; command -v pvesm"
            if job["kind"]=="offline_incremental":
                dest_check+="; test \"$(qm status '"+str(vm["target_vmid"])+"')\" = 'status: stopped'"
            else:
                dest_check+="; ! qm config '"+str(vm["target_vmid"])+"' >/dev/null 2>&1"
            _check_remote(client_target,dest_check)
        except PRAJobError:
            raise
        except Exception:
            raise PRAJobError("Connexion/permissions SSH destination refusées") from None
        try:
            client_source,_=_ssh(None,{"host":source["fqdn"],"port":source["port"]},source_cred,40)
        except Exception:
            raise PRAJobError("Connexion SSH source refusée") from None
        check=(
            "test \"$(id -u)\" -eq 0 || sudo -n true; "
            "test -f "+BIN+"/pra-direct-seed.sh; "
            "test -f "+BIN+"/pra-incremental-cold.sh; "
            "test -f "+BIN+"/pra-sync-report.py; "
            "test -d "+BIN+"; "
            "test \"$(stat -c %U "+BIN+")\" = root"
        )
        _check_remote(client_source,check)
        # Exact argument list, no user-provided shell expansion. The script
        # name is determined by the internally recorded job type.
        if job["kind"]=="seed":
            args=[str(vm["source_vmid"]),target["fqdn"],str(vm["target_vmid"]),
                  vm["target_storage"],"--confirm"]
        else:
            args=[str(vm["source_vmid"]),target["fqdn"],str(vm["target_vmid"]),
                  "--confirm-offline",str(target["port"]),str(vm["id"])]
        package={
            "kind":job["kind"],"args":args,
            "key":target_key,"known_hosts":pinned
        }
        # Script text contains no secret. The key and pinned host record are
        # provided exclusively on the encrypted SSH stdin, never on argv.
        py="import base64;exec(compile(base64.b64decode("+repr(
            base64.b64encode(_REMOTE_PY.encode()).decode()
        )+"),'<origami-pra>','exec'))"
        cmd=('python3 -c ' if str(source_cred.get("username"))=="root" else
             'sudo -n python3 -c ')+shlex.quote(py)
        input_,output,errors=client_source.exec_command(cmd,timeout=80)
        payload=json.dumps(package).encode()
        if len(payload)>65000:
            raise PRAJobError("Clé/paramètres SSH trop volumineux")
        input_.channel.sendall(payload)
        input_.channel.shutdown_write()
        del package,target_key,payload
        channel=output.channel
        started=time.monotonic()
        stdout=bytearray()
        stderr=bytearray()
        while True:
            if channel.recv_ready():
                data=channel.recv(65536)
                if len(stdout)<8192: stdout.extend(data[:8192-len(stdout)])
            if channel.recv_stderr_ready():
                data=channel.recv_stderr(65536)
                if len(stderr)<8192: stderr.extend(data[:8192-len(stderr)])
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic()-started>MAX_SECONDS:
                channel.close()
                raise PRAJobError("Délai maximal dépassé ; vérifier le résultat distant avant toute relance")
            time.sleep(.07)
        code=channel.recv_exit_status()
        text_out=stdout.decode("utf8","replace")
        if code:
            raise PRAJobError("Transfert distant en échec (code "+str(code)+
                               ") ; vérifier la VM secours avant nouvelle tentative")
        marker=("Seed complete" if job["kind"]=="seed" else "ORIGAMI_INCREMENTAL_COLD_OK")
        if marker not in text_out:
            raise PRAJobError("Transfert terminé sans confirmation explicite")
        # Check once more that a target exists, is stopped and cannot
        # reboot automatically; do not allow failover on this evidence.
        _check_remote(client_target,"test \"$(qm status '"+str(vm["target_vmid"])+
                      "')\" = 'status: stopped'; test \"$(qm config '"+str(vm["target_vmid"])+
                      "' | sed -n 's/^onboot: //p')\" != 1")
        return {"output":text_out[-1200:]}
    finally:
        if client_target: client_target.close()
        if client_source: client_source.close()


def process_one():
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute(
            "SELECT * FROM pra_direct_jobs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return False
        job=dict(row)
        con.execute(
            "UPDATE pra_direct_jobs SET status='running',started_at=? WHERE id=?",
            (db.now(),job["id"])
        )
    status,error,seconds="success","",None
    started=time.monotonic()
    try:
        vm,node,source,target,agent=_validate(
            job["protected_vm_id"],job["kind"],
            job["source_credential_id"],job["target_credential_id"]
        )
        if (source["fqdn"]!=job["source_host"] or
                target["fqdn"]!=job["target_host"]):
            # A VM migrating during a queued job must be reapproved rather
            # than silently sending secrets to a new node.
            raise PRAJobError("Nœud changé depuis la validation : relancer depuis la nouvelle VM source")
        _execute(job,vm,source,target)
    except PRAJobError as exc:
        status,error="failed",str(exc)[:250]
    except Exception:
        status,error="uncertain","Erreur de transfert : état distant incertain ; vérifier le nœud AU"
    seconds=int(time.monotonic()-started)
    with db.session() as con:
        con.execute(
            "UPDATE pra_direct_jobs SET status=?,error=?,elapsed_seconds=?,finished_at=? "
            "WHERE id=?",(status,error,seconds,db.now() if status!="uncertain" else None,job["id"])
        )
    db.audit(job["actor"],"pra.direct."+status,job["protected_vm_id"],
             "job="+str(job["id"])+" kind="+job["kind"])
    return True


def loop():
    while not STOP.is_set():
        try:
            if process_one():
                continue
        except Exception:
            pass
        STOP.wait(3)


def start():
    global WORKER
    with db.session() as con:
        # Never automatically resume a stream interrupted by controller restart.
        con.execute(
            "UPDATE pra_direct_jobs SET status='uncertain',error=? "
            "WHERE status='running'",
            ("Contrôleur redémarré pendant le transfert ; vérifier le stockage cible",)
        )
    STOP.clear()
    if WORKER is None or not WORKER.is_alive():
        WORKER=threading.Thread(target=loop,name="pra-direct-worker",daemon=True)
        WORKER.start()
