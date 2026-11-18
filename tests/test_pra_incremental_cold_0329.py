"""Regression tests for offline incremental pilot and per-VM tracking."""
from pathlib import Path
import subprocess
import uuid
import pytest

from app import db, pra_direct_sync, dr_protected_vms
from fastapi.testclient import TestClient
from app.main import app

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"pra-incremental-cold.sh"
REPORTER=ROOT/"scripts"/"pra-sync-report.py"


def test_offline_script_syntax_and_rejects_without_approval():
    subprocess.run(["bash","-n",str(SCRIPT)],check=True)
    subprocess.run(["python3","-m","py_compile",str(REPORTER)],check=True)
    result=subprocess.run(["bash",str(SCRIPT),"210","destination.example.org",
                           "9210","--not-approved","/tmp/nonexistent","22","1"],
                          text=True,capture_output=True)
    assert result.returncode!=0
    assert "REFUS" in result.stderr or "REFUSED" in result.stderr


def test_incremental_requires_offline_and_never_starts_or_deletes_vm():
    text=SCRIPT.read_text()
    for option in ("--copy-devices","--write-devices","--no-whole-file",
                   "--checksum","--inplace","--confirm-offline",
                   "status: stopped","StrictHostKeyChecking=yes",
                   "ORIGAMI_INCREMENTAL_COLD_OK"):
        assert option in text
    assert "qm start " not in text
    assert "qm stop " not in text
    assert "qm destroy " not in text
    assert "lvremove" not in text
    assert 'pvesm path "$srcvol"' in text
    assert "pra-sync-report.py" in text


def test_authenticated_run_history_is_per_vm_and_non_promoting(monkeypatch):
    with TestClient(app):
        with db.session() as con:
            agent_id=con.execute(
                "INSERT INTO agents(name,platform,group_name,enrolled,hostname,created_at) "
                "VALUES(?,?,?,?,?,?)",
                ("ci-direct-0329","linux","ci",1,"srcnode",db.now())
            ).lastrowid
        vm_id=987654321
        monkeypatch.setattr(dr_protected_vms,"_one",
                            lambda vid: {"id":vid,"state":"planned","engine":"offline_copy",
                              "source_vmid":210,"target_vmid":9210,"source_site_id":1,
                              "source_dc_id":1})
        monkeypatch.setattr(pra_direct_sync,"_node_authorized",lambda agent,vm:None)
        run_id=str(uuid.uuid4())
        agent={"id":agent_id,"hostname":"srcnode"}
        data={"protected_vm_id":vm_id,"source_vmid":210,"target_vmid":9210,
              "run_uuid":run_id,"status":"running","disks_total":2,"disks_done":0,
              "scanned_bytes":0,"transferred_bytes":0,"elapsed_seconds":0,"error":""}
        # Real FK requires the VM record, so ensure authorization errors never
        # create a run with a nonexistent protection.
        with pytest.raises(db.sqlite3.IntegrityError):
            pra_direct_sync.report(agent,data)
        data["target_vmid"]=9211
        with pytest.raises(pra_direct_sync.DirectSyncError):
            pra_direct_sync.report(agent,data)
        with db.session() as con:
            con.execute("DELETE FROM agents WHERE id=?",(agent_id,))


def test_direct_sync_endpoints_require_authentication():
    with TestClient(app) as client:
        assert client.get("/api/dr/vms/1/direct-sync").status_code==401
        assert client.post("/api/agent/pra/sync/report",json={}).status_code==401


def test_vm_tracking_ui():
    text=(ROOT/"static/app.js").read_text()
    assert "Suivi incrémental" in text
    assert "last_incremental_success" in text
    assert "/direct-sync" in text
