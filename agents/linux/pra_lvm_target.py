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
import fcntl
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
VOLUME = re.compile(r"^vm-([1-9][0-9]{2,8})-disk-([0-9]{1,18})\.qcow2$")


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

    def __init__(self, storage, vmid, runner, journal_root="/var/lib/origamitasks-pra/lvm",
                 protected_vm_id=None):
        if not isinstance(storage, str) or not STORAGE.fullmatch(storage):
            raise LvmTargetError("Invalid storage identifier")
        if type(vmid) is not int or not VMID.fullmatch(str(vmid)):
            raise LvmTargetError("Invalid target VMID")
        if (protected_vm_id is not None and
                (type(protected_vm_id) is not int or protected_vm_id <= 0)):
            raise LvmTargetError("Invalid PRA protection identity")
        self.storage, self.vmid = storage, vmid
        self.protected_vm_id = protected_vm_id
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

    def _node_capacity(self):
        """Query actual storage status on this node; never trust VG name alone."""
        node = socket.gethostname().split(".", 1)[0]
        if not STORAGE.fullmatch(node):
            raise LvmTargetError("Untrusted Proxmox node identity")
        try:
            rows = json.loads(self.run("pvesh", "get", "/nodes/" + node + "/storage",
                                      "--output-format", "json"))
        except (ValueError, TypeError) as exc:
            raise LvmTargetError("Invalid PVE node storage status") from exc
        if not isinstance(rows, list):
            raise LvmTargetError("PVE node storage inventory unavailable")
        found = [r for r in rows if isinstance(r, dict)
                 and r.get("storage") == self.storage]
        if len(found) != 1 or not _flag(found[0].get("active")):
            raise LvmTargetError("Target LVM storage is not active on this node")
        available = found[0].get("avail")
        if type(available) is not int or available < 0:
            raise LvmTargetError("Target available space cannot be verified")
        return node, available

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
        node, available = self._node_capacity()
        if available < estimate + reserve:
            raise LvmTargetError("Insufficient free space for thick LVM checkpoint allocation")
        return {"storage": self.storage, "vmid": self.vmid, "mode": mode,
                "disks": sizes, "target_node": node, "available_bytes": available,
                "required_bytes_lower_bound": estimate + reserve,
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

    def _target_stopped(self):
        """Check cluster-wide VMID ownership, not merely local qm status."""
        node, _ = self._node_capacity()
        try:
            rows = json.loads(self.run("pvesh", "get", "/cluster/resources",
                                      "--type", "vm", "--output-format", "json"))
        except (ValueError, TypeError) as exc:
            raise LvmTargetError("Invalid cluster-wide VMID inventory") from exc
        if not isinstance(rows, list):
            raise LvmTargetError("Cluster-wide VMID inventory unavailable")
        owners = [r for r in rows if isinstance(r, dict)
                  and str(r.get("vmid")) == str(self.vmid)
                  and r.get("type") in ("qemu", "lxc")]
        if not owners:
            # First seed may prepare volumes before the standby VM is created.
            return False
        if len(owners) != 1 or owners[0].get("type") != "qemu" or owners[0].get("node") != node:
            raise LvmTargetError("Destination VMID is owned by another cluster resource")
        if self.protected_vm_id is None:
            raise LvmTargetError("Cannot stage into an existing VM without protection identity")
        state = self.run("qm", "status", str(self.vmid)).strip()
        if state != "status: stopped":
            raise LvmTargetError("Destination VM must be explicitly stopped before staging")
        config = self.run("qm", "config", str(self.vmid), "--current")
        attrs = {}
        for line in config.splitlines():
            if ": " in line:
                k, val = line.split(": ", 1)
                attrs[k] = val
        if attrs.get("onboot", "0") not in ("0", "false"):
            raise LvmTargetError("Destination VM must have onboot disabled")
        if "ORIGAMITASKS_PRA_" + str(self.protected_vm_id) not in attrs.get("description", ""):
            raise LvmTargetError("Destination VM is not owned by this protection")
        if any(re.fullmatch(r"net[0-9]{1,2}", key) for key in attrs):
            raise LvmTargetError("Destination standby has a network interface")
        return True


    def stage_allocations(self, plan, *, approved=False, previous=None):
        """Operator-only staging, deliberately absent from production agent API.

        Record intent *before* PVE alloc and persist each successful LV. After
        an ambiguous allocation failure stop and require manual reconciliation.
        Concurrent staging on the same VM is serialized by an advisory lock.
        Never free an LV or start/stop a VM automatically.
        """
        if approved is not True:
            raise LvmTargetError("Explicit staging approval required")
        if not isinstance(plan, dict) or plan.get("storage") != self.storage or plan.get("vmid") != self.vmid:
            raise LvmTargetError("Plan does not match destination")
        validated = self.plan(plan.get("disks"), plan.get("mode"), previous=previous)
        if plan != validated:
            raise LvmTargetError("Plan changed since preflight")
        self._target_stopped()
        self._journal_path()
        lock_path = self.journal / ".lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            journal_file = self.journal / "pending.json"
            if journal_file.exists() or journal_file.is_symlink():
                raise LvmTargetError("Unresolved staging operation; reconcile manually")
            token = uuid.uuid4().hex
            slots = list(validated["disks"])
            # The allocator must still reject any name collision.
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
                    self.run("pvesm", "alloc", self.storage, str(self.vmid),
                             name, str(size), "--format", "qcow2")
                    record["allocated"][slot] = {"volid": volid, "size": size}
                    _atomic(journal_file, record)
            except BaseException as exc:
                # Persist only a bounded, allowlisted code. The PVE CLI may
                # have created a volume without returning success: NEVER
                # delete it or clear the journal merely because allocated={}
                record["status"] = "uncertain"
                category = str(exc)
                if re.fullmatch(r"PVE_LVM_ALLOC_[A-Z_]{1,50}", category):
                    record["failure_category"] = category
                else:
                    record["failure_category"] = "PVE_LVM_ALLOC_UNCONFIRMED"
                _atomic(journal_file, record)
                raise
            record["status"] = "staged_only"
            _atomic(journal_file, record)
            return dict(record)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

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
