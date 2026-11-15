"""Persistent run queue + branchable graph interpreter."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from . import config, db, executor

STOP = threading.Event()


def evaluate_condition(rule, ctx):
    if not rule or not rule.get('enabled', True):
        return True
    source = rule.get('source')
    if source == 'last_status':
        left = ctx['last'].get('status')
    elif source == 'last_exit_code':
        left = ctx['last'].get('exit_code')
    elif source == 'last_output':
        left = ctx['last'].get('output')
    elif source == 'variable':
        left = ctx['variables'].get(rule.get('key', ''), '')
    else:
        return False
    right = executor.interpolate(str(rule.get('value', '')), ctx)
    operator = rule.get('operator', 'equals')
    if operator == 'equals': return str(left) == right
    if operator == 'not_equals': return str(left) != right
    if operator == 'contains': return right in str(left)
    if operator == 'not_contains': return right not in str(left)
    if operator == 'exists': return left not in ('', None)
    return False


def run_graph(steps, run_id, step_exec=executor.execute, variables=None):
    """Return terminal status. Loops cap at 200 transitions to prevent accidents."""
    if not steps:
        return 'success', ''
    lookup = {s['id']: s for s in steps}
    order = [s['id'] for s in steps]
    ctx = {'run_id': run_id, 'steps': {}, 'last': {}, 'variables': dict(variables or {})}
    current = order[0]
    for _ in range(200):
        if current in ('END', None):
            return 'success', ''
        if current == 'FAIL':
            return 'failed', 'Branche d’échec atteinte'
        if current not in lookup:
            return 'failed', f'Étape inconnue {current}'
        step = lookup[current]
        index = order.index(current)
        default_next = order[index+1] if index+1 < len(order) else 'END'
        if not evaluate_condition(step.get('condition'), ctx):
            record_step(run_id, step, 'skipped', 0, 'Condition non remplie', 0)
            ctx['steps'][current] = {'status':'skipped','exit_code':0,'output':''}
            current = step.get('on_success') or default_next
            continue
        attempts = min(max(int(step.get('retries',0)), 0), 5) + 1
        result = None
        for attempt in range(1, attempts+1):
            start = db.now()
            try:
                result = step_exec(step, ctx)
            except executor.StepError as exc:
                result = {'status':'failed', 'exit_code':exc.code, 'output':exc.output}
            except Exception as exc:
                # No tracebacks containing potential credential payloads in task output.
                result = {'status':'failed','exit_code':1,'output':f'{type(exc).__name__}: exécution impossible'}
            record_step(run_id, step, result['status'], result.get('exit_code'), result.get('output',''), attempt, start)
            if result['status'] == 'success':
                break
            if attempt < attempts:
                time.sleep(min(max(int(step.get('retry_delay',2)),0),60))
        if result['status'] == 'success' and step.get('type') == 'set_variable':
            name = str(step.get('config',{}).get('name','')).strip()
            if name and len(name) <= 64:
                ctx['variables'][name] = executor.expand(step.get('config',{}).get('value',''),ctx)
        ctx['steps'][current] = result
        ctx['last'] = result
        current = (step.get('on_success') or default_next) if result['status']=='success' else (step.get('on_failure') or 'FAIL')
    return 'failed', 'Nombre maximal de transitions (200) atteint'


def record_step(run_id, step, status, exit_code, output, attempt=1, started_at=None):
    with db.session() as con:
        con.execute('INSERT INTO step_runs (run_id,step_id,name,type,status,exit_code,output,attempt,started_at,finished_at) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (run_id,step['id'],step['name'],step['type'],status,exit_code,str(output)[:config.MAX_LOG_BYTES],attempt,started_at or db.now(),db.now()))


def enqueue(job, trigger, actor, variables=None):
    with db.session() as con:
        cur = con.execute('INSERT INTO runs (job_id,job_name,steps_snapshot,variables_json,trigger,requested_by,queued_at,status) VALUES (?,?,?,?,?,?,?,?)',
                          (job['id'],job['name'],job['steps_json'] if isinstance(job['steps_json'],str) else json.dumps(job['steps_json']), json.dumps(variables or {},ensure_ascii=False), trigger,actor,db.now(),'queued'))
        return cur.lastrowid


def take():
    with db.session() as con:
        row = con.execute("SELECT * FROM runs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row:
            result = con.execute("UPDATE runs SET status='running',started_at=? WHERE id=? AND status='queued'", (db.now(),row['id']))
            if result.rowcount:
                return dict(row)
    return None


def process(run):
    try:
        steps = json.loads(run['steps_snapshot'])
        status, error = run_graph(steps, run['id'],variables=json.loads(run.get('variables_json') or '{}'))
    except Exception as exc:
        status,error = 'failed',f'Erreur moteur: {type(exc).__name__}'
    with db.session() as con:
        con.execute('UPDATE runs SET status=?,error=?,finished_at=? WHERE id=?', (status,error,db.now(),run['id']))
    db.audit('engine', 'run.finished', run['id'], status)


def loop():
    with ThreadPoolExecutor(max_workers=max(1,min(config.WORKERS,8)),thread_name_prefix='origami-step') as pool:
        while not STOP.is_set():
            next_run = take()
            if next_run:
                pool.submit(process,next_run)
            else:
                STOP.wait(0.7)


def start():
    STOP.clear()
    threading.Thread(target=loop, name='origami-engine', daemon=True).start()
