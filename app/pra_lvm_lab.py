"""Manual, lab-only QMP-to-LVM cross-cluster replication runner.

Run inside the OrigamiTasks controller container with:
  python -m app.pra_lvm_lab --vm-id 123
  python -m app.pra_lvm_lab --vm-id 123 --execute --lab-confirm DISPOSABLE-LAB-ONLY
  python -m app.pra_lvm_lab --vm-id 123 --incremental --execute --lab-confirm DISPOSABLE-LAB-ONLY

This is NOT the production PRA engine. The destination must be an existing,
stopped, networkless VM explicitly marked ORIGAMITASKS_LAB_ONLY and owned by
the protection. Its source stays ON; no QM start/stop, VM attach, disk free,
fencing, automatic failover or failback is executed. LVM metadata is still
node-local: a host failure requires manual inspection and MUST NOT trigger
automatic promotion. The CLI is intentionally not exposed as a web action.
"""
import argparse
from datetime import datetime, timezone
import json
import sys

from . import dr_protected_vms, integrations, pra_live


class LabError(ValueError):
    pass


LAB_ACK = "DISPOSABLE-LAB-ONLY"


def _runtime(vm):
    pra_live._scope(vm)
    source_dc, source_cred, source_inventory = dr_protected_vms._inventory(vm["source_dc_id"])
    target_dc, target_cred, target_inventory = dr_protected_vms._inventory(vm["target_dc_id"])
    sources = [r for r in source_inventory if r.get("type") == "qemu"
               and r.get("vmid") == vm["source_vmid"] and r.get("status") == "running"]
    if len(sources) != 1:
        raise LabError("Source VM must be running on exactly one Proxmox node")
    source_node = sources[0].get("node")
    candidates = [r for r in target_inventory if r.get("type") in ("qemu", "lxc")
                  and r.get("vmid") == vm["target_vmid"]]
    if (len(candidates) != 1 or candidates[0].get("type") != "qemu"
            or candidates[0].get("node") != vm["target_node"]
            or candidates[0].get("status") != "stopped"):
        raise LabError("LAB target VM must exist, be stopped and have exclusive VMID")
    storages = dr_protected_vms._storages(target_dc, target_cred)
    target = next((s for s in storages if s.get("storage")==vm["target_storage"]),None)
    if not target or target["type"] != "lvm" or not target.get("snapshot_as_volume_chain") or not target.get("images"):
        raise LabError("LAB target must use writable Proxmox LVM with QCOW2 volume chains enabled")
    node = dr_protected_vms._node_storage(target_dc,target_cred,
                                          vm["target_node"],vm["target_storage"])
    if not node or not node.get("active") or type(node.get("available_bytes")) is not int:
        raise LabError("LAB LVM target unavailable or capacity unknown")
    disk_info = integrations.assess_vm_dr(source_dc,source_cred,vm["source_vmid"],timeout=18)
    slots = [d.get("slot") for d in disk_info.get("disks",[])]
    if (not slots or len(slots) != len(set(slots))
            or any(not isinstance(s,str) or not pra_live._SLOTS.fullmatch(s) for s in slots)):
        raise LabError("Unsupported source disk configuration")
    src_binding = pra_live._remote_ssh_binding(vm["source_site_id"],source_node)
    dst_binding = pra_live._remote_ssh_binding(vm["target_site_id"],vm["target_node"])
    if src_binding["host"].lower() == dst_binding["host"].lower():
        raise LabError("Source and destination must be different hosts")
    return {"source":src_binding,"destination":dst_binding,
            "slots":sorted(slots),"source_node":source_node,
            "available_bytes":node["available_bytes"]}


def _request(vm, token=None):
    request = {"vmid": vm["target_vmid"], "storage": vm["target_storage"],
               "protected_vm_id": vm["id"], "lab_ack": LAB_ACK}
    if token is not None:
        request["token"] = token
    return request


def run(vm_id, *, execute=False, incremental=False, lab_confirm=None):
    vm = dr_protected_vms._one(vm_id)
    info = _runtime(vm)
    # Even the diagnostic checks require remote helper authentication so
    # an operator knows that the declared target is REALLY LAB ONLY.
    src = dst = None
    forwards=[]
    threads=[]
    token=None
    transfer_started=False
    try:
        src = pra_live._connect(info["source"])
        dst = pra_live._connect(info["destination"])
        source = pra_live._run_helper(src,"source_probe",
            {"vmid":vm["source_vmid"],"slots":info["slots"]})
        destination = pra_live._run_helper(dst,"lvm_lab_probe",_request(vm))
        if source.get("active_jobs"):
            raise LabError("QMP source has an unresolved OrigamiTasks backup job")
        if destination.get("pending"):
            raise LabError("Unresolved LVM generation: manual reconciliation required")
        if source.get("pra_protocol",0) < 2 or destination.get("pra_protocol",0) < 3:
            raise LabError("PRA agents must be upgraded to a compatible lab protocol")
        sizes=source.get("sizes") or {}
        if set(sizes)!=set(info["slots"]):
            raise LabError("Source disk sizes missing or changed")
        current=destination.get("checkpoint")
        mode = "incremental" if incremental else "full"
        if incremental:
            if (not isinstance(current,dict) or current.get("status") != "committed"
                    or current.get("source_vm_uuid") != source.get("source_vm_uuid")
                    or set(current.get("disks",{}))!=set(info["slots"])):
                raise LabError("No compatible verified checkpoint; FULL LAB reseed required")
            for slot in info["slots"]:
                if current["disks"][slot].get("size")!=sizes[slot]:
                    raise LabError("Source size changed; FULL LAB reseed required")
                bitmap=source.get("bitmaps",{}).get(slot,{}).get(
                    "origami_"+str(vm["source_vmid"])+"_"+slot,{})
                if (bitmap.get("recording") is not True or bitmap.get("busy") is True
                        or bitmap.get("inconsistent") is True):
                    raise LabError("QMP dirty bitmap missing or unsafe; FULL LAB reseed required")
        result = {
            "vm_id":vm_id, "source_vmid":vm["source_vmid"],
            "target_vmid":vm["target_vmid"],"mode":mode,
            "disk_slots":info["slots"],"source_running":True,
            "target_stopped":True, "destination_storage":vm["target_storage"],
            "available_bytes":info["available_bytes"],
            "checkpoint":current.get("token") if isinstance(current,dict) else None,
            "lab_only":True, "real_pve_replication_performed":False,
            "standby_attached":False, "vm_started":False,
            "failover_available":False,
        }
        if not execute:
            result["note"]="READ ONLY: supply --execute and --lab-confirm to allocate a disposable test LV"
            return result
        if lab_confirm != LAB_ACK:
            raise LabError("LAB execute requires explicit acknowledgement")
        prepared = pra_live._run_helper(dst,"lvm_lab_begin",{
            **_request(vm),
            "mode": mode, "sizes": sizes,
            "source_vm_uuid":source["source_vm_uuid"]
        },timeout=86460)  # Local flattening of a multi-TB LV can take hours.
        token=prepared["token"]
        mapped,forwards,threads,errors=pra_live._forward(src,dst,prepared["ports"])
        transfer_started=True
        try:
            backup=pra_live._run_helper(src,"source_copy",{
                "vmid":vm["source_vmid"],"slots":info["slots"],
                "mode":mode,"source_vm_uuid":source["source_vm_uuid"],
                "ports":mapped,"timeout":21600
            },timeout=21660)
        finally:
            for port in forwards:
                src.get_transport().cancel_port_forward("127.0.0.1",port)
            for thread in threads:
                thread.join(timeout=3)
        if errors or backup.get("qmp_jobs_completed") is not True:
            raise LabError("Unverified QMP NBD source result; preserve the LVM checkpoint")
        completed=pra_live._run_helper(dst,"lvm_lab_finish",{
            **_request(vm,token),"verified_by_source":True
        },timeout=300)
        if completed.get("checkpoint",{}).get("token")!=token:
            raise LabError("LVM commit token mismatch")
        evidence=pra_live._run_helper(dst,"lvm_lab_verify",_request(vm,token),timeout=300)
        if (evidence.get("verified_offline") is not True
                or evidence.get("checkpoint_token")!=token
                or evidence.get("vm_started") is not False
                or set(evidence.get("disks",[]))!=set(info["slots"])):
            raise LabError("Final LVM disk structural evidence missing")
        return {**result, "real_pve_replication_performed":True,
                "checkpoint_token":token,
                "point_at":datetime.fromtimestamp(
                    backup["snapshot_epoch"],timezone.utc).isoformat(timespec="seconds"),
                "offline_structure_verified":True,
                "note":"LAB checkpoint copied and verified on LVM; not attached/booted. NO failover."}
    except BaseException as exc:
        if token:
            # Never blindly clean up block LVs after a lost acknowledgement.
            raise LabError("LVM LAB checkpoint UNCONFIRMED: token="+token+
                           "; reconcile manually. Cause="+type(exc).__name__) from exc
        raise
    finally:
        if src:
            src.close()
        if dst:
            dst.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vm-id",type=int,required=True)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--incremental",action="store_true",
                        help="Request incremental only with a verified QMP bitmap and base")
    parser.add_argument("--lab-confirm",choices=[LAB_ACK])
    args=parser.parse_args(argv)
    try:
        result=run(args.vm_id,execute=args.execute,
                   incremental=args.incremental,lab_confirm=args.lab_confirm)
        print(json.dumps({"ok":True,"result":result},indent=2,sort_keys=True))
        return 0
    except (ValueError,RuntimeError) as exc:
        print(json.dumps({"ok":False,"error":str(exc)[:400]},indent=2),file=sys.stderr)
        return 2


if __name__=="__main__":
    sys.exit(main())
