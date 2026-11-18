"""Independent, customizable DR protection plans / environments.

Each plan is isolated (e.g. pre-production vs production). Legacy sites are
left unassigned on upgrade and require explicit administrator classification.
"""
from . import db


class DRPlanError(ValueError):
    pass


def _fields(data):
    if not isinstance(data,dict) or set(data)-{'name','description'}:
        raise DRPlanError("Paramètres du groupe PRA invalides")
    name=str(data.get('name') or '').strip()
    description=str(data.get('description') or '').strip()
    if not 2<=len(name)<=80 or any(ord(c)<32 for c in name):
        raise DRPlanError("Nom du groupe PRA invalide (2 à 80 caractères)")
    if len(description)>300 or any(ord(c)<32 for c in description):
        raise DRPlanError("Description trop longue ou invalide (300 caractères maximum)")
    return name, description


def list_plans():
    with db.session() as con:
        return [dict(row) for row in con.execute(
            "SELECT p.id,p.name,p.description,p.created_at,p.updated_at,"
            "(SELECT COUNT(*) FROM dr_sites s WHERE s.plan_id=p.id) AS sites_count,"
            "(SELECT COUNT(*) FROM dr_sites s JOIN dr_site_datacenters d ON d.site_id=s.id "
            " WHERE s.plan_id=p.id) AS clusters_count "
            "FROM dr_plans p ORDER BY p.name COLLATE NOCASE"
        )]


def save(data, plan_id=None):
    name,description=_fields(data)
    try:
        with db.session() as con:
            con.execute("BEGIN IMMEDIATE")
            if plan_id is None:
                cursor=con.execute(
                    "INSERT INTO dr_plans(name,description,created_at,updated_at) VALUES(?,?,?,?)",
                    (name,description,db.now(),db.now()))
                return cursor.lastrowid
            found=con.execute("SELECT id FROM dr_plans WHERE id=?",(plan_id,)).fetchone()
            if not found:
                raise DRPlanError("Groupe PRA introuvable")
            con.execute(
                "UPDATE dr_plans SET name=?,description=?,updated_at=? WHERE id=?",
                (name,description,db.now(),plan_id))
    except db.sqlite3.IntegrityError:
        raise DRPlanError("Un groupe PRA utilise déjà ce nom") from None
    return plan_id


def delete(plan_id):
    with db.session() as con:
        row=con.execute("SELECT name FROM dr_plans WHERE id=?",(plan_id,)).fetchone()
        if not row:
            raise DRPlanError("Groupe PRA introuvable")
        used=con.execute("SELECT COUNT(*) FROM dr_sites WHERE plan_id=?",(plan_id,)).fetchone()[0]
        if used:
            raise DRPlanError(
                "Ce groupe contient "+str(used)+" site(s). "
                "Réaffecter explicitement les sites avant de supprimer le groupe."
            )
        con.execute("DELETE FROM dr_plans WHERE id=?",(plan_id,))
    return row["name"]
