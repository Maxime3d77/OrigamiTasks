#!/usr/bin/env python3
"""Explicit, fail-closed one-time consolidation of an old PRA QCOW2/LVM chain.

No automatic GC. Run only on the owning destination Proxmox node with the
LAB-only standby stopped. Original LVs survive preparation, comparison,
journal publication, and until an independent, explicitly approved prune.

This is a one-time migration tool, NOT the continual incremental engine.
"""
import argparse
import fcntl
import json
import os
import re
import socket
import stat
import subprocess
import time
import uuid
from pathlib import Path

from pra_lvm_target import PveLvmTarget, _atomic

MARKER = "DELETE-OLD-PRA-CHAIN"
SLOT = re.compile(r"^(?:scsi|virtio|sata|ide)[0-9]{1,2}$")
VOL = re.compile(r"^vm-[1-9][0-9]{2,8}-disk-[0-9]{1,18}\.qcow2$")


class ConsolidationError(RuntimeError):
    pass


def run(*argv, timeout=120):
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConsolidationError("Commande indisponible ou délai dépassé: " + argv[0]) from exc
    if result.returncode:
        raise ConsolidationError("Commande refusée: " + argv[0] + " (code " +
                                 str(result.returncode) + "): " + result.stderr[-500:])
    return result.stdout.strip()


def load(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 131072:
        raise ConsolidationError("Journal absent, lien symbolique ou taille anormale: " + str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConsolidationError("Journal illisible: " + str(path)) from exc
    if not isinstance(value, dict):
        raise ConsolidationError("Journal invalide")
    return value


def qm_config(vmid):
    cfg = {}
    for line in run("qm", "config", str(vmid)).splitlines():
        if ": " in line:
            k, val = line.split(": ", 1)
            cfg[k] = val
    return cfg


def volume(v):
    if not isinstance(v, str) or ":" not in v:
        raise ConsolidationError("Volid invalide")
    name = v.split(":", 1)[1]
    if not VOL.fullmatch(name):
        raise ConsolidationError("Nom de LV non autorisé: " + name)
    return name


def path_of(volid):
    name = volume(volid)
    name_seen = run("pvesm", "path", volid)
    if not name_seen.startswith("/dev/") or name not in name_seen:
        raise ConsolidationError("Chemin LV Proxmox non conforme: " + volid)
    return Path(name_seen)


def active_path(volid, vg, *, activate=False):
    candidate = path_of(volid)
    if not candidate.exists():
        if not activate:
            raise ConsolidationError("LV inactif: " + volid + " (aucune activation en mode plan)")
        # Proxmox deactivates stopped shared-LVM guests. Only activate locally
        # after verifying cluster VM ownership and every reference.
        run("lvchange", "-aly", vg + "/" + volume(volid))
    resolved = candidate.resolve(strict=True)
    if not str(resolved).startswith("/dev/") or not stat.S_ISBLK(resolved.stat().st_mode):
        raise ConsolidationError("Volume non bloc ou chemin inattendu: " + volid)
    return str(resolved)


def lv_exists(volid, vg):
    """Verify physical LV presence independently of a qm config reference."""
    try:
        result = subprocess.run(
            ("lvs", "--noheadings", "-o", "lv_name", vg + "/" + volume(volid)),
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConsolidationError("Inventaire LVM inaccessible après suppression") from exc
    if result.returncode == 0:
        if result.stdout.strip() != volume(volid):
            raise ConsolidationError("Inventaire LVM contradictoire")
        return True
    if result.returncode == 5 and not result.stdout.strip():
        return False
    raise ConsolidationError("Inventaire LVM non concluant après suppression")


def image_chain(path):
    try:
        rows = json.loads(run("qemu-img", "info", "-f", "qcow2", "--backing-chain",
                              "--output=json", path, timeout=900))
    except ValueError as exc:
        raise ConsolidationError("Chaîne QCOW2 illisible") from exc
    if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
        raise ConsolidationError("Chaîne QCOW2 manquante ou anormalement profonde")
    names = []
    for row in rows:
        if not isinstance(row, dict) or row.get("format") != "qcow2":
            raise ConsolidationError("Format QCOW2 inattendu")
        filename = row.get("filename")
        if not isinstance(filename, str) or not filename.startswith("/dev/"):
            raise ConsolidationError("Chemin backing QCOW2 suspect")
        resolved = Path(filename).resolve(strict=True)
        if not stat.S_ISBLK(resolved.stat().st_mode):
            raise ConsolidationError("Backing QCOW2 non bloc")
        names.append(str(resolved))
    if len(set(names)) != len(names):
        raise ConsolidationError("Boucle QCOW2")
    return names


class Maintenance:
    def __init__(self, vmid, storage, protection):
        self.vmid, self.storage, self.protection = vmid, storage, protection
        self.target = PveLvmTarget(storage, vmid, run, protected_vm_id=protection)
        self.root = self.target.journal
        self.state_file = self.root / "consolidation.json"
        self.current_file = self.root / "current.json"
        self.owned_file = self.root / "owned.json"
        self.pending_file = self.root / "pending.json"
        self.lock_file = self.root / ".checkpoint.lock"
        self.config_root = Path("/etc/pve/nodes")
        self.lock_fd = None

    def __enter__(self):
        if (not self.root.is_dir() or self.root.is_symlink()
                or not self.current_file.exists() or not self.owned_file.exists()):
            raise ConsolidationError("Journal PRA source absent ou non confirmé")
        self.lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)

    def inspect(self):
        if self.pending_file.exists() or self.pending_file.is_symlink():
            raise ConsolidationError("Génération en attente: réconciliation manuelle obligatoire")
        if any(p.name.startswith(".origami-") and p.name.endswith(".pid")
               for p in self.root.iterdir()):
            raise ConsolidationError("Fichiers PID NBD présents")
        meta = self.target.inspect()
        node = socket.gethostname().split(".", 1)[0]
        resources = json.loads(run("pvesh", "get", "/cluster/resources",
                                   "--type", "vm", "--output-format", "json"))
        owners = [r for r in resources if isinstance(r, dict)
                  and str(r.get("vmid")) == str(self.vmid)]
        if (len(owners) != 1 or owners[0].get("node") != node
                or owners[0].get("type") != "qemu"):
            raise ConsolidationError("Propriété du VMID non confirmée sur ce nœud")
        if run("qm", "status", str(self.vmid)) != "status: stopped":
            raise ConsolidationError("VM de secours non arrêtée")
        conf = qm_config(self.vmid)
        desc = set(re.findall(r"[A-Za-z0-9_]+", conf.get("description", "")))
        if (("ORIGAMITASKS_PRA_" + str(self.protection)) not in desc
                or "ORIGAMITASKS_LAB_ONLY" not in desc
                or conf.get("onboot", "0") not in ("0", "false")
                or any(re.fullmatch(r"net[0-9]{1,2}", x) for x in conf)):
            raise ConsolidationError("VM non identifiée, connectée au réseau ou démarrage automatique")
        snapshots = run("qm", "listsnapshot", str(self.vmid))
        if any("current" not in line for line in snapshots.splitlines() if line.strip()):
            raise ConsolidationError("Snapshots Proxmox présents ou état non vérifiable")
        return meta, conf

    def records(self):
        current, owned = load(self.current_file), load(self.owned_file)
        generations = owned.get("generations")
        if current.get("status") != "committed" or not isinstance(generations, list):
            raise ConsolidationError("Checkpoint non validé ou historique invalide")
        disks = current.get("disks")
        if not isinstance(disks, dict) or len(disks) != 1:
            raise ConsolidationError("Cette migration contrôlée exige une VM à un disque")
        slot = next(iter(disks))
        if not SLOT.fullmatch(slot):
            raise ConsolidationError("Slot incorrect")
        top = disks[slot].get("volid")
        if not isinstance(top, str) or not top.startswith(self.storage + ":"):
            raise ConsolidationError("Disque publié sur un stockage différent")
        old = []
        for gen in generations:
            vols = gen.get("volids") if isinstance(gen, dict) else None
            if not isinstance(vols, list) or len(vols) != 1:
                raise ConsolidationError("Génération multivolume non prise en charge")
            v = vols[0]
            if not isinstance(v, str) or not v.startswith(self.storage + ":"):
                raise ConsolidationError("Volume historique étranger")
            volume(v)
            old.append(v)
        if not old or len(set(old)) != len(old) or top != old[-1]:
            raise ConsolidationError("Historique non linéaire ou disque courant non journalisé")
        return current, owned, slot, top, old

    def references(self, volumes, slot, conf, *, prepared=False):
        refs = {}
        root = self.config_root
        if not root.is_dir():
            raise ConsolidationError("Inventaire cluster Proxmox indisponible")
        for directory in ("qemu-server", "lxc"):
            for fp in root.glob("*/" + directory + "/*.conf"):
                if not fp.is_file():
                    continue
                data = fp.read_text(encoding="utf-8")
                for v in volumes:
                    for line in data.splitlines():
                        # Exact volid, not a substring of a longer disk name.
                        if re.search(re.escape(v) + r"(?=,|$|\s)", line):
                            refs.setdefault(v, []).append((str(fp), line))
        expected_file = str(root / socket.gethostname().split(".", 1)[0] /
                            "qemu-server" / (str(self.vmid) + ".conf"))
        if not Path(expected_file).exists():
            raise ConsolidationError("Config VM cluster introuvable")
        for v in volumes:
            actual = refs.get(v, [])
            if len(actual) != 1 or actual[0][0] != expected_file:
                raise ConsolidationError("Référence étrangère ou multiple du volume " + v)
            key = actual[0][1].split(":", 1)[0].strip()
            if key != slot and not re.fullmatch(r"unused[0-9]+", key):
                raise ConsolidationError("Référence Proxmox inattendue du volume " + v)
            if prepared and not re.fullmatch(r"unused[0-9]+", key):
                raise ConsolidationError("Ancien disque encore actif")
        return {v: refs[v][0][1].split(":", 1)[0] for v in volumes}

    def plan(self):
        meta, conf = self.inspect()
        current, owned, slot, top, old = self.records()
        if self.state_file.exists():
            raise ConsolidationError("Consolidation déjà commencée; utiliser inspect-state ou reprendre selon le journal")
        if conf.get(slot, "").split(",", 1)[0] != top:
            raise ConsolidationError("Le disque actif n'est pas le checkpoint déclaré")
        refs = self.references(old, slot, conf)
        status = {}
        for v in old:
            status[v] = path_of(v).exists()
        return {"vmid": self.vmid, "storage": self.storage, "slot": slot,
                "generation_count": len(old), "current": top,
                "old_volumes": [{"volid": v, "vm_reference": refs[v],
                                 "active_on_node": status[v]} for v in old],
                "estimated_new_lv_bytes": current["disks"][slot]["size"],
                "requires_one_time_full_local_consolidation": True,
                "destructive_cleanup_performed": False}

    def prepare(self):
        meta, conf = self.inspect()
        current, owned, slot, top, old = self.records()
        if self.state_file.exists() or self.state_file.is_symlink():
            raise ConsolidationError("Journal de consolidation déjà présent: ne pas réallouer")
        if conf.get(slot, "").split(",", 1)[0] != top:
            raise ConsolidationError("Le disque de secours ne correspond plus au checkpoint")
        self.references(old, slot, conf)
        vg = meta["vgname"]
        paths = {v: active_path(v, vg, activate=True) for v in old}
        chain = image_chain(paths[top])
        allowed = {str(Path(p).resolve(strict=True)) for p in paths.values()}
        if (not set(chain).issubset(allowed) or chain[0] != paths[top]
                or chain != [paths[v] for v in reversed(old[-len(chain):])]):
            raise ConsolidationError("Chaîne QCOW2 incohérente avec l’ordre historique des LV")
        virtual = current["disks"][slot].get("size")
        if type(virtual) is not int or virtual < 1024:
            raise ConsolidationError("Taille virtuelle absente")
        # Exactly one new thick LV. It does not become the current disk until
        # full conversion, no backing dependencies, qemu-img check and compare.
        self.target.plan({slot: virtual}, "full")
        token = uuid.uuid4().hex
        name = "vm-" + str(self.vmid) + "-disk-" + str(int(token[:12], 16)) + ".qcow2"
        fresh = self.storage + ":" + name
        record = {"status": "allocating", "token": token, "slot": slot,
                  "old_current": top, "old_volumes": old, "fresh": fresh,
                  "previous": current, "created_at": int(time.time()),
                  "verified": False, "pruned": []}
        _atomic(self.state_file, record)
        run("pvesm", "alloc", self.storage, str(self.vmid), name,
            str((virtual + max(512 * 1024 * 1024, virtual // 20) + 1023) // 1024),
            "--format", "qcow2", timeout=300)
        record["status"] = "allocated"
        _atomic(self.state_file, record)
        destination = active_path(fresh, vg, activate=True)
        run("qemu-img", "create", "-f", "qcow2", destination, str(virtual), timeout=900)
        record["status"] = "copying"
        _atomic(self.state_file, record)
        run("qemu-img", "convert", "-f", "qcow2", "-O", "qcow2", "-n",
            paths[top], destination, timeout=86400)
        if image_chain(destination) != [destination]:
            raise ConsolidationError("La nouvelle image contient encore un backing QCOW2")
        run("qemu-img", "check", "-f", "qcow2", destination, timeout=3600)
        # Compare the entire guest-visible content once. No per-cycle FULL.
        run("qemu-img", "compare", "-f", "qcow2", "-F", "qcow2",
            paths[top], destination, timeout=86400)
        record["status"] = "verified"
        record["verified"] = True
        _atomic(self.state_file, record)
        self.inspect()
        if qm_config(self.vmid).get(slot, "").split(",", 1)[0] != top:
            raise ConsolidationError("Destination VM configuration changed during copy")
        run("qm", "set", str(self.vmid), "--" + slot, fresh, timeout=300)
        record["status"] = "attached"
        _atomic(self.state_file, record)
        self._commit_prepared(record, current, owned, slot, fresh)
        return {"status": "consolidated", "new_current": fresh,
                "old_volumes_preserved": len(old), "prune_requires_confirmation": True}

    def _commit_prepared(self, record, current, owned, slot, fresh):
        _, conf = self.inspect()
        if conf.get(slot, "").split(",", 1)[0] != fresh:
            raise ConsolidationError("Nouveau disque non attaché")
        self.references(record["old_volumes"], slot, conf, prepared=True)
        # The source may have a stale/consumed QMP bitmap after an uncertain
        # run. Require explicit FULL reseed; never silently resume incrementals.
        new_current = dict(current)
        new_current["token"] = record["token"]
        new_current["mode"] = "consolidated"
        new_current["depth"] = 1
        new_current["previous_token"] = current.get("token")
        new_current["disks"] = {slot: dict(current["disks"][slot], volid=fresh)}
        new_current["requires_full_reseed"] = True
        new_current["committed_at"] = int(time.time())
        archive = self.root / ("before-consolidation-" + record["token"] + ".json")
        if not archive.exists():
            _atomic(archive, {"current": current, "owned": owned})
        # Retain all historical ownership until explicit physical prune.
        new_owned = dict(owned)
        if not any(fresh in g.get("volids", []) for g in new_owned["generations"]):
            new_owned["generations"] = new_owned["generations"] + [{
                "token": record["token"], "volids": [fresh],
                "created_at": record["created_at"]}]
        _atomic(self.owned_file, new_owned)
        _atomic(self.current_file, new_current)
        record["status"] = "consolidated"
        _atomic(self.state_file, record)

    def prune(self, confirmation):
        if confirmation != MARKER:
            raise ConsolidationError("Confirmation de destruction incorrecte")
        _, conf = self.inspect()
        if not self.state_file.exists():
            raise ConsolidationError("Aucune consolidation vérifiée")
        record = load(self.state_file)
        if record.get("status") not in ("consolidated", "pruning"):
            raise ConsolidationError("Copie autonome non validée; suppression refusée")
        if record.get("verified") is not True:
            raise ConsolidationError("Comparaison QCOW2 non validée")
        current = load(self.current_file)
        fresh = record["fresh"]
        slot = record["slot"]
        if current.get("disks", {}).get(slot, {}).get("volid") != fresh:
            raise ConsolidationError("Disque actuel différent de la copie validée")
        if conf.get(slot, "").split(",", 1)[0] != fresh:
            raise ConsolidationError("Disque Proxmox différent de la copie validée")
        vg = self.target.inspect()["vgname"]
        new_path = active_path(fresh, vg, activate=True)
        if image_chain(new_path) != [new_path]:
            raise ConsolidationError("Le disque actif a un backing: suppression refusée")
        run("qemu-img", "check", "-f", "qcow2", new_path, timeout=3600)
        all_old = record["old_volumes"]
        if not isinstance(all_old, list) or not all_old or len(set(all_old)) != len(all_old):
            raise ConsolidationError("Liste d'anciens volumes invalide")
        # Work from newest to oldest. A missing physical LV is accepted only
        # if it was previously recorded as pruned by this exact journal.
        pruned = list(record.get("pruned", []))
        for volid in reversed(all_old):
            if volid in pruned:
                continue
            self.inspect()
            conf = qm_config(self.vmid)
            if conf.get(slot, "").split(",", 1)[0] != fresh:
                raise ConsolidationError("Le disque actif a changé durant la suppression")
            # If the previous attempt crashed after the physical unlink but
            # before its journal update, resume only after proving no config
            # reference AND no LV physically exists.
            attached = [key for key, val in conf.items()
                        if val.split(",", 1)[0] == volid]
            if not attached:
                if lv_exists(volid, vg):
                    raise ConsolidationError("Volume sans référence encore présent: nettoyage manuel requis")
            else:
                refs = self.references([volid], slot, conf, prepared=True)
                key = refs[volid]
                if not re.fullmatch(r"unused[0-9]+", key):
                    raise ConsolidationError("Ancien disque non unused")
                # Proxmox owns physical deletion, not raw lvremove. 'unusedN'
                # unlink ALWAYS removes underlying storage: explicit marker above.
                record["status"] = "pruning"
                _atomic(self.state_file, record)
                run("qm", "disk", "unlink", str(self.vmid), "--idlist", key, timeout=900)
                post = qm_config(self.vmid)
                if any(volid in val for val in post.values()):
                    raise ConsolidationError("Proxmox conserve une référence à l'ancien volume")
                if lv_exists(volid, vg):
                    raise ConsolidationError("Volume toujours présent dans LVM après unlink Proxmox")
            pruned.append(volid)
            record["pruned"] = pruned
            _atomic(self.state_file, record)
        new_owned = load(self.owned_file)
        new_owned["generations"] = [{
            "token": record["token"], "volids": [fresh],
            "created_at": record["created_at"]}]
        _atomic(self.owned_file, new_owned)
        record["status"] = "done"
        _atomic(self.state_file, record)
        return {"status": "done", "new_current": fresh,
                "removed_old_volumes": len(pruned), "generation_count": 1,
                "requires_full_reseed": True}

    def report_state(self):
        record = load(self.state_file) if self.state_file.exists() else None
        return {"vmid": self.vmid, "state": record.get("status") if record else "none",
                "record": record}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "prepare", "prune", "state"))
    parser.add_argument("--vmid", type=int, required=True)
    parser.add_argument("--storage", required=True)
    parser.add_argument("--protected-id", type=int, required=True)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("exécution root obligatoire sur le nœud Proxmox destination")
    try:
        with Maintenance(args.vmid, args.storage, args.protected_id) as m:
            if args.action == "plan":
                result = m.plan()
            elif args.action == "prepare":
                result = m.prepare()
            elif args.action == "prune":
                result = m.prune(args.confirm)
            else:
                result = m.report_state()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ConsolidationError, ValueError, KeyError, OSError) as exc:
        parser.exit(2, "REFUS SECURISE: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
