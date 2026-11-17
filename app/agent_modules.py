"""Single OrigamiTasks agent installation with remotely managed module roles.

Only the standard agent currently executes work. The Proxmox role is metadata
for the central API inventory; PRA has a strictly passive local watchdog.
There is intentionally no promotion, replication or fencing command here.
"""
import json
from fastapi import HTTPException
from . import db

DEFAULT = {"standard": True, "proxmox": False, "pra": False}
REPORTED = {"standard", "proxmox", "pra"}
STATUSES = {"ready", "configured", "disabled", "passive", "stale", "not_installed", "unsupported", "error"}


def parse(value):
    if not value:
        return dict(DEFAULT)
    try:
        result = json.loads(value) if isinstance(value,str) else dict(value)
    except (ValueError, TypeError):
        return dict(DEFAULT)
    return {key: bool(result.get(key, DEFAULT[key])) for key in DEFAULT}


def _site(con, agent_id):
    row=con.execute(
        "SELECT s.id,s.name,s.role,s.storage_profile FROM dr_site_agents link "
        "JOIN dr_sites s ON s.id=link.site_id WHERE link.agent_id=?",
        (agent_id,)
    ).fetchone()
    if not row:
        return None
    site=dict(row)
    site["clusters"]=[
        {"id":r["id"],"name":r["name"],"url":r["url"]}
        for r in con.execute(
            "SELECT d.id,d.name,d.url FROM datacenters d "
            "JOIN dr_site_datacenters link ON link.datacenter_id=d.id "
            "WHERE link.site_id=? ORDER BY d.name", (site["id"],)
        )
    ]
    return site


def set_modules(agent_id, payload):
    if (not isinstance(payload,dict) or set(payload)!={"standard","proxmox","pra"}
            or any(type(v) is not bool for v in payload.values())
            or payload["standard"] is not True):
        raise HTTPException(422,"Modules invalides : Standard doit rester actif")
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        agent=con.execute(
            "SELECT id,platform,enrolled,revoked,uninstalled_at,modules_json,modules_revision "
            "FROM agents WHERE id=?",(agent_id,)
        ).fetchone()
        if not agent or agent["revoked"] or agent["uninstalled_at"]:
            raise HTTPException(404,"Agent absent, révoqué ou désinstallé")
        if not agent["enrolled"]:
            raise HTTPException(422,"Attendre l'enrôlement avant d'activer les modules")
        site=_site(con,agent_id)
        if payload["proxmox"] or payload["pra"]:
            if site is None or not site["clusters"]:
                raise HTTPException(422,
                    "Associer d'abord cet agent à un site PRA contenant un cluster Proxmox")
        if payload["pra"]:
            if agent["platform"]!="linux":
                raise HTTPException(422,"Le superviseur PRA passif n'est disponible que sur Linux")
            if site["role"] not in ("primary","standby"):
                raise HTTPException(422,"Le site PRA doit avoir le rôle principal ou secours")
        old=parse(agent["modules_json"])
        revision=agent["modules_revision"]
        if old!=payload:
            revision+=1
            con.execute(
                "UPDATE agents SET modules_json=?,modules_revision=? WHERE id=?",
                (json.dumps(payload,separators=(",",":"),sort_keys=True),revision,agent_id)
            )
    return {"agent_id":agent_id,"modules":payload,"revision":revision,
            "status":"pending_agent_ack" if old!=payload else "unchanged"}


def bundle(agent_id, con=None):
    if con is None:
        with db.session() as session:
            return bundle(agent_id,session)
    agent=con.execute(
        "SELECT modules_json,modules_revision,platform FROM agents WHERE id=?",
        (agent_id,)
    ).fetchone()
    if not agent:
        raise HTTPException(404,"Agent introuvable")
    desired=parse(agent["modules_json"])
    site=_site(con,agent_id)
    return {
        "modules":desired,
        "modules_revision":int(agent["modules_revision"]),
        "site":site,
        # Static safety limits cannot be overridden by a checkbox/config.
        "pra_policy":{"replication_enabled":False,"promotion_enabled":False,
                      "fencing_ready":False,"witness_ready":False}
    }


def report(agent_id, body, con):
    if "modules_applied_revision" not in body and "modules_status" not in body:
        return
    revision=body.get("modules_applied_revision")
    statuses=body.get("modules_status")
    if (type(revision) is not int or revision<0 or not isinstance(statuses,dict)
            or set(statuses)-REPORTED or
            any(type(value) is not str or value not in STATUSES for value in statuses.values())):
        raise HTTPException(422,"Statut des modules invalide")
    current=con.execute(
        "SELECT modules_revision,modules_applied_revision FROM agents WHERE id=?",
        (agent_id,)
    ).fetchone()
    if revision>current["modules_revision"] or revision<current["modules_applied_revision"]:
        raise HTTPException(422,"Version de politique des modules incohérente")
    # The agent reports local runtime state, never authorizes a VM promotion.
    con.execute(
        "UPDATE agents SET modules_applied_revision=?,modules_reported_json=? WHERE id=?",
        (revision,json.dumps(statuses,separators=(",",":")),agent_id)
    )


def describe(agent):
    row=dict(agent)
    row["modules"]=parse(row.pop("modules_json",None))
    try:
        row["modules_status"]=json.loads(row.pop("modules_reported_json","{}") or "{}")
    except (ValueError,TypeError):
        row["modules_status"]={}
    row["modules_in_sync"]=(
        row.get("modules_applied_revision",0)==row.get("modules_revision",1)
        and row.get("status")=="online"
    )
    return row
