"""Cron, one-time and interval workflow schedules with UTC persistence."""
import json
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from . import db, engine

STOP = threading.Event()
UNITS = {'minutes': 60, 'hours': 3600, 'days': 86400, 'weeks': 604800}


def schedule_mode(schedule):
    mode = schedule.get('mode')
    if not mode:
        return 'cron' if schedule.get('cron') else 'manual'
    if mode not in ('manual', 'once', 'interval', 'cron'):
        raise ValueError('Mode de planification inconnu')
    return mode


def schedule_zone(schedule):
    try:
        return ZoneInfo(schedule.get('timezone') or 'Europe/Paris')
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError('Fuseau horaire inconnu') from exc


def next_time(expr, tzname, after=None):
    try:
        from croniter import croniter
    except ImportError:
        raise ValueError('Dépendance croniter requise pour planifier des tâches')
    if not isinstance(expr, str) or len(expr.split()) != 5:
        raise ValueError('Cron doit avoir exactement 5 champs')
    try:
        tz = ZoneInfo(tzname)
        base = (after or datetime.now(timezone.utc)).astimezone(tz)
        return croniter(expr, base).get_next(datetime).astimezone(timezone.utc).isoformat(timespec='seconds')
    except (ValueError, KeyError, ZoneInfoNotFoundError) as exc:
        raise ValueError('Expression cron ou fuseau horaire invalide') from exc


def check_schedule(schedule, after=None):
    if not schedule:
        return None
    mode = schedule_mode(schedule)
    if mode == 'manual':
        return None
    zone = schedule_zone(schedule)
    base = after or datetime.now(timezone.utc)
    if mode == 'cron':
        return next_time(schedule.get('cron', ''), zone.key, base)
    if mode == 'once':
        value = str(schedule.get('at') or '')
        try:
            target = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError('Date/heure unique invalide') from exc
        if target.tzinfo is None:
            target = target.replace(tzinfo=zone)
        target = target.astimezone(timezone.utc)
        # Once consumed, do not replay it after a workflow edit.
        return target.isoformat(timespec='seconds') if target > base else None
    every = schedule.get('every')
    if isinstance(every, bool):
        raise ValueError('Intervalle invalide')
    try:
        every = int(every)
    except (TypeError, ValueError) as exc:
        raise ValueError('Intervalle invalide') from exc
    unit = schedule.get('unit')
    if every < 1 or every > 100000 or unit not in UNITS:
        raise ValueError('Répétition invalide (1 à 100000, minutes/heures/jours/semaines)')
    if unit == 'minutes' and every < 1:
        raise ValueError('Une minute minimum')
    return (base + timedelta(seconds=every * UNITS[unit])).isoformat(timespec='seconds')


def tick():
    from . import pra_lvm_publish, pra_lvm_schedule
    pra_lvm_publish.tick()
    pra_lvm_schedule.tick()
    with db.session() as con:
        rows = con.execute('SELECT * FROM jobs WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY id', (db.now(),)).fetchall()
        for job in rows:
            schedule = json.loads(job['schedule_json'])
            mode = schedule_mode(schedule)
            if mode == 'manual':
                con.execute('UPDATE jobs SET next_run_at=NULL WHERE id=?', (job['id'],))
                continue
            nxt = None if mode == 'once' else check_schedule(schedule)
            # Single DB transaction ensures one enqueue per due schedule.
            con.execute('UPDATE jobs SET next_run_at=? WHERE id=?', (nxt, job['id']))
            con.execute('INSERT INTO runs (job_id,job_name,steps_snapshot,notification_json,trigger,requested_by,queued_at,status) VALUES (?,?,?,?,?,?,?,?)',
                        (job['id'], job['name'], job['steps_json'], job['notification_json'], 'schedule', 'scheduler', db.now(), 'queued'))


def loop():
    while not STOP.is_set():
        try:
            tick()
        except Exception as exc:
            print('scheduler error:', type(exc).__name__, flush=True)
        STOP.wait(5)


def start():
    STOP.clear()
    threading.Thread(target=loop, name='origami-scheduler', daemon=True).start()
