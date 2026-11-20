"""Controller-side guarded provisioning of a disposable LVM standby VM.

This prepares only the empty, OFF and networkless standby. It does not
replicate disks, attach bootable generations, or authorize failover.
"""
from . import db, dr_protected_vms, pra_live
from .pra_lvm_lab import LAB_ACK


def prepare(vm_id, confirmation):
    if confirmation != LAB_ACK:
        raise ValueError("Saisir DISPOSABLE-LAB-ONLY pour confirmer la recette")
    vm = dr_protected_vms._one(vm_id)
    pra_live._scope(vm)
    src, srccred, resources = dr_protected_vms._inventory(vm["source_dc_id"])
    dst, dstcred, targets = dr_protected_vms._inventory(vm["target_dc_id"])
    source = [r for r in resources if r.get("type") == "qemu"
              and r.get("vmid") == vm["source_vmid"] and r.get("status") == "running"]
    if len(source) != 1:
        raise ValueError("VM source non allumée ou introuvable")
    if not any(r.get("type") == "node" and r.get("node") == vm["target_node"]
               and r.get("status") == "online" for r in targets):
        raise ValueError("Nœud de secours hors ligne")
    occupied = [r for r in targets if r.get("type") in ("qemu", "lxc")
                and r.get("vmid") == vm["target_vmid"]]
    if occupied and (len(occupied) != 1 or occupied[0].get("type") != "qemu"
                     or occupied[0].get("node") != vm["target_node"]
                     or occupied[0].get("status") != "stopped"):
        raise ValueError("VMID cible déjà occupé ou VM de secours en marche")
    storages = dr_protected_vms._storages(dst, dstcred)
    compatible = [s for s in storages
                  if s.get("storage") == vm["target_storage"]
                  and s.get("type") == "lvm"
                  and s.get("snapshot_as_volume_chain") and s.get("images")]
    if len(compatible) != 1:
        raise ValueError("Stockage LVM incompatible avec les chaînes QCOW2")
    node_store = dr_protected_vms._node_storage(
        dst, dstcred, vm["target_node"], vm["target_storage"])
    if not node_store or not node_store.get("active"):
        raise ValueError("Stockage LVM inactif sur le nœud PRA")
    with db.session() as con:
        blocking = con.execute(
            "SELECT id FROM pra_lvm_lab_runs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') LIMIT 1", (vm_id,)
        ).fetchone()
        if blocking:
            raise ValueError("Réplication LVM active ou incertaine : préparation refusée")
        conflict = con.execute(
            "SELECT id FROM pra_live_runs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') LIMIT 1", (vm_id,)
        ).fetchone()
        if conflict:
            raise ValueError("Réplication QMP active ou incertaine : préparation refusée")
    binding = pra_live._remote_ssh_binding(vm["target_site_id"], vm["target_node"])
    connection = pra_live._connect(binding)
    try:
        result = pra_live._run_helper(connection, "lvm_lab_prepare_standby", {
            "vmid": vm["target_vmid"],
            "protected_vm_id": vm["id"],
            "storage": vm["target_storage"],
            "name": vm["replica_name"],
            "lab_ack": LAB_ACK
        }, timeout=180)
    finally:
        connection.close()
    if (result.get("vmid") != vm["target_vmid"]
            or result.get("target_stopped") is not True
            or result.get("network_isolated") is not True
            or result.get("lab_only") is not True
            or type(result.get("created")) is not bool):
        raise ValueError("L'agent n'a pas confirmé la création sûre du secours")
    return result
