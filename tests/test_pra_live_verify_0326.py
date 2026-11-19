"""Controller confirms target integrity before marking a QMP run completed.

Runs against mocked connections and an isolated test DB; no PVE operations.
"""
from fastapi.testclient import TestClient

from app import db, pra_live
from app.main import app
from test_dr_protected_vms_0321 import login
from test_pra_live_0324 import _setup, _remove


class SSH:
    def __init__(self, name):
        self.name = name
        self.closed = False
    def get_transport(self):
        return self
    def cancel_port_forward(self, bind, port):
        assert bind == "127.0.0.1"
    def close(self):
        self.closed = True


def _stub_live(monkeypatch, *, proof_ok):
    monkeypatch.setattr(pra_live, "_vm_runtime", lambda vm: {
        "source_node": "fake-s", "target_node": "fake-t",
        "source": {"host": "source.invalid", "credential_id": 1, "port": 22},
        "destination": {"host": "target.invalid", "credential_id": 2, "port": 22},
        "slots": ["scsi0"],
    })
    source, target = SSH("source"), SSH("target")
    monkeypatch.setattr(pra_live, "_connect",
        lambda binding: source if binding["host"]=="source.invalid" else target)
    monkeypatch.setattr(pra_live, "_forward",
        lambda *_: ({"scsi0": 25000}, [25000], [], []))
    token = "e" * 32
    calls=[]
    def helper(ssh, action, data, timeout=60):
        calls.append(action)
        if action == "source_probe":
            return {"source_vm_uuid":"vm-stable-process",
                    "active_jobs": [], "bitmaps":{"scsi0":{}},
                    "sizes":{"scsi0":1024**3},
                    "config":{"bios":"seabios","memory":"2048","cores":"2","sockets":"1"}}
        if action == "target_probe":
            return {"pending":None, "checkpoint":None}
        if action == "target_begin":
            return {"token":token,"ports":{"scsi0":10809}}
        if action == "source_copy":
            return {"qmp_jobs_completed":True,"snapshot_epoch":1783860000.0}
        if action == "target_finish":
            return {"checkpoint":{"token":token}}
        if action == "target_publish":
            return {"standby_configured":True,"vm_started":False}
        if action == "target_verify":
            return {"checkpoint_token":token, "verified_offline":proof_ok,
                    "disk_format_checked":True,"standby_configured":True,
                    "vm_started":False,"failover_authorized":False,
                    "disks":["scsi0"]}
        raise AssertionError(action)
    monkeypatch.setattr(pra_live,"_run_helper",helper)
    class NoThread:
        def __init__(self,*a,**kw): pass
        def start(self): pass
    monkeypatch.setattr(pra_live.threading,"Thread",NoThread)
    return calls, source, target


def test_qmp_only_completed_after_target_verification(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vid,sites,clusters,plans=_setup(client,h,monkeypatch)
        try:
            calls, source, target=_stub_live(monkeypatch, proof_ok=True)
            run=pra_live.start(vid,"ci",approved=True)
            pra_live._perform(run["run_id"])
            statuses=pra_live.list_runs(vid)
            assert statuses[0]["status"]=="completed"
            assert "target_verify" in calls
            assert calls.index("target_verify") > calls.index("target_publish")
            assert source.closed and target.closed
            assert pra_live.latest_status(vid)["last_successful_sync"]
        finally:
            _remove(client,h,vid,sites,clusters,plans)


def test_unverified_checkpoint_is_uncertain_and_cannot_be_replicated_again(monkeypatch):
    with TestClient(app) as client:
        h=login(client)
        vid,sites,clusters,plans=_setup(client,h,monkeypatch)
        try:
            calls, source, target=_stub_live(monkeypatch, proof_ok=False)
            run=pra_live.start(vid,"ci",approved=True)
            pra_live._perform(run["run_id"])
            latest=pra_live.latest_status(vid)
            assert latest["replication_status"]=="uncertain"
            assert latest["last_successful_sync"] is None
            assert calls.count("target_verify")==1
            assert "target_abort" not in calls
            try:
                pra_live.start(vid, "ci", approved=True)
            except pra_live.LiveReplicationError as exc:
                assert "indéterminé" in str(exc)
            else:
                raise AssertionError("Must refuse to overwrite an uncertain checkpoint")
            assert source.closed and target.closed
        finally:
            _remove(client,h,vid,sites,clusters,plans)
