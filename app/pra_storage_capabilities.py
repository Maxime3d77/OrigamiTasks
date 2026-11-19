"""Read-only, vendor-neutral Proxmox storage capability classification.

The Proxmox storage API advertises configuration capabilities; it does NOT
prove that an existing disk, host, or source/target pair is replicable.
Never choose a mutating transport based on a shared/LVM name or a vendor.
"""


def _enabled_flag(value):
    # PVE commonly serializes booleans as ints, but API mocks may use bools.
    return value is True or value == 1 or value == "1"


def describe_storage(config):
    """Return conservative, JSON-serializable capability metadata.

    PVE9 snapshot-as-volume-chain is a technology preview. Turning the flag on
    only affects newly created disks. Existing raw volumes must not be marked
    chain-snapshot compatible merely because the parent storage is opted in.
    """
    if not isinstance(config, dict):
        raise ValueError("Storage configuration must be an object")
    kind = str(config.get("type") or "unknown")
    name = str(config.get("storage") or "")
    contents = config.get("content") or ""
    if isinstance(contents, str):
        types = [x.strip() for x in contents.split(",")]
    elif isinstance(contents, list):
        types = contents
    else:
        types = []
    images = "images" in types
    shared = _enabled_flag(config.get("shared", False))
    disabled = _enabled_flag(config.get("disable", False))
    volume_chain = (kind == "lvm" and
                    _enabled_flag(config.get("snapshot-as-volume-chain", False)))
    # Dir/QCOW2 is the only implemented receiver in this release. Other
    # formats/backends require a separate, tested allocation/commit adapter.
    active_receiver = (kind == "dir" and images and not disabled)
    if not images:
        detail = "Stockage sans contenu images"
    elif disabled:
        detail = "Stockage désactivé"
    elif kind == "lvm" and volume_chain:
        detail = ("Snapshots LVM en chaîne QCOW2 (préversion Proxmox); "
                  "compatibilité à confirmer pour chaque disque; "
                  "adaptateur cible PRA LVM non développé")
    elif kind == "lvm":
        detail = "Stockage LVM; adaptateur cible PRA non développé"
    elif active_receiver:
        detail = "Pilote de réception DIR disponible; montage et droits à valider sur le nœud"
    else:
        detail = "Adaptateur cible PRA non développé ou non validé"

    return {
        "storage": name,
        "type": kind,
        "shared": shared,
        "images": images,
        "content": ",".join(types),
        "disabled": disabled,
        "snapshot_as_volume_chain": volume_chain,
        "snapshot_chain_preview": volume_chain,
        "snapshot_chain_existing_raw_supported": False,
        "per_disk_format_probe_required": True,
        "qmp_receiver_available": active_receiver,
        "qmp_receiver_adapter": "dir-qcow2-cow" if active_receiver else None,
        "capability_note": detail,
    }
