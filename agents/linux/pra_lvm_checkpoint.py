#!/usr/bin/env python3
"""PVE-managed QCOW2-on-LVM *laboratory* checkpoint receiver.

This is deliberately NOT exposed in the PRA UI or agent remote actions.
It prepares a target implementation for real Proxmox lab qualification.
Only an exclusively owned, newly allocated Proxmox volume can be written.

WARNING: Local journal is NOT cluster-wide. If the target node fails, this
prototype cannot independently recover authority or current checkpoints.
It does not authorize standby promotion, automatic garbage collection,
guest boot, failback or production replication.
"""
import fcntl
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from pra_lvm_target import LvmTargetError, PveLvmTarget, _atomic

_VOL = re.compile(r"^vm-[1-9][0-9]+-disk-[0-9]+\.qcow2$")
_TOKEN = re.compile(r"^[0-9a-f]{32}$")


class LvmCheckpointError(RuntimeError):
    pass


def _qemu(*args, timeout=120):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LvmCheckpointError("QEMU image operation unavailable") from exc
    if result.returncode != 0:
        raise LvmCheckpointError("QEMU refused block-device checkpoint")
    return result.stdout


class LvmCheckpointStore:
    """Single-node test receiver; no automatic cleanup or failover.

    The metadata location is explicitly root-owned and local. It is NEVER
    mistaken for shared cluster storage. A future durable, quorum-guarded
    Proxmox-wide checkpoint catalog must replace it before enabling DR.
    """

    def __init__(self, target: PveLvmTarget, command=_qemu):
        if not isinstance(target, PveLvmTarget):
            raise LvmCheckpointError("A PVE LVM target adapter is required")
        if target.protected_vm_id is None:
            raise LvmCheckpointError("Protection identity required")
        self.target = target
        self.command = command
        self.root = target.journal
        self.pending = self.root / "pending.json"
        self.current_file = self.root / "current.json"
        self.owned = self.root / "owned.json"
        # Separate from PveLvmTarget allocation lock: nested flock on two
        # file descriptors of one lock file could deadlock the same worker.
        self.lock_file = self.root / ".checkpoint.lock"

    def _read(self, path):
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_size > 65536:
            raise LvmCheckpointError("Unsafe checkpoint metadata")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise LvmCheckpointError("Invalid checkpoint metadata") from exc
        if not isinstance(data, dict):
            raise LvmCheckpointError("Malformed checkpoint metadata")
        return data

    @contextmanager
    def _exclusive(self):
        self.target._journal_path()
        fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def current(self):
        with self._exclusive():
            return self._read(self.current_file)

    def _safe_path(self, volid):
        if not isinstance(volid, str) or ":" not in volid:
            raise LvmCheckpointError("Invalid volume ID")
        try:
            return self.target.resolve_existing(volid)
        except (OSError, ValueError, LvmTargetError) as exc:
            raise LvmCheckpointError("Target volume cannot be resolved safely") from exc

    @staticmethod
    def _matches(record, sizes, generation):
        if (not isinstance(record, dict) or record.get("status") != "committed"
                or record.get("source_vm_uuid") != generation):
            return False
        disks = record.get("disks")
        return (isinstance(disks, dict) and set(disks) == set(sizes)
                and all(isinstance(disks.get(s), dict)
                        and disks[s].get("size") == v for s, v in sizes.items()))

    def begin(self, *, mode, sizes, source_vm_uuid, operator_approved=False):
        """Allocate PVE volumes; do not expose them to a VM or NBD yet."""
        if operator_approved is not True:
            raise LvmCheckpointError("Explicit lab staging approval required")
        if not isinstance(source_vm_uuid, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", source_vm_uuid):
            raise LvmCheckpointError("Invalid source generation")
        sizes = self.target.validate_sizes(sizes)
        with self._exclusive():
            if self.pending.exists() or self.pending.is_symlink():
                raise LvmCheckpointError("Staged or uncertain checkpoint requires reconciliation")
            previous = self._read(self.current_file)
            # Every incremental LVM checkpoint allocates a full-size thick LV
            # per disk. Automatic garbage collection is not proven safe for
            # backing chains and Proxmox snapshots; stop instead of exhausting
            # a production VG. Operators must use a separately validated
            # retention/consolidation mechanism before continuous PRA.
            ledger = self._read(self.owned) or {"generations": []}
            generations = ledger.get("generations")
            if not isinstance(generations, list) or len(generations) >= 8:
                raise LvmCheckpointError(
                    "Limite de 8 générations LVM épaisses atteinte ; "
                    "réconciliation et politique de rétention nécessaires")
            if mode == "incremental" and (
                    not self._matches(previous, sizes, source_vm_uuid)
                    or previous.get("protected_vm_id") != self.target.protected_vm_id
                    or previous.get("target_vmid") != self.target.vmid
                    or previous.get("storage") != self.target.storage):
                raise LvmCheckpointError("No verified compatible LVM base: FULL reseed required")
            plan_previous = previous if mode == "incremental" else None
            plan = self.target.plan(sizes, mode, previous=plan_previous)
            # PveLvmTarget.stage_allocations() itself journals allocation
            # intent prior to invoking PVE. It requires an admin-approved,
            # stopped and owned standby VM or an unused VMID.
            allocation = self.target.stage_allocations(
                plan, approved=True, previous=plan_previous)
            token = allocation["token"]
            disks = {}
            try:
                for slot, detail in sorted(allocation["allocated"].items()):
                    volid = detail["volid"]
                    if not _VOL.fullmatch(volid.split(":", 1)[-1]):
                        raise LvmCheckpointError("Unexpected newly allocated QCOW2 volume")
                    path = self._safe_path(volid)
                    # Only the fresh PVE-allocated staging LV is initialized.
                    # Never write to a previous checkpoint or a source LV.
                    if mode == "full":
                        self.command("qemu-img", "create", "-f", "qcow2",
                                     path, str(sizes[slot]))
                    else:
                        backing = self._safe_path(previous["disks"][slot]["volid"])
                        self.command("qemu-img", "create", "-f", "qcow2", "-F",
                                     "qcow2", "-b", backing, path)
                    disks[slot] = {"volid": volid, "size": sizes[slot]}
                prepared = {
                    "token": token, "status": "staging", "storage": self.target.storage,
                    "target_vmid": self.target.vmid,
                    "protected_vm_id": self.target.protected_vm_id,
                    "mode": mode, "source_vm_uuid": source_vm_uuid,
                    "previous_token": previous.get("token") if previous else None,
                    "depth": int(previous.get("depth", 1)) + 1 if mode == "incremental" else 1,
                    "disks": disks, "created_at": int(time.time()),
                    "cluster_visible": False, "vm_started": False,
                    "replication_enabled": False, "failover_enabled": False
                }
                _atomic(self.pending, prepared)
                return prepared
            except BaseException:
                # An allocation may have succeeded even if QEMU refused to
                # initialize its QCOW2 header. Preserve every LV and record
                # the uncertain state for explicit operator reconciliation.
                record = self._read(self.pending)
                if record and record.get("token") == token:
                    record["status"] = "uncertain"
                    record["checkpoint_committed"] = False
                    record["note"] = "QCOW2 initialization incomplete; no LV freed"
                    _atomic(self.pending, record)
                raise

    def image_path(self, slot, token):
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise LvmCheckpointError("Invalid checkpoint token")
        with self._exclusive():
            data = self._read(self.pending)
            if not data or data.get("token") != token or data.get("status") != "staging":
                raise LvmCheckpointError("No matching staged checkpoint")
            disk = data.get("disks", {}).get(slot)
            if not isinstance(disk, dict):
                raise LvmCheckpointError("Unknown staged disk slot")
            return self._safe_path(disk["volid"])

    def commit(self, token, *, writers_closed=False, source_jobs_verified=False):
        """Structural QCOW2 checks only; not a boot or application test."""
        if writers_closed is not True or source_jobs_verified is not True:
            raise LvmCheckpointError("Cannot publish while NBD or source proof is uncertain")
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise LvmCheckpointError("Invalid checkpoint token")
        with self._exclusive():
            staged = self._read(self.pending)
            if (not staged or staged.get("token") != token or staged.get("status") != "staging"
                    or staged.get("protected_vm_id") != self.target.protected_vm_id
                    or staged.get("target_vmid") != self.target.vmid
                    or staged.get("storage") != self.target.storage):
                raise LvmCheckpointError("Unknown or foreign staged checkpoint")
            for slot, item in sorted(staged["disks"].items()):
                path = self._safe_path(item["volid"])
                report = self.command("qemu-img", "info", "-f", "qcow2",
                                      "--backing-chain", "--output=json", path)
                try:
                    chain = json.loads(report)
                except (ValueError, TypeError) as exc:
                    raise LvmCheckpointError("QCOW2 block chain is malformed") from exc
                if not isinstance(chain, list) or not chain:
                    raise LvmCheckpointError("Missing target QCOW2 chain")
                if Path(chain[0].get("filename", "")).resolve(strict=True) != Path(path).resolve(strict=True):
                    raise LvmCheckpointError("Checkpoint top image mismatch")
                if staged["mode"] == "incremental":
                    base = self._read(self.current_file)
                    if not self._matches(base, {k: d["size"] for k,d in staged["disks"].items()},
                                         staged["source_vm_uuid"]):
                        raise LvmCheckpointError("Backing checkpoint changed while copying")
                    expected_base = self._safe_path(base["disks"][slot]["volid"])
                    if len(chain) < 2 or Path(chain[1].get("filename", "")).resolve(strict=True) != Path(expected_base).resolve(strict=True):
                        raise LvmCheckpointError("Wrong LVM backing volume")
                elif len(chain) != 1:
                    raise LvmCheckpointError("FULL seed unexpectedly depends on prior volumes")
                self.command("qemu-img", "check", "-f", "qcow2", path)
            committed = dict(staged, status="committed", committed_at=int(time.time()))
            previous_ledger = self._read(self.owned) or {"generations": []}
            existing = previous_ledger.get("generations")
            if not isinstance(existing, list) or len(existing) > 100000:
                raise LvmCheckpointError("Malformed owned-volume ledger")
            # Record all owned volumes before making the new checkpoint
            # current. On crash an orphan can be reconciled, not deleted.
            ledger = {"generations": existing + [{
                "token": token,
                "volids": [item["volid"] for item in committed["disks"].values()],
                "created_at": committed["created_at"]
            }]}
            _atomic(self.owned, ledger)
            _atomic(self.current_file, committed)
            self.pending.unlink()
            return committed

    def abort(self, token):
        """Quarantine instead of freeing LVs; operator must reconcile safely."""
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise LvmCheckpointError("Invalid checkpoint token")
        with self._exclusive():
            staged = self._read(self.pending)
            if not staged or staged.get("token") != token:
                raise LvmCheckpointError("Unknown pending generation")
            quarantine = dict(staged, status="uncertain",
                              note="No LV freed automatically; reconcile PVE volume ownership")
            _atomic(self.pending, quarantine)
            return {"quarantined": True, "freed": 0, "last_checkpoint_preserved": True}
