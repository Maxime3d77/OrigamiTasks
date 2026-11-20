"""Read-only, vendor-neutral PRA recovery gates.

A completed disk transfer is not enough to authorize activation. This module
only reports evidence and blockers. It deliberately has no start/stop,
power-control, witness-write, fencing or failover entrypoint. In particular,
an independent witness cannot be simulated by controller-local state.
"""
from . import dr_protected_vms, pra_live


def _gate(code, name, passed, explanation):
    return {"code": code, "name": name, "passed": passed is True,
            "detail": explanation}


def assess(vm_id):
    """Explain which recovery capabilities exist, without authorizing failover."""
    vm = dr_protected_vms._one(vm_id)
    if vm.get("engine") == "qmp_live_incremental":
        last = pra_live.latest_status(vm_id)
    else:
        last = {"replication_status": "not_supported",
                "offline_image_verified": False,
                "last_successful_sync": None}

    storage_error = None
    try:
        storage = dr_protected_vms.storage_readiness(vm_id)
    except (dr_protected_vms.ProtectedVMError, Exception) as exc:
        # Proxmox connectivity is optional for explaining the other gates,
        # but it is NEVER treated as proof that the standby is usable.
        storage = None
        storage_error = type(exc).__name__

    target_backend = storage.get("backend") if storage else None
    structural = (vm.get("engine") == "qmp_live_incremental"
                  and last.get("replication_status") == "completed"
                  and last.get("offline_image_verified") is True)
    target_online = bool(storage and storage.get("node_online") is True
                         and storage.get("node_storage_active") is True)
    backend_implemented = target_backend == "dir-qcow2-cow"

    checks = [
        _gate("policy", "Politique PRA active", vm.get("state") == "planned",
              "Le groupe PRA et la VM doivent rester actifs"),
        _gate("replication_engine", "Transport PRA opérationnel",
              vm.get("engine") == "qmp_live_incremental" and backend_implemented,
              "Le pilote QMP DIR existe ; le récepteur LVM est encore en préparation"),
        _gate("destination", "Nœud et stockage de destination disponibles",
              target_online, "Détection Proxmox et stockage actif confirmés par API" if target_online
              else "Nœud ou stockage indisponible, non vérifié ou sans accès API"),
        _gate("disk_checkpoint", "Dernier checkpoint structurellement vérifié",
              structural, "Images QCOW2 vérifiées hors ligne uniquement" if structural
              else "Aucune preuve structurelle récente pour le dernier transfert"),
        _gate("isolated_restore", "Démarrage isolé et restauration validés", False,
              "Il manque un test indépendant de boot de la copie restaurée"),
        _gate("application_health", "Cohérence applicative prouvée", False,
              "Il manque les contrôles OS, applicatifs et transactionnels"),
        _gate("witness", "Témoin indépendant et autorité exclusive", False,
              "Aucun bail d'autorité issu d'un témoin externe n'est vérifié"),
        _gate("fencing", "Fencing de la source confirmé", False,
              "Aucune preuve d'arrêt effectif de l'ancien maître n'est vérifiée"),
        _gate("start_guard", "Protection anti-double démarrage", False,
              "Un verrou de démarrage imposé sur chaque hyperviseur manque"),
        _gate("network", "Réseau de secours validé", False,
              "Plan de mappage réseau isolé et routage de bascule non validés"),
        _gate("failback", "Retour arrière opérationnel", False,
              "Synchronisation inverse et restitution d'autorité non validées"),
    ]
    blockers = [x for x in checks if not x["passed"]]
    return {
        "vm_id": vm_id,
        "source_vmid": vm["source_vmid"],
        "target_vmid": vm["target_vmid"],
        "engine": vm["engine"],
        "replication_status": last.get("replication_status"),
        "last_structural_sync": last.get("last_successful_sync") if structural else None,
        "target_backend": target_backend,
        "storage_inspection_error": storage_error,
        "checks": checks,
        "blockers": [x["code"] for x in blockers],
        "readiness": "blocked",
        "can_simulate": True,
        "can_promote": False,
        "can_failback": False,
        "safety_note": (
            "La vérification structurelle ne prouve pas le démarrage ni le "
            "fencing. Aucun basculement réel n'est autorisé."
        ),
    }
