#!/usr/bin/env python3
"""Fail-closed, copy-on-write checkpoint staging for OrigamiTasks PRA.

The destination must be a *writable filesystem*, never a readonly array
replica. New delta images are built as qcow2 overlays of the previous
checkpoint. The active manifest is changed only after all disks validate.
This component NEVER starts or configures a standby VM.
"""
import fcntl
import json
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_ID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class CheckpointError(RuntimeError):
    pass


def _slot(slot):
    if not isinstance(slot, str) or not _ID.fullmatch(slot):
        raise CheckpointError("Invalid disk slot")
    return slot


def _atomic_json(path, obj):
    temporary = path.with_name("." + path.name + ".tmp-" + uuid.uuid4().hex)
    fd = os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(obj, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckpointError("qemu-img operation unavailable") from exc
    if result.returncode != 0:
        raise CheckpointError("qemu-img rejected the checkpoint image")
    return result.stdout


class CheckpointStore:
    def __init__(self, root, target_vmid=None):
        if target_vmid is not None and (type(target_vmid) is not int or not 100 <= target_vmid <= 999999999):
            raise CheckpointError("Invalid target VMID")
        self.target_vmid = target_vmid
        self.root = Path(root)
        # Never create or chase an unexpected filesystem path as root.
        if not self.root.is_absolute() or self.root.is_symlink() or not self.root.is_dir():
            raise CheckpointError("Target checkpoint directory must exist and be non-symlink")
        self.root = self.root.resolve()
        if not os.access(self.root, os.W_OK):
            raise CheckpointError("Target checkpoint directory is not writable")
        self.manifest = self.root / "current.json"
        self.pending = self.root / "pending.json"
        self.owned = self.root / "owned.json"
        self.lock = self.root / ".lock"

    @contextmanager
    def _exclusive(self):
        handle = os.open(str(self.lock), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            os.close(handle)

    def _read(self, path):
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_size > 65536:
            raise CheckpointError("Invalid checkpoint metadata")
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CheckpointError("Unreadable checkpoint metadata") from exc
        if not isinstance(obj, dict):
            raise CheckpointError("Malformed checkpoint metadata")
        return obj

    def current(self):
        with self._exclusive():
            return self._read(self.manifest)

    def begin(self, mode, sizes, source_vm_uuid):
        """Allocate isolated targets. No published image can be overwritten."""
        if mode not in ("full", "incremental") or not isinstance(sizes, dict) or not sizes:
            raise CheckpointError("Invalid checkpoint request")
        if not isinstance(source_vm_uuid, str) or not _ID.fullmatch(source_vm_uuid):
            raise CheckpointError("Invalid source generation identifier")
        if len(sizes) > 32 or any(
                type(v) is not int or not 1024 <= v <= (1 << 60)
                for v in sizes.values()):
            raise CheckpointError("Invalid disk sizes")
        if any(_slot(s) != s for s in sizes):
            raise CheckpointError("Invalid disk identifiers")
        with self._exclusive():
            if self.pending.exists():
                raise CheckpointError("Unresolved pending checkpoint: reconcile before retry")
            previous = self._read(self.manifest)
            if mode == "incremental":
                if (not previous or previous.get("source_vm_uuid") != source_vm_uuid
                        or set(previous.get("disks", {})) != set(sizes)):
                    raise CheckpointError("No compatible committed base; FULL reseed required")
                for slot, size in sizes.items():
                    if previous["disks"][slot]["size"] != size:
                        raise CheckpointError("Disk size changed; FULL reseed required")
            token = uuid.uuid4().hex
            disks = {}
            created = []
            try:
                for index, (slot, size) in enumerate(sorted(sizes.items())):
                    filename = ("vm-" + str(self.target_vmid) + "-disk-" +
                                str(int(token[:12], 16) + index) + ".qcow2") if self.target_vmid is not None else (
                                "checkpoint-" + token + "-" + slot + ".qcow2")
                    path = self.root / filename
                    if mode == "full":
                        _command("qemu-img", "create", "-f", "qcow2",
                                 str(path), str(size))
                    else:
                        base_name = previous["disks"][slot]["file"]
                        if (not isinstance(base_name, str)
                                or Path(base_name).name != base_name
                                or not (base_name.startswith("checkpoint-") or
                                        (self.target_vmid is not None and base_name.startswith("vm-" + str(self.target_vmid) + "-disk-")))
                                or not base_name.endswith(".qcow2")):
                            raise CheckpointError("Invalid base image in manifest")
                        base = self.root / base_name
                        if base.is_symlink() or not base.is_file():
                            raise CheckpointError("Missing committed backing image")
                        _command("qemu-img", "create", "-f", "qcow2",
                                 "-F", "qcow2", "-b", str(base), str(path))
                    created.append(path)
                    disks[slot] = {"file": filename, "size": size}
                document = {
                    "token": token, "status": "staging", "mode": mode,
                    "target_vmid": self.target_vmid,
                    "source_vm_uuid": source_vm_uuid,
                    "previous_token": previous.get("token") if previous else None,
                    "depth": (int(previous.get("depth",1))+1 if mode=="incremental" else 1),
                    "disks": disks, "created_at": int(time.time())
                }
                _atomic_json(self.pending, document)
                return document
            except BaseException:
                for path in created:
                    path.unlink(missing_ok=True)
                raise

    def commit(self, token):
        """Publish a point only after all NBD exports are CLOSED and qemu-img check passes."""
        with self._exclusive():
            pending = self._read(self.pending)
            if not pending or pending.get("token") != token:
                raise CheckpointError("Unknown/expired pending checkpoint")
            for details in pending["disks"].values():
                image = self.root / details["file"]
                if image.is_symlink() or not image.is_file():
                    raise CheckpointError("Missing checkpoint disk image")
                _command("qemu-img", "check", "-f", "qcow2", str(image))
            committed = dict(pending, status="committed", committed_at=int(time.time()))
            # Atomic manifest update is the only checkpoint publication.
            _atomic_json(self.manifest, committed)
            tracked = self._read(self.owned) or {"files": []}
            existing = tracked.get("files", [])
            if not isinstance(existing, list):
                raise CheckpointError("Corrupt ownership ledger")
            current_files = [item["file"] for item in pending["disks"].values()]
            _atomic_json(self.owned, {"files": sorted(set(existing + current_files))})
            self.pending.unlink()
            return committed


    def garbage_collect(self, protected_files=()):
        """Disabled until full PVE snapshot, backup and storage reference audit.

        A guest's current qm configuration is NOT an exhaustive inventory of
        all references to old images. Never delete a checkpoint based only on
        the current active chain or a single VM's running configuration.
        """
        raise CheckpointError(
            "Automatic checkpoint garbage collection disabled: "
            "external PVE snapshot references cannot yet be proven absent")

    def abort(self, token):
        """Keep the last published point intact after a failed or uncertain run."""
        with self._exclusive():
            pending = self._read(self.pending)
            if not pending or pending.get("token") != token:
                raise CheckpointError("Unknown pending checkpoint")
            for details in pending["disks"].values():
                filename = details["file"]
                if (not isinstance(filename, str) or
                        (not filename.startswith("checkpoint-" + token + "-") and
                         not (self.target_vmid is not None and
                              filename.startswith("vm-" + str(self.target_vmid) + "-disk-")))
                        or Path(filename).name != filename):
                    raise CheckpointError("Unexpected staging disk; refusing to delete")
                path = self.root / filename
                if path.is_symlink():
                    raise CheckpointError("Staging image is a symlink")
                path.unlink(missing_ok=True)
            self.pending.unlink()
            return {"aborted": True, "last_checkpoint_preserved": True}
