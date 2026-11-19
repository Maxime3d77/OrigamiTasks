#!/usr/bin/env python3
"""OrigamiTasks Linux agent: Python 3.9+ stdlib, outbound HTTPS only."""
import json
import os
import platform
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError

CONFIG = Path(__file__).with_name('config.json')
VERSION = '0.3.24'
MODULES_FILE = CONFIG.with_name('modules.json')
PRA_HEALTH = CONFIG.with_name('pra-state.json')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Agent refuses redirects')

def read_config():
    return json.loads(CONFIG.read_text(encoding='utf-8-sig'))

def write_config(data):
    fd, path = tempfile.mkstemp(prefix='.origami-', dir=str(CONFIG.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(path, CONFIG)
    finally:
        if os.path.exists(path):
            os.unlink(path)

def request(cfg, path, payload, authorized=False):
    server = cfg['server'].rstrip('/')
    if not server.startswith('https://'):
        raise ValueError('Agent requires HTTPS')
    headers = {'Content-Type':'application/json'}
    if authorized:
        headers['Authorization'] = 'Bearer '+cfg['token']
        headers['X-Origami-Agent-ID'] = str(cfg['agent_id'])
    ca = cfg.get('ca_cert') or None
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context(cafile=ca)),NoRedirect())
    data = json.dumps(payload).encode('utf-8')
    with opener.open(Request(server+path, data=data,headers=headers,method='POST'),timeout=40) as response:
        return json.load(response)

def command(args, timeout):
    try:
        p = subprocess.run(args, capture_output=True, text=True, errors='replace', timeout=timeout)
        return p.returncode, (p.stdout+'\n'+p.stderr).strip()[:32000]
    except subprocess.TimeoutExpired:
        return 124, 'Command timed out; verify the target before retrying'
    except Exception as exc:
        return 1, type(exc).__name__+': '+str(exc)[:300]

def run(task):
    action = task['action']
    options = task.get('payload') or {}
    timeout = min(max(int(task.get('timeout') or 60),1),1800)
    if action == 'inventory':
        return 0, json.dumps({'hostname':socket.gethostname(), 'os':platform.platform(),
             'python':platform.python_version(),'architecture':platform.machine(),'agent':VERSION})
    if action == 'script':
        return command(['/bin/bash','-c',options['script']],timeout)
    if action == 'processes':
        return command(['ps','ax','-o','pid,user,comm,%cpu,%mem'],timeout)
    if action == 'disk':
        return command(['df','-h'],timeout)
    if action.startswith('service_'):
        name = options['service']
        verbs = {'service_status':'status','service_start':'start',
                 'service_stop':'stop','service_restart':'restart'}
        if action not in verbs:
            return 1,'Unknown action'
        return command(['systemctl',verbs[action],name],timeout)
    return 1, 'Unsupported action'

def process_memory_mb():
    """Current RSS for this agent process only, not its child processes."""
    try:
        with open('/proc/self/statm',encoding='ascii') as stream:
            pages=int(stream.read().split()[1])
        return round(pages*os.sysconf('SC_PAGE_SIZE')/(1024*1024),2)
    except (OSError,ValueError,IndexError):
        return None


def apply_modules(response, agent_id):
    """Persist admin policy locally. No secrets or VM mutation instructions."""
    modules=response.get('modules')
    revision=response.get('modules_revision')
    if (not isinstance(modules,dict) or
            set(modules)!={'standard','proxmox','pra'} or
            any(type(x) is not bool for x in modules.values()) or
            modules['standard'] is not True or
            type(revision) is not int or revision<1):
        return
    policy=response.get('pra_policy')
    if (not isinstance(policy,dict) or
            set(policy)!={'replication_enabled','promotion_enabled',
                          'fencing_ready','witness_ready'} or
            any(x is not False for x in policy.values())):
        return
    site=response.get('site')
    if site is not None and (not isinstance(site,dict) or
                             not isinstance(site.get('clusters'),list)):
        return
    data={'agent_id':agent_id,'revision':revision,'modules':modules,
          'site':site,'pra_policy':policy}
    if MODULES_FILE.exists():
        try:
            old=json.loads(MODULES_FILE.read_text(encoding='utf-8'))
            if old==data:
                return
        except (OSError,ValueError):
            pass
    fd, name=tempfile.mkstemp(prefix='.origami-modules-',dir=str(MODULES_FILE.parent))
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            json.dump(data,output,separators=(',',':'))
            output.flush()
            os.fsync(output.fileno())
        os.replace(name,MODULES_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def module_report(agent_id):
    """Report applied configuration and local worker liveness, not DR readiness."""
    base={'standard':'ready','proxmox':'disabled','pra':'disabled'}
    revision=0
    if MODULES_FILE.is_file() and not MODULES_FILE.is_symlink():
        try:
            policy=json.loads(MODULES_FILE.read_text(encoding='utf-8'))
            if policy.get('agent_id')==agent_id:
                revision=int(policy['revision'])
                base['proxmox']='configured' if policy['modules']['proxmox'] else 'disabled'
                if policy['modules']['pra']:
                    base['pra']='not_installed'
                    try:
                        health=json.loads(PRA_HEALTH.read_text(encoding='utf-8'))
                        if (time.time()-float(health['timestamp'])<=50 and
                                health['revision']==revision and
                                health['status']=='passive' and
                                health.get('promotion_permitted') is False):
                            base['pra']='passive'
                        elif time.time()-float(health['timestamp'])>50:
                            base['pra']='stale'
                    except (OSError,ValueError,TypeError,KeyError):
                        pass
        except (OSError,ValueError,TypeError,KeyError):
            revision=0
            base['proxmox']='error'
            base['pra']='error'
    return {'modules_applied_revision':revision,'modules_status':base}


def main():
    cfg = read_config()
    if not cfg.get('token'):
        auth = request(cfg,'/api/agent/enroll',{'agent_id':cfg['agent_id'],
            'enrollment_token':cfg['enrollment_token'], 'platform':'linux',
            'hostname':socket.gethostname()})
        cfg['token']=auth['token']
        cfg.pop('enrollment_token',None)
        write_config(cfg)
    interval = min(60,max(5,int(cfg.get('poll_interval_seconds',10))))
    delay = interval
    previous_cpu=time.process_time()
    previous_wall=time.monotonic()
    while True:
        try:
            now_cpu=time.process_time()
            now_wall=time.monotonic()
            elapsed=max(.001,now_wall-previous_wall)
            cpu=round(max(0,min(1000,100*(now_cpu-previous_cpu)/elapsed)),2)
            previous_cpu,previous_wall=now_cpu,now_wall
            body={'hostname':socket.gethostname(),'version':VERSION,
                  'cpu_percent':cpu,'memory_mb':process_memory_mb(),
                  **module_report(cfg['agent_id'])}
            response=request(cfg,'/api/agent/poll',body,True)
            apply_modules(response,cfg['agent_id'])
            task=response.get('task')
            if task:
                try:
                    code,output=run(task)
                except Exception as exc:
                    code,output=1,type(exc).__name__+': '+str(exc)[:300]
                request(cfg,'/api/agent/report',{'task_id':task['id'],'lease':task['lease'],
                    'exit_code':int(code),'output':str(output)[:32000]},True)
            delay=interval
        except (KeyboardInterrupt,SystemExit):
            raise
        except Exception as exc:
            print('Agent communication error:',type(exc).__name__,file=sys.stderr,flush=True)
            delay=min(60,delay*2)
        time.sleep(delay)

if __name__=='__main__':
    main()
