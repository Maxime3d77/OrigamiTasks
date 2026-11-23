#!/usr/bin/env python3
"""Disposable, isolated test-VM preparation from a committed LVM checkpoint.

LAB ONLY: this creates independent QCOW2-on-LV volumes for a *new* VMID and
attaches them to a networkless VM with onboot=0. It deliberately NEVER starts
a VM: an operator must inspect the new configuration and approve the guest
boot separately, on a disconnected Proxmox test network. No existing LV is
overwritten or automatically freed. A missing acknowledgement leaves
all source/target disks unchanged.
"""
import json
import re
from pathlib import Path

from pra_lvm_target import PveLvmTarget

class RestoreLabError(ValueError):
    pass

def _hardware(source, key, default, maximum):
    val = str(source.get(key, default))
    if not val.isdigit() or not 1 <= int(val) <= maximum:
        raise RestoreLabError("Unsafe VM hardware setting: " + key)
    return val

def prepare_clone(store, *, vmid, source_config, command, inspect_config,
                  approved=False, lab_ack=None):
    """Allocate a separate test VM and flatten its disks, without booting it.

    command(*args, timeout=...) is an injected Proxmox/QEMU host runner.
    inspect_config(vmid) is an injected read-only Proxmox configuration lookup.
    """
    if approved is not True or lab_ack != "DISPOSABLE-LAB-ONLY":
        raise RestoreLabError("Disposable lab clone requires explicit approval")
    if type(vmid) is not int or not 100 <= vmid <= 999999999:
        raise RestoreLabError("Invalid test VMID")
    if vmid == store.target.vmid:
        raise RestoreLabError("Test VMID must differ from the protected standby")
    if not isinstance(source_config, dict) or source_config.get("bios","seabios")!="seabios":
        raise RestoreLabError("Unsupported BIOS/UEFI recovery profile")
    if any(key in source_config for key in ("efidisk0","tpmstate0")):
        raise RestoreLabError("EFI and TPM require separately qualified recovery")
    committed=store.current()
    if not committed or committed.get("status")!="committed":
        raise RestoreLabError("No committed checkpoint")
    if store.pending.exists():
        raise RestoreLabError("Unresolved checkpoint prevents recovery test")
    if committed.get("target_vmid")!=store.target.vmid:
        raise RestoreLabError("Committed checkpoint target VMID mismatch")
    if committed.get("protected_vm_id")!=store.target.protected_vm_id:
        raise RestoreLabError("Committed checkpoint protection ownership mismatch")
    if inspect_config(store.target.vmid).get("onboot","0") not in ("0","false"):
        raise RestoreLabError("Standby onboot is enabled")
    if command("qm","status",str(store.target.vmid)).strip()!="status: stopped":
        raise RestoreLabError("Standby must remain OFF")
    if inspect_config(vmid) is not None:
        raise RestoreLabError("Test VMID is already occupied")
    try:
        inventory=json.loads(command("pvesh","get","/cluster/resources",
                                     "--type","vm","--output-format","json"))
    except (TypeError, ValueError) as exc:
        raise RestoreLabError("Invalid cluster VMID inventory") from exc
    if not isinstance(inventory,list) or any(
            isinstance(entry,dict) and str(entry.get("vmid"))==str(vmid)
            for entry in inventory):
        raise RestoreLabError("Test VMID already reserved in the cluster")
    disks=committed.get("disks")
    if not isinstance(disks,dict) or not 1<=len(disks)<=32:
        raise RestoreLabError("Invalid disk manifest")
    sizes=PveLvmTarget.validate_sizes({
        slot: disk.get("size") for slot,disk in disks.items()})
    ledger=store._read(store.owned)
    if not isinstance(ledger,dict) or not isinstance(ledger.get("generations"),list):
        raise RestoreLabError("Missing ownership ledger")
    allowed={v for entry in ledger["generations"] if isinstance(entry,dict)
             for v in entry.get("volids",[]) if isinstance(v,str)}
    for slot,item in disks.items():
        if item.get("volid") not in allowed:
            raise RestoreLabError("Checkpoint disk outside ownership ledger")
    hardware={key:_hardware(source_config,key,defaults,maxv)
              for key,defaults,maxv in (
                  ("memory","1024",2097152),("cores","1",512),("sockets","1",16))}
    scsihw=source_config.get("scsihw")
    if scsihw is not None and scsihw not in (
            "virtio-scsi-pci","virtio-scsi-single","lsi","megasas","pvscsi"):
        raise RestoreLabError("Unsupported SCSI controller")
    # Re-check all source QCOW2 disks BEFORE allocating the first new LV.
    for item in disks.values():
        source_path=store.target.resolve_existing(item["volid"])
        command("qemu-img","check","-f","qcow2",source_path,timeout=120)
    test=PveLvmTarget(store.target.storage,vmid,command,
                      protected_vm_id=store.target.protected_vm_id)
    plan=test.plan(sizes,"full")
    allocation=test.stage_allocations(plan,approved=True)
    # Never rollback automatically: partial allocation must be reconciled.
    fresh={}
    for slot,record in sorted(allocation["allocated"].items()):
        dest=test.resolve_existing(record["volid"])
        source=store.target.resolve_existing(disks[slot]["volid"])
        command("qemu-img","convert","-f","qcow2","-O","qcow2",source,dest,
                timeout=21600)
        report=command("qemu-img","info","-f","qcow2","--backing-chain",
                       "--output=json",dest,timeout=120)
        try:
            chain=json.loads(report)
        except (ValueError, TypeError) as exc:
            raise RestoreLabError("Invalid cloned image information") from exc
        if (not isinstance(chain,list) or len(chain)!=1 or
                Path(chain[0].get("filename","")).resolve(strict=True)!=Path(dest).resolve(strict=True)):
            raise RestoreLabError("Cloned disk still depends on another image")
        command("qemu-img","check","-f","qcow2",dest,timeout=120)
        fresh[slot]=record["volid"]
    name="PRA-TEST-"+str(vmid)  # PVE qm --name must be DNS-format (no underscores)
    marker="ORIGAMITASKS_LAB_CLONE_"+str(store.target.protected_vm_id)
    args=["qm","create",str(vmid),"--name",name,"--onboot","0",
          "--description",marker,
          "--memory",hardware["memory"],"--cores",hardware["cores"],
          "--sockets",hardware["sockets"]]
    if scsihw:
        args.extend(["--scsihw",scsihw])
    command(*args)
    set_args=["qm","set",str(vmid),"--onboot","0"]
    for slot,volid in sorted(fresh.items()):
        set_args.extend(["--"+slot,volid])
    set_args.extend(["--boot","order="+sorted(fresh)[0]])
    command(*set_args)
    new=inspect_config(vmid)
    if (not isinstance(new,dict) or marker not in new.get("description","")
            or new.get("onboot","0") not in ("0","false")
            or any(re.fullmatch(r"net[0-9]{1,2}",key) for key in new)):
        raise RestoreLabError("Clone not proven isolated; do not start")
    if any(new.get(slot,"").split(",",1)[0]!=volid for slot,volid in fresh.items()):
        raise RestoreLabError("Clone disk attachments do not match new LVs")
    if command("qm","status",str(vmid)).strip()!="status: stopped":
        raise RestoreLabError("Clone unexpectedly running")
    return {"test_vmid":vmid,"name":name,"checkpoint":committed["token"],
            "test_volumes":fresh,"vm_started":False,"standby_started":False,
            "network_isolated":True,"boot_verified":False,
            "application_verified":False,"failover_authorized":False,
            "note":"Clone prepared and OFF. Manually validate isolation before any test boot."}
