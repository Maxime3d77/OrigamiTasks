"""Named Proxmox DR site inventory and safe, read-only disaster scenarios.

This module NEVER starts/stops VMs, attempts storage operations, or grants
autonomous agent permissions. Current agents depend on OrigamiTasks for jobs.
"""
import json
import re

from . import agents, db

ROLES = {"primary", "standby", "other"}
STORAGE_PROFILES = {"auto", "iscsi_lvm", "zfs", "ceph", "pbs", "other"}
SCENARIOS = {"cluster_down", "network_partition", "console_down"}


class DRSiteError(ValueError):
    pass


def _id_list(data, maximum, label):
    if not isinstance(data, list) or len(data) > maximum:
        raise DRSiteError(label+": liste invalide")
    if any(type(x) is not int or x <= 0 for x in data) or len(set(data)) != len(data):
        raise DRSiteError(label+": identifiants invalides ou doublons")
    return data


def _validated(payload):
    if not isinstance(payload, dict) or set(payload)-{
        "name", "role", "storage_profile", "datacenter_ids", "agent_ids", "plan_id"
    }:
        raise DRSiteError("Configuration du site PRA invalide")
    name = str(payload.get("name") or "").strip()
    if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
        raise DRSiteError("Nom du site PRA invalide (1 à 80 caractères)")
    role = str(payload.get("role") or "other")
    storage = str(payload.get("storage_profile") or "auto")
    if role not in ROLES or storage not in STORAGE_PROFILES:
        raise DRSiteError("Rôle du site ou profil de stockage invalide")
    dcids = _id_list(payload.get("datacenter_ids", []), 20, "Clusters Proxmox")
    agentids = _id_list(payload.get("agent_ids", []), 40, "Agents")
    if not dcids:
        raise DRSiteError("Associer au moins un cluster Proxmox déclaré")
    plan_id=payload.get("plan_id")
    if plan_id is not None and (type(plan_id) is not int or plan_id<=0):
        raise DRSiteError("Groupe PRA invalide")
    return name,role,storage,dcids,agentids,plan_id


def list_sites():
    with db.session() as con:
        sites = [dict(s) for s in con.execute(
            "SELECT id,plan_id,name,role,storage_profile,created_at,updated_at FROM dr_sites ORDER BY name"
        )]
        for site in sites:
            site["datacenters"]=[dict(r) for r in con.execute(
                "SELECT dc.id,dc.name,dc.url,dc.kind FROM dr_site_datacenters link "
                "JOIN datacenters dc ON dc.id=link.datacenter_id "
                "WHERE link.site_id=? ORDER BY dc.name",(site["id"],)
            )]
            site["agents"]=[dict(r) for r in con.execute(
                "SELECT ag.id,ag.name,ag.hostname,ag.platform,ag.version,"
                "ag.last_seen,ag.enrolled,ag.revoked FROM dr_site_agents link "
                "JOIN agents ag ON ag.id=link.agent_id "
                "WHERE link.site_id=? ORDER BY ag.name",(site["id"],)
            )]
            cutoff=agents.utc_plus(-90)
            for ag in site["agents"]:
                ag["status"]=("revoked" if ag["revoked"] else "pending" if not ag["enrolled"] else
                              "online" if ag["last_seen"] and ag["last_seen"] >= cutoff else "offline")
                ag["pra_autonomous"]=False
    return sites


def save_site(payload, existing_id=None):
    name,role,storage,dcids,agentids,plan_id=_validated(payload)
    with db.session() as con:
        if plan_id is not None and not con.execute("SELECT 1 FROM dr_plans WHERE id=?",(plan_id,)).fetchone():
            raise DRSiteError("Groupe PRA sélectionné introuvable")
        if existing_id is not None:
            old=con.execute("SELECT id,plan_id,role FROM dr_sites WHERE id=?",(existing_id,)).fetchone()
            if not old:
                raise DRSiteError("Site PRA introuvable")
            active=con.execute(
                "SELECT source_dc_id,target_dc_id FROM dr_protected_vms "
                "WHERE source_site_id=? OR target_site_id=?",
                (existing_id,existing_id)
            ).fetchall()
            if active:
                needed={r["source_dc_id"] if old["role"]=="primary" else
                        r["target_dc_id"] for r in active}
                if old["plan_id"]!=plan_id or old["role"]!=role or not needed.issubset(set(dcids)):
                    raise DRSiteError(
                        "Site utilisé par des VM protégées : conserver le groupe, "
                        "le rôle et les clusters concernés, ou retirer d'abord les protections."
                    )
        marks=",".join("?" for _ in dcids)
        existing_dc=con.execute(
            "SELECT id FROM datacenters WHERE id IN ("+marks+")",dcids
        ).fetchall()
        if len(existing_dc)!=len(dcids):
            raise DRSiteError("Un cluster Proxmox sélectionné n'existe plus")
        conflict=con.execute(
            "SELECT dc.name FROM dr_site_datacenters link JOIN datacenters dc "
            "ON dc.id=link.datacenter_id WHERE link.datacenter_id IN ("+marks+")"
            " AND link.site_id<>? LIMIT 1",(*dcids,existing_id or -1)
        ).fetchone()
        if conflict:
            raise DRSiteError("Cluster déjà associé à un autre site : "+conflict["name"][:80])
        if agentids:
            marks_agents=",".join("?" for _ in agentids)
            found=con.execute(
                "SELECT id,platform,revoked FROM agents WHERE id IN ("+marks_agents+")",agentids
            ).fetchall()
            if len(found)!=len(agentids) or any(a["platform"]!="linux" or a["revoked"] for a in found):
                raise DRSiteError("Choisir uniquement des agents Linux existants et non révoqués")
        try:
            if existing_id is None:
                cursor=con.execute(
                    "INSERT INTO dr_sites(name,role,storage_profile,plan_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?)",(name,role,storage,plan_id,db.now(),db.now())
                )
                site_id=cursor.lastrowid
            else:
                site_id=existing_id
                con.execute(
                    "UPDATE dr_sites SET name=?,role=?,storage_profile=?,plan_id=?,updated_at=? WHERE id=?",
                    (name,role,storage,plan_id,db.now(),site_id)
                )
            con.execute("DELETE FROM dr_site_datacenters WHERE site_id=?",(site_id,))
            con.execute("DELETE FROM dr_site_agents WHERE site_id=?",(site_id,))
            con.executemany(
                "INSERT INTO dr_site_datacenters(site_id,datacenter_id) VALUES(?,?)",
                [(site_id,i) for i in dcids]
            )
            con.executemany(
                "INSERT INTO dr_site_agents(site_id,agent_id) VALUES(?,?)",
                [(site_id,i) for i in agentids]
            )
        except db.sqlite3.IntegrityError:
            raise DRSiteError("Nom de site déjà utilisé ou association de cluster invalide") from None
    return site_id


def delete_site(site_id):
    with db.session() as con:
        row=con.execute("SELECT name FROM dr_sites WHERE id=?",(site_id,)).fetchone()
        if not row:
            raise DRSiteError("Site PRA introuvable")
        if con.execute(
            "SELECT 1 FROM dr_protected_vms "
            "WHERE source_site_id=? OR target_site_id=? LIMIT 1",
            (site_id,site_id)
        ).fetchone():
            raise DRSiteError("Site utilisé par des VM protégées : retirer les protections avant la suppression")
        con.execute("DELETE FROM dr_sites WHERE id=?",(site_id,))
    return row["name"]


def simulate(payload, actor):
    """An offline what-if simulation. Makes no Proxmox/agent/storage requests.

    All automatic promotion paths remain blocked until independent witness,
    real fencing, consistent replication and runtime ownership checks exist.
    """
    if not isinstance(payload, dict) or set(payload)-{"source_site_id","target_site_id","scenario"}:
        raise DRSiteError("Scénario de simulation invalide")
    try:
        source_id=payload["source_site_id"]
        target_id=payload["target_site_id"]
        if type(source_id) is not int or type(target_id) is not int or source_id==target_id:
            raise ValueError()
    except (KeyError, ValueError):
        raise DRSiteError("Choisir deux sites distincts") from None
    scenario=payload.get("scenario")
    if scenario not in SCENARIOS:
        raise DRSiteError("Type de simulation inconnu")
    site_map={s["id"]:s for s in list_sites()}
    if source_id not in site_map or target_id not in site_map:
        raise DRSiteError("Site source ou destination inconnu")
    source,target=site_map[source_id],site_map[target_id]
    if source["plan_id"] is None or target["plan_id"] is None:
        raise DRSiteError(
            "Simulation refusée : classer les deux sites dans un groupe PRA "
            "explicite avant tout test, pour éviter le mélange PP / PROD."
        )
    if source["plan_id"]!=target["plan_id"]:
        raise DRSiteError(
            "Simulation refusée : sites appartenant à deux groupes PRA différents (PP / PROD)."
        )
    with db.session() as con:
        selected_vms=[
            dict(r) for r in con.execute(
                "SELECT id,source_vmid,source_name,source_node,target_vmid,"
                "replica_name,target_node,target_storage,state,last_check_at "
                "FROM dr_protected_vms "
                "WHERE source_site_id=? AND target_site_id=? ORDER BY source_vmid",
                (source_id,target_id)
            )
        ]
    checks=[
        {"name":"Sites", "pass":source["role"]=="primary" and target["role"]=="standby",
         "detail":"Source principale et cible secours associées" if source["role"]=="primary" and target["role"]=="standby" else "Rôles primary/standby à corriger"},
        {"name":"Clusters Proxmox", "pass":bool(source["datacenters"] and target["datacenters"]),
         "detail":str(len(source["datacenters"]))+" cluster(s) source, "+str(len(target["datacenters"]))+" cible"},
        {"name":"Agents rattachés", "pass":False,
         "detail":"Les agents actuels restent dépendants de la console (polling HTTP), pas des agents PRA autonomes"},
        {"name":"Témoin indépendant", "pass":False,
         "detail":"Aucun témoin/epoch intersites opérationnel"},
        {"name":"Fencing source", "pass":False,
         "detail":"Aucune preuve de neutralisation de la VM source ; injoignable ne veut pas dire arrêtée"},
        {"name":"Réplication consistante", "pass":False,
         "detail":str(len(selected_vms))+" VM(s) sélectionnées ; aucune copie REP_ "
                  "cohérente ou point de restauration validé"},
        {"name":"Interdiction de redémarrage SD", "pass":False,
         "detail":"Aucun verrou HA/onboot et stockage opposable au site SD"},
    ]
    conclusions={
        "cluster_down":"Panne du cluster simulée : la perte de l'API ne prouve pas l'arrêt des VM.",
        "network_partition":"Partition réseau simulée : SD peut toujours écrire ; risque de split-brain.",
        "console_down":"Console simulée indisponible : les agents actuels ne peuvent pas arbitrer ni recevoir de nouvelles tâches.",
    }
    result={
        "mode":"simulation_read_only",
        "scenario":scenario,
        "source":{"id":source["id"],"name":source["name"]},
        "target":{"id":target["id"],"name":target["name"]},
        "conclusion":conclusions[scenario],
        "decision":"BLOCKED",
        "would_start_replica":False,
        "would_stop_source":False,
        "would_modify_infrastructure":False,
        "protected_vms":[dict(vm,replica_verified=False,
                              last_successful_sync=None,
                              failover_ready=False) for vm in selected_vms],
        "checks":checks,
        "next_step":"Installer et vérifier des agents PRA autonomes, témoin, fencing et copie consistante avant une bascule réelle.",
    }
    with db.session() as con:
        cur=con.execute(
            "INSERT INTO dr_simulations(source_site_id,target_site_id,scenario,outcome,result_json,actor,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (source_id,target_id,scenario,"BLOCKED",json.dumps(result,ensure_ascii=False),actor,db.now())
        )
        result["simulation_id"]=cur.lastrowid
    return result


def history(limit=25):
    with db.session() as con:
        rows=con.execute("SELECT id,scenario,outcome,actor,created_at,"
                         "result_json FROM dr_simulations ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
    return [{"id":r["id"],"scenario":r["scenario"],"outcome":r["outcome"],
             "actor":r["actor"],"created_at":r["created_at"],
             "result":json.loads(r["result_json"])} for r in rows]
