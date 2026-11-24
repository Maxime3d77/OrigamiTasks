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
    # Flattening a multi-terabyte checkpoint is local but can take hours.
    if len(args) > 1 and args[1] == "convert" and timeout == 120:
        timeout = 86400
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

    def _active_chain_paths(self, previous):
        """Resolve every ancestor of the *published* checkpoint.

        A reusable buffer may never be the top image, or any backing image,
        of the last committed checkpoint, including legacy LVM chains.
        """
        if not previous:
            return set()
        if previous.get("status") != "committed":
            raise LvmCheckpointError("Uncommitted base cannot be reused")
        paths = set()
        for item in previous.get("disks", {}).values():
            root = self._safe_path(item["volid"])
            output = self.command("qemu-img", "info", "-f", "qcow2",
                                  "--backing-chain", "--output=json", root)
            try:
                chain = json.loads(output)
            except (ValueError, TypeError) as exc:
                raise LvmCheckpointError("Previous QCOW2 chain cannot be audited") from exc
            if not isinstance(chain, list) or not 1 <= len(chain) <= 128:
                raise LvmCheckpointError("Unverifiable previous backing chain")
            if Path(chain[0].get("filename", "")).resolve(strict=True) != Path(root).resolve(strict=True):
                raise LvmCheckpointError("Published checkpoint does not match its QCOW2 chain")
            for link in chain:
                if not isinstance(link, dict) or link.get("format") not in (None, "qcow2"):
                    raise LvmCheckpointError("Unsupported previous disk backing format")
                name = link.get("filename")
                if not isinstance(name, str) or not name.startswith("/dev/"):
                    # The actual production adapter resolves to block devices.
                    # Fake test targets may use a temporary absolute path.
                    if not isinstance(name, str) or not Path(name).is_absolute():
                        raise LvmCheckpointError("Invalid previous backing path")
                paths.add(str(Path(name).resolve(strict=True)))
        return paths

    def _buffer_catalog(self, ledger, sizes):
        """Only volumes explicitly enrolled as recyclable buffers may be reused."""
        if not isinstance(ledger, dict):
            raise LvmCheckpointError("Invalid volume ownership ledger")
        raw = ledger.get("buffers", {})
        if not isinstance(raw, dict):
            raise LvmCheckpointError("Invalid reusable LVM volume catalog")
        if raw and (ledger.get("protected_vm_id") != self.target.protected_vm_id
                    or ledger.get("target_vmid") != self.target.vmid
                    or ledger.get("storage") != self.target.storage):
            raise LvmCheckpointError("Foreign or unowned LVM buffer catalog")
        result = {}
        seen = set()
        for slot, entries in raw.items():
            if slot not in sizes or not isinstance(entries, list) or len(entries) > 2:
                raise LvmCheckpointError("Unrecognized reusable LVM disk slot")
            result[slot] = []
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("size") != sizes[slot]:
                    raise LvmCheckpointError("LVM disk geometry changed; manually reseed")
                volid = entry.get("volid")
                if not isinstance(volid, str) or volid in seen:
                    raise LvmCheckpointError("Duplicate or invalid reusable LVM volume")
                if not _VOL.fullmatch(volid.split(":", 1)[-1]):
                    raise LvmCheckpointError("Untrusted reusable LVM volume name")
                # Do not rely on a name alone: Proxmox must resolve ownership.
                self._safe_path(volid)
                seen.add(volid)
                result[slot].append({"volid": volid, "size": sizes[slot]})
        return result

    def begin(self, *, mode, sizes, source_vm_uuid, operator_approved=False):
        """Stage into at most TWO durable LVs per source disk.

        First FULL seed allocates A; first incremental allocates B. Later
        cycles alternate A/B. An inactive buffer is reset and filled with a
        FLATTENED copy of the committed image, then QMP writes its delta.
        Publication only happens after the source jobs and QEMU checks pass.
        No published image or its backing chain is ever modified.
        """
        if operator_approved is not True:
            raise LvmCheckpointError("Explicit lab staging approval required")
        if mode not in ("full", "incremental"):
            raise LvmCheckpointError("Unsupported replication mode")
        if not isinstance(source_vm_uuid, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", source_vm_uuid):
            raise LvmCheckpointError("Invalid source generation")
        sizes = self.target.validate_sizes(sizes)
        with self._exclusive():
            if self.pending.exists() or self.pending.is_symlink():
                raise LvmCheckpointError("Staged or uncertain checkpoint requires reconciliation")
            previous = self._read(self.current_file)
            ledger = self._read(self.owned) or {"generations": []}
            if previous and (previous.get("status") != "committed"
                             or previous.get("protected_vm_id") != self.target.protected_vm_id
                             or previous.get("target_vmid") != self.target.vmid
                             or previous.get("storage") != self.target.storage):
                raise LvmCheckpointError("Previous checkpoint ownership is not verified")
            if mode == "incremental" and not self._matches(previous, sizes, source_vm_uuid):
                raise LvmCheckpointError("No verified compatible LVM base: FULL reseed required")

            catalog = self._buffer_catalog(ledger, sizes)
            active = self._active_chain_paths(previous)
            reuse, allocate = {}, {}
            for slot, size in sizes.items():
                candidates = catalog.get(slot, [])
                for entry in candidates:
                    volid = entry["volid"]
                    path = self._safe_path(volid)
                    if path in active:
                        continue
                    # Also protects references from ANY VM and snapshot that
                    # appear in cluster-wide PVE configuration (even 'unused').
                    self.target.assert_volume_unattached(volid)
                    reuse[slot] = volid
                    break
                if slot not in reuse:
                    if len(candidates) >= 2:
                        raise LvmCheckpointError("Both LVM buffers are in use; manual audit required")
                    allocate[slot] = size

            # The normal PveLvmTarget staging allocator is used ONLY when
            # another buffer really is needed, never for every generation.
            # For a two-buffer steady state no 'pvesm alloc' occurs.
            if allocate:
                plan = self.target.plan(allocate, "full")
                allocation = self.target.stage_allocations(plan, approved=True)
                token = allocation["token"]
                fresh = allocation["allocated"]
            else:
                self.target.inspect()
                self.target._target_stopped()
                token = os.urandom(16).hex()
                fresh = {}
            disks = {}
            for slot, size in sorted(sizes.items()):
                volid = reuse.get(slot) or fresh[slot]["volid"]
                if not _VOL.fullmatch(volid.split(":", 1)[-1]):
                    raise LvmCheckpointError("Unexpected allocated QCOW2 volume")
                disks[slot] = {"volid": volid, "size": size, "reused": slot in reuse}
            preparing = {
                "token": token, "status": "preparing", "storage": self.target.storage,
                "target_vmid": self.target.vmid,
                "protected_vm_id": self.target.protected_vm_id,
                "mode": mode, "source_vm_uuid": source_vm_uuid,
                "previous_token": previous.get("token") if previous else None,
                "depth": 1,  # Every new checkpoint is independently flattened.
                "sequence": int(previous.get("sequence", 1)) + 1 if previous else 1,
                "disks": disks, "created_at": int(time.time()),
                "checkpoint_committed": False,
                "cluster_visible": False, "vm_started": False,
                "replication_enabled": False, "failover_enabled": False,
            }
            # Persist intent BEFORE resetting an inactive LV. A crash cannot
            # silently make an interrupted buffer look available for retry.
            _atomic(self.pending, preparing)
            try:
                for slot, details in sorted(disks.items()):
                    path = self._safe_path(details["volid"])
                    if details["reused"]:
                        self.target.assert_volume_unattached(details["volid"])
                    if path in active:
                        raise LvmCheckpointError("Refusing to overwrite published checkpoint")
                    # Reset the QCOW2 allocation table: convert -n may skip
                    # zero extents, so an old dirty image cannot be reused as-is.
                    self.command("qemu-img", "create", "-f", "qcow2",
                                 path, str(details["size"]))
                    if mode == "incremental":
                        base = self._safe_path(previous["disks"][slot]["volid"])
                        if path == base:
                            raise LvmCheckpointError("Source and target buffer are identical")
                        self.command("qemu-img", "convert", "-f", "qcow2",
                                     "-O", "qcow2", "-n", base, path)
                preparing["status"] = "staging"
                _atomic(self.pending, preparing)
                return preparing
            except BaseException:
                preparing["status"] = "uncertain"
                preparing["note"] = "Buffer preparation failed; do not retry incrementally without reconciliation"
                _atomic(self.pending, preparing)
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
        """Atomically publish a flattened, verified buffer; never grow a chain."""
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
            previous = self._read(self.current_file)
            if (previous.get("token") if previous else None) != staged.get("previous_token"):
                raise LvmCheckpointError("Published checkpoint changed during transfer")
            self.target._target_stopped()
            for slot, item in sorted(staged["disks"].items()):
                path = self._safe_path(item["volid"])
                report = self.command("qemu-img", "info", "-f", "qcow2",
                                      "--backing-chain", "--output=json", path)
                try:
                    chain = json.loads(report)
                except (ValueError, TypeError) as exc:
                    raise LvmCheckpointError("QCOW2 block image is malformed") from exc
                # Independent images: any backing reference is a regression.
                if (not isinstance(chain, list) or len(chain) != 1
                        or not isinstance(chain[0], dict)
                        or Path(chain[0].get("filename", "")).resolve(strict=True)
                           != Path(path).resolve(strict=True)):
                    raise LvmCheckpointError("Checkpoint buffer is not standalone QCOW2")
                self.command("qemu-img", "check", "-f", "qcow2", path)
            committed = dict(staged, status="committed", committed_at=int(time.time()))
            old_ledger = self._read(self.owned) or {"generations": []}
            existing = old_ledger.get("generations")
            if not isinstance(existing, list) or len(existing) > 100000:
                raise LvmCheckpointError("Malformed owned-volume ledger")
            sizes = {s: d["size"] for s, d in committed["disks"].items()}
            catalog = self._buffer_catalog(old_ledger, sizes)
            known_volids = {v for generation in existing
                            if isinstance(generation, dict)
                            for v in generation.get("volids", []) if isinstance(v, str)}
            newly_owned = []
            for slot, detail in committed["disks"].items():
                volid = detail["volid"]
                entries = catalog.setdefault(slot, [])
                if volid not in {entry["volid"] for entry in entries}:
                    if detail.get("reused") or len(entries) >= 2:
                        raise LvmCheckpointError("Unrecognized or excessive LVM buffer")
                    entries.append({"volid": volid, "size": detail["size"]})
                if volid not in known_volids:
                    if detail.get("reused"):
                        raise LvmCheckpointError("Reused LV missing from ownership ledger")
                    newly_owned.append(volid)
            # Generations are an ownership history, NOT a new allocation.
            # Append only when a genuinely new LV was allocated (max 2/disk).
            if newly_owned:
                existing = existing + [{
                    "token": token, "volids": sorted(newly_owned),
                    "created_at": committed["created_at"]
                }]
            new_ledger = {
                "generations": existing, "buffers": catalog,
                "protected_vm_id": self.target.protected_vm_id,
                "target_vmid": self.target.vmid, "storage": self.target.storage,
            }
            _atomic(self.owned, new_ledger)
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
