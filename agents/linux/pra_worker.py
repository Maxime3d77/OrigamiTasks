#!/usr/bin/env python3
"""OrigamiTasks PRA local passive guard — no VM, network or storage actions.

The module is part of the same agent installation but runs as an isolated
systemd service. It may stay alive if the OrigamiTasks console/SD site dies.
It cannot promote a VM; the independent witness and fencing are future work.
"""
import json
import os
import signal
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POLICY = ROOT / "modules.json"
STATUS = ROOT / "pra-state.json"
STOP = False


def shutdown(_signum, _frame):
    global STOP
    STOP = True


def atomic_write(path, document):
    fd, tmp = tempfile.mkstemp(prefix=".pra-state-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, separators=(",",":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_policy():
    """Return a deliberately non-authorizing projection of local policy."""
    try:
        if POLICY.is_symlink() or POLICY.stat().st_size > 65536:
            return False, 0
        data=json.loads(POLICY.read_text(encoding="utf-8"))
        modules=data.get("modules",{})
        safety=data.get("pra_policy",{})
        # Configuration cannot activate business operations; require all
        # guard flags OFF to accept even passive monitoring.
        guards=("replication_enabled","promotion_enabled","fencing_ready","witness_ready")
        if (not isinstance(modules,dict) or not isinstance(safety,dict)
                or any(safety.get(key) is not False for key in guards)
                or data.get("agent_id") is None):
            return False, 0
        return modules.get("pra") is True, int(data.get("revision",0))
    except (OSError,ValueError,TypeError,KeyError):
        return False, 0


def step():
    active, revision = read_policy()
    status = {
        "status":"passive" if active else "disabled",
        "revision":revision,
        "timestamp":time.time(),
        "replication_active":False,
        "promotion_permitted":False,
        "fencing_operational":False,
        "witness_operational":False,
    }
    atomic_write(STATUS,status)
    return status


def main():
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    while not STOP:
        try:
            step()
        except (OSError,ValueError):
            # No safety-sensitive action depends on a successful loop.
            pass
        for _ in range(15):
            if STOP:
                break
            time.sleep(1)


if __name__=="__main__":
    main()
