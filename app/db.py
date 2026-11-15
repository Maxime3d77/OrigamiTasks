"""SQLite persistence, initialized once and used with per-thread connections."""
import sqlite3
import json
from datetime import datetime, timezone
from contextlib import contextmanager
from . import config


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect():
    config.DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DB, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('PRAGMA foreign_keys=ON')
    return db


@contextmanager
def session():
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def obj(row, *json_fields):
    if not row:
        return None
    item = dict(row)
    for field in json_fields:
        if field in item:
            item[field] = json.loads(item[field] or '[]')
    return item


SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
 password_hash TEXT, source TEXT NOT NULL DEFAULT 'local', role TEXT NOT NULL,
 groups_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 sid_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 csrf_hash TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 folder TEXT NOT NULL DEFAULT 'Général', tags_json TEXT NOT NULL DEFAULT '[]',
 acl_json TEXT NOT NULL DEFAULT '{}', steps_json TEXT NOT NULL DEFAULT '[]',
 schedule_json TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
 next_run_at TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhooks (
 job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credentials (
 id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
 encrypted_payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datacenters (
 id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, url TEXT NOT NULL,
 credential_id INTEGER NOT NULL REFERENCES credentials(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
 job_name TEXT NOT NULL, steps_snapshot TEXT NOT NULL, variables_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'queued',
 trigger TEXT NOT NULL, requested_by TEXT NOT NULL, queued_at TEXT NOT NULL,
 started_at TEXT, finished_at TEXT, error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS step_runs (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 step_id TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL,
 status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, exit_code INTEGER,
 output TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, finished_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
 target TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS updates (
 id INTEGER PRIMARY KEY, action TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 actor TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, queued_at);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_id, queued_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_next ON jobs(enabled, next_run_at);
'''


def initialize():
    config.TRANSFER_ROOT.mkdir(parents=True, exist_ok=True)
    with session() as con:
        con.executescript(SCHEMA)
        # On restart, a process killed mid-execution must not silently re-run side effects.
        con.execute("UPDATE runs SET status='interrupted', finished_at=?, error='Service redémarré pendant l’exécution' WHERE status='running'", (now(),))


def audit(actor, action, target, detail=''):
    with session() as con:
        con.execute('INSERT INTO audit (actor,action,target,detail,created_at) VALUES (?,?,?,?,?)', (actor, action, str(target), detail[:1000], now()))
