#!/usr/bin/env python3
"""Separate privileged host service; read database update requests and perform trusted Git fast-forward updates.

It MUST run on the Docker host, not inside the web container. Do not point
its repository to an untrusted origin. Access to the web admin = update rights.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


def execute(args, root, timeout=600):
    p=subprocess.run(args, cwd=root, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f'{" ".join(args[:4])}: {(p.stderr or p.stdout)[-1500:]}')
    return p.stdout.strip()


def state_write(root,message,**kwargs):
    dst=root/'data/update-state.json'
    dst.write_text(json.dumps({'checked_at':now(),'message':message,**kwargs},ensure_ascii=False,indent=2))


def version_at(root, git, ref):
    """Read advertised version from trusted Git content, without executing it."""
    try:
        source=execute(git+['show',ref+':app/config.py'],root,timeout=15)
    except Exception:
        return None
    match=re.search(r"(?m)^APP_VERSION\s*=\s*['\"]([^'\"]{1,64})['\"]",source)
    return match.group(1) if match else None


def process(root, action, branch, allowed_origin):
    git=['git','-c','core.hooksPath=/dev/null','-c','protocol.file.allow=never']
    # Restrict the update to a configured, pre-reviewed origin, never a user-supplied URL.
    origin=execute(git+['remote','get-url','origin'],root)
    if not allowed_origin or origin != allowed_origin:
        raise RuntimeError('Origin Git non épinglée ou différente de GIT_ALLOWED_ORIGIN. Fournir l’URL exacte du dépôt approuvé dans .env')
    status=execute(git+['status','--porcelain'],root)
    if status: raise RuntimeError('Dépôt non propre : modifier/committer les fichiers locaux avant toute mise à jour')
    old=execute(git+['rev-parse','HEAD'],root)
    execute(git+['fetch','--no-tags','origin',f'refs/heads/{branch}'],root,timeout=180)
    remote=execute(git+['rev-parse','FETCH_HEAD'],root)
    subprocess.run(git+['merge-base','--is-ancestor',old,remote],cwd=root,check=True,stdout=subprocess.DEVNULL)
    available=(old!=remote)
    current_version=version_at(root,git,'HEAD')
    latest_version=version_at(root,git,'FETCH_HEAD')
    latest_date=execute(git+['show','-s','--format=%cI','FETCH_HEAD'],root,timeout=15)
    remote_count=int(execute(git+['rev-list','--count',old+'..'+remote],root,timeout=15))
    state_write(root,'Mise à jour disponible' if available else 'Déjà à jour',
                current=old, latest=remote, available=available, origin=origin,
                branch=branch, current_version=current_version,
                latest_version=latest_version, latest_date=latest_date,
                commits_behind=remote_count)
    if action=='preflight':
        # Read-only validation: no merge, no rebuild, no Docker changes.
        execute(['docker','compose','version'],root,timeout=15)
        execute(['docker','compose','config','--quiet'],root,timeout=25)
        data_dir=root/'data'
        if not data_dir.is_dir() or not os.access(data_dir,os.R_OK|os.W_OK):
            raise RuntimeError('Le répertoire de données ne permet pas les sauvegardes')
        database=data_dir/'origamitasks.db'
        if not database.is_file():
            raise RuntimeError('Base SQLite introuvable pour sauvegarde')
        state_write(root,'Préparation de mise à jour validée (simulation, aucune installation)',
                    current=old,latest=remote,available=available,origin=origin,
                    branch=branch,current_version=current_version,
                    latest_version=latest_version,latest_date=latest_date,
                    commits_behind=remote_count,preflight_ok=True,
                    preflight_at=now())
        return ('Simulation réussie : '+str(remote_count)+' commit(s) disponibles, '
                'Docker Compose et sauvegardes vérifiés ; aucune mise à jour installée')
    if action=='check' or not available:
        return 'Nouveau commit : '+remote[:12] if available else 'Aucune mise à jour disponible'
    backup=root/'data/backups'
    backup.mkdir(parents=True,exist_ok=True)
    database=root/'data/origamitasks.db'
    # SQLite online backup supports an active web process writing to WAL.
    if database.exists():
        src=sqlite3.connect(database)
        dst=sqlite3.connect(backup/f'origamitasks-{int(time.time())}.db')
        src.backup(dst);src.close();dst.close()
    try:
        execute(git+['merge','--ff-only','FETCH_HEAD'],root,timeout=30)
        execute(['docker','compose','up','-d','--build','--remove-orphans'],root,timeout=900)
        port=int(os.environ.get('ORIGAMI_PORT','8080'))
        last=''
        for _ in range(30):
            try:
                with urlopen(f'http://127.0.0.1:{port}/api/health',timeout=3) as response:
                    if response.status==200:
                        state_write(root,'Déploiement confirmé',
                                    current=remote,latest=remote,available=False,branch=branch,
                                    current_version=latest_version,latest_version=latest_version,
                                    latest_date=latest_date,commits_behind=0)
                        return 'Mise à jour appliquée : '+remote[:12]
            except Exception as exc:
                last=type(exc).__name__
            time.sleep(2)
        raise RuntimeError('Healthcheck échoué après redéploiement '+last)
    except Exception as exc:
        execute(git+['reset','--hard',old],root,timeout=30)
        execute(['docker','compose','up','-d','--build'],root,timeout=900)
        raise RuntimeError('Rollback applicatif demandé: '+str(exc))


def heartbeat(root):
    """Heartbeat separates a running host updater from stale state.json."""
    import tempfile
    target=root/'data'/'update-worker-heartbeat.json'
    if not target.parent.is_dir(): return
    tmp=target.with_name(target.name+'.tmp')
    tmp.write_text(json.dumps({'seen_at':now(),'pid':os.getpid()}))
    os.replace(tmp,target)


def loop(root,once=False):
    branch='main'
    allowed_origin=''
    env=root/'.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith('GIT_BRANCH='):branch=line.split('=',1)[1].strip().strip('"')
            if line.startswith('ORIGAMI_PORT='):os.environ['ORIGAMI_PORT']=line.split('=',1)[1].strip().strip('"')
            if line.startswith('GIT_ALLOWED_ORIGIN='):allowed_origin=line.split('=',1)[1].strip().strip('"')
    if not branch or not all(ch.isalnum() or ch in '-_/' for ch in branch):
        raise RuntimeError('GIT_BRANCH invalide')
    state_write(root,'Service de maintenance actif',branch=branch)
    last_auto_check=0
    while True:
        try: heartbeat(root)
        except OSError: pass
        # Periodic, passive check: fetch only the pinned origin. Never install
        # updates automatically and never claim "up-to-date" if check failed.
        if not once and allowed_origin and time.monotonic()-last_auto_check>=3600:
            last_auto_check=time.monotonic()
            try:
                process(root,'check',branch,allowed_origin)
            except Exception as exc:
                state_write(root,'Vérification impossible : '+type(exc).__name__,
                            branch=branch,error='Vérifier GIT_BRANCH, GIT_ALLOWED_ORIGIN et la branche locale')
        try:
            con=sqlite3.connect(root/'data/origamitasks.db',timeout=30)
            con.row_factory=sqlite3.Row
            row=con.execute("SELECT id,action FROM updates WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                changed=con.execute("UPDATE updates SET status='running',message='Traitement par le service hôte' WHERE id=? AND status='queued'",(row['id'],))
                con.commit()
                if changed.rowcount:
                    try:
                        message=process(root,row['action'],branch,allowed_origin)
                        status='success'
                    except Exception as exc:
                        status='failed'
                        message=str(exc)[:1000]
                        state_write(root,'Dernière opération en échec',error=message)
                    con.execute('UPDATE updates SET status=?,message=?,finished_at=? WHERE id=?',(status,message,now(),row['id']))
                    con.commit()
            con.close()
        except sqlite3.OperationalError:
            pass
        if once: break
        time.sleep(5)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--once',action='store_true')
    a=parser.parse_args()
    loop(a.root.resolve(),a.once)
