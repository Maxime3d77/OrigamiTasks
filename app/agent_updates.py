"""Opt-in SSH updates of installed agents, preserving registration and policy."""
import base64
import json
import re
import secrets
import shlex
import threading
from pathlib import Path
from . import db, host_keys

STOP=threading.Event()
WORKER=None
ROOT=Path(__file__).resolve().parent.parent/"agents"

class UpdateError(ValueError):
    pass

def available_versions():
    versions={}
    for platform,path,pattern in (
        ("linux",ROOT/"linux/agent.py",r"^VERSION = '([^']+)'"),
        ("windows",ROOT/"windows/agent.ps1",r"^\$Version = '([^']+)'"),
    ):
        m=re.search(pattern,path.read_text(),re.M)
        if not m:
            raise UpdateError("Version intégrée inconnue : "+platform)
        versions[platform]=m.group(1)
    return versions

def outdated(current,latest):
    def parts(value):
        m=re.fullmatch(r"(\d+)\.(\d+)\.(\d+)",str(value or ""))
        return tuple(map(int,m.groups())) if m else None
    a,b=parts(current),parts(latest)
    return bool(a and b and a<b)

def target(con,agent_id):
    row=con.execute("SELECT id,platform,hostname,version,enrolled,revoked,uninstalled_at FROM agents WHERE id=?",
                    (agent_id,)).fetchone()
    if not row or not row["enrolled"] or row["revoked"] or row["uninstalled_at"]:
        raise UpdateError("Agent indisponible ou non enrôlé")
    hostname=str(row["hostname"] or "").lower().rstrip(".")
    hosts=[dict(h) for h in con.execute("SELECT fqdn,port FROM ssh_servers")
           if h["fqdn"]==hostname or h["fqdn"].split(".")[0]==hostname]
    if len(hosts)!=1:
        raise UpdateError("Hôte absent ou ambigu dans l'inventaire SSH")
    return dict(row),hosts[0]

def queue(agent_ids,credential_id,approved,actor):
    if approved is not True or type(credential_id) is not int or credential_id<1:
        raise UpdateError("Validation et identité SSH obligatoires")
    if not isinstance(agent_ids,list) or not 1<=len(agent_ids)<=100 or any(type(i) is not int or i<1 for i in agent_ids) or len(set(agent_ids))!=len(agent_ids):
        raise UpdateError("Sélection invalide : 1 à 100 agents sans doublon")
    versions=available_versions()
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        credential=con.execute("SELECT kind FROM credentials WHERE id=?",(credential_id,)).fetchone()
        if not credential or credential["kind"]!="ssh":
            raise UpdateError("Identité SSH du coffre requise")
        items=[]
        for agent_id in agent_ids:
            agent,host=target(con,agent_id)
            if not outdated(agent["version"],versions[agent["platform"]]):
                raise UpdateError("Agent #"+str(agent_id)+" déjà à jour ou version inconnue")
            if con.execute("SELECT 1 FROM agent_update_jobs WHERE agent_id=? AND status IN ('queued','running','awaiting_heartbeat')",(agent_id,)).fetchone():
                raise UpdateError("Mise à jour déjà demandée pour agent #"+str(agent_id))
            items.append((agent,host))
        ids=[]
        for agent,host in items:
            ids.append(con.execute(
                "INSERT INTO agent_update_jobs(agent_id,host,port,platform,from_version,target_version,credential_id,status,actor,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (agent["id"],host["fqdn"],host["port"],agent["platform"],agent["version"],
                 versions[agent["platform"]],credential_id,"queued",actor,db.now())).lastrowid)
    db.audit(actor,"agent.update.queue",",".join(map(str,agent_ids)),str(len(ids))+" updates")
    return {"ids":ids,"queued":len(ids)}

def jobs():
    with db.session() as con:
        rows=con.execute("SELECT id,agent_id,host,platform,from_version,target_version,status,error,created_at,finished_at "
                         "FROM agent_update_jobs ORDER BY id DESC LIMIT 100").fetchall()
    return {"available_versions":available_versions(),"items":[dict(r) for r in rows]}

def _remote(job):
    from .executor import _ssh,credential
    if host_keys.inspect(job["host"],job["port"]).get("status")!="trusted":
        raise UpdateError("Clé d'hôte SSH non approuvée ou modifiée")
    client,_=_ssh(None,{"host":job["host"],"port":job["port"]},credential(job["credential_id"]),120)
    try:
        sftp=client.open_sftp()
        try:
            base=sftp.normalize(".").rstrip("/")
            stage=base+"/origami_upgrade_"+secrets.token_hex(12)
            names=["agent.ps1"] if job["platform"]=="windows" else ["agent.py","pra_worker.py"]
            for name in names:
                src=ROOT/("windows" if job["platform"]=="windows" else "linux")/name
                with sftp.file(stage+"_"+name,"xb") as f:
                    f.write(src.read_bytes())
                if job["platform"]=="linux":
                    sftp.chmod(stage+"_"+name,0o600)
        finally:
            sftp.close()
        if job["platform"]=="linux":
            check="import json,sys;c=json.load(open('/opt/origamitasks-agent/config.json'));sys.exit(0 if c.get('agent_id')=="+str(job["agent_id"])+" else 5)"
            rollback=(
                "cp -p /opt/origamitasks-agent/agent.py.origami-backup /opt/origamitasks-agent/agent.py || true; "
                "if test -f /opt/origamitasks-agent/pra_worker.py.origami-backup; then "
                "cp -p /opt/origamitasks-agent/pra_worker.py.origami-backup /opt/origamitasks-agent/pra_worker.py || true; fi; "
                "systemctl restart origamitasks-agent.service >/dev/null 2>&1 || true; "
                "systemctl try-restart origamitasks-agent-pra.service >/dev/null 2>&1 || true"
            )
            script=(
                "set -eu; python3 -c "+shlex.quote(check)+"; "
                "cp -p /opt/origamitasks-agent/agent.py /opt/origamitasks-agent/agent.py.origami-backup; "
                "if test -f /opt/origamitasks-agent/pra_worker.py; then cp -p /opt/origamitasks-agent/pra_worker.py /opt/origamitasks-agent/pra_worker.py.origami-backup; fi; "
                "trap "+shlex.quote(rollback)+" EXIT; "
                "install -m 500 -o origamitasks-agent -g origamitasks-agent "+shlex.quote(stage+"_agent.py")+" /opt/origamitasks-agent/agent.py; "
                "install -m 500 -o origamitasks-agent -g origamitasks-agent "+shlex.quote(stage+"_pra_worker.py")+" /opt/origamitasks-agent/pra_worker.py; "
                "systemctl restart origamitasks-agent.service; "
                "if systemctl is-enabled --quiet origamitasks-agent-pra.service 2>/dev/null; then systemctl restart origamitasks-agent-pra.service; fi; "
                "systemctl is-active --quiet origamitasks-agent.service; "
                "trap - EXIT; "
                "rm -f "+shlex.quote(stage+"_agent.py")+" "+shlex.quote(stage+"_pra_worker.py")+"; "
                "echo ORIGAMI_AGENT_UPDATED"
            )
            inner='if [ "$(id -u)" -eq 0 ]; then exec sh -c '+shlex.quote(script)+'; else exec sudo -n sh -c '+shlex.quote(script)+'; fi'
            cmd="sh -c "+shlex.quote(inner)
        else:
            def winpath(path):
                return re.sub(r"^/([A-Za-z]:/)",r"\1",path).replace("/","\\").replace("'","''")
            staged=winpath(stage+"_agent.ps1")
            ps=(
                "$ErrorActionPreference='Stop';$dest=Join-Path $env:ProgramData 'OrigamiTasks\Agent';"
                "$cfg=Get-Content -LiteralPath (Join-Path $dest 'config.json') -Raw | ConvertFrom-Json;"
                "if([int]$cfg.agent_id -ne "+str(job["agent_id"])+"){throw 'Wrong agent ID'};"
                "$target=Join-Path $dest 'agent.ps1';"
                "Copy-Item -LiteralPath $target -Destination (Join-Path $dest 'agent.ps1.origami-backup') -Force;"
                "Stop-ScheduledTask -TaskName 'OrigamiTasksAgent' -ErrorAction SilentlyContinue;"
                "Copy-Item -LiteralPath '"+staged+"' -Destination $target -Force;"
                "Start-ScheduledTask -TaskName 'OrigamiTasksAgent' -ErrorAction Stop;"
                "Remove-Item -LiteralPath '"+staged+"' -Force;"
                "Write-Output ORIGAMI_AGENT_UPDATED"
            )
            cmd="powershell.exe -NoProfile -NonInteractive -EncodedCommand "+base64.b64encode(ps.encode("utf-16le")).decode("ascii")
        _,stdout,_=client.exec_command(cmd,timeout=120)
        output=stdout.read(4096).decode("utf-8",errors="replace")
        if stdout.channel.recv_exit_status()!=0 or "ORIGAMI_AGENT_UPDATED" not in output:
            raise UpdateError("Remplacement distant non confirmé")
    finally:
        client.close()

def process_one():
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute("SELECT * FROM agent_update_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:return False
        job=dict(row)
        con.execute("UPDATE agent_update_jobs SET status='running' WHERE id=?",(job["id"],))
    status,error="awaiting_heartbeat",""
    try:
        with db.session() as con:
            agent,host=target(con,job["agent_id"])
        if host["fqdn"]!=job["host"] or host["port"]!=job["port"] or agent["platform"]!=job["platform"]:
            raise UpdateError("Association agent / serveur modifiée")
        _remote(job)
    except Exception:
        status,error="failed","Échec ou état incertain : vérifier les services et la sauvegarde locale"
    with db.session() as con:
        con.execute("UPDATE agent_update_jobs SET status=?,error=?,finished_at=? WHERE id=?",
                    (status,error,db.now() if status=="failed" else None,job["id"]))
    db.audit(job["actor"],"agent.update."+status,job["agent_id"],"job="+str(job["id"]))
    return True

def reconcile(agent_id,version,con):
    rows=con.execute("SELECT id,target_version FROM agent_update_jobs WHERE agent_id=? AND status='awaiting_heartbeat'",(agent_id,)).fetchall()
    for row in rows:
        if version==row["target_version"]:
            con.execute("UPDATE agent_update_jobs SET status='success',finished_at=? WHERE id=?",(db.now(),row["id"]))

def loop():
    while not STOP.is_set():
        try:
            if process_one():continue
        except Exception:pass
        STOP.wait(2)

def start():
    global WORKER
    if WORKER is not None and WORKER.is_alive():
        STOP.clear();return
    STOP.clear()
    with db.session() as con:
        con.execute("UPDATE agent_update_jobs SET status='interrupted',finished_at=?,error='Redémarrage contrôleur : vérifier manuellement' "
                    "WHERE status IN ('queued','running')",(db.now(),))
    WORKER=threading.Thread(target=loop,name="OrigamiAgentUpdater",daemon=True)
    WORKER.start()
