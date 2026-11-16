"""Reusable SSH server inventory; credentials are stored separately in the vault.

Group membership never grants SSH access, and no remote connection is made by CRUD.
A server can belong to several groups. Deleting a group never deletes its servers.
"""
import re
import json

from . import db


class InventoryError(ValueError):
    pass


def fqdn(value):
    value = str(value or "").strip().lower().rstrip(".")
    if len(value)>253 or len(value)<3 or "." not in value:
        raise InventoryError("FQDN requis (exemple : pve01.infra.example.org)")
    labels=value.split(".")
    if any(not 1<=len(label)<=63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?",label)
           for label in labels):
        raise InventoryError("FQDN invalide : uniquement lettres, chiffres, points et tirets")
    return value


def port(value):
    try:
        value=int(value)
    except (ValueError,TypeError):
        raise InventoryError("Port SSH invalide") from None
    if not 1<=value<=65535:
        raise InventoryError("Le port SSH doit être compris entre 1 et 65535")
    return value


def group_name(value):
    value=str(value or "").strip()
    if not 1<=len(value)<=80 or any(ord(c)<32 or c in "<>\"'/" for c in value):
        raise InventoryError("Nom de groupe invalide (1 à 80 caractères)")
    return value


def label(value):
    value=str(value or "").strip()
    if len(value)>120 or any(ord(c)<32 for c in value):
        raise InventoryError("Libellé de serveur invalide")
    return value


def normalize_hosts(items):
    """Accept a newline/comma separated list or list of {fqdn, port} records."""
    if isinstance(items,str):
        items=[piece.strip() for piece in items.replace(",","\n").splitlines() if piece.strip()]
    if not isinstance(items,list) or len(items)>250:
        raise InventoryError("250 serveurs maximum par groupe")
    result={}
    for item in items:
        if isinstance(item,str):
            host,ssh_port=fqdn(item),22
        elif isinstance(item,dict) and set(item)<={"fqdn","port"}:
            host,ssh_port=fqdn(item.get("fqdn")),port(item.get("port",22))
        else:
            raise InventoryError("FQDN du groupe invalide")
        if host in result and result[host]!=ssh_port:
            raise InventoryError("FQDN en double avec des ports différents")
        result[host]=ssh_port
    return result


def inventory():
    with db.session() as con:
        servers=[dict(x) for x in con.execute(
            "SELECT id,fqdn,port,label FROM ssh_servers ORDER BY fqdn"
        )]
        groups=[dict(x) for x in con.execute(
            "SELECT id,name,description FROM ssh_server_groups ORDER BY name COLLATE NOCASE"
        )]
        links=[dict(x) for x in con.execute(
            "SELECT server_id,group_id FROM ssh_server_group_members"
        )]
    for server in servers:
        server["group_ids"]=sorted(x["group_id"] for x in links if x["server_id"]==server["id"])
    for group in groups:
        group["server_ids"]=sorted(x["server_id"] for x in links if x["group_id"]==group["id"])
        group["server_count"]=len(group["server_ids"])
    return {"servers":servers,"groups":groups}


def server(server_id):
    try:
        id_=int(server_id)
    except (TypeError,ValueError):
        raise InventoryError("Identifiant serveur SSH invalide") from None
    with db.session() as con:
        record=con.execute("SELECT id,fqdn,port,label FROM ssh_servers WHERE id=?",(id_,)).fetchone()
    if not record:
        raise InventoryError("Serveur SSH introuvable dans l'inventaire")
    return dict(record)


def create_group(payload):
    name=group_name(payload.get("name"))
    description=label(payload.get("description",""))
    hosts=normalize_hosts(payload.get("servers",[]))
    with db.session() as con:
        if con.execute("SELECT 1 FROM ssh_server_groups WHERE lower(name)=lower(?)",(name,)).fetchone():
            raise InventoryError("Ce groupe de serveurs existe déjà")
        cur=con.execute("INSERT INTO ssh_server_groups(name,description,created_at) VALUES (?,?,?)",
                        (name,description,db.now()))
        group_id=cur.lastrowid
        for hostname,ssh_port in hosts.items():
            row=con.execute("SELECT id,port FROM ssh_servers WHERE fqdn=?",(hostname,)).fetchone()
            if row:
                if row["port"]!=ssh_port:
                    raise InventoryError("Le serveur "+hostname+" existe avec un autre port : modifier sa fiche")
                sid=row["id"]
            else:
                sid=con.execute("INSERT INTO ssh_servers(fqdn,port,label,created_at) VALUES (?,?,?,?)",
                                (hostname,ssh_port,"",db.now())).lastrowid
            con.execute("INSERT INTO ssh_server_group_members(group_id,server_id) VALUES (?,?)",
                        (group_id,sid))
    return group_id


def update_group(group_id,payload):
    if "name" not in payload:
        raise InventoryError("Nom du groupe obligatoire")
    name=group_name(payload["name"])
    description=label(payload.get("description",""))
    with db.session() as con:
        if not con.execute("SELECT 1 FROM ssh_server_groups WHERE id=?",(group_id,)).fetchone():
            raise InventoryError("Groupe de serveurs introuvable")
        if con.execute("SELECT 1 FROM ssh_server_groups WHERE lower(name)=lower(?) AND id<>?",
                       (name,group_id)).fetchone():
            raise InventoryError("Un autre groupe porte ce nom")
        con.execute("UPDATE ssh_server_groups SET name=?,description=? WHERE id=?",
                    (name,description,group_id))
        if "servers" in payload:
            hosts=normalize_hosts(payload["servers"])
            ids=[]
            for hostname,ssh_port in hosts.items():
                row=con.execute("SELECT id,port FROM ssh_servers WHERE fqdn=?",(hostname,)).fetchone()
                if row:
                    if row["port"]!=ssh_port:
                        raise InventoryError("Port incohérent pour "+hostname)
                    ids.append(row["id"])
                else:
                    ids.append(con.execute(
                        "INSERT INTO ssh_servers(fqdn,port,label,created_at) VALUES (?,?,?,?)",
                        (hostname,ssh_port,"",db.now())).lastrowid)
            con.execute("DELETE FROM ssh_server_group_members WHERE group_id=?",(group_id,))
            for sid in ids:
                con.execute("INSERT INTO ssh_server_group_members(group_id,server_id) VALUES (?,?)",
                            (group_id,sid))


def create_server(payload):
    hostname=fqdn(payload.get("fqdn"))
    ssh_port=port(payload.get("port",22))
    display=label(payload.get("label",""))
    group_ids=payload.get("group_ids",[])
    if not isinstance(group_ids,list) or len(group_ids)>50:
        raise InventoryError("Groupes du serveur invalides")
    with db.session() as con:
        if con.execute("SELECT 1 FROM ssh_servers WHERE fqdn=?",(hostname,)).fetchone():
            raise InventoryError("Ce FQDN existe déjà")
        for gid in group_ids:
            if not con.execute("SELECT 1 FROM ssh_server_groups WHERE id=?",(gid,)).fetchone():
                raise InventoryError("Groupe serveur introuvable")
        sid=con.execute("INSERT INTO ssh_servers(fqdn,port,label,created_at) VALUES (?,?,?,?)",
                        (hostname,ssh_port,display,db.now())).lastrowid
        for gid in set(group_ids):
            con.execute("INSERT INTO ssh_server_group_members(group_id,server_id) VALUES (?,?)",
                        (gid,sid))
    return sid


def update_server(server_id,payload):
    with db.session() as con:
        row=con.execute("SELECT * FROM ssh_servers WHERE id=?",(server_id,)).fetchone()
        if not row:
            raise InventoryError("Serveur SSH introuvable")
        if "fqdn" in payload and fqdn(payload["fqdn"])!=row["fqdn"]:
            raise InventoryError("Le FQDN d'un serveur ne peut pas être modifié : ajouter un nouveau serveur pour éviter de rediriger des workflows")
        new_port=port(payload.get("port",row["port"]))
        display=label(payload.get("label",row["label"]))
        if "group_ids" in payload:
            gids=payload["group_ids"]
            if not isinstance(gids,list) or len(gids)>50:
                raise InventoryError("Groupes invalides")
            for gid in gids:
                if not con.execute("SELECT 1 FROM ssh_server_groups WHERE id=?",(gid,)).fetchone():
                    raise InventoryError("Groupe SSH introuvable")
            con.execute("DELETE FROM ssh_server_group_members WHERE server_id=?",(server_id,))
            for gid in set(gids):
                con.execute("INSERT INTO ssh_server_group_members(group_id,server_id) VALUES (?,?)",
                            (gid,server_id))
        con.execute("UPDATE ssh_servers SET label=?,port=? WHERE id=?",
                    (display,new_port,server_id))


def delete_server(server_id):
    with db.session() as con:
        row=con.execute("SELECT 1 FROM ssh_servers WHERE id=?",(server_id,)).fetchone()
        if not row: raise InventoryError("Serveur SSH introuvable")
        jobs=con.execute("SELECT steps_json FROM jobs").fetchall()
        if any(str(step.get("config",{}).get("ssh_server_id",""))==str(server_id)
               for job in jobs for step in json.loads(job["steps_json"])):
            raise InventoryError("Ce serveur est référencé par un workflow : retirer la référence avant suppression")
        con.execute("DELETE FROM ssh_server_group_members WHERE server_id=?",(server_id,))
        con.execute("DELETE FROM ssh_servers WHERE id=?",(server_id,))


def delete_group(group_id):
    with db.session() as con:
        if not con.execute("SELECT 1 FROM ssh_server_groups WHERE id=?",(group_id,)).fetchone():
            raise InventoryError("Groupe de serveurs introuvable")
        con.execute("DELETE FROM ssh_server_group_members WHERE group_id=?",(group_id,))
        con.execute("DELETE FROM ssh_server_groups WHERE id=?",(group_id,))
