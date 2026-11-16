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
VERSION = '0.3.0'

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

def main():
    cfg = read_config()
    if not cfg.get('token'):
        auth = request(cfg,'/api/agent/enroll',{'agent_id':cfg['agent_id'],
            'enrollment_token':cfg['enrollment_token'], 'platform':'linux',
            'hostname':socket.gethostname()})
        cfg['token']=auth['token']
        cfg.pop('enrollment_token',None)
        write_config(cfg)
    delay=3
    while True:
        try:
            body={'hostname':socket.gethostname(),'version':VERSION}
            response=request(cfg,'/api/agent/poll',body,True)
            task=response.get('task')
            if task:
                try:
                    code,output=run(task)
                except Exception as exc:
                    code,output=1,type(exc).__name__+': '+str(exc)[:300]
                request(cfg,'/api/agent/report',{'task_id':task['id'],'lease':task['lease'],
                    'exit_code':int(code),'output':str(output)[:32000]},True)
            delay=3
        except (KeyboardInterrupt,SystemExit):
            raise
        except Exception as exc:
            print('Agent communication error:',type(exc).__name__,file=sys.stderr,flush=True)
            delay=min(60,delay*2)
        time.sleep(delay)

if __name__=='__main__':
    main()
