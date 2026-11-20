"""Live Proxmox VM replication coordinator, independent of storage-array replication.

Data plane: QMP dirty bitmaps -> loopback NBD -> encrypted SSH forwarding via
controller -> target qemu-nbd -> COW qcow2 checkpoint -> OFF standby VM.

Only admin-approved manual starts; subsequent schedules run only after a
verified successful seed. A failed/uncertain run always forces a FULL reseed.
No VM start/stop, no automatic promotion or failback, no external Pure calls.
"""
import base64
import json
import re
import select
import shlex
import threading
import time
from datetime import datetime, timezone, timedelta

from . import db, dr_protected_vms, integrations

STOP = threading.Event()
_WORKERS = set()
_LOCK = threading.Lock()
_HELPER = "/usr/local/lib/origamitasks-pra/pra_host_worker.py"
_SLOTS = re.compile(r"^(scsi|virtio|sata|ide)[0-9]{1,2}$")


class LiveReplicationError(ValueError):
    pass


def _scope(vm):
    if vm["engine"] != "qmp_live_incremental":
        raise LiveReplicationError("Choisir le moteur incrémental QEMU pour cette VM")
    if vm["state"] != "planned":
        raise LiveReplicationError("Protection VM en pause")
    if vm["source_dc_id"] == vm["target_dc_id"]:
        raise LiveReplicationError("Source et secours ne peuvent pas être le même cluster")
    # Ensure site/cluster ownership has not changed after policy creation.
    dr_protected_vms._sites(vm["plan_id"], vm["source_dc_id"], vm["target_dc_id"])


def _remote_ssh_binding(site_id, expected_node):
    """Reuse approved SSH credentials from a successfully installed site agent."""
    with db.session() as con:
        rows = con.execute(
            "SELECT t.host,t.port,t.credential_id,a.id AS agent_id,a.last_seen "
            "FROM dr_site_agents ds JOIN agents a ON a.id=ds.agent_id "
            "JOIN agent_deploy_targets t ON t.agent_id=a.id "
            "WHERE ds.site_id=? AND a.platform='linux' AND a.enrolled=1 "
            "AND a.revoked=0 AND a.uninstalled_at IS NULL AND t.status='success' "
            "ORDER BY t.id DESC", (site_id,)
        ).fetchall()
    match = [dict(r) for r in rows
             if r["host"].lower().split(".")[0] == expected_node.lower().split(".")[0]]
    if len({(r["host"], r["credential_id"]) for r in match}) != 1:
        raise LiveReplicationError("Agent déployé / identifiants SSH du nœud " +
                                   expected_node + " absents ou ambigus")
    return match[0]


def _vm_runtime(vm):
    """Perform read-only Proxmox queries before any agent/SSH mutation."""
    src, sc, inventory = dr_protected_vms._inventory(vm["source_dc_id"])
    dst, dc, target = dr_protected_vms._inventory(vm["target_dc_id"])
    sources = [i for i in inventory if i.get("type") == "qemu"
               and i.get("vmid") == vm["source_vmid"]]
    if len(sources) != 1 or sources[0].get("status") != "running":
        raise LiveReplicationError("VM source introuvable ou arrêtée : copie à chaud impossible")
    source_node = sources[0].get("node")
    target_nodes = [i for i in target if i.get("type") == "node"
                    and i.get("node") == vm["target_node"] and i.get("status") == "online"]
    if len(target_nodes) != 1:
        raise LiveReplicationError("Nœud PRA cible indisponible")
    present = [i for i in target if i.get("type") in ("qemu", "lxc")
               and i.get("vmid") == vm["target_vmid"]]
    if present and (len(present) != 1 or present[0].get("type") != "qemu"
                    or present[0].get("status") != "stopped"
                    or present[0].get("node") != vm["target_node"]):
        raise LiveReplicationError("VM cible déjà active ou non conforme : aucune écriture")
    storages = dr_protected_vms._storages(dst, dc)
    choice = next((s for s in storages if s["storage"] == vm["target_storage"]), None)
    if not choice or choice["type"] != "dir" or not choice["images"]:
        raise LiveReplicationError(
            "Le pilote QMP nécessite un stockage Proxmox DIR inscriptible au PRA. "
            "Un volume Pure répliqué RO ou un VG/LVM brut est refusé.")
    source_assessment = integrations.assess_vm_dr(src, sc, vm["source_vmid"], timeout=18)
    slots = [str(d.get("slot") or "") for d in source_assessment.get("disks", [])]
    if not slots or any(not _SLOTS.fullmatch(s) for s in slots) or len(slots) != len(set(slots)):
        raise LiveReplicationError(
            "VM non supportée : disques non détectés ou périphériques EFI/TPM spéciaux")
    source = _remote_ssh_binding(vm["source_site_id"], source_node)
    destination = _remote_ssh_binding(vm["target_site_id"], vm["target_node"])
    if (source["host"].lower() == destination["host"].lower()):
        raise LiveReplicationError("Source et destination sur le même hyperviseur")
    return {"source_node": source_node, "source": source,
            "destination": destination, "slots": sorted(slots)}


def preflight(vm_id):
    vm = dr_protected_vms._one(vm_id)
    _scope(vm)
    info = _vm_runtime(vm)
    return {"vm_id": vm_id, "source_node": info["source_node"],
            "target_node": vm["target_node"], "storage": vm["target_storage"],
            "mode": "qmp_live_incremental",
            "source_remains_running": True, "target_must_remain_stopped": True,
            "disks": info["slots"],
            "transport": "SSH forwarded NBD via controller",
            "requires_root_helper": True,
            "ready_for_manual_test": True,
            "note": "Prévol API valide, capacité QMP et stockage vérifiés lors de l'exécution."}


def _connect(binding):
    from .executor import _ssh, credential
    credentials = credential(binding["credential_id"])
    connection, _ = _ssh(None,
                         {"host": binding["host"], "port": binding["port"]},
                         credentials, 30)
    return connection


def _run_helper(ssh, action, data, timeout=60):
    if action not in {"source_probe", "source_copy", "source_dismiss",
                      "target_probe", "target_begin",
                      "target_finish", "target_abort", "target_publish", "target_verify",
                       "lvm_lab_probe", "lvm_lab_begin", "lvm_lab_finish",
                       "lvm_lab_verify", "lvm_lab_abort", "lvm_lab_prepare_clone"}:
        raise LiveReplicationError("Action PRA non autorisée")
    encoded = base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    invoke = "/usr/bin/python3 " + shlex.quote(_HELPER) + " " + action + " " + shlex.quote(encoded)
    command = 'if [ "$(id -u)" -eq 0 ]; then exec ' + invoke + '; else exec sudo -n ' + invoke + '; fi'
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    channel = stdout.channel
    channel.settimeout(timeout)
    output = stdout.read(262144).decode(errors="replace")
    code = channel.recv_exit_status()
    if code:
        # Error details from a remote host are deliberately sanitized.
        raise LiveReplicationError("Échec commande agent PRA: " + action +
                                   " (vérifier root helper, qemu-nbd et logs locaux)")
    try:
        response = json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise LiveReplicationError("Réponse agent PRA invalide") from None
    if response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise LiveReplicationError("Action agent PRA rejetée: " + action)
    return response["result"]


def _relay_pair(source_channel, target_ssh, port, errors):
    """Bridge one encrypted SSH forward, never publish an NBD TCP port."""
    peer = None
    try:
        peer = target_ssh.get_transport().open_channel(
            "direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0), timeout=30)
        while not STOP.is_set():
            readable, _, _ = select.select([source_channel, peer], [], [], 10)
            for stream in readable:
                content = stream.recv(262144)
                if not content:
                    return
                other = peer if stream is source_channel else source_channel
                other.sendall(content)
    except BaseException as exc:
        errors.append(type(exc).__name__)
    finally:
        source_channel.close()
        if peer:
            peer.close()


def _forward(source_ssh, target_ssh, slots_ports):
    """Create a target-specific remote forwarding port on the source SSHD."""
    transport = source_ssh.get_transport()
    forwards = []
    threads = []
    errors = []
    for slot, target_port in slots_ports.items():
        if type(target_port) is not int or not 1 <= target_port <= 65535:
            raise LiveReplicationError("Port NBD cible invalide")
        def handle(channel, origin, server, destination=target_port):
            thread = threading.Thread(target=_relay_pair,
                       args=(channel, target_ssh, destination, errors), daemon=True)
            threads.append(thread)
            thread.start()
        port = transport.request_port_forward("127.0.0.1", 0, handler=handle)
        forwards.append(port)
    mapping = dict(zip(slots_ports, forwards))
    return mapping, forwards, threads, errors


def _state(run_id, state, note="", mode=None, checkpoint=None, point_at=None,
           verification_level=None):
    with db.session() as con:
        con.execute(
            "UPDATE pra_live_runs SET status=?,details=?,mode=COALESCE(?,mode),"
            "checkpoint_token=COALESCE(?,checkpoint_token),"
            "point_at=COALESCE(?,point_at),"
            "verification_level=COALESCE(?,verification_level),"
            "started_at=CASE WHEN ?='running' THEN COALESCE(started_at,?) ELSE started_at END,"
            "finished_at=CASE WHEN ? IN ('completed','failed','uncertain') THEN ? ELSE finished_at END "
            "WHERE id=?", (state, note[:500], mode, checkpoint, point_at,
                           verification_level, state, db.now(), state, db.now(), run_id))


def _perform(run_id):
    with db.session() as con:
        row = con.execute("SELECT * FROM pra_live_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return
    vm_id = row["protected_vm_id"]
    src_ssh = dst_ssh = None
    token = None
    phase = "preflight"
    successful = False
    try:
        vm = dr_protected_vms._one(vm_id)
        _scope(vm)
        info = _vm_runtime(vm)
        _state(run_id, "running", "Connexion aux deux agents et validation QMP")
        src_ssh = _connect(info["source"])
        dst_ssh = _connect(info["destination"])
        source = _run_helper(src_ssh, "source_probe",
                 {"vmid": vm["source_vmid"], "slots": info["slots"]})
        target = _run_helper(dst_ssh, "target_probe",
                 {"vmid": vm["target_vmid"], "storage": vm["target_storage"]})
        # An older host helper cannot verify its output and could re-enable
        # unsafe checkpoint GC. Reject the entire run *before* allocating or
        # transmitting any data, rather than discovering this after a huge copy.
        if (type(source.get("pra_protocol")) is not int or source["pra_protocol"] < 2
                or type(target.get("pra_protocol")) is not int or target["pra_protocol"] < 2
                or target.get("offline_structural_verify") is not True
                or target.get("checkpoint_retention") != "manual_only"):
            raise LiveReplicationError(
                "Agents PRA obsolètes : mettre à jour les deux assistants Proxmox "
                "avant une nouvelle réplication")
        if target.get("pending"):
            raise LiveReplicationError("Checkpoint cible non confirmé : réconciliation requise")
        if source.get("active_jobs"):
            raise LiveReplicationError("Un job QMP de réplication est déjà actif")
        checkpoint = target["checkpoint"]
        state_good = all(source["bitmaps"][slot].get(
            "origami_" + str(vm["source_vmid"]) + "_" + slot, {}).get("recording") is True
            and source["bitmaps"][slot]["origami_" + str(vm["source_vmid"]) + "_" + slot].get("inconsistent") is not True
            and source["bitmaps"][slot]["origami_" + str(vm["source_vmid"]) + "_" + slot].get("busy") is not True
            for slot in info["slots"])
        # Missing bitmap after QEMU restart, any failed job or uncertain ACK:
        # FULL reseed. No delta is ever applied to an unacknowledged base.
        with db.session() as con:
            previous = con.execute(
                "SELECT status,verification_level FROM pra_live_runs WHERE protected_vm_id=? AND id<>? "
                "ORDER BY id DESC LIMIT 1", (vm_id, run_id)).fetchone()
        mode = ("incremental" if checkpoint and state_good and
                checkpoint.get("source_vm_uuid") == source["source_vm_uuid"] and
                set(checkpoint.get("disks", {})) == set(info["slots"]) and
                all(checkpoint["disks"][s]["size"] == source["sizes"][s] for s in info["slots"])
                and previous is not None and previous["status"] == "completed"
                and previous["verification_level"] == "offline_structural"
                else "full")
        _state(run_id, "running", "Préparation checkpoint de type " + mode, mode=mode)
        prepared = _run_helper(dst_ssh, "target_begin", {
            "vmid": vm["target_vmid"], "storage": vm["target_storage"],
            "source_vm_uuid": source["source_vm_uuid"],
            "mode": mode, "sizes": source["sizes"]})
        token = prepared["token"]
        _state(run_id, "running", "NBD chiffré en cours de transfert", mode=mode, checkpoint=token)
        mapped, listening, threads, errors = _forward(
            src_ssh, dst_ssh, prepared["ports"])
        phase = "copy"
        try:
            backup = _run_helper(src_ssh, "source_copy", {
                "vmid": vm["source_vmid"], "slots": info["slots"],
                "source_vm_uuid": source["source_vm_uuid"],
                "mode": mode, "ports": mapped, "timeout": 21600
            }, timeout=21660)
        finally:
            for port in listening:
                src_ssh.get_transport().cancel_port_forward("127.0.0.1", port)
            for thread in threads:
                thread.join(timeout=3)
        if errors or not backup.get("qmp_jobs_completed"):
            raise LiveReplicationError("Transfert QMP non vérifié")
        phase = "commit"
        _state(run_id, "running", "Vérification et publication du checkpoint")
        finished = _run_helper(dst_ssh, "target_finish", {
            "vmid": vm["target_vmid"], "storage": vm["target_storage"],
            "token": token, "verified_by_source": True
        }, timeout=180)
        if finished["checkpoint"].get("token") != token:
            raise LiveReplicationError("Incohérence du checkpoint distant")
        phase = "publish"
        # Source metadata always comes from the source VM, never arbitrary UI.
        published = _run_helper(dst_ssh, "target_publish", {
            "vmid": vm["target_vmid"], "storage": vm["target_storage"],
            "token": token, "protected_vm_id": vm_id,
            "name": vm["replica_name"], "config": source["config"],
        }, timeout=180)
        if not published.get("standby_configured") or published.get("vm_started"):
            raise LiveReplicationError("La configuration de secours n'a pas été confirmée")
        phase = "verify"
        _state(run_id, "running", "Contrôle des disques et de la configuration de secours arrêtée")
        proof = _run_helper(dst_ssh, "target_verify", {
            "vmid": vm["target_vmid"], "storage": vm["target_storage"],
            "token": token, "protected_vm_id": vm_id,
        }, timeout=300)
        if (proof.get("checkpoint_token") != token or
                proof.get("verified_offline") is not True or
                proof.get("disk_format_checked") is not True or
                proof.get("standby_configured") is not True or
                proof.get("vm_started") is not False or
                proof.get("failover_authorized") is not False or
                set(proof.get("disks", [])) != set(info["slots"])):
            raise LiveReplicationError("Le contrôle de la copie de secours n'a pas été confirmé")
        point = backup.get("snapshot_epoch")
        if type(point) not in (int,float) or point <= 0:
            raise LiveReplicationError("Horodatage du point QMP absent")
        point_at = datetime.fromtimestamp(point,timezone.utc).isoformat(timespec="seconds")
        _state(run_id, "completed", mode + ": image QCOW2 vérifiée hors ligne, VM arrêtée (non testée au démarrage)",
               point_at=point_at, verification_level="offline_structural")
        db.audit("system", "pra.live.replication.completed", vm_id, "run=" + str(run_id))
    except BaseException as exc:
        # A source bitmap may have advanced even when the acknowledgement was
        # lost. No blind retry; require full copy on the next attempt.
        uncertain = phase in ("commit", "publish", "verify") or (
            isinstance(exc, LiveReplicationError) and "réconciliation requise" in str(exc))
        _state(run_id, "uncertain" if uncertain else "failed",
               "Transfert ou publication non confirmé (" + phase + ") : " + type(exc).__name__)
        if token and phase not in ("commit", "publish", "verify") and dst_ssh:
            try:
                _run_helper(dst_ssh, "target_abort",
                    {"vmid": vm["target_vmid"], "storage": vm["target_storage"],
                     "token": token}, timeout=60)
            except BaseException:
                _state(run_id, "uncertain", "Checkpoint staging à réconcilier manuellement")
    finally:
        if src_ssh:
            src_ssh.close()
        if dst_ssh:
            dst_ssh.close()
        with _LOCK:
            _WORKERS.discard(run_id)


def start(vm_id, actor, approved=False, scheduled=False):
    if not scheduled and approved is not True:
        raise LiveReplicationError("Validation explicite requise pour répliquer une VM")
    vm = dr_protected_vms._one(vm_id)
    _scope(vm)
    # Do not reserve a task until the read-only preflight can pass.
    _vm_runtime(vm)
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute(
            "SELECT id FROM pra_live_runs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') LIMIT 1", (vm_id,)
        ).fetchone()
        if previous:
            raise LiveReplicationError(
                "Réplication active ou état indéterminé : vérifier et réconcilier avant de relancer")
        lvm_conflict = con.execute(
            "SELECT id FROM pra_lvm_lab_runs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain') LIMIT 1", (vm_id,)
        ).fetchone()
        if lvm_conflict:
            raise LiveReplicationError(
                "Copie LVM de recette active/incertaine : résoudre son état avant un QMP DIR")
        row = con.execute("SELECT engine,state FROM dr_protected_vms WHERE id=?",
                          (vm_id,)).fetchone()
        if not row or row["engine"] != "qmp_live_incremental" or row["state"] != "planned":
            raise LiveReplicationError("La politique PRA a changé")
        run_id = con.execute(
            "INSERT INTO pra_live_runs(protected_vm_id,status,actor,created_at) "
            "VALUES (?,?,?,?)", (vm_id, "queued", actor, db.now())
        ).lastrowid
    with _LOCK:
        _WORKERS.add(run_id)
    threading.Thread(target=_perform, args=(run_id,), daemon=True,
                     name="pra-live-" + str(run_id)).start()
    db.audit(actor, "pra.live.replication.request", vm_id, "run=" + str(run_id))
    return {"run_id": run_id, "status": "queued",
            "source_stopped": False, "target_started": False}



def reconcile(vm_id, run_id, actor, approved=False):
    """Reconcile only after verifying no source QMP backup is active."""
    if approved is not True:
        raise LiveReplicationError("Validation explicite nécessaire")
    vm = dr_protected_vms._one(vm_id)
    if vm["engine"] != "qmp_live_incremental":
        raise LiveReplicationError("Moteur QMP attendu")
    with db.session() as con:
        row = con.execute(
            "SELECT id,status FROM pra_live_runs WHERE protected_vm_id=? "
            "ORDER BY id DESC LIMIT 1", (vm_id,)).fetchone()
    if not row or row["id"] != run_id or row["status"] != "uncertain":
        raise LiveReplicationError("Seule la dernière réplication incertaine peut être réconciliée")
    info = _vm_runtime(vm)
    source_ssh = target_ssh = None
    try:
        source_ssh = _connect(info["source"])
        target_ssh = _connect(info["destination"])
        src = _run_helper(source_ssh, "source_probe",
                {"vmid": vm["source_vmid"], "slots": info["slots"]})
        jobs = src.get("active_jobs") or []
        if any(j.get("status") != "concluded" for j in jobs):
            raise LiveReplicationError("Job QMP encore actif : attendre sa fin")
        if jobs:
            _run_helper(source_ssh, "source_dismiss",
                {"vmid": vm["source_vmid"], "slots": info["slots"]})
        target = _run_helper(target_ssh, "target_probe",
                 {"vmid": vm["target_vmid"], "storage": vm["target_storage"]})
        pending = target.get("pending")
        if pending:
            _run_helper(target_ssh, "target_abort",
                {"vmid": vm["target_vmid"], "storage": vm["target_storage"],
                 "token": pending["token"]}, timeout=120)
        with db.session() as con:
            con.execute("BEGIN IMMEDIATE")
            changed = con.execute(
                "UPDATE pra_live_runs SET status='failed',"
                "details='Réconcilié : checkpoint provisoire abandonné, prochaine copie FULL',"
                "finished_at=? WHERE id=? AND protected_vm_id=? AND status='uncertain'",
                (db.now(),run_id,vm_id)).rowcount
            if changed != 1:
                raise LiveReplicationError("État du job modifié pendant la réconciliation")
        db.audit(actor,"pra.live.reconcile",vm_id,"run="+str(run_id))
        return {"run_id":run_id,"status":"failed","force_full_next":True,
                "source_vm_stopped":False,"target_vm_started":False}
    finally:
        if source_ssh:
            source_ssh.close()
        if target_ssh:
            target_ssh.close()


def list_runs(vm_id, limit=30):
    dr_protected_vms._one(vm_id)
    limit = max(1, min(100, int(limit)))
    with db.session() as con:
        rows = con.execute(
            "SELECT id,status,actor,mode,details,created_at,started_at,finished_at,point_at,verification_level "
            "FROM pra_live_runs WHERE protected_vm_id=? ORDER BY id DESC LIMIT ?",
            (vm_id, limit)).fetchall()
    return [dict(r) for r in rows]


def latest_status(vm_id):
    with db.session() as con:
        row = con.execute(
            "SELECT * FROM pra_live_runs WHERE protected_vm_id=? ORDER BY id DESC LIMIT 1",
            (vm_id,)).fetchone()
        last_verified = con.execute(
            "SELECT COALESCE(point_at,finished_at) AS point_at FROM pra_live_runs "
            "WHERE protected_vm_id=? AND status='completed' "
            "AND verification_level='offline_structural' ORDER BY id DESC LIMIT 1",
            (vm_id,)).fetchone()
    if not row:
        return {"replication_status": "not_configured",
                "last_successful_sync": None,
                "offline_image_verified": False,
                "boot_verified": False,
                "failover_available": False}
    offline = row["status"] == "completed" and row["verification_level"] == "offline_structural"
    return {"replication_status": row["status"],
            "last_successful_sync": last_verified["point_at"] if last_verified else None,
            "offline_image_verified": offline,
            "boot_verified": False,
            "failover_available": False,
            "last_replication_run": {"id": row["id"], "mode": row["mode"],
                                     "details": row["details"], "status": row["status"],
                                     "verification_level": row["verification_level"]}}


def tick():
    """Auto-schedule ONLY successfully seeded policies, never replay errors."""
    now = datetime.now(timezone.utc)
    with db.session() as con:
        rows = con.execute(
            "SELECT p.id,p.interval_minutes,r.status,r.finished_at FROM dr_protected_vms p "
            "JOIN pra_live_runs r ON r.id=(SELECT MAX(id) FROM pra_live_runs "
            "WHERE protected_vm_id=p.id) "
            "WHERE p.engine='qmp_live_incremental' AND p.state='planned' "
            "AND r.status='completed' AND r.verification_level='offline_structural'").fetchall()
    for row in rows:
        try:
            past = datetime.fromisoformat(row["finished_at"])
            if past + timedelta(minutes=row["interval_minutes"]) <= now:
                start(row["id"], "scheduler", scheduled=True)
        except (ValueError, TypeError, LiveReplicationError):
            continue


def _loop():
    while not STOP.wait(30):
        try:
            tick()
        except Exception:
            pass


def initialize():
    # Thread crashes / application restarts must not replay destructive work.
    with db.session() as con:
        con.execute("UPDATE pra_live_runs SET status='uncertain',"
                    "details='Serveur redémarré : vérifier les checkpoints sur les nœuds'"
                    " WHERE status IN ('queued','running')")
    STOP.clear()
    threading.Thread(target=_loop, daemon=True, name="pra-live-scheduler").start()
