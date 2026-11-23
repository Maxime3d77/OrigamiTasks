"""Read-only network cutover planning for PRA, never a VM NIC operation.

Existing source NIC intent and destination bridge existence are evidence, not
a working network cutover. VLAN trunking, routing, L2 reachability, IP/MAC
uniqueness and witness fencing are not validated by this preview.
"""
from . import dr_protected_vms, pra_lvm_lab, pra_live


def build_preview(source, target):
    if (not isinstance(source,dict) or not isinstance(target,dict)
            or not isinstance(source.get("nics"),list)
            or not isinstance(target.get("bridges"),list)):
        raise ValueError("Inventaires réseau Proxmox non confirmés")
    bridges=target["bridges"]
    if any(not isinstance(b,str) for b in bridges):
        raise ValueError("Ponts du site secours invalides")
    result=[]
    slots=set()
    for nic in source["nics"]:
        if not isinstance(nic,dict):
            raise ValueError("NIC source invalide")
        slot=nic.get("slot")
        bridge=nic.get("source_bridge")
        vlan=nic.get("source_vlan")
        if not isinstance(slot,str) or slot in slots or not isinstance(bridge,str):
            raise ValueError("Slots ou bridges source invalides")
        slots.add(slot)
        if vlan is not None and (type(vlan) is not int or not 1<=vlan<=4094):
            raise ValueError("VLAN source invalide")
        exists=bridge in bridges
        result.append({
            "slot":slot,"model":nic.get("model"),
            "source_bridge":bridge,"source_vlan":vlan,
            "suggested_target_bridge":bridge if exists else None,
            "suggested_target_vlan":vlan if exists else None,
            "bridge_exists":exists,
            "vlan_reachability_verified":False,
            "mapping_verified":False,
            "source_mac_reuse_authorized":False,
        })
    return {
        "nics":result,"source_nic_count":len(result),
        "destination_bridges":bridges,
        "missing_bridges":[n["source_bridge"] for n in result if not n["bridge_exists"]],
        "network_ready":False,
        "apply_allowed":False,
        "production_failover_authorized":False,
        "warning":("Les bridges communs sont seulement des propositions. "
                   "VLAN/routage/MAC, isolation du maître et fencing non vérifiés. "
                   "Aucune carte réseau n'est ajoutée à REP_."),
    }


def inspect(vm_id):
    vm=dr_protected_vms._one(vm_id)
    if vm.get("engine")!="qmp_live_incremental":
        raise ValueError("Moteur QMP requis")
    scope=pra_lvm_lab._runtime(vm)
    source=destination=None
    try:
        source=pra_live._connect(scope["source"])
        destination=pra_live._connect(scope["destination"])
        src=pra_live._run_helper(source,"source_network_inventory",
                                 {"vmid":vm["source_vmid"]},timeout=60)
        dst=pra_live._run_helper(destination,"destination_network_inventory",
                                 {"vmid":vm["target_vmid"]},timeout=60)
        preview=build_preview(src,dst)
        preview.update({"vm_id":vm_id,"source_vmid":vm["source_vmid"],
                        "target_vmid":vm["target_vmid"],
                        "target_node":vm["target_node"]})
        return preview
    finally:
        if source: source.close()
        if destination: destination.close()
