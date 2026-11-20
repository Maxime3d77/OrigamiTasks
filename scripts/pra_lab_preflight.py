#!/usr/bin/env python3
"""Read-only, generic Proxmox PRA lab preflight (no installed agent required).

Run only on a disposable/authorized test hypervisor. No qm start/stop/set,
no pvesm alloc/free, no block-device access, no source disk reads or writes.
The output intentionally excludes credentials, IPs and raw guest config.
"""
import argparse
import json
import re
import shutil
import socket
import subprocess
from pathlib import Path

STORE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
VMID = re.compile(r"^[1-9][0-9]{1,8}$")
SLOT = re.compile(r"^(?:scsi|virtio|sata|ide)[0-9]{1,2}$")


class ProbeError(RuntimeError):
    pass


def run(*args):
    try:
        r = subprocess.run(args, check=False, capture_output=True,
                           text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError("Proxmox command unavailable: " + args[0]) from exc
    if r.returncode != 0:
        raise ProbeError("Proxmox read-only command failed: " + args[0])
    return r.stdout


def _parse_json(payload, kind):
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ProbeError("Invalid JSON from " + kind) from exc
    return value


def node_storage(storage, node, executor=run):
    if not STORE.fullmatch(storage) or not STORE.fullmatch(node):
        raise ProbeError("Invalid storage or node identifier")
    config = _parse_json(executor("pvesh", "get", "/storage/" + storage,
                                  "--output-format", "json"), "storage config")
    rows = _parse_json(executor("pvesh", "get", "/nodes/" + node + "/storage",
                                "--output-format", "json"), "node storage status")
    if not isinstance(config, dict) or not isinstance(rows, list):
        raise ProbeError("Invalid storage configuration or status schema")
    present = [x for x in rows if isinstance(x, dict) and x.get("storage") == storage]
    if len(present) != 1:
        raise ProbeError("Storage not available on selected node")
    status = present[0]
    return {
        "storage": storage, "type": config.get("type"), "shared": config.get("shared") in (1, True, "1"),
        "images": "images" in str(config.get("content") or "").split(","),
        "active": status.get("active") in (1, True),
        "snapshot_as_volume_chain": config.get("snapshot-as-volume-chain") in (1, True, "1"),
        "available_bytes": status.get("avail") if type(status.get("avail")) is int else None,
        "receiver_available_now": config.get("type") == "dir" and
                                  status.get("active") in (1, True),
        "lvm_receiver_status": "unimplemented" if config.get("type") == "lvm" else "not_applicable",
    }


def guest(vmid, executor=run):
    if not VMID.fullmatch(str(vmid)):
        raise ProbeError("Invalid VMID")
    lines = executor("qm", "config", str(vmid), "--current").splitlines()
    data = {}
    for line in lines:
        if ": " in line:
            k, v = line.split(": ", 1)
            data[k] = v
    slots = sorted(x for x in data if SLOT.fullmatch(x)
                   and "media=cdrom" not in data[x])
    return {
        "vmid": int(vmid),
        "bios": data.get("bios", "seabios"),
        "has_efidisk": "efidisk0" in data,
        "has_tpm": "tpmstate0" in data,
        "has_passthrough": any(x.startswith("hostpci") for x in data),
        "slots": slots,
        "eligible_for_current_dir_qmp_pilot":
            bool(slots) and data.get("bios", "seabios") == "seabios" and
            "efidisk0" not in data and "tpmstate0" not in data and
            not any(x.startswith(("unused", "hostpci")) for x in data),
    }


def preflight(storage=None, vmid=None, executor=run, node=None):
    node = node or socket.gethostname().split(".", 1)[0]
    if not STORE.fullmatch(node):
        raise ProbeError("Unsafe host node identifier")
    tools = {x: shutil.which(x) is not None for x in
             ("pvesh", "qm", "qemu-img", "qemu-io", "qemu-nbd")}
    report = {
        "node": node, "read_only": True, "hypervisor_mutated": False,
        "tools": tools, "storage": None, "guest": None,
        "checks": [],
        "real_witness_qualified": False,
        "real_failover_qualified": False,
    }
    for name, found in tools.items():
        report["checks"].append({"name": "binary:" + name, "pass": found})
    if storage:
        report["storage"] = node_storage(storage, node, executor)
        report["checks"].append({"name": "storage_active",
                                 "pass": report["storage"]["active"]})
    if vmid:
        report["guest"] = guest(vmid, executor)
        report["checks"].append({
            "name": "guest_pilot_hardware_supported",
            "pass": report["guest"]["eligible_for_current_dir_qmp_pilot"]})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", help="Proxmox storage ID to inspect")
    parser.add_argument("--vmid", type=int, help="Optional TEST source VMID")
    args = parser.parse_args()
    try:
        result = preflight(storage=args.storage, vmid=args.vmid)
    except ProbeError as exc:
        parser.exit(2, "PRA lab preflight failed: " + str(exc) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
