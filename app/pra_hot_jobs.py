"""Hot-source QEMU snapshot differential generations, direct SD->AU.

Not QEMU CBT: every cycle exports/scans a complete point-in-time source
snapshot to a dedicated scratch filesystem. rsync transfers the differing
bytes to a fresh full clone of the previous OFFLINE AU generation. Nothing
is promoted until all disks pass full hash verification.

Never stops source SD; never powers on standby; never removes old generation.
"""
import re
import threading
from datetime import datetime, timezone
from . import db, integrations, dr_protected_vms, pra_direct_jobs
from .executor import credential

STOP=threading.Event()
WORKER=None

class HotPRAError(ValueError):
    pass


def _scope(vm_id,source_cred_id,target_cred_id):
    vm,node,source,target,agent=pra_direct_jobs._validate(
        vm_id,"seed",source_cred_id,target_cred_id
    )
    # Prove the SD guest is actually running *now*, no stop allowed.
    dc=integrations.configured_datacenter(vm["source_dc_id"])
    secret=credential(dc["credential_id"])
    auth=dict(secret,ca_cert=dc.get("ca_cert") or secret.get("ca_cert") or "",
              skip_tls_verification=bool(dc.get("skip_tls_verification",False)))
    resources=integrations.discover_datacenter(dc,auth,20)
    source_vms=[r for r in resources if r.get("type")=="qemu" and r.get("vmid")==vm["source_vmid"]]
    if len(source_vms)!=1 or source_vms[0].get("status")!="running" or source_vms[0].get("node")!=node:
        raise HotPRAError("VM principale SD doit rester allumée sur son nœud courant")
    return vm,node,source,target,agent


def _latest_base(con,vm):
    last=con.execute(
        "SELECT candidate_vmid FROM pra_hot_jobs "
        "WHERE protected_vm_id=? AND status='success' ORDER BY id DESC LIMIT 1",
        (vm["id"],)
    ).fetchone()
    return last["candidate_vmid"] if last else vm["target_vmid"]


def queue(vm_id,source_cred_id,target_cred_id,scratch_path,approved,actor):
    if approved is not True:
        raise HotPRAError("Confirmation explicite requise")
    if not isinstance(scratch_path,str) or not re.fullmatch(r"/[A-Za-z0-9_./-]{2,190}",scratch_path):
        raise HotPRAError("Sélectionner un répertoire de travail dédié sur une autre partition que /")
    if ".." in scratch_path.split("/") or scratch_path in ("/tmp","/var/tmp"):
        raise HotPRAError("Chemin de travail temporaire interdit")
    vm,node,source,target,agent=_scope(vm_id,source_cred_id,target_cred_id)
    if vm["engine"]!="offline_copy" or vm["state"]!="planned":
        raise HotPRAError("Moteur direct et protection active requis")
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute(
            "SELECT 1 FROM pra_hot_jobs j WHERE j.protected_vm_id=? "
            "AND (j.status IN ('queued','running') OR "
            "(j.status='uncertain' AND NOT EXISTS "
            "(SELECT 1 FROM pra_hot_resolutions r WHERE r.job_id=j.id)))",(vm_id,)
        ).fetchone():
            raise HotPRAError("Génération en cours ou incertaine, contrôle manuel préalable")
        if con.execute(
            "SELECT 1 FROM pra_direct_jobs WHERE protected_vm_id=? "
            "AND status IN ('queued','running','uncertain')",(vm_id,)
        ).fetchone():
            raise HotPRAError("Une copie directe précédente est toujours active ou indéterminée")
        # The initial seed must have successfully created a standby, never
        # infer it from a planned VMID alone.
        seed=con.execute(
            "SELECT 1 FROM pra_direct_jobs WHERE protected_vm_id=? "
            "AND kind='seed' AND status='success' LIMIT 1",(vm_id,)
        ).fetchone()
        if not seed:
            raise HotPRAError("Effectuer d'abord une copie initiale réussie")
        base=_latest_base(con,vm)
        job_id=con.execute(
            "INSERT INTO pra_hot_jobs(protected_vm_id,status,source_credential_id,"
            "target_credential_id,scratch_path,base_vmid,actor,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (vm_id,"queued",source_cred_id,target_cred_id,scratch_path,base,actor,db.now())
        ).lastrowid
    db.audit(actor,"pra.hot.queue",vm_id,"job="+str(job_id)+", base="+str(base))
    return {"job_id":job_id,"status":"queued","base_vmid":base,
            "source_always_running":True,"standby_offline":True}


def history(vm_id):
    dr_protected_vms._one(vm_id)
    with db.session() as con:
        rows=con.execute(
            "SELECT j.id,j.status,j.base_vmid,j.candidate_vmid,j.disk_count,j.scanned_bytes,"
            "j.transferred_bytes,j.error,j.created_at,j.started_at,j.finished_at,"
            "r.checked_at AS resolved_at FROM pra_hot_jobs j "
            "LEFT JOIN pra_hot_resolutions r ON r.job_id=j.id "
            "WHERE j.protected_vm_id=? ORDER BY j.id DESC LIMIT 50",
            (vm_id,)
        ).fetchall()
    return {"items":[dict(row) for row in rows],"source_always_running":True}


def _target_verified(vm,candidate,base):
    dc=integrations.configured_datacenter(vm["target_dc_id"])
    key=credential(dc["credential_id"])
    auth=dict(key,ca_cert=dc.get("ca_cert") or key.get("ca_cert") or "",
              skip_tls_verification=bool(dc.get("skip_tls_verification",False)))
    resources=integrations.discover_datacenter(dc,auth,20)
    vmids=[x for x in resources if x.get("type")=="qemu" and x.get("vmid")==candidate]
    old=[x for x in resources if x.get("type")=="qemu" and x.get("vmid")==base]
    if len(vmids)!=1 or vmids[0].get("status")!="stopped" or vmids[0].get("node")!=vm["target_node"]:
        raise HotPRAError("Nouvelle génération AU introuvable ou démarrée")
    if len(old)!=1 or old[0].get("status")!="stopped":
        raise HotPRAError("Ancienne génération AU modifiée ou démarrée")
    if candidate==base:
        raise HotPRAError("Une génération doit avoir un nouveau VMID")


def process_one():
    with db.session() as con:
        con.execute("BEGIN IMMEDIATE")
        found=con.execute(
            "SELECT * FROM pra_hot_jobs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not found:
            return False
        job=dict(found)
        con.execute(
            "UPDATE pra_hot_jobs SET status='running',started_at=? WHERE id=?",
            (db.now(),job["id"])
        )
    try:
        vm,node,source,target,agent=_scope(
            job["protected_vm_id"],job["source_credential_id"],job["target_credential_id"]
        )
        with db.session() as con:
            # A previous promotion made while this job waited changes the
            # baseline; refuse instead of applying a delta to the wrong base.
            if _latest_base(con,vm)!=job["base_vmid"]:
                raise HotPRAError("Génération de base modifiée : nouvelle validation requise")
        _target_verified_base(vm,job["base_vmid"])
        payload=dict(job,kind="hot_differential")
        result=pra_direct_jobs._execute(payload,vm,source,target)
        candidate=result["candidate_vmid"]
        _target_verified(vm,candidate,job["base_vmid"])
        with db.session() as con:
            con.execute(
                "UPDATE pra_hot_jobs SET status='success',candidate_vmid=?,disk_count=?,"
                "scanned_bytes=?,transferred_bytes=?,finished_at=? WHERE id=?",
                (candidate,result["disks"],result["scanned_bytes"],
                 result["transferred_bytes"],db.now(),job["id"])
            )
        db.audit(job["actor"],"pra.hot.success",vm["id"],
                 "job="+str(job["id"])+", candidate="+str(candidate))
    except Exception as exc:
        # An incomplete clone is never promoted or removed automatically.
        # Preserve candidate ID if the agent reported creation before failure.
        candidate=getattr(exc,"candidate_vmid",None)
        candidate=candidate if type(candidate) is int and 100<=candidate<=999999999 else None
        info=(str(exc)[:175] if isinstance(exc,(HotPRAError,pra_direct_jobs.PRAJobError))
              else "Erreur de synchronisation ou perte de contrôle SSH")
        with db.session() as con:
            con.execute(
                "UPDATE pra_hot_jobs SET status='uncertain',candidate_vmid=?,error=? WHERE id=?",
                (candidate,"Génération incertaine : "+info+" ; inspecter AU avant relance",job["id"])
            )
        db.audit(job["actor"],"pra.hot.uncertain",job["protected_vm_id"],
                 "job="+str(job["id"]))
    return True


def _target_verified_base(vm,base):
    dc=integrations.configured_datacenter(vm["target_dc_id"])
    key=credential(dc["credential_id"])
    auth=dict(key,ca_cert=dc.get("ca_cert") or key.get("ca_cert") or "",
              skip_tls_verification=bool(dc.get("skip_tls_verification",False)))
    resources=integrations.discover_datacenter(dc,auth,20)
    existing=[x for x in resources if x.get("type")=="qemu" and x.get("vmid")==base]
    if len(existing)!=1 or existing[0].get("node")!=vm["target_node"] or existing[0].get("status")!="stopped":
        raise HotPRAError("La génération de base AU doit exister, être arrêtée et localisée sur le nœud prévu")


def loop():
    while not STOP.is_set():
        try:
            if process_one():
                continue
        except Exception:
            pass
        STOP.wait(4)


def start():
    global WORKER
    with db.session() as con:
        con.execute("UPDATE pra_hot_jobs SET status='uncertain',error=? WHERE status='running'",
                    ("Contrôleur redémarré : vérifier l'intégrité de la génération AU",))
    STOP.clear()
    if WORKER is None or not WORKER.is_alive():
        WORKER=threading.Thread(target=loop,name="pra-hot-worker",daemon=True)
        WORKER.start()



def resolve_uncertain(vm_id,job_id,approved,actor):
    """Release an unknown job ONLY after destination collision checks.

    Remote clones are never deleted here. An operator first deletes any
    incomplete candidate and verifies nothing in AU uses its VMID.
    """
    if approved is not True:
        raise HotPRAError("Confirmation explicite requise")
    vm=dr_protected_vms._one(vm_id)
    with db.session() as con:
        row=con.execute(
            "SELECT * FROM pra_hot_jobs WHERE id=? AND protected_vm_id=?",
            (job_id,vm_id)
        ).fetchone()
        if not row or row["status"]!="uncertain":
            raise HotPRAError("Seule une génération incertaine peut être libérée")
    dc=integrations.configured_datacenter(vm["target_dc_id"])
    token=credential(dc["credential_id"])
    auth=dict(token,ca_cert=dc.get("ca_cert") or token.get("ca_cert") or "",
              skip_tls_verification=bool(dc.get("skip_tls_verification",False)))
    items=integrations.discover_datacenter(dc,auth,20)
    all_vms=[x for x in items if x.get("type")=="qemu"]
    if row["candidate_vmid"] and any(x.get("vmid")==row["candidate_vmid"] for x in all_vms):
        raise HotPRAError("Candidate AU toujours présente : retirer manuellement le clone incomplet")
    with db.session() as con:
        known={x["candidate_vmid"] for x in con.execute(
            "SELECT candidate_vmid FROM pra_hot_jobs "
            "WHERE protected_vm_id=? AND status='success'",(vm_id,)
        )}
    # Prevent clearing ambiguous incomplete clones even if their IDs were
    # lost during a controller crash, based on the predictable candidate name.
    prefix="REP-"+str(vm_id)+"-G"
    untracked=[x for x in all_vms if str(x.get("name") or "").startswith(prefix)
               and x.get("vmid") not in known]
    if untracked:
        raise HotPRAError(
            "Clone(s) AU non vérifié(s) toujours présent(s) : "
            +", ".join(str(x.get("vmid")) for x in untracked[:5])
        )
    with db.session() as con:
        con.execute(
            "INSERT OR IGNORE INTO pra_hot_resolutions(job_id,actor,checked_at) "
            "VALUES(?,?,?)",(job_id,actor,db.now())
        )
    db.audit(actor,"pra.hot.uncertain.resolved",vm_id,
             "job="+str(job_id)+" ; aucune VM supprimée par OrigamiTasks")
    return {"resolved":True,"remote_deleted":False,
            "note":"Seul le verrou de suivi est libéré ; historique incertain conservé"}
