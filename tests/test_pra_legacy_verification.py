"""Legacy replication history cannot masquerade as a newly verified base."""
from fastapi.testclient import TestClient
from app import db, pra_live
from app.main import app
from test_dr_protected_vms_0321 import login
from test_pra_live_0324 import _setup, _remove


def _legacy_completed(vm_id):
    with db.session() as con:
        con.execute(
            "INSERT INTO pra_live_runs(protected_vm_id,status,mode,actor,created_at,finished_at,point_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (vm_id, "completed", "incremental", "legacy",
             "2026-11-18T08:00:00+00:00", "2026-11-18T08:05:00+00:00",
             "2026-11-18T08:00:00+00:00"))


def test_old_completed_run_is_unverified_and_not_eligible_for_auto_schedule(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vm_id,sites,clusters,plans=_setup(client,h,monkeypatch)
        try:
            _legacy_completed(vm_id)
            status=pra_live.latest_status(vm_id)
            assert status["replication_status"]=="completed"
            assert status["last_successful_sync"] is None
            assert status["offline_image_verified"] is False
            assert status["boot_verified"] is False
            assert status["failover_available"] is False
            vm=next(x for x in client.get("/api/dr/vms").json()["items"]
                    if x["id"]==vm_id)
            assert vm["replica_verified"] is False
            assert vm["last_successful_sync"] is None
            assert vm["failover_available"] is False
            assert pra_live.list_runs(vm_id)[0]["verification_level"]=="unverified"
            called=[]
            monkeypatch.setattr(pra_live,"start",
                lambda *args,**kwargs: called.append((args,kwargs)))
            pra_live.tick()
            assert called==[]
        finally:
            _remove(client,h,vm_id,sites,clusters,plans)


def test_verification_level_column_is_present_and_defaults_to_unverified():
    with db.session() as con:
        columns={r["name"] for r in con.execute("PRAGMA table_info(pra_live_runs)")}
    assert "verification_level" in columns
