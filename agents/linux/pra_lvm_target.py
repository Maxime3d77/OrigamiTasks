#!/usr/bin/env python3
"""Generic Proxmox LVM target adapter: read-only preflight and staged allocation.

This adapter is intentionally NOT wired to the live replication actions yet.
A PVE-managed LVM LV is a BLOCK DEVICE, not a filesystem path. Never mkdir in
the VG, create a LV by hand, inspect an attached source VM disk by writing, or
use array-vendor commands.

The receiver will only be enabled after implementing an atomic, durable,
cluster-visible checkpoint manifest and verifying block-device QCOW2 backing
chains on real Proxmox hosts. In the meantime staging is an explicit operator-
approved entrypoint, never called by the OrigamiTasks server/agent API.

The local journal is for auditing and crash recovery of *staged allocations*.
It is deliberately NOT considered a publishable/bootable PRA checkpoint.
"""
import json
import os
import re
import socket
import stat
import uuid
from pathlib import Path

STORAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
SLOT = re.compile(r"^(?:scsi|virtio|sata|ide)[0-9]{1,2}$")
VMID = re.compile(r"^[1-9][0-9]{2,8}$")
VOLUME = re.compile(r"^vm-([1-9][0-9]{2,8})-disk-([0-9]{1,18})\\.qcow2$")


class LvmTargetError(ValueError):
    pass


def _flag(value):
    return value in (1, "1", True)


def _json(text):
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise LvmTargetError("Proxmox returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise LvmTargetError("Expected a JSON object")
    return data


def _atomic(path, record):
    tmp = path.with_name("." + path.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


class PveLvmTarget:
    """PVE-managed volume allocation, with no implicit VM or LV mutations."""

    def __init__(self, storage, vmid, runner, journal_root="/var/lib/origamitasks-pra/lvm"):
        if not isinstance(storage, str) or not STORAGE.fullmatch(storage):
            raise LvmTargetError("Invalid storage identifier")
        if type(vmid) is not int or not VMID.fullmatch(str(vmid)):
            raise LvmTargetError("Invalid target VMID")
        self.storage, self.vmid = storage, vmid
        self.run = runner
        self.journal = Path(journal_root) / storage / str(vmid)

    def inspect(self):
        """Never changes PVE or touches an LV."""
        cfg = _json(self.run("pvesh", "get", "/storage/" + self.storage,
                             "--output-format", "json"))
        if cfg.get("storage") != self.storage or cfg.get("type") != "lvm":
            raise LvmTargetError("Configured storage is not the requested LVM backend")
        if _flag(cfg.get("disable")):
            raise LvmTargetError("Storage is disabled")
        content = cfg.get("content") or ""
        if not isinstance(content, str) or "images" not in content.split(","):
            raise LvmTargetError("Storage does not allow VM images")
        if not _flag(cfg.get("snapshot-as-volume-chain")):
            raise LvmTargetError("QCOW2 volume chains are not enabled on this LVM store")
        vgname = cfg.get("vgname")
        if not isinstance(vgname, str) or not STORAGE.fullmatch(vgname):
            raise LvmTargetError("Invalid LVM volume group")
        # This is an allocation capability check, NOT permission to perform a
        # cross-cluster replication or to start a standby VM.
        return {"storage": self.storage, "vmid": self.vmid, "vgname": vgname,
                "shared": _flag(cfg.get("shared")), "type": "lvm",
                "format": "qcow2", "chain_preview": True,
                "phase": "allocation_staging_only", "replication_enabled": False,
                "failover_enabled": False,
                "requires_shared_checkpoint_journal": True}

    @staticmethod
    def validate_sizes(sizes):
        if not isinstance(sizes, dict) or not 1 <= len(sizes) <= 32:
            raise LvmTargetError("Invalid disk count")
        for slot, size in sizes.items():
            if not isinstance(slot, str) or not SLOT.fullmatch(slot):
                raise LvmTargetError("Invalid VM disk slot")
            if type(size) is not int or not 1024 <= size <= 1 << 60:
                raise LvmTargetError("Invalid virtual disk size")
        return dict(sorted(sizes.items()))

    def plan(self, sizes, mode, previous=None):
        """Pure planning, no mutations; provision *thick* LV per disk per point."""
        if mode not in ("full", "incremental"):
            raise LvmTargetError("Invalid replication mode")
        sizes = self.validate_sizes(sizes)
        self.inspect()
        if mode == "incremental":
            if not isinstance(previous, dict) or previous.get("status") != "committed":
                raise LvmTargetError("Incremental requires a verified committed base")
            if set(previous.get("disks", {})) != set(sizes):
                raise LvmTargetError("Previous checkpoint disk set changed")
            if any(previous["disks"][slot].get("size") != size for slot, size in sizes.items()):
                raise LvmTargetError("Previous checkpoint disk size changed")
        # LVM volume-chain snapshots are thick LVs even for tiny deltas. The
        # *full virtual disk* plus qcow2 overhead must fit for each generation.
        estimate = sum(sizes.values())
        reserve = max(512 * 1024 * 1024, estimate // 20)
        return {"storage": self.storage, "vmid": self.vmid, "mode": mode,
                "disks": sizes, "required_bytes_lower_bound": estimate + reserve,
                "allocation_is_thick": True,
                "published": False, "can_failover": False,
                "note": "Estimate only: reserve includes a 5% margin, actual PVE allocation may require more."}

    def _journal_path(self):
        # Journal is intentionally node-local. It is never used as a committed
        # checkpoint because other Proxmox nodes may not be able to read it.
        for p in (self.journal.parent.parent, self.journal.parent, self.journal):
            if p.is_symlink():
                raise LvmTargetError("Symlink in root-owned staging journal")
        self.journal.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.journal.is_symlink():
            raise LvmTargetError("Unsafe staging journal")

    def stage_allocations(self, plan, *, approved=False):
        """Operator-only staging, never invoked from the production agent API.

        Record intent *before* PVE alloc; record every successful LV immediately.
        After an ambiguous command failure, stop and require operator reconcile.
        Never free disks automatically: a remote successful allocation could be
        missed due to a network/process failure. No dangerous blind rollback.
        """
        if approved is not True:
            raise LvmTargetError("Explicit staging approval required")
        if not isinstance(plan, dict) or plan.get("storage") != self.storage or plan.get("vmid") != self.vmid:
            raise LvmTargetError("Plan does not match destination")
        validated = self.plan(plan.get("disks"), plan.get("mode"))
        if plan != validated:
            raise LvmTargetError("Plan changed since preflight")
        self._journal_path()
        journal_file = self.journal / "pending.json"
        if journal_file.exists() or journal_file.is_symlink():
            raise LvmTargetError("Unresolved staging operation; reconcile manually")
        token = uuid.uuid4().hex
        slots = list(validated["disks"])
        # UUID-derived integers are distinct within this attempt, but must
        # still be treated as tentative: the PVE allocator rejects collisions.
        sequence = int(token[:12], 16)
        record = {"token": token, "storage": self.storage, "vmid": self.vmid,
                  "status": "pending", "mode": validated["mode"],
                  "allocated": {}, "planned": slots,
                  "checkpoint_committed": False, "vm_started": False}
        _atomic(journal_file, record)
        try:
            for index, slot in enumerate(slots):
                size = validated["disks"][slot]
                name = "vm-" + str(self.vmid) + "-disk-" + str(sequence + index) + ".qcow2"
                volid = self.storage + ":" + name
                # Must allocate through PVE rather than lvcreate in a shared VG.
                self.run("pvesm", "alloc", self.storage, str(self.vmid),
                         name, str(size), "--format", "qcow2")
                record["allocated"][slot] = {"volid": volid, "size": size}
                _atomic(journal_file, record)
        except BaseException:
            record["status"] = "uncertain"
            _atomic(journal_file, record)
            raise
        record["status"] = "staged_only"
        _atomic(journal_file, record)
        return dict(record)

    def resolve_existing(self, volid, *, verified_block=True):
        """Translate a PVE volume ID; never compose /dev/<vg>/<lv> ourselves."""
        if not isinstance(volid, str) or not volid.startswith(self.storage + ":"):
            raise LvmTargetError("Volume belongs to another store")
        name = volid.split(":", 1)[1]
        matched = VOLUME.fullmatch(name)
        if not matched or int(matched.group(1)) != self.vmid:
            raise LvmTargetError("Volume is not a QCOW2 LV owned by target VM")
        path = self.run("pvesm", "path", volid).strip()
        if not path.startswith("/dev/") or "\n" in path or "\r" in path:
            raise LvmTargetError("Untrusted PVE block-device path")
        canonical = Path(path).resolve(strict=True)
        if not str(canonical).startswith("/dev/"):
            raise LvmTargetError("Block device resolves outside /dev")
        if verified_block and not stat.S_ISBLK(canonical.stat().st_mode):
            raise LvmTargetError("Destination volume is not a block device")
        return str(canonical)
