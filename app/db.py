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
 schedule_json TEXT NOT NULL DEFAULT '{}', notification_json TEXT NOT NULL DEFAULT '{}',
 enabled INTEGER NOT NULL DEFAULT 1,
 next_run_at TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_folders (
 id INTEGER PRIMARY KEY,
 path TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhooks (
 job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ldap_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), encrypted_config TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credentials (
 id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
 encrypted_payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ssh_server_groups (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ssh_servers (
 id INTEGER PRIMARY KEY, fqdn TEXT NOT NULL COLLATE NOCASE UNIQUE,
 port INTEGER NOT NULL DEFAULT 22 CHECK(port BETWEEN 1 AND 65535),
 label TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ssh_server_group_members (
 group_id INTEGER NOT NULL REFERENCES ssh_server_groups(id) ON DELETE CASCADE,
 server_id INTEGER NOT NULL REFERENCES ssh_servers(id) ON DELETE CASCADE,
 PRIMARY KEY (group_id,server_id)
);
CREATE INDEX IF NOT EXISTS idx_ssh_server_members ON ssh_server_group_members(server_id);
CREATE TABLE IF NOT EXISTS datacenters (
 id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, url TEXT NOT NULL,
 credential_id INTEGER NOT NULL REFERENCES credentials(id),
 kind TEXT NOT NULL DEFAULT 'cluster', ca_cert TEXT NOT NULL DEFAULT '',
 skip_tls_verification INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dr_sites (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL UNIQUE COLLATE NOCASE,
 role TEXT NOT NULL CHECK(role IN ('primary','standby','other')),
 storage_profile TEXT NOT NULL DEFAULT 'auto',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dr_site_datacenters (
 site_id INTEGER NOT NULL REFERENCES dr_sites(id) ON DELETE CASCADE,
 datacenter_id INTEGER NOT NULL UNIQUE REFERENCES datacenters(id) ON DELETE CASCADE,
 PRIMARY KEY(site_id,datacenter_id)
);
CREATE TABLE IF NOT EXISTS dr_site_agents (
 site_id INTEGER NOT NULL REFERENCES dr_sites(id) ON DELETE CASCADE,
 agent_id INTEGER NOT NULL UNIQUE REFERENCES agents(id) ON DELETE CASCADE,
 PRIMARY KEY(site_id,agent_id)
);
CREATE TABLE IF NOT EXISTS dr_simulations (
 id INTEGER PRIMARY KEY, source_site_id INTEGER REFERENCES dr_sites(id) ON DELETE SET NULL,
 target_site_id INTEGER REFERENCES dr_sites(id) ON DELETE SET NULL,
 scenario TEXT NOT NULL, outcome TEXT NOT NULL,
 result_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
 job_name TEXT NOT NULL, steps_snapshot TEXT NOT NULL, variables_json TEXT NOT NULL DEFAULT '{}',
 notification_json TEXT NOT NULL DEFAULT '{}', notification_status TEXT NOT NULL DEFAULT '',
 notification_error TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'queued',
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
CREATE TABLE IF NOT EXISTS agents (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, platform TEXT NOT NULL,
 group_name TEXT NOT NULL DEFAULT 'default', hostname TEXT NOT NULL DEFAULT '',
 version TEXT NOT NULL DEFAULT '', enroll_hash TEXT, enroll_expires TEXT,
 token_hash TEXT, enrolled INTEGER NOT NULL DEFAULT 0,
 revoked INTEGER NOT NULL DEFAULT 0, last_seen TEXT,
 cpu_percent REAL, memory_mb REAL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_deploy_batches (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, platform TEXT NOT NULL,
 agent_group TEXT NOT NULL, total INTEGER NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_deploy_targets (
 id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES agent_deploy_batches(id),
 host TEXT NOT NULL, port INTEGER NOT NULL, server_id INTEGER,
 credential_id INTEGER NOT NULL, status TEXT NOT NULL,
 agent_id INTEGER, error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_deploy_queue ON agent_deploy_targets(status,id);
CREATE TABLE IF NOT EXISTS agent_tasks (
 id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id),
 run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
 step_id TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', timeout_seconds INTEGER NOT NULL,
 lease_hash TEXT, queued_at TEXT NOT NULL, claimed_at TEXT,
 completed_at TEXT, exit_code INTEGER, output TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_queue ON agent_tasks(agent_id,status,id);
CREATE INDEX IF NOT EXISTS idx_agents_online ON agents(last_seen);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, queued_at);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_id, queued_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_next ON jobs(enabled, next_run_at);
'''


def initialize():
    config.TRANSFER_ROOT.mkdir(parents=True, exist_ok=True)
    with session() as con:
        con.executescript(SCHEMA)
        # Idempotent migration: preserve existing workflows and execution history.
        for table, column, definition in (
            ("jobs", "notification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("runs", "notification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("runs", "notification_status", "TEXT NOT NULL DEFAULT ''"),
            ("runs", "notification_error", "TEXT NOT NULL DEFAULT ''"),
            ("datacenters", "kind", "TEXT NOT NULL DEFAULT 'cluster'"),
            ("datacenters", "ca_cert", "TEXT NOT NULL DEFAULT ''"),
            ("datacenters", "skip_tls_verification", "INTEGER NOT NULL DEFAULT 0"),
            ("agents", "cpu_percent", "REAL"),
            ("agents", "memory_mb", "REAL"),
        ):
            existing = {row["name"] for row in con.execute("PRAGMA table_info("+table+")")}
            if column not in existing:
                con.execute("ALTER TABLE "+table+" ADD COLUMN "+column+" "+definition)
        con.execute("INSERT OR IGNORE INTO workflow_folders (path,created_at) VALUES ('Général',?)", (now(),))
        # Never silently replay a claimed remote action after a server restart.
        con.execute("UPDATE agent_tasks SET status='interrupted', lease_hash=NULL WHERE status IN ('queued','claimed')")
        # On restart, a process killed mid-execution must not silently re-run side effects.
        con.execute("UPDATE runs SET status='interrupted', finished_at=?, error='Service redémarré pendant l’exécution' WHERE status='running'", (now(),))


def audit(actor, action, target, detail=''):
    with session() as con:
        con.execute('INSERT INTO audit (actor,action,target,detail,created_at) VALUES (?,?,?,?,?)', (actor, action, str(target), detail[:1000], now()))
