#!/usr/bin/env python3
"""QEMU/QMP block-level full and incremental copy for an *online* source VM.

Experimental data-plane component. It is intentionally NOT exposed as an agent
task or an API endpoint until the target-side COW checkpoint protocol, root
privilege boundary and per-VM fencing have been validated.

The NBD destination must be a fresh, private and writable STAGING image (not the
disk attached to the standby VM). Only the target coordinator may publish a
verified checkpoint. Never connect to a readonly Pure replica or a live disk.
"""
import json
import re
import socket
import time
from dataclasses import dataclass

IDENTIFIER = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,100}$")
HOST = re.compile(r"^(?:127\.0\.0\.1|::1)$")


class ReplicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Disk:
    source: str
    bitmap: str
    host: str
    port: int
    export: str = ""

    def validate(self):
        if not IDENTIFIER.fullmatch(self.source):
            raise ReplicationError("Invalid QEMU source node")
        if not IDENTIFIER.fullmatch(self.bitmap):
            raise ReplicationError("Invalid dedicated bitmap identifier")
        if not HOST.fullmatch(self.host):
            raise ReplicationError("NBD must use a local SSH tunnel (loopback only)")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ReplicationError("Invalid NBD port")
        if self.export and not IDENTIFIER.fullmatch(self.export):
            raise ReplicationError("Invalid NBD export name")


class QMP:
    """Minimal synchronous QMP client with asynchronous event collection."""

    def __init__(self, path, timeout=15):
        if not isinstance(path, str) or not path.startswith("/"):
            raise ReplicationError("QMP path must be absolute")
        self.path = path
        self.timeout = timeout
        self.sock = None
        self.reader = None
        self.number = 0
        self.events = []

    def __enter__(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(self.path)
            self.reader = self.sock.makefile("rb")
            greeting = self._read()
            if not isinstance(greeting, dict) or "QMP" not in greeting:
                raise ReplicationError("Missing QMP greeting")
            self.execute("qmp_capabilities")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.reader:
            self.reader.close()
            self.reader = None
        if self.sock:
            self.sock.close()
            self.sock = None

    def _read(self):
        line = self.reader.readline(4 * 1024 * 1024)
        if not line:
            raise ReplicationError("QMP connection closed")
        try:
            return json.loads(line)
        except ValueError as exc:
            raise ReplicationError("Malformed QMP response") from exc

    def execute(self, command, arguments=None):
        self.number += 1
        ident = "origami-" + str(self.number)
        msg = {"execute": command, "id": ident}
        if arguments is not None:
            msg["arguments"] = arguments
        self.sock.sendall((json.dumps(msg, separators=(",", ":")) + "\r\n").encode())
        while True:
            result = self._read()
            if "event" in result:
                self.events.append(result)
                continue
            if result.get("id") != ident:
                raise ReplicationError("Unexpected QMP response ID")
            if "error" in result:
                error = result["error"]
                desc = error.get("desc", "QMP error") if isinstance(error, dict) else "QMP error"
                raise ReplicationError(str(desc)[:300])
            if "return" not in result:
                raise ReplicationError("QMP response missing return")
            return result["return"]


def _bitmaps(qmp, node):
    blocks = qmp.execute("query-named-block-nodes")
    selected = [item for item in blocks if item.get("node-name") == node]
    if len(selected) != 1:
        raise ReplicationError("QMP source node does not exist or is ambiguous: " + node)
    image = selected[0]
    return {bm.get("name"): bm for bm in image.get("dirty-bitmaps", [])}


def _job_result(qmp, job_ids, timeout):
    """Fail closed on missing/failed jobs; never declare a target committed."""
    pending = set(job_ids)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        jobs = qmp.execute("query-jobs")
        observed = {j.get("id"): j for j in jobs}
        for ident in list(pending):
            job = observed.get(ident)
            if not job:
                # Jobs can be automatically dismissed by QEMU; an event must
                # then prove completion. Absence alone is NEVER proof.
                matches = [e for e in qmp.events
                           if e.get("event") == "JOB_STATUS_CHANGE"
                           and e.get("data", {}).get("id") == ident
                           and e.get("data", {}).get("status") == "concluded"]
                if not matches:
                    continue
                # Need query-jobs (with auto-dismiss disabled) to establish
                # the absence of the 'error' property.
                raise ReplicationError("QMP job disappeared before verification")
            status = job.get("status")
            if status in ("aborting", "null"):
                raise ReplicationError("QMP backup job aborted")
            if status == "concluded":
                if job.get("error"):
                    raise ReplicationError("QMP backup job failed: " + str(job["error"])[:250])
                qmp.execute("job-dismiss", {"id": ident})
                pending.remove(ident)
        if pending:
            time.sleep(0.25)
    if pending:
        raise ReplicationError("QMP backup timed out; checkpoint MUST be discarded")


def backup(qmp, disks, mode, timeout=3600):
    """Copy one point-in-time disk set through pre-established loopback NBD.

    The caller MUST guarantee: source running, standby stopped, validated
    private NBD staging exports, and independent target-side commit/rollback.
    A successful return means *backup jobs* completed, not that a standby VM
    is bootable or that the target checkpoint has been committed.
    """
    if mode not in ("full", "incremental") or not disks:
        raise ReplicationError("Mode full/incremental and disks required")
    if type(timeout) not in (int, float) or not 1 <= timeout <= 86400:
        raise ReplicationError("Invalid job timeout")
    if len(disks) > 32:
        raise ReplicationError("Too many disks in one consistency group")
    for disk in disks:
        disk.validate()
    sources = [disk.source for disk in disks]
    bitmaps = [disk.bitmap for disk in disks]
    if len(set(sources)) != len(sources) or len(set(bitmaps)) != len(bitmaps):
        raise ReplicationError("Duplicate source nodes or dirty bitmap names")

    # Check all disks BEFORE modifying QEMU state.
    all_bitmaps = [_bitmaps(qmp, disk.source) for disk in disks]
    for disk, available in zip(disks, all_bitmaps):
        bitmap = available.get(disk.bitmap)
        if mode == "incremental":
            if not bitmap or bitmap.get("inconsistent") or bitmap.get("busy"):
                raise ReplicationError("Incremental bitmap missing/inconsistent; FULL reseed required")
            if bitmap.get("recording") is False:
                raise ReplicationError("Dirty bitmap is disabled; FULL reseed required")

    added = []
    started = False
    jobs = ["origami-bak-" + str(i) for i in range(len(disks))]
    try:
        for i, disk in enumerate(disks):
            name = "origami-nbd-" + str(i)
            target = {
                "node-name": name, "driver": "nbd",
                "server": {"type": "inet", "host": disk.host, "port": str(disk.port)}
            }
            if disk.export:
                target["export"] = disk.export
            qmp.execute("blockdev-add", target)
            added.append(name)

        actions = []
        for i, disk in enumerate(disks):
            if mode == "full":
                # Drop stale volatile bitmap only in a FULL reseed. Never
                # pretend an obsolete bitmap is a valid incremental base.
                if disk.bitmap in all_bitmaps[i]:
                    qmp.execute("block-dirty-bitmap-remove",
                                {"node": disk.source, "name": disk.bitmap})
                actions.append({
                    "type": "block-dirty-bitmap-add",
                    "data": {"node": disk.source, "name": disk.bitmap,
                             "persistent": False}
                })
            params = {
                "device": disk.source,
                "target": added[i],
                "sync": mode,
                "job-id": jobs[i],
                "auto-dismiss": False,
                "auto-finalize": True,
                "on-source-error": "stop",
                "on-target-error": "stop"
            }
            if mode == "incremental":
                params["bitmap"] = disk.bitmap
            actions.append({"type": "blockdev-backup", "data": params})
        # Transaction establishes one cross-disk backup point. It does NOT
        # provide guest application-consistency without fsfreeze/VSS.
        snapshot_epoch = time.time()
        qmp.execute("transaction", {
            "actions": actions,
            "properties": {"completion-mode": "grouped"}
        })
        started = True
        _job_result(qmp, jobs, timeout)
        return {
            "transport": "qmp-nbd",
            "mode": mode, "disks": len(disks),
            "qmp_jobs_completed": True,
            "snapshot_epoch": snapshot_epoch,
            "target_checkpoint_committed": False,
            "standby_vm_started": False
        }
    except Exception:
        if started:
            # Never retry an uncertain incremental over the same checkpoint.
            for job in jobs:
                try:
                    qmp.execute("job-cancel", {"id": job, "force": True})
                except ReplicationError:
                    pass
        raise
    finally:
        for node in reversed(added):
            try:
                qmp.execute("blockdev-del", {"node-name": node})
            except ReplicationError:
                # Target coordinator must quarantine this attempt if cleanup
                # was incomplete. It must not publish an unverified delta.
                pass
