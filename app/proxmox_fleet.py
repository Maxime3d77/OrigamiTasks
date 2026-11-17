"""Cluster-linked SSH inventory and explicit, reviewed multi-node agent rollout.

Read-only Proxmox discovery never deploys anything. Admin selects nodes and
confirms a dedicated managed SSH group; subsequent deployment is an explicitly
approved bulk SSH operation. No Proxmox API token is forwarded to nodes.

Runtime trust is checked again by the bulk worker: membership in a generated
group is not an authorization to execute on an arbitrary host.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from . import agents, agent_deployments, db, engine, integrations, ssh_inventory

MAX_CLUSTER_NODES = 100


class FleetError(ValueError):
    pass


def domain_for_dc(dc):
    hostname=(urlsplit(dc["url"]).hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(hostname)
        return ""
    except ValueError:
        pass
    labels=hostname.split(".")
    if len(labels)<3:
        return ""
    try:
        return ssh_inventory.fqdn("pve."+".".join(labels[1:]))[4:]
    except ssh_inventory.InventoryError:
        return ""


def _valid_suffix(dc, suffix):
    suffix=str(suffix or domain_for_dc(dc)).lower().strip().lstrip(".").rstrip(".")
    if not suffix:
        raise FleetError(
            "FQDN Proxmox en IP ou sans domaine : renseigner le suffixe DNS SSH des nœuds"
        )
    try:
        ssh_inventory.fqdn("pve."+suffix)
    except ssh_inventory.InventoryError:
        raise FleetError("Suffixe DNS SSH invalide") from None
    return suffix


def _node(name, suffix):
    name=str(name or "").lower().strip().rstrip(".")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,119}",name) or name.startswith("-"):
        raise FleetError("Nom de nœud renvoyé par Proxmox invalide")
    # Do not follow arbitrary FQDNs reported by a remote API across domains.
    if "." in name:
        if not name.endswith("."+suffix):
            raise FleetError(
                "Le FQDN d'un nœud Proxmox est hors du domaine SSH confirmé"
            )
        host=name
    else:
        host=name+"."+suffix
    try:
        return ssh_inventory.fqdn(host)
    except ssh_inventory.InventoryError:
        raise FleetError("FQDN du nœud Proxmox invalide") from None


def _discover(dc):
    from .executor import credential
    values=credential(dc["credential_id"])
    try:
        resources=integrations.discover_datacenter(dc, values, timeout=18)
    except integrations.IntegrationError as exc:
        raise FleetError(str(exc)) from None
    except Exception:
        raise FleetError("Découverte Proxmox indisponible : vérifier l'API et le certificat") from None
    if not isinstance(resources,list):
        raise FleetError("Inventaire des nœuds Proxmox indisponible")
    nodes=sorted({
        str(item.get("node") or item.get("name") or "").strip().lower()
        for item in resources if isinstance(item,dict) and item.get("type")=="node"
    })
    nodes=[node for node in nodes if node]
    if not nodes:
        raise FleetError("Aucun nœud découvert via l'API Proxmox")
    if len(nodes)>MAX_CLUSTER_NODES:
        raise FleetError("Plus de 100 nœuds : déploiement automatique non autorisé")
    return nodes


def _agent_index():
    with db.session() as con:
        rows=con.execute(
            "SELECT id,name,hostname,version,revoked,uninstalled_at,last_seen "
            "FROM agents WHERE platform='linux' AND revoked=0 AND uninstalled_at IS NULL"
        ).fetchall()
    agents_by_host={}
    for row in rows:
        item=dict(row)
        for target in (str(item["name"] or ""),str(item["hostname"] or "")):
            if target:
                agents_by_host.setdefault(target.lower().rstrip("."),item)
    return agents_by_host


def preview(dc_id, suffix=None):
    dc=integrations.configured_datacenter(dc_id)
    suffix=_valid_suffix(dc,suffix)
    discovered=_discover(dc)
    known=_agent_index()
    try:
        mapping=[{"node":node,"fqdn":_node(node,suffix),"port":22} for node in discovered]
    except FleetError:
        raise
    with db.session() as con:
        record=con.execute(
            "SELECT group_id FROM proxmox_ssh_groups WHERE datacenter_id=?",(dc["id"],)
        ).fetchone()
        group_id=record["group_id"] if record else None
        linked={
            row["node_name"]:row["server_id"] for row in con.execute(
                "SELECT node_name,server_id FROM proxmox_ssh_nodes WHERE datacenter_id=?",
                (dc["id"],)
            )
        }
    for entry in mapping:
        item=known.get(entry["fqdn"]) or known.get(entry["node"])
        entry["agent_id"]=item["id"] if item else None
        entry["agent_version"]=item["version"] if item else None
        entry["server_id"]=linked.get(entry["node"])
        entry["already_registered"]=item is not None
        # Do not call SSH to inspect keys for an entire fleet during preview.
        entry["ssh_trust"]="check_before_install"
    return {
        "datacenter_id":dc["id"],"datacenter":dc["name"],"domain_suffix":suffix,
        "group_id":group_id,"nodes":mapping,
        "note":"FQDN calculés depuis les noms Proxmox et le suffixe DNS. "
               "Valider les noms et les empreintes SSH avant installation.",
    }


def sync_group(dc_id, payload):
    if not isinstance(payload,dict) or set(payload)-{"domain_suffix","nodes"}:
        raise FleetError("Paramètres du groupe Proxmox invalides")
    dc=integrations.configured_datacenter(dc_id)
    suffix=_valid_suffix(dc,payload.get("domain_suffix"))
    discovered=_discover(dc)
    selected=payload.get("nodes")
    if (not isinstance(selected,list) or not 1<=len(selected)<=MAX_CLUSTER_NODES or
            any(not isinstance(x,str) for x in selected)):
        raise FleetError("Sélectionner de 1 à 100 nœuds Proxmox")
    selected=[item.strip().lower() for item in selected]
    if len(set(selected))!=len(selected) or not set(selected).issubset(set(discovered)):
        raise FleetError("Des nœuds sélectionnés ne figurent plus dans le cluster : actualiser")
    chosen=[(name,_node(name,suffix)) for name in selected]
    if len({host for _,host in chosen})!=len(chosen):
        raise FleetError("Plusieurs nœuds partagent le même FQDN SSH")
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        current=con.execute(
            "SELECT group_id FROM proxmox_ssh_groups WHERE datacenter_id=?",(dc_id,)
        ).fetchone()
        if current:
            group_id=current["group_id"]
        else:
            slug=re.sub(r"[^a-zA-Z0-9_-]+","-",dc["name"])[:52]
            group_name=ssh_inventory.group_name("PVE-"+str(dc_id)+"-"+slug)
            row=con.execute(
                "SELECT id FROM ssh_server_groups WHERE lower(name)=lower(?)",(group_name,)
            ).fetchone()
            if row:
                raise FleetError("Le nom du groupe SSH automatique est déjà utilisé")
            group_id=con.execute(
                "INSERT INTO ssh_server_groups(name,description,created_at) VALUES (?,?,?)",
                (group_name,"Groupe géré automatiquement par le cluster Proxmox "+dc["name"][:60],db.now())
            ).lastrowid
            con.execute(
                "INSERT INTO proxmox_ssh_groups(datacenter_id,group_id,domain_suffix,updated_at) "
                "VALUES (?,?,?,?)",(dc_id,group_id,suffix,db.now())
            )
        ids=[]
        for name,hostname in chosen:
            found=con.execute(
                "SELECT id,port FROM ssh_servers WHERE fqdn=?",(hostname,)
            ).fetchone()
            if found:
                if found["port"]!=22:
                    raise FleetError("Le nœud "+hostname+" utilise déjà un port SSH différent : "
                                     "modifier le port de sa fiche avant synchronisation")
                server_id=found["id"]
            else:
                server_id=con.execute(
                    "INSERT INTO ssh_servers(fqdn,port,label,created_at) VALUES (?,?,?,?)",
                    (hostname,22,"Proxmox "+dc["name"][:90],db.now())
                ).lastrowid
            ids.append((server_id,name))
        # The dedicated group is managed by Proxmox discovery, not manual CRUD.
        con.execute("DELETE FROM ssh_server_group_members WHERE group_id=?",(group_id,))
        con.execute("DELETE FROM proxmox_ssh_nodes WHERE datacenter_id=?",(dc_id,))
        con.executemany(
            "INSERT INTO ssh_server_group_members(group_id,server_id) VALUES (?,?)",
            [(group_id,sid) for sid,_ in ids]
        )
        con.executemany(
            "INSERT INTO proxmox_ssh_nodes(datacenter_id,server_id,node_name) VALUES (?,?,?)",
            [(dc_id,sid,node) for sid,node in ids]
        )
        con.execute(
            "UPDATE proxmox_ssh_groups SET domain_suffix=?,updated_at=? WHERE datacenter_id=?",
            (suffix,db.now(),dc_id)
        )
    return {"group_id":group_id,"datacenter_id":dc_id,"count":len(ids),
            "servers":[{"node":node,"server_id":sid} for sid,node in ids]}


def deploy(dc_id, payload, actor):
    """Queue only selected, previously discovered node mappings, with approval."""
    if not isinstance(payload,dict) or set(payload)-{"group_id","server_ids","credential_id","approved"}:
        raise FleetError("Paramètres de déploiement groupé invalides")
    if payload.get("approved") is not True:
        raise FleetError("Confirmation explicite obligatoire avant déploiement SSH")
    try:
        group_id=payload["group_id"]
        credential_id=payload["credential_id"]
        if type(group_id) is not int or type(credential_id) is not int:
            raise ValueError()
    except (KeyError,ValueError):
        raise FleetError("Groupe ou identité SSH invalide") from None
    ids=payload.get("server_ids")
    if (not isinstance(ids,list) or not 1<=len(ids)<=MAX_CLUSTER_NODES or
            any(type(v) is not int or v<=0 for v in ids) or len(set(ids))!=len(ids)):
        raise FleetError("Sélectionner entre 1 et 100 hôtes enregistrés")
    with db.session() as con:
        group=con.execute(
            "SELECT group_id FROM proxmox_ssh_groups WHERE datacenter_id=?",(dc_id,)
        ).fetchone()
        if not group or group["group_id"]!=group_id:
            raise FleetError("Ce groupe SSH n'appartient pas au cluster Proxmox choisi")
        placeholders=",".join("?" for _ in ids)
        mapped=con.execute(
            "SELECT s.id,s.fqdn,s.port FROM proxmox_ssh_nodes n "
            "JOIN ssh_servers s ON s.id=n.server_id "
            "JOIN ssh_server_group_members m ON m.server_id=s.id "
            "WHERE n.datacenter_id=? AND m.group_id=? AND s.id IN ("+placeholders+")",
            (dc_id,group_id,*ids)
        ).fetchall()
        if len(mapped)!=len(ids):
            raise FleetError("Un hôte a été retiré ou modifié dans le groupe : synchroniser")
    idx=_agent_index()
    if any(row["fqdn"] in idx or row["fqdn"].split(".")[0] in idx for row in mapped):
        raise FleetError("Un agent existe déjà sur un des nœuds choisis : le retirer de la sélection")
    try:
        queued=agent_deployments.queue({
            "platform":"linux",
            "credential_id":credential_id,
            "agent_group":"pve_cluster_"+str(dc_id),
            "targets":[{"server_id":i} for i in ids],
            "proxmox_datacenter_id":dc_id,
            "proxmox_group_id":group_id,
            "approved":True,
        },actor)
    except agent_deployments.DeploymentError as exc:
        raise FleetError(str(exc)) from None
    return {"group_id":group_id,"batch_id":queued["batch_id"],
            "total":queued["total"],"status":queued["status"]}
