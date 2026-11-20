#!/usr/bin/env python3
"""Operator-only Proxmox LVM laboratory compatibility tool.

Never use on production VMIDs. Defaults to read-only inspection.
The stage subcommand allocates real LV(s) and initializes QCOW2 headers;
it explicitly requires acknowledgement and never attaches, starts,
promotes, free's or deletes any VM/volume. There is no live replication or
checkpoint promotion through this tool.
"""
import argparse
import json
import re
import subprocess
import sys

from pra_lvm_target import PveLvmTarget
from pra_lvm_checkpoint import LvmCheckpointStore

_UNITS = {"": 1, "K": 1024, "M": 1024**2,
          "G": 1024**3, "T": 1024**4, "P": 1024**5}


def _run(*args):
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Proxmox command unavailable") from exc
    if p.returncode:
        raise RuntimeError("Proxmox command rejected: " + args[0])
    return p.stdout


def parse_disk(text):
    if not isinstance(text, str) or "=" not in text:
        raise argparse.ArgumentTypeError("Use scsi0=16G")
    slot, value = text.split("=", 1)
    if not re.fullmatch(r"(scsi|virtio|sata|ide)[0-9]{1,2}", slot):
        raise argparse.ArgumentTypeError("Invalid disk slot")
    match = re.fullmatch(r"([0-9]+)([KMGTP]?)", value.upper())
    if not match:
        raise argparse.ArgumentTypeError("Invalid disk size")
    count = int(match.group(1)) * _UNITS[match.group(2)]
    if count < 1024 or count > 1 << 60:
        raise argparse.ArgumentTypeError("Disk size out of range")
    return slot, count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", required=True,
                        help="Configured Proxmox storage identifier (LVM)")
    parser.add_argument("--vmid", required=True, type=int,
                        help="Unique destination VMID on this cluster")
    parser.add_argument("--protection-id", required=True, type=int,
                        help="Existing or reserved OrigamiTasks protection ID")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("inspect", help="Read-only PVE storage and VMID capability check")
    plan = subs.add_parser("plan", help="Read-only thick LV capacity plan")
    plan.add_argument("--disk", action="append", required=True, type=parse_disk)
    stage = subs.add_parser("stage", help="Allocate fresh, orphaned LAB ONLY LVs")
    stage.add_argument("--disk", action="append", required=True, type=parse_disk)
    stage.add_argument("--acknowledge-volume-creation", action="store_true",
                       help="I understand real Proxmox volumes will be created")
    stage.add_argument("--lab-confirm", choices=["DISPOSABLE-LAB-ONLY"])
    subs.add_parser("status", help="Read-only local staging/committed journal")
    args = parser.parse_args(argv)
    target = PveLvmTarget(args.storage, args.vmid, _run,
                          protected_vm_id=args.protection_id)
    store = LvmCheckpointStore(target)
    if args.command == "inspect":
        info = target.inspect()
        node, free = target._node_capacity()
        info.update({"node": node, "available_bytes": free,
                     "replication_enabled": False, "failover_enabled": False,
                     "cluster_witness_validated": False})
        result = info
    elif args.command in ("plan", "stage"):
        sizes = dict(args.disk)
        if len(sizes) != len(args.disk):
            parser.error("Duplicate disk slot")
        if args.command == "plan":
            result = target.plan(sizes, "full")
        else:
            if (args.acknowledge_volume_creation is not True or
                    args.lab_confirm != "DISPOSABLE-LAB-ONLY"):
                parser.error("Staging requires both explicit LAB-only confirmations")
            # This test allocates actual PVE-owned LVs but cannot prove
            # recoverability and NEVER connects them to a VM.
            result = store.begin(mode="full", sizes=sizes,
                                 source_vm_uuid="lab-standalone-stage",
                                 operator_approved=True)
    else:
        result = {"current": store.current(), "pending": store._read(store.pending),
                  "storage":target.storage,"vmid":target.vmid,
                  "failover_enabled":False}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"ok":False,"error":str(exc)}),file=sys.stderr)
        sys.exit(2)
