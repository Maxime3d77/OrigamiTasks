"""Read-only, vendor-neutral PRA recovery gates.

A completed disk transfer is not enough to authorize activation. This module
only reports evidence and blockers. It deliberately has no start/stop,
power-control, witness-write, fencing or failover entrypoint. In particular,
an independent witness cannot be simulated by controller-local state.
"""
from . import db, dr_protected_vms, pra_live, pra_lvm_guest_evidence


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
    except Exception as exc:
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
    lvm_copy = lvm_published = boot_test = None
    if target_backend == "lvm-qcow2-staging":
        with db.session() as con:
            lvm_copy = con.execute(
                "SELECT status,checkpoint_token,point_at FROM pra_lvm_lab_runs "
                "WHERE protected_vm_id=? AND mode IN ('full','incremental') "
                "ORDER BY id DESC LIMIT 1",(vm_id,)).fetchone()
            if lvm_copy and lvm_copy["checkpoint_token"]:
                lvm_published = con.execute(
                    "SELECT status FROM pra_lvm_publications WHERE protected_vm_id=? "
                    "AND checkpoint_token=? ORDER BY id DESC LIMIT 1",
                    (vm_id,lvm_copy["checkpoint_token"])).fetchone()
            boot_test = con.execute(
                "SELECT status FROM pra_lvm_boot_tests WHERE protected_vm_id=? "
                "ORDER BY id DESC LIMIT 1",(vm_id,)).fetchone()
        structural = bool(lvm_copy and lvm_copy["status"]=="completed")
        backend_implemented = bool(structural and lvm_published
                                   and lvm_published["status"]=="completed")
    qemu_tested = bool(boot_test and boot_test["status"]=="completed")
    # A console-login observation is human-reported, checkpoint-specific and
    # never equivalent to proof of application health or fencing.
    os_observation = (pra_lvm_guest_evidence.current(vm_id)
                      if target_backend == "lvm-qcow2-staging" else None)

    checks = [
        _gate("policy", "Politique PRA active", vm.get("state") == "planned",
              "Le groupe PRA et la VM doivent rester actifs"),
        _gate("replication_engine", "Transport PRA opérationnel",
              vm.get("engine") == "qmp_live_incremental" and backend_implemented,
              "Un checkpoint vérifié et publié est attesté par le contrôleur" if backend_implemented
              else "Le moteur choisi n'a pas de preuve de publication compatible"),
        _gate("destination", "Nœud et stockage de destination disponibles",
              target_online, "Détection Proxmox et stockage actif confirmés par API" if target_online
              else "Nœud ou stockage indisponible, non vérifié ou sans accès API"),
        _gate("disk_checkpoint", "Dernier checkpoint structurellement vérifié",
              structural, "Images QCOW2 vérifiées hors ligne uniquement" if structural
              else "Aucune preuve structurelle récente pour le dernier transfert"),
        _gate("qemu_test", "Démarrage QEMU d'un clone isolé",
              qemu_tested, "Test QEMU terminé puis clone arrêté" if qemu_tested
              else "Aucun test QEMU isolé et arrêté n'a été confirmé"),
        _gate("isolated_restore", "Connexion à l'OS restauré observée",
              bool(os_observation),
              ("Observée par "+os_observation["actor"]+" sur clone #"
               +str(os_observation["test_vmid"])+" pour le checkpoint courant ; "
               "preuve humaine, non automatisée et sans validation applicative")
              if os_observation else
              "Aucun contrôle de connexion OS enregistré pour le dernier checkpoint"),
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
        "last_structural_sync": (lvm_copy["point_at"] if lvm_copy and structural
                                 else last.get("last_successful_sync") if structural else None),
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
