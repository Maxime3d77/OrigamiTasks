"""Protected Proxmox VM inventory and strictly read-only DR rehearsals.

There is intentionally NO disk-copy, restore, remote-migrate, VM start/stop,
HA reconfiguration or fencing code in this module. PVE API operations are GET
only. Local "protected" records are plans, not successfully replicated VMs.

Native pvesr is intra-cluster ZFS replication, NOT cross-cluster iSCSI/LVM.
A future transport must first establish write consistency, isolated target
storage, source fencing, witness ownership and rollback semantics.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import db, dr_sites, integrations

STRATEGIES = {
    "planning_only": "Étude / simulation uniquement",
    "offline_copy": "Copie à froid (transport à développer)",
    "pbs_backup_restore": "Sauvegarde PBS + restauration (à intégrer)",
    "storage_array_replication": "Réplication baie / LUN (à qualifier)",
}
SCENARIOS = {
    "replication_preflight": "Prévol de réplication (lecture seule)",
    "failover_simulation": "Bascule simulée (sans démarrer de VM)",
    "failback_simulation": "Retour vers le site principal (simulation)",
    "network_partition": "Coupure réseau intersites (simulation)",
    "console_down": "Console indisponible (simulation)",
}
MAX_VM_ID = 999999999


class ProtectedVMError(ValueError):
    pass


def _positive(value, label):
    if type(value) is not int or not 1 <= value <= MAX_VM_ID:
        raise ProtectedVMError(label+" invalide")
    return value


def _sites(plan_id, source_dc_id, target_dc_id):
    with db.session() as con:
        rows=con.execute(
            "SELECT s.id AS site_id,s.plan_id,s.role,d.datacenter_id "
            "FROM dr_sites s JOIN dr_site_datacenters d ON d.site_id=s.id "
            "WHERE d.datacenter_id IN (?,?)",
            (source_dc_id,target_dc_id)
        ).fetchall()
    dc={r["datacenter_id"]:dict(r) for r in rows}
    source=dc.get(source_dc_id)
    target=dc.get(target_dc_id)
    if (not source or not target or source["plan_id"]!=plan_id or
            target["plan_id"]!=plan_id or source["site_id"]==target["site_id"] or
            source["role"]!="primary" or target["role"]!="standby"):
        raise ProtectedVMError(
            "Choisir un cluster principal et un cluster secours de deux sites "
            "différents appartenant au même groupe PRA."
        )
    return source["site_id"],target["site_id"]


def _inventory(dc_id):
    from .executor import credential
    dc=integrations.configured_datacenter(dc_id)
    cred=credential(dc["credential_id"])
    cred=dict(cred,ca_cert=dc.get("ca_cert") or cred.get("ca_cert") or "",
              skip_tls_verification=bool(dc.get("skip_tls_verification",False)))
    resources=integrations.discover_datacenter(dc,cred,timeout=18)
    return dc,cred,resources


def _storages(dc, cred):
    items=integrations.proxmox_request(dc["url"],cred,"/api2/json/storage",18)
    if not isinstance(items,list):
        raise ProtectedVMError("Liste des stockages Proxmox indisponible")
    result=[]
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get("storage"),str):
            continue
        name=item["storage"]
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}",name):
            continue
        contents=str(item.get("content") or "")
        result.append({"storage":name,"type":str(item.get("type") or "unknown"),
                       "shared":bool(item.get("shared",False)),
                       "images":"images" in contents.split(","),
                       "content":contents})
    return result


def options(plan_id, source_dc_id, target_dc_id):
    plan_id=_positive(plan_id,"Groupe PRA")
    source_dc_id=_positive(source_dc_id,"Cluster source")
    target_dc_id=_positive(target_dc_id,"Cluster cible")
    if source_dc_id==target_dc_id:
        raise ProtectedVMError("La source et la destination doivent être distinctes")
    source_site_id,target_site_id=_sites(plan_id,source_dc_id,target_dc_id)
    # discover_datacenter uses the cluster-wide /cluster/resources endpoint,
    # not /nodes/{node}; count excluded containers for troubleshooting.
    source_dc,_,source_resources=_inventory(source_dc_id)
    target_dc,target_cred,target_resources=_inventory(target_dc_id)
    vms=[{"vmid":r["vmid"],"name":r.get("name") or ("VM "+str(r["vmid"])),
          "node":r.get("node") or "","status":r.get("status") or "unknown"}
         for r in source_resources if r.get("type")=="qemu" and
         type(r.get("vmid")) is int]
    vms=sorted(vms,key=lambda x:x["vmid"])
    nodes=[{"name":r.get("node") or r.get("name") or "",
            "status":r.get("status") or "unknown"}
           for r in target_resources if r.get("type")=="node"]
    used=sorted({r["vmid"] for r in target_resources
                 if r.get("type") in ("qemu","lxc") and type(r.get("vmid")) is int})
    return {"plan_id":plan_id,"source_dc_id":source_dc_id,"target_dc_id":target_dc_id,
            "source_site_id":source_site_id,"target_site_id":target_site_id,
            "source_name":source_dc["name"],"target_name":target_dc["name"],
            "vms":vms,"source_vm_total":len(vms),
            "source_lxc_excluded":sum(r.get("type")=="lxc" for r in source_resources),
            "target_nodes":nodes,"target_vmids_in_use":used,
            "target_storages":_storages(target_dc,target_cred),
            "note":"Découverte API en lecture seule ; pas de copie ni de synchronisation."}


def _payload(data):
    if (not isinstance(data,dict) or set(data)-{
        "plan_id","source_dc_id","target_dc_id","source_vmid","target_vmid",
        "target_node","target_storage","interval_minutes","engine"}):
        raise ProtectedVMError("Paramètres de protection VM invalides")
    plan_id=_positive(data.get("plan_id"),"Groupe PRA")
    source_dc_id=_positive(data.get("source_dc_id"),"Cluster source")
    target_dc_id=_positive(data.get("target_dc_id"),"Cluster destination")
    vmid=_positive(data.get("source_vmid"),"VMID source")
    target_vmid=_positive(data.get("target_vmid"),"VMID cible")
    interval=data.get("interval_minutes",60)
    if type(interval) is not int or not 5<=interval<=10080:
        raise ProtectedVMError("Période indicative : 5 à 10080 minutes")
    engine=data.get("engine","planning_only")
    if engine not in STRATEGIES:
        raise ProtectedVMError("Stratégie de copie inconnue")
    node=str(data.get("target_node") or "").strip()
    storage=str(data.get("target_storage") or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}",node):
        raise ProtectedVMError("Nœud cible invalide")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}",storage):
        raise ProtectedVMError("Stockage cible invalide")
    return plan_id,source_dc_id,target_dc_id,vmid,target_vmid,node,storage,interval,engine


def save(data, actor, existing_id=None):
    (plan_id,src,dst,vmid,target_vmid,node,storage,interval,engine)=_payload(data)
    if src==dst:
        raise ProtectedVMError("Impossible de protéger une VM sur le même cluster")
    source_site_id,target_site_id=_sites(plan_id,src,dst)
    discovered=options(plan_id,src,dst)
    matches=[x for x in discovered["vms"] if x["vmid"]==vmid]
    if len(matches)!=1:
        raise ProtectedVMError("La VM source n'existe plus dans le cluster")
    vm=matches[0]
    if target_vmid in discovered["target_vmids_in_use"]:
        raise ProtectedVMError("VMID cible déjà utilisé : choisir un ID libre")
    if not any(x["name"]==node and x["status"]=="online"
               for x in discovered["target_nodes"]):
        raise ProtectedVMError("Nœud cible non découvert ou hors ligne")
    if not any(x["storage"]==storage and x["images"]
               for x in discovered["target_storages"]):
        raise ProtectedVMError("Stockage cible absent ou non utilisable pour des images VM")
    name="REP_"+re.sub(r"[^a-zA-Z0-9_-]","-",str(vm["name"]))[:45]
    # Source disk metadata is descriptive; do not assume one independent LUN
    # per guest on shared iSCSI+LVM or a single-disk source.
    try:
        source_dc,source_cred,_=_inventory(src)
        assess=integrations.assess_vm_dr(source_dc,source_cred,vmid,timeout=18)
        disk_names=sorted({str(x.get("storage") or "") for x in assess["disks"]
                           if x.get("storage")})
    except Exception:
        raise ProtectedVMError("Impossible de vérifier les disques source en lecture seule") from None
    if not assess["disks"]:
        raise ProtectedVMError("Aucun disque VM source éligible identifié")
    source_storage=", ".join(disk_names)[:240]
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        # Recheck bindings after the potentially slow API lookups, before save.
        records=con.execute(
            "SELECT s.id AS site_id,s.plan_id,s.role,d.datacenter_id "
            "FROM dr_sites s JOIN dr_site_datacenters d ON d.site_id=s.id "
            "WHERE d.datacenter_id IN (?,?)",(src,dst)
        ).fetchall()
        mapped={r["datacenter_id"]:r for r in records}
        if (mapped.get(src) is None or mapped.get(dst) is None or
                mapped[src]["site_id"]!=source_site_id or
                mapped[dst]["site_id"]!=target_site_id or
                mapped[src]["plan_id"]!=plan_id or mapped[dst]["plan_id"]!=plan_id):
            raise ProtectedVMError("Association des clusters modifiée : recommencer")
        now=db.now()
        values=(plan_id,source_site_id,target_site_id,src,dst,vmid,
                str(vm["name"])[:120],str(vm["node"])[:100],
                target_vmid,node,storage,name,source_storage,engine,interval)
        try:
            if existing_id is None:
                cursor=con.execute(
                    "INSERT INTO dr_protected_vms("
                    "plan_id,source_site_id,target_site_id,source_dc_id,target_dc_id,"
                    "source_vmid,source_name,source_node,target_vmid,target_node,"
                    "target_storage,replica_name,source_storage,engine,interval_minutes,"
                    "created_by,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*values,actor,now,now)
                )
                vm_record_id=cursor.lastrowid
            else:
                old=con.execute(
                    "SELECT id FROM dr_protected_vms WHERE id=?",(existing_id,)
                ).fetchone()
                if not old:
                    raise ProtectedVMError("Protection VM introuvable")
                if con.execute(
                    "SELECT 1 FROM pra_pbs_restores WHERE protected_vm_id=?",(existing_id,)
                ).fetchone():
                    raise ProtectedVMError(
                        "Une copie de secours a été créée ou tentée : "
                        "modification du VMID/site/stockage bloquée tant que le suivi PRA est actif"
                    )
                con.execute(
                    "UPDATE dr_protected_vms SET plan_id=?,source_site_id=?,target_site_id=?,"
                    "source_dc_id=?,target_dc_id=?,source_vmid=?,source_name=?,source_node=?,"
                    "target_vmid=?,target_node=?,target_storage=?,replica_name=?,"
                    "source_storage=?,engine=?,interval_minutes=?,last_check_at=NULL,"
                    "last_check_json='{}',updated_at=? WHERE id=?",
                    (*values,now,existing_id)
                )
                vm_record_id=existing_id
        except db.sqlite3.IntegrityError:
            raise ProtectedVMError(
                "VM source déjà protégée ou VMID cible réservé par une autre protection"
            ) from None
    return {"id":vm_record_id,"status":"planned","replica_name":name,
            "disk_copy_performed":False,"vm_created":False}


def _one(vm_id):
    with db.session() as con:
        row=con.execute("SELECT * FROM dr_protected_vms WHERE id=?",(vm_id,)).fetchone()
    if not row:
        raise ProtectedVMError("VM protégée introuvable")
    return dict(row)


def list_vms(plan_id=None):
    with db.session() as con:
        query=(
            "SELECT p.*,pl.name AS plan_name,ss.name AS source_site_name,"
            "ts.name AS target_site_name,sc.name AS source_dc_name,"
            "tc.name AS target_dc_name FROM dr_protected_vms p "
            "JOIN dr_plans pl ON pl.id=p.plan_id "
            "JOIN dr_sites ss ON ss.id=p.source_site_id "
            "JOIN dr_sites ts ON ts.id=p.target_site_id "
            "JOIN datacenters sc ON sc.id=p.source_dc_id "
            "JOIN datacenters tc ON tc.id=p.target_dc_id"
        )
        if plan_id is not None:
            _positive(plan_id,"Groupe PRA")
            query+=" WHERE p.plan_id=?"
        query+=" ORDER BY pl.name,source_site_name,p.source_vmid"
        rows=con.execute(query,(plan_id,) if plan_id is not None else ()).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        try:
            item["last_check"]=json.loads(item.pop("last_check_json") or "{}")
        except (TypeError,ValueError):
            item["last_check"]={}
        with db.session() as inspect:
            last=inspect.execute(
                "SELECT status,finished_at FROM pra_pbs_restores "
                "WHERE protected_vm_id=? ORDER BY id DESC LIMIT 1",(item["id"],)
            ).fetchone()
        item["replication_status"]=(
            "restored_offline" if last and last["status"]=="completed"
            else last["status"] if last else "not_configured"
        )
        item["last_successful_sync"]=last["finished_at"] if last and last["status"]=="completed" else None
        # Requires independent fencing before a VM can ever be deemed ready to fail over.
        item["replica_verified"]=bool(last and last["status"]=="completed")
        item["failover_available"]=False
        with db.session() as sync:
            latest=sync.execute(
                "SELECT status,disks_total,disks_done,scanned_bytes,elapsed_seconds,"
                "error,started_at,finished_at FROM pra_direct_sync_runs "
                "WHERE protected_vm_id=? ORDER BY id DESC LIMIT 1",(item["id"],)
            ).fetchone()
            last_ok=sync.execute(
                "SELECT finished_at FROM pra_direct_sync_runs "
                "WHERE protected_vm_id=? AND status='success' "
                "ORDER BY id DESC LIMIT 1",(item["id"],)
            ).fetchone()
        item["incremental_sync"]=dict(latest) if latest else None
        item["last_incremental_success"]=last_ok["finished_at"] if last_ok else None
        result.append(item)
    return result


def set_state(vm_id, state):
    if state not in ("planned","paused"):
        raise ProtectedVMError("État demandé invalide")
    with db.session() as con:
        current=con.execute("SELECT state FROM dr_protected_vms WHERE id=?",(vm_id,)).fetchone()
        if not current:
            raise ProtectedVMError("Protection VM introuvable")
        con.execute(
            "UPDATE dr_protected_vms SET state=?,updated_at=? WHERE id=?",
            (state,db.now(),vm_id)
        )
    return {"id":vm_id,"state":state,"replication_active":False}


def delete(vm_id):
    with db.session() as con:
        row=con.execute("SELECT id FROM dr_protected_vms WHERE id=?",(vm_id,)).fetchone()
        if not row:
            raise ProtectedVMError("Protection VM introuvable")
        pending=con.execute(
            "SELECT 1 FROM pra_pbs_backups WHERE protected_vm_id=? "
            "AND status IN ('starting','submitted','running','uncertain')",(vm_id,)
        ).fetchone()
        if pending:
            raise ProtectedVMError("Sauvegarde PBS active ou indéterminée : contrôler la tâche avant suppression")
        if con.execute(
            "SELECT 1 FROM pra_direct_sync_runs WHERE protected_vm_id=?",(vm_id,)
        ).fetchone():
            raise ProtectedVMError("Synchronisations directes liées à la protection : conserver leur historique pour audit")
        existing=con.execute(
            "SELECT id FROM pra_pbs_restores WHERE protected_vm_id=?",(vm_id,)
        ).fetchone()
        if existing:
            raise ProtectedVMError(
                "Protection associée à une restauration PRA : retrait interdit "
                "pour éviter de déverrouiller un clone non vérifié. "
                "Contrôler et retirer la VM secours via une procédure de maintenance."
            )
        con.execute("DELETE FROM dr_protected_vms WHERE id=?",(vm_id,))
    return {"ok":True,"vm_config_removed":True,
            "vm_deleted":False,"disks_deleted":False}


def history(vm_id, limit=30):
    _one(vm_id)
    with db.session() as con:
        items=con.execute(
            "SELECT id,scenario,outcome,report_json,actor,created_at "
            "FROM dr_vm_tests WHERE protected_vm_id=? ORDER BY id DESC LIMIT ?",
            (vm_id,limit)
        ).fetchall()
    return [{"id":x["id"],"scenario":x["scenario"],"outcome":x["outcome"],
             "actor":x["actor"],"created_at":x["created_at"],
             "report":json.loads(x["report_json"])} for x in items]


def _probe(dc_id):
    try:
        dc,creds,resources=_inventory(dc_id)
        return (dc,creds,resources),None
    except Exception:
        # Never return URLs/credentials/errors from PVE HTTP clients to UI.
        return None,"API Proxmox injoignable ou certificat TLS non vérifiable"


def _agent_health(site_id):
    """Last received heartbeat only; never contact agents or execute commands."""
    cutoff=(datetime.now(timezone.utc)-timedelta(seconds=90)).isoformat(timespec="seconds")
    with db.session() as con:
        rows=con.execute(
            "SELECT a.id,a.enrolled,a.revoked,a.uninstalled_at,a.last_seen "
            "FROM agents a JOIN dr_site_agents l ON l.agent_id=a.id "
            "WHERE l.site_id=? AND a.platform='linux'",
            (site_id,)
        ).fetchall()
    return {"total":len(rows),"online":sum(bool(r["enrolled"] and
           not r["revoked"] and not r["uninstalled_at"] and
           r["last_seen"] and r["last_seen"]>=cutoff) for r in rows)}


def _node_storage(dc, credential, node, storage):
    """PVE node-level runtime storage status, not just cluster configuration."""
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}",node):
        return None
    rows=integrations.proxmox_request(
        dc["url"],credential,
        "/api2/json/nodes/"+quote(node,safe="")+"/storage",timeout=18
    )
    if not isinstance(rows,list):
        return None
    candidates=[r for r in rows if isinstance(r,dict) and r.get("storage")==storage]
    if len(candidates)!=1:
        return None
    r=candidates[0]
    return {"active":r.get("active") in (1,True),
            "available_bytes":r.get("avail") if type(r.get("avail")) is int else None,
            "total_bytes":r.get("total") if type(r.get("total")) is int else None}


def run_readonly_test(vm_id, scenario, actor):
    if scenario not in SCENARIOS:
        raise ProtectedVMError("Type de test PRA inconnu")
    vm=_one(vm_id)
    checks=[]
    def check(name, passed, detail, category="readiness"):
        checks.append({"name":name,"pass":bool(passed),
                       "detail":detail,"category":category})
    check("Périmètre PRA",True,"Deux clusters configurés sur des sites distincts du groupe")
    check("Protection active dans l'inventaire",vm["state"]=="planned",
          "Protection en préparation" if vm["state"]=="planned" else "Protection mise en pause")
    source_agents=_agent_health(vm["source_site_id"])
    target_agents=_agent_health(vm["target_site_id"])
    check("Agent Linux source en ligne",source_agents["online"]>0,
          str(source_agents["online"])+" / "+str(source_agents["total"])+
          " agent(s) Linux avec heartbeat récent sur le site principal")
    check("Agent Linux secours en ligne",target_agents["online"]>0,
          str(target_agents["online"])+" / "+str(target_agents["total"])+
          " agent(s) Linux avec heartbeat récent sur le site secours")
    source,source_error=_probe(vm["source_dc_id"])
    target,target_error=_probe(vm["target_dc_id"])
    source_vm=None
    if source:
        matches=[r for r in source[2] if r.get("type")=="qemu" and
                 r.get("vmid")==vm["source_vmid"]]
        source_vm=matches[0] if len(matches)==1 else None
    check("Source accessible",source is not None,
          "Lecture API source réussie" if source else source_error)
    check("VM source retrouvée",source_vm is not None,
          ("VM détectée sur "+str(source_vm.get("node"))+", état : "+
           str(source_vm.get("status"))) if source_vm else "VM source non observable")
    if source_vm:
        check("VM source stable pour copie à froid",
              source_vm.get("status")=="stopped",
              "Source arrêtée" if source_vm.get("status")=="stopped" else
              "Source non arrêtée : une copie à froid serait interdite")
    else:
        check("VM source stable pour copie à froid",False,
              "État source non vérifiable")
    target_vm_ids=set()
    target_nodes=[]
    if target:
        target_vm_ids={r["vmid"] for r in target[2] if r.get("type") in ("qemu","lxc")
                       and type(r.get("vmid")) is int}
        target_nodes=[r for r in target[2] if r.get("type")=="node"]
    check("Destination accessible",target is not None,
          "Lecture API destination réussie" if target else target_error)
    check("VMID cible disponible",target is not None and
          vm["target_vmid"] not in target_vm_ids,
          "ID libre pour une future copie" if target and vm["target_vmid"] not in target_vm_ids
          else "VMID déjà occupé ou destination inconnue : aucune modification autorisée")
    check("Nœud cible disponible",any(
          r.get("node")==vm["target_node"] and r.get("status")=="online"
          for r in target_nodes),
          "Nœud choisi : "+vm["target_node"])
    available_storage=False
    if target:
        try:
            stores=_storages(target[0],target[1])
            available_storage=any(
                r["storage"]==vm["target_storage"] and r["images"] for r in stores
            )
        except Exception:
            available_storage=False
    check("Stockage cible déclaré",available_storage,
          "Stockage VM "+vm["target_storage"]+
          (" défini dans Proxmox" if available_storage else " non vérifié ou indisponible"))
    node_store=None
    if target and available_storage:
        try:
            node_store=_node_storage(target[0],target[1],
                                     vm["target_node"],vm["target_storage"])
        except Exception:
            node_store=None
    check("Stockage accessible depuis le nœud cible",
          bool(node_store and node_store["active"]),
          ("Actif sur le nœud cible, espace indicatif libre : "+
           (str(node_store["available_bytes"])+" octets" if
            node_store["available_bytes"] is not None else "inconnu")) if
          node_store and node_store["active"] else
          "Statut runtime du stockage cible indisponible ou inactif")
    # Node free space may be insufficient for one or more guest disks.
    # This observation alone must never authorize disk writes.
    check("Capacité cible et mapping volumes validés",False,
          "Capacité, LUN, groupes de cohérence et tous les disques VM à vérifier ; "
          "les VGs iSCSI/LVM peuvent être partagés entre plusieurs VM")
    check("Superviseurs PRA autonomes et arbitrage en service",False,
          "Heartbeat d'agent standard ≠ supervision PRA autonome, et aucun "
          "témoin/fencing indépendant n'est opérationnel")
    check("Moteur de copie effectif",False,
          "Aucune copie froide, sauvegarde PBS ou synchronisation Pure n'est exécutée "
          "par OrigamiTasks. Choix actuel : "+STRATEGIES[vm["engine"]])
    check("Point de restauration vérifié",False,
          "Aucun snapshot cohérent lié à cette VM, aucune réplique REP_ validée")
    check("Témoin et epoch propriétaire",False,
          "Aucun témoin indépendant / bail propriétaire validé")
    check("Fencing source",False,
          "Une API source indisponible ne prouve jamais que la VM source est arrêtée")
    check("Anti-redémarrage source",False,
          "Aucun interverrouillage Proxmox HA/onboot / stockage actif opposable au site source")
    conclusion={
        "replication_preflight":
            "Test de faisabilité en lecture seule. Aucune donnée VM copiée.",
        "failover_simulation":
            "Bascule simulée : démarrage de REP_ interdit sans réplique cohérente, témoin et fencing.",
        "failback_simulation":
            "Retour simulé : impossible de rendre la propriété à la source sans "
            "arrêt certifié de la cible, reprise des écritures et validation du témoin.",
        "network_partition":
            "Partition réseau simulée : la VM source peut continuer à écrire. Split-brain possible.",
        "console_down":
            "Console indisponible simulée : le superviseur passif ne peut pas répliquer ni arbitrer.",
    }[scenario]
    report={
        "mode":"dry_run_no_infrastructure_writes","scenario":scenario,
        "protected_vm_id":vm_id,
        "vmid":vm["source_vmid"],"replica_name":vm["replica_name"],
        "source_dc_id":vm["source_dc_id"],"target_dc_id":vm["target_dc_id"],
        "source_site_id":vm["source_site_id"],"target_site_id":vm["target_site_id"],
        "source_status":source_vm.get("status") if source_vm else "unknown",
        "checks":checks,"decision":"BLOCKED",
        "conclusion":conclusion,
        "disk_copy_performed":False,"replica_created":False,
        "target_vm_started":False,"source_vm_stopped":False,
        "would_modify_infrastructure":False,
        "last_successful_sync":None,
        "next_step":"Développer un transport cohérent de réplication et prouver le fencing "
                    "source/contrôle de propriété avant de permettre une bascule."
    }
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        # No state transitions to "replicated" or "ready" from simulations.
        current=con.execute(
            "SELECT plan_id,source_dc_id,target_dc_id,state FROM dr_protected_vms WHERE id=?",
            (vm_id,)
        ).fetchone()
        if not current or any(current[key]!=vm[key] for key in (
            "plan_id","source_dc_id","target_dc_id","state"
        )):
            raise ProtectedVMError("La protection VM a été modifiée pendant le test")
        now=db.now()
        cur=con.execute(
            "INSERT INTO dr_vm_tests(protected_vm_id,scenario,outcome,report_json,actor,created_at)"
            "VALUES (?,?,?,?,?,?)",
            (vm_id,scenario,"BLOCKED",json.dumps(report,ensure_ascii=False),actor,now)
        )
        con.execute(
            "UPDATE dr_protected_vms SET last_check_at=?,last_check_json=? WHERE id=?",
            (now,json.dumps(report,ensure_ascii=False),vm_id)
        )
        report["test_id"]=cur.lastrowid
        report["checked_at"]=now
    return report
