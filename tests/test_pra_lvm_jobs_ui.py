"""API and DB lifecycle for real QMP-to-LVM lab replication.

The data plane is mocked here. Separate QEMU/NBD integration smoke tests
exercise the actual block copy. These tests never touch a Proxmox host.
"""
from fastapi.testclient import TestClient

from app import db, pra_live, pra_lvm_jobs
from app.main import app
from test_dr_protected_vms_0321 import login
from test_pra_live_0324 import _setup, _remove


class NoThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


def preflight(_):
    return {
        "source_running": True, "target_stopped": True,
        "destination_storage": "target-lvm",
        "source_vmid": 210, "target_vmid": 321, "disk_slots": ["scsi0"],
        "available_bytes": 500 * 1024**3,
        "checkpoint": None,
    }


def mock_success(_vmid, *, execute=False, incremental=False, lab_confirm=None):
    assert execute is True
    assert lab_confirm == pra_lvm_jobs.LAB_ACK
    return {
        "real_pve_replication_performed": True,
        "offline_structure_verified": True,
        "source_running": True, "target_stopped": True, "vm_started": False,
        "checkpoint_token": "a" * 32,
        "point_at": "2026-11-20T09:35:00+00:00",
        "mode": "incremental" if incremental else "full",
    }


def test_admin_only_and_required_lab_ack(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vm_id,sites,clusters,plans=_setup(client,h,monkeypatch)
        try:
            monkeypatch.setattr(pra_lvm_jobs,"preflight",preflight)
            bad=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/replicate",
                            headers=h,json={"mode":"full","confirmation":"yes"})
            assert bad.status_code==409
            unauthorized=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/replicate",
                                     json={"mode":"full","confirmation":pra_lvm_jobs.LAB_ACK})
            assert unauthorized.status_code in (401,403)
            malformed=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/replicate",
                                  headers=h,json={"mode":"full","confirmation":pra_lvm_jobs.LAB_ACK,
                                                  "override_production":True})
            assert malformed.status_code==422
            # A TestClient keeps login cookies across requests; explicitly
            # clear them before testing unauthenticated read-only routes.
            client.cookies.clear()
            assert client.get(f"/api/dr/vms/{vm_id}/lvm-lab/preflight").status_code in (401,403)
            assert client.get(f"/api/dr/vms/{vm_id}/lvm-lab/runs").status_code in (401,403)
            h=login(client)
            assert client.get(f"/api/dr/vms/{vm_id}/lvm-lab/preflight",headers=h).json()["source_running"]
            assert client.get(f"/api/dr/vms/{vm_id}/lvm-lab/runs",headers=h).json()["items"]==[]
            with db.session() as con:
                assert con.execute("SELECT COUNT(*) FROM pra_lvm_lab_runs WHERE protected_vm_id=?",
                                   (vm_id,)).fetchone()[0]==0
        finally:
            _remove(client,h,vm_id,sites,clusters,plans)


def test_full_run_is_exclusive_tracked_and_distinct_from_failover(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vm_id,sites,clusters,plans=_setup(client,h,monkeypatch)
        try:
            monkeypatch.setattr(pra_lvm_jobs,"preflight",preflight)
            monkeypatch.setattr(pra_lvm_jobs.threading,"Thread",NoThread)
            accepted=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/replicate",
                                 headers=h,json={"mode":"full","confirmation":pra_lvm_jobs.LAB_ACK})
            assert accepted.status_code==200,accepted.text
            run_id=accepted.json()["run_id"]
            assert accepted.json()["source_remains_running"] is True
            assert accepted.json()["standby_must_remain_stopped"] is True
            again=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/replicate",
                              headers=h,json={"mode":"full","confirmation":pra_lvm_jobs.LAB_ACK})
            assert again.status_code==409
            monkeypatch.setattr(pra_live,"_vm_runtime",lambda _: {})
            try:
                pra_live.start(vm_id,"admin",approved=True)
            except pra_live.LiveReplicationError as exc:
                assert "LVM" in str(exc)
            else:
                raise AssertionError("LVM/QMP simultaneous source backup must be rejected")
            monkeypatch.setattr(pra_lvm_jobs.pra_lvm_lab,"run",mock_success)
            pra_lvm_jobs._perform(run_id)
            history=client.get(f"/api/dr/vms/{vm_id}/lvm-lab/runs",headers=h).json()["items"]
            assert history[0]["status"]=="completed"
            assert history[0]["point_at"]=="2026-11-20T09:35:00+00:00"
            assert history[0]["checkpoint_token"]=="a"*32
            listed=client.get("/api/dr/vms",headers=h).json()["items"]
            vm=next(row for row in listed if row["id"]==vm_id)
            assert vm["lvm_lab_latest_run"]["status"]=="completed"
            assert vm["failover_available"] is False
            # A structural LVM lab checkpoint must NOT make the DIR engine
            # claim a bootable REP_ or a real failover-ready guest.
            assert vm["replica_verified"] is False
        finally:
            _remove(client,h,vm_id,sites,clusters,plans)


def test_ambiguous_write_is_persistently_blocked_after_restart(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vm_id,sites,clusters,plans=_setup(client,h,monkeypatch)
        try:
            monkeypatch.setattr(pra_lvm_jobs,"preflight",preflight)
            monkeypatch.setattr(pra_lvm_jobs.threading,"Thread",NoThread)
            run=pra_lvm_jobs.start(vm_id,"admin",mode="full",confirmation=pra_lvm_jobs.LAB_ACK)
            def fail_copy(*args,**kwargs):
                raise RuntimeError("source/disconnect: ACK lost after possible LVM write")
            monkeypatch.setattr(pra_lvm_jobs.pra_lvm_lab,"run",fail_copy)
            pra_lvm_jobs._perform(run["run_id"])
            status=pra_lvm_jobs.list_runs(vm_id)[0]
            assert status["status"]=="uncertain"
            try:
                pra_lvm_jobs.start(vm_id,"admin",mode="full",confirmation=pra_lvm_jobs.LAB_ACK)
            except pra_lvm_jobs.LabJobError as exc:
                assert "incertain" in str(exc)
            else:
                raise AssertionError("Uncertain LVM run must not be replayed")
            with db.session() as con:
                con.execute("UPDATE pra_lvm_lab_runs SET status='running' WHERE id=?",
                            (run["run_id"],))
            pra_lvm_jobs.initialize()
            assert pra_lvm_jobs.list_runs(vm_id)[0]["status"]=="uncertain"
        finally:
            _remove(client,h,vm_id,sites,clusters,plans)


def test_incremental_requires_committed_checkpoint_before_job_creation(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vm_id,sites,clusters,plans=_setup(client,h,monkeypatch)
        try:
            monkeypatch.setattr(pra_lvm_jobs,"preflight",preflight)
            missing=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/replicate",
                                headers=h,json={"mode":"incremental",
                                                "confirmation":pra_lvm_jobs.LAB_ACK})
            assert missing.status_code==409
            with db.session() as con:
                assert con.execute("SELECT COUNT(*) FROM pra_lvm_lab_runs WHERE protected_vm_id=?",
                                   (vm_id,)).fetchone()[0]==0
        finally:
            _remove(client,h,vm_id,sites,clusters,plans)


def test_ui_does_not_claim_lvm_recovery_is_production_safe():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    ui=(root/"static/app.js").read_text(encoding="utf-8")
    for phrase in ("Copie LVM recette","DISPOSABLE-LAB-ONLY",
                   "lvm-lab/replicate","lvm-lab/runs",
                   "Copie complète à chaud","Synchronisation incrémentale",
                   "Préparer automatiquement la VM de secours",
                   "lvm-lab/prepare-standby"):
        assert phrase in ui
    # The legacy UI must no longer tell operators to run qm create manually.
    assert "qm create " not in ui
    assert "Bascule réelle interdite" in ui


def test_restore_clone_requires_verified_full_and_never_starts_vm(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vm_id,sites,clusters,plans=_setup(client,h,monkeypatch)
        try:
            token="a"*32
            def pre(_):
                return {**preflight(None),"checkpoint":token}
            monkeypatch.setattr(pra_lvm_jobs,"preflight",pre)
            monkeypatch.setattr(pra_lvm_jobs.threading,"Thread",NoThread)
            # A remote checkpoint alone is not enough: it must correspond to a
            # completed, tracked LVM copy of this exact protected VM.
            blocked=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/restore-clone",headers=h,
                                json={"test_vmid":4301,"confirmation":pra_lvm_jobs.LAB_ACK})
            assert blocked.status_code==409
            with db.session() as con:
                con.execute(
                    "INSERT INTO pra_lvm_lab_runs("
                    "protected_vm_id,status,mode,actor,created_at,point_at,checkpoint_token,"
                    "source_vmid,target_vmid,target_storage,source_dc_id,target_dc_id)"
                    " SELECT id,'completed','full','admin',?,?,?,source_vmid,target_vmid,"
                    "target_storage,source_dc_id,target_dc_id FROM dr_protected_vms WHERE id=?",
                    (db.now(),"2026-11-20T09:35:00+00:00",token,vm_id))
            missing_ack=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/restore-clone",headers=h,
                                    json={"test_vmid":4301,"confirmation":"anything"})
            assert missing_ack.status_code==409
            vmid_collision=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/restore-clone",headers=h,
                                       json={"test_vmid":321,"confirmation":pra_lvm_jobs.LAB_ACK})
            assert vmid_collision.status_code==409
            requested=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/restore-clone",headers=h,
                                  json={"test_vmid":4301,"confirmation":pra_lvm_jobs.LAB_ACK})
            assert requested.status_code==200,requested.text
            run_id=requested.json()["run_id"]
            assert requested.json()["test_vm_started"] is False
            simultaneous=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/restore-clone",headers=h,
                                     json={"test_vmid":4302,"confirmation":pra_lvm_jobs.LAB_ACK})
            assert simultaneous.status_code==409
            def isolated(_):
                return {"test_vmid":4301,"checkpoint_token":token,
                        "network_isolated":True,"vm_started":False,
                        "standby_started":False,"boot_verified":False,
                        "application_verified":False,"failover_authorized":False}
            monkeypatch.setattr(pra_lvm_jobs,"_prepare_isolated_clone",isolated)
            pra_lvm_jobs._perform(run_id)
            history=pra_lvm_jobs.list_runs(vm_id)
            assert history[0]["mode"]=="restore"
            assert history[0]["status"]=="completed"
            assert history[0]["test_vmid"]==4301
            assert history[0]["checkpoint_token"]==token
            assert "arrêté" in history[0]["details"]
        finally:
            _remove(client,h,vm_id,sites,clusters,plans)


def test_clone_approval_never_exposes_generic_hypervisor_start(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vm_id,sites,clusters,plans=_setup(client,h,monkeypatch)
        try:
            denied=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/restore-clone",
                               json={"test_vmid":4301,"confirmation":pra_lvm_jobs.LAB_ACK})
            assert denied.status_code in (401,403)
            wrong=client.post(f"/api/dr/vms/{vm_id}/lvm-lab/restore-clone",headers=h,
                              json={"test_vmid":4301,"confirmation":pra_lvm_jobs.LAB_ACK,
                                    "start_vm":True})
            assert wrong.status_code==422
            assert "lvm_lab_prepare_clone" in (
                __import__("pathlib").Path(__file__).resolve().parents[1]/
                "agents/linux/pra_host_worker.py").read_text()
        finally:
            _remove(client,h,vm_id,sites,clusters,plans)
