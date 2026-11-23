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
                     or occupied[0].get("status") == "running"):
        raise ValueError("VMID cible occupé par une autre ressource, un autre nœud ou VM en marche")
    # A newly created VM may temporarily be reported as 'unknown'. Do not
    # reject that cached state: the host helper rechecks its actual qm status,
    # exact policy ownership, onboot and network isolation before returning.
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
            "source_name": vm.get("source_name") or "VM-"+str(vm["source_vmid"]),
            "source_vmid": vm["source_vmid"],
            "source_node": source[0].get("node") or vm.get("source_node") or "unknown",
            "protection_note": vm.get("protection_note") or "",
            "reference": vm.get("reference") or "",
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


def diagnose(vm_id):
    """No infrastructure mutations: collect host-side storage and VMID checks."""
    vm = dr_protected_vms._one(vm_id)
    pra_live._scope(vm)
    binding = pra_live._remote_ssh_binding(vm["target_site_id"],vm["target_node"])
    ssh = pra_live._connect(binding)
    try:
        result = pra_live._run_helper(ssh,"lvm_lab_diagnose_standby",{
            "vmid":vm["target_vmid"],
            "protected_vm_id":vm["id"],
            "storage":vm["target_storage"],
            "name":vm["replica_name"],
        },timeout=90)
    finally:
        ssh.close()
    if (result.get("vmid")!=vm["target_vmid"]
            or not isinstance(result.get("checks"),list)
            or type(result.get("ready_for_creation")) is not bool):
        raise ValueError("Réponse du diagnostic distant invalide")
    return result


def verify_existing(vm_id):
    """Read-only verification of an existing owned standby and empty checkpoint state."""
    vm=dr_protected_vms._one(vm_id)
    pra_live._scope(vm)
    binding=pra_live._remote_ssh_binding(vm["target_site_id"],vm["target_node"])
    connection=pra_live._connect(binding)
    try:
        owned=pra_live._run_helper(connection,"lvm_lab_validate_standby",{
            "vmid":vm["target_vmid"],"protected_vm_id":vm["id"],
            "name":vm["replica_name"]
        },timeout=90)
        if (owned.get("owned") is not True or owned.get("vmid")!=vm["target_vmid"]
                or owned.get("target_stopped") is not True
                or owned.get("network_isolated") is not True):
            raise ValueError("REP_ n'est pas attestée comme appartenant à cette protection")
        state=pra_live._run_helper(connection,"lvm_lab_probe",{
            "vmid":vm["target_vmid"],"protected_vm_id":vm["id"],
            "storage":vm["target_storage"],"lab_ack":LAB_ACK
        },timeout=90)
    finally:
        connection.close()
    if state.get("pending") or state.get("checkpoint"):
        raise ValueError("Checkpoint LVM présent ou incertain : réconciliation manuelle obligatoire")
    return owned


def diagnose_source(vm_id):
    """Check the source with the actual PRA helper, without copy/guest mutation."""
    from . import pra_lvm_lab
    vm=dr_protected_vms._one(vm_id)
    info=pra_lvm_lab._runtime(vm)
    ssh=pra_live._connect(info["source"])
    try:
        result=pra_live._run_helper(ssh,"source_diagnose",{
            "vmid":vm["source_vmid"],"slots":info["slots"]
        },timeout=90)
    finally:
        ssh.close()
    if (type(result.get("ready")) is not bool
            or result.get("vmid") != vm["source_vmid"]
            or not isinstance(result.get("stage"),str)):
        raise ValueError("Diagnostic QMP source non confirmé")
    return result
