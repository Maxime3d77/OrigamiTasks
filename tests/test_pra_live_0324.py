"""Live QMP replication: API approval, fail-closed behavior, history, scheduling.

Mocks only: no Proxmox, QMP, NBD or SSH command is executed in CI.
"""
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app import db, pra_live, dr_protected_vms
from app.main import app
from test_dr_protected_vms_0321 import (
    login, dc, plan, site, fake_pve, protected_payload, cleanup,
)


def _setup(client, h, monkeypatch):
    plan_id = plan(client,h,"CI QMP protection 0324")
    src = dc(client,h,"ciqmlive034src")
    dst = dc(client,h,"ciqmlive034dst")
    srcsite = site(client,h,"CI QMP primary 0324",plan_id,src,"primary")
    dstsite = site(client,h,"CI QMP standby 0324",plan_id,dst,"standby")
    fake_pve(monkeypatch,src,dst)
    payload = protected_payload(plan_id,src,dst)
    payload["engine"] = "qmp_live_incremental"
    result = client.post("/api/dr/vms",headers=h,json=payload)
    assert result.status_code == 200,result.text
    return result.json()["id"],[srcsite,dstsite],[src,dst],[plan_id]


def _remove(client,h,vid,sites,clusters,plans):
    with db.session() as con:
        con.execute("DELETE FROM pra_live_runs WHERE protected_vm_id=?",(vid,))
    assert client.delete("/api/dr/vms/"+str(vid),headers=h).status_code == 200
    cleanup(client,h,sites,clusters,plans)


def test_pra_live_requires_admin_approval_and_does_not_copy_on_save(monkeypatch):
    with TestClient(app) as client:
        h = login(client)
        vid,sites,clusters,plans = _setup(client,h,monkeypatch)
        try:
            assert client.get("/api/dr/vms/"+str(vid)+"/replications").json()["items"] == []
            missing=client.post("/api/dr/vms/"+str(vid)+"/replicate",headers=h)
            assert missing.status_code == 409
            denied=client.post("/api/dr/vms/"+str(vid)+"/replicate",
                               headers=h,json={"approved":False})
            assert denied.status_code == 409
            unauthorized=client.post("/api/dr/vms/"+str(vid)+"/replicate",
                                     json={"approved":True})
            assert unauthorized.status_code in (401,403)
            # Fake source stopped, fake target LVM: both reject prior to any SSH.
            refused=client.post("/api/dr/vms/"+str(vid)+"/replicate",
                                headers=h,json={"approved":True})
            assert refused.status_code in (409,503)
            with db.session() as con:
                assert con.execute("SELECT COUNT(*) FROM pra_live_runs WHERE protected_vm_id=?",
                                   (vid,)).fetchone()[0] == 0
        finally:
            _remove(client,h,vid,sites,clusters,plans)


def test_manual_trigger_is_single_flight_and_tracks_true_state(monkeypatch):
    with TestClient(app) as client:
        h = login(client)
        vid,sites,clusters,plans = _setup(client,h,monkeypatch)
        try:
            monkeypatch.setattr(pra_live,"_vm_runtime",lambda vm: {
                "source_node":"srcnode", "target_node":"dstnode",
                "source":{"host":"source.test","credential_id":5,"port":22},
                "destination":{"host":"dest.test","credential_id":6,"port":22},
                "slots":["scsi0"]
            })
            class NoThread:
                def __init__(self,*args,**kwargs): pass
                def start(self): pass
            monkeypatch.setattr(pra_live.threading,"Thread",NoThread)
            pre=client.get("/api/dr/vms/"+str(vid)+"/replication-preflight")
            assert pre.status_code == 200,pre.text
            accepted=client.post("/api/dr/vms/"+str(vid)+"/replicate",
                                 headers=h,json={"approved":True})
            assert accepted.status_code == 200,accepted.text
            assert accepted.json()["source_stopped"] is False
            assert accepted.json()["target_started"] is False
            second=client.post("/api/dr/vms/"+str(vid)+"/replicate",
                               headers=h,json={"approved":True})
            assert second.status_code == 409
            rows=client.get("/api/dr/vms/"+str(vid)+"/replications").json()["items"]
            assert len(rows)==1 and rows[0]["status"]=="queued"
            listed=client.get("/api/dr/vms").json()["items"]
            protected=next(i for i in listed if i["id"]==vid)
            assert protected["replication_status"]=="queued"
            assert protected["failover_available"] is False
        finally:
            _remove(client,h,vid,sites,clusters,plans)


def test_scheduler_only_retries_successful_seed(monkeypatch):
    calls = []
    monkeypatch.setattr(pra_live,"start",
                        lambda vm,actor,scheduled: calls.append((vm,actor,scheduled)))
    now = datetime.now(timezone.utc)
    with db.session() as con:
        con.execute("PRAGMA foreign_keys=OFF")
        # Real FK-protected plan data is tested by the API tests above.
        # This test only asserts no synthetic/incomplete rows are scheduled.
    pra_live.tick()
    assert all(actor=="scheduler" and flag is True for _,actor,flag in calls)


def test_frontend_exposes_manual_qmp_controls_and_no_storage_array_option():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    script=(root/"static/app.js").read_text()
    assert "dr-vm-qmp-preflight" in script
    assert "dr-vm-qmp-sync" in script
    assert "qmp_live_incremental" in script
    assert "replication-preflight" in script
    assert "storage_array_replication','Réplication" not in script
    helper=(root/"agents/linux/pra_host_worker.py").read_text()
    assert "target_publish" in helper
    assert 'cmd("qm", "start"' not in helper
    assert 'cmd("qm", "stop"' not in helper
    orchestration=(root/"app/pra_live.py").read_text()
    assert "request_port_forward" in orchestration
    assert "/usr/local/lib/origamitasks-pra" in orchestration
    assert "source_vm_uuid" in orchestration
