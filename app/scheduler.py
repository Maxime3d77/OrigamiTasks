"""Five-field cron schedules in user-selected IANA time zones, UTC persistence."""
import json
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from . import db, engine
STOP = threading.Event()


def next_time(expr, tzname, after=None):
    try:
        from croniter import croniter
    except ImportError:
        raise ValueError('Dépendance croniter requise pour planifier des tâches')
    tz = ZoneInfo(tzname)
    base = (after or datetime.now(timezone.utc)).astimezone(tz)
    t = croniter(expr,base).get_next(datetime)
    return t.astimezone(timezone.utc).isoformat(timespec='seconds')


def check_schedule(schedule):
    if not schedule or not schedule.get('cron'):
        return None
    expr = schedule['cron']
    if len(expr.split()) != 5:
        raise ValueError('Cron doit avoir exactement 5 champs (minutes heures jour mois semaine)')
    return next_time(expr,schedule.get('timezone') or 'Europe/Paris')


def tick():
    with db.session() as con:
        rows = con.execute('SELECT * FROM jobs WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY id', (db.now(),)).fetchall()
        for job in rows:
            schedule = json.loads(job['schedule_json'])
            if not schedule.get('cron'): continue
            # Atomic update and enqueue inside a single DB transaction: avoid duplicate triggers.
            nxt = next_time(schedule['cron'],schedule.get('timezone','Europe/Paris'))
            con.execute('UPDATE jobs SET next_run_at=? WHERE id=?', (nxt,job['id']))
            con.execute('INSERT INTO runs (job_id,job_name,steps_snapshot,trigger,requested_by,queued_at,status) VALUES (?,?,?,?,?,?,?)',
                        (job['id'],job['name'],job['steps_json'],'schedule','scheduler',db.now(),'queued'))


def loop():
    while not STOP.is_set():
        try: tick()
        except Exception as exc:
            print('scheduler error:', type(exc).__name__, flush=True)
        STOP.wait(5)


def start():
    STOP.clear()
    threading.Thread(target=loop,name='origami-scheduler',daemon=True).start()
