#!/usr/bin/env python3
"""Privileged Proxmox host side of a live DR replication (manual pilot).

Installed WITH the OrigamiTasks Linux agent. Invoked via already-vetted
SSH credentials owned by the controller, never via an unrestricted script task.
No VM start/stop, no Pure/array modifications, no privilege escalation beyond
this narrowly scoped helper. Target must be a writable Proxmox DIR storage.
"""
import argparse
import base64
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from pra_checkpoint import CheckpointStore, CheckpointError
from pra_qmp_incremental import QMP, Disk, backup, ReplicationError

_VM = re.compile(r"^[1-9][0-9]{0,8}$")
_NODE = re.compile(r"^(?:scsi|virtio|sata|ide)[0-9]{1,2}$")
_STORE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")
_TOKEN = re.compile(r"^[0-9a-f]{32}$")


class HostError(RuntimeError):
    pass


def cmd(*args, timeout=25):
    try:
        r = subprocess.run(args, check=False, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostError("Required host command unavailable") from exc
    if r.returncode:
        raise HostError("Host operation rejected: " + args[0])
    return r.stdout


def vmid(value):
    if type(value) is not int or not _VM.fullmatch(str(value)):
        raise HostError("Invalid VMID")
    return value


def _slots(obj):
    slots = obj.get("slots")
    if (not isinstance(slots, list) or not 1 <= len(slots) <= 32 or
            any(not isinstance(s, str) or not _NODE.fullmatch(s) for s in slots)
            or len(slots) != len(set(slots))):
        raise HostError("Invalid disk slot list")
    return slots


def _qmp_path(vmid_):
    path = Path("/var/run/qemu-server") / (str(vmid_) + ".qmp")
    if not path.exists() or path.is_symlink() or not path.is_socket():
        raise HostError("VM QMP socket unavailable (VM must be running)")
    return str(path)


def _vm_config(vmid_):
    lines = cmd("qm", "config", str(vmid_), "--current").splitlines()
    values = {}
    for line in lines:
        if ": " in line:
            k, v = line.split(": ", 1)
            values[k] = v
    return values


def _source_generation(vmid_):
    # QEMU pid AND /proc start ticks change whenever QEMU is restarted.
    value = cmd("qm", "status", str(vmid_), "--verbose")
    found = re.search(r"(?m)^pid:\s*([0-9]+)$", value)
    if not found:
        raise HostError("Source VM PID unavailable")
    pid = int(found.group(1))
    try:
        ticks = Path("/proc/" + str(pid) + "/stat").read_text().split(") ", 1)[1].split()[19]
    except (OSError, IndexError) as exc:
        raise HostError("Source QEMU process identity unavailable") from exc
    return "vm-" + str(vmid_) + "-" + str(pid) + "-" + str(ticks)


def source_probe(data):
    ident = vmid(data["vmid"])
    configured = _vm_config(ident)
    slots = _slots(data)
    actual = [key for key in configured if _NODE.fullmatch(key)]
    # Do not protect only a subset of data disks accidentally.
    if sorted(actual) != sorted(slots):
        raise HostError("The request does not cover every source disk")
    if any(x in configured for x in ("efidisk0", "tpmstate0", "unused0")):
        raise HostError("EFI/TPM/unused disk requires an explicit specialized recovery profile")
    if any("media=cdrom" in configured[s] for s in slots):
        raise HostError("Optical drives cannot be included as backup disks")
    if "running" not in cmd("qm", "status", str(ident)).split():
        raise HostError("Source VM not running")
    with QMP(_qmp_path(ident)) as qmp:
        block = qmp.execute("query-block")
        entries = {row.get("device"): row for row in block if isinstance(row, dict)}
        sizes = {}
        bitmaps = {}
        nodes = {}
        for slot in slots:
            node = "drive-" + slot
            row = entries.get(node)
            inserted = row.get("inserted") if row else None
            if not isinstance(inserted, dict):
                raise HostError("QMP disk unavailable: " + slot)
            size = inserted.get("image", {}).get("virtual-size")
            if type(size) is not int or size < 1024:
                raise HostError("Invalid disk virtual size")
            sizes[slot] = size
            # Device aliases may differ from graph node names in PVE builds.
            nodes[slot] = inserted.get("node-name") or node
        named = qmp.execute("query-named-block-nodes")
        named_by_name = {r.get("node-name"): r for r in named}
        for slot in slots:
            node = nodes[slot]
            found = named_by_name.get(node, {})
            bitmaps[slot] = {
                b.get("name"): {"busy": b.get("busy"), "inconsistent": b.get("inconsistent"),
                                "recording": b.get("recording")}
                for b in found.get("dirty-bitmaps", [])
            }
    return {"source_vm_uuid": _source_generation(ident),
            "slots": slots, "sizes": sizes, "nodes": nodes, "bitmaps": bitmaps,
            "config": {k: configured[k] for k in ("name", "memory", "cores", "sockets", "bios", "scsihw", "cpu")
                       if k in configured}}


def _dir_path(storage, target_vmid):
    if not isinstance(storage, str) or not _STORE.fullmatch(storage):
        raise HostError("Invalid target storage name")
    ident = vmid(target_vmid)
    # Use only a Proxmox-managed directory storage; no arbitrary path input.
    output = cmd("pvesm", "config", storage)
    if not re.search(r"(?m)^dir: " + re.escape(storage) + r"\s*$", output):
        raise HostError("Target storage must be registered PVE type dir")
    found = re.search(r"(?m)^\s*path\s+(\/\S+)\s*$", output)
    if not found:
        raise HostError("Directory storage path unavailable")
    root = Path(found.group(1))
    if root.is_symlink() or not root.is_dir():
        raise HostError("Target storage unavailable")
    resolved = root.resolve()
    if not os.path.ismount(str(resolved)) and str(resolved) == "/":
        raise HostError("Invalid storage mount")
    if "images" not in re.search(r"(?m)^\s*content\s+(.+)$", output).group(1).split(","):
        raise HostError("Target storage does not allow VM images")
    # Never write to readonly array replicas. The check is advisory; the
    # exclusive writable filesystem requirement is enforced by mkdir/create.
    folder = resolved / "images" / str(ident)
    if folder.is_symlink():
        raise HostError("Unsafe VM checkpoint directory")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def _store(data):
    return CheckpointStore(_dir_path(data["storage"], data["vmid"]),
                           target_vmid=data["vmid"])


def _is_target_stopped(ident):
    try:
        output = cmd("qm", "status", str(ident))
    except HostError:
        # Distinguish absent VMID from an API/PVE malfunction before creation.
        rows = cmd("qm", "list")
        if re.search(r"(?m)^\\s*" + str(ident) + r"\\s+", rows):
            raise HostError("VM target exists but cannot be queried")
        return False
    if "status: stopped" not in output:
        raise HostError("The standby VM must remain OFF")
    configured = _vm_config(ident)
    if configured.get("onboot", "0") not in ("0", "false"):
        raise HostError("Standby VM has onboot enabled")
    return True


def target_probe(data):
    ident = vmid(data["vmid"])
    exists = _is_target_stopped(ident)
    store = _store(data)
    return {"vm_exists": exists, "checkpoint": store.current(),
            "writable": True, "storage_type": "dir"}


def _kill_export(store, slot, strict=False):
    pidfile = store.root / (".origami-" + slot + ".pid")
    if not pidfile.exists():
        return
    if pidfile.is_symlink():
        raise HostError("Invalid NBD pidfile")
    try:
        pid = int(pidfile.read_text().strip())
        if pid <= 1:
            raise ValueError()
        # Only signal a qemu-nbd process with this exact checkpoint path.
        command = Path("/proc/" + str(pid) + "/cmdline").read_bytes().replace(b"\x00", b" ")
        if b"qemu-nbd" not in command or str(store.root).encode() not in command:
            raise HostError("NBD owner mismatch: manual intervention required")
        os.kill(pid, signal.SIGTERM)
        for _ in range(60):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            if strict:
                raise HostError("NBD export did not terminate")
    except (ValueError, OSError) as exc:
        if strict:
            raise HostError("NBD process cannot be stopped safely") from exc
    else:
        pidfile.unlink(missing_ok=True)


def target_begin(data):
    ident = vmid(data["vmid"])
    _is_target_stopped(ident)
    store = _store(data)
    mode = data["mode"]
    sizes = data["sizes"]
    source_gen = data["source_vm_uuid"]
    if not isinstance(sizes, dict) or any(not _NODE.fullmatch(k) for k in sizes):
        raise HostError("Invalid disk sizes")
    checkpoint = store.begin(mode, sizes, source_gen)
    ports = {}
    try:
        for slot in sorted(sizes):
            # Kernel assigns the port through an explicit temporary reservation.
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            disk = store.root / checkpoint["disks"][slot]["file"]
            pidpath = store.root / (".origami-" + slot + ".pid")
            if pidpath.exists():
                raise HostError("Existing NBD export requires operator reconciliation")
            cmd("qemu-nbd", "--format=qcow2", "--fork", "--persistent",
                "--bind=127.0.0.1", "--port=" + str(port),
                "--pid-file=" + str(pidpath), str(disk), timeout=20)
            ports[slot] = port
        return {"token": checkpoint["token"], "ports": ports, "mode": mode}
    except BaseException:
        for slot in ports:
            _kill_export(store, slot)
        store.abort(checkpoint["token"])
        raise


def target_finish(data):
    store = _store(data)
    token = data["token"]
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise HostError("Invalid checkpoint token")
    pending = store._read(store.pending)
    if not pending or pending["token"] != token:
        raise HostError("Unknown checkpoint")
    for slot in pending["disks"]:
        _kill_export(store, slot, strict=True)
    # Successful block copy must be proven by controller before this call.
    if data.get("verified_by_source") is not True:
        raise HostError("Source backup proof missing")
    return {"checkpoint": store.commit(token), "vm_started": False}


def target_abort(data):
    store = _store(data)
    token = data["token"]
    pending = store._read(store.pending)
    if not pending or pending.get("token") != token:
        raise HostError("No matching pending checkpoint")
    for slot in pending["disks"]:
        _kill_export(store, slot, strict=True)
    return store.abort(token)



def target_publish(data):
    """Attach a committed, QCOW2 COW checkpoint to an OFF Proxmox standby VM.

    The target is either absent (created safely) or clearly owned by this
    protection policy.  No network interfaces are copied or enabled.
    """
    ident = vmid(data["vmid"])
    store_name = data["storage"]
    token = data["token"]
    protected = data["protected_vm_id"]
    config = data["config"]
    name = data["name"]
    if (type(protected) is not int or protected <= 0 or
            not isinstance(name, str) or not re.fullmatch(r"REP_[a-zA-Z0-9_-]{1,50}", name) or
            not isinstance(token, str) or not _TOKEN.fullmatch(token) or
            not isinstance(config, dict)):
        raise HostError("Invalid protected VM identity")
    store = _store(data)
    checkpoint = store.current()
    if not checkpoint or checkpoint.get("token") != token or checkpoint.get("status") != "committed":
        raise HostError("Refusing to attach an uncommitted checkpoint")
    exists = _is_target_stopped(ident)
    marker = "ORIGAMITASKS_PRA_" + str(protected)
    if exists:
        previous = _vm_config(ident)
        if marker not in previous.get("description", ""):
            raise HostError("Existing target VM is not owned by this PRA protection")
        if previous.get("onboot", "0") != "0":
            raise HostError("Automatic target boot must be disabled")
    else:
        def integer(field, default, maximum):
            val = str(config.get(field, default))
            if not val.isdigit() or not 1 <= int(val) <= maximum:
                raise HostError("Unsafe source VM option " + field)
            return val
        memory = integer("memory", "1024", 2097152)
        cores = integer("cores", "1", 512)
        sockets = integer("sockets", "1", 16)
        args = ["qm", "create", str(ident), "--name", name, "--onboot", "0",
                "--memory", memory, "--cores", cores, "--sockets", sockets,
                "--description", marker]
        scsihw = config.get("scsihw")
        if scsihw is not None:
            if scsihw not in ("virtio-scsi-pci", "virtio-scsi-single", "lsi", "megasas", "pvscsi"):
                raise HostError("Unsupported SCSI controller profile")
            args.extend(["--scsihw", scsihw])
        # Legacy BIOS only in this pilot; EFI/TPM require dedicated handling.
        if config.get("bios", "seabios") != "seabios":
            raise HostError("EFI/OVMF source requires a dedicated recovery profile")
        cmd(*args, timeout=60)
    args = ["qm", "set", str(ident), "--onboot", "0"]
    slots = sorted(checkpoint["disks"].keys())
    for slot in slots:
        if not _NODE.fullmatch(slot):
            raise HostError("Invalid disk slot in checkpoint")
        filename = checkpoint["disks"][slot]["file"]
        if not filename.startswith("vm-" + str(ident) + "-disk-") or not filename.endswith(".qcow2"):
            raise HostError("Checkpoint not in PVE image namespace")
        path = store.root / filename
        if path.is_symlink() or not path.is_file():
            raise HostError("Checkpoint image missing")
        args.extend(["--" + slot, store_name + ":" + str(ident) + "/" + filename])
    # Single config transaction; target remains powered off during reattach.
    cmd(*args, timeout=120)
    # Ensure the target can attempt boot without changing source/network.
    cmd("qm", "set", str(ident), "--boot", "order=" + slots[0])
    if "status: stopped" not in cmd("qm", "status", str(ident)):
        raise HostError("Target VM unexpectedly running; operator intervention required")
    return {"standby_configured": True, "vm_started": False,
            "vmid": ident, "checkpoint": token, "slots": slots}


def source_copy(data):
    ident = vmid(data["vmid"])
    slots = _slots(data)
    ports = data["ports"]
    if not isinstance(ports, dict) or set(ports) != set(slots):
        raise HostError("Missing destination NBD ports")
    inspected = source_probe({"vmid": ident, "slots": slots})
    if inspected["source_vm_uuid"] != data["source_vm_uuid"]:
        raise HostError("Source QEMU process changed since checkpoint preparation")
    with QMP(_qmp_path(ident), timeout=30) as qmp:
        disks = []
        for slot in slots:
            port = ports[slot]
            disks.append(Disk(source=inspected["nodes"][slot],
                              bitmap="origami_" + str(ident) + "_" + slot,
                              host="127.0.0.1", port=port))
        return backup(qmp, disks, mode=data["mode"], timeout=data.get("timeout", 3600))


ACTIONS = {"source_probe": source_probe, "source_copy": source_copy,
           "target_probe": target_probe, "target_begin": target_begin,
           "target_finish": target_finish, "target_abort": target_abort,
           "target_publish": target_publish}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=sorted(ACTIONS))
    p.add_argument("payload")
    args = p.parse_args()
    if os.geteuid() != 0:
        raise HostError("PRA privileged helper requires root SSH credentials or configured sudo")
    try:
        raw = base64.b64decode(args.payload, validate=True)
        if len(raw) > 30000:
            raise HostError("Oversized request")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise HostError("Invalid payload")
        result = ACTIONS[args.action](request)
        print(json.dumps({"ok": True, "result": result}, separators=(",", ":")))
    except (ValueError, KeyError, OSError, ReplicationError,
            CheckpointError, HostError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:400]}))
        sys.exit(1)


if __name__ == "__main__":
    main()
