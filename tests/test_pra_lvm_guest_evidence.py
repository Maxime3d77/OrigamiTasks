"""Checkpoint-scoped LAB console login observation: pure audit, no guest mutation."""
import json
import sqlite3
import pytest

from app import pra_lvm_guest_evidence as evidence


@pytest.fixture
def setup(monkeypatch,tmp_path):
    file=tmp_path/"pra-guest-evidence.sqlite3"
    monkeypatch.setattr(evidence.db.config,"DB",file)
    monkeypatch.setattr(evidence.db.config,"DATA",tmp_path)
    with evidence.db.session() as con:
        con.execute("""CREATE TABLE pra_lvm_lab_runs (
            id INTEGER PRIMARY KEY,protected_vm_id INTEGER,mode TEXT,
            test_vmid INTEGER,checkpoint_token TEXT,status TEXT,result_json TEXT
        )""")
        con.execute("""INSERT INTO pra_lvm_lab_runs
            (id,protected_vm_id,mode,test_vmid,checkpoint_token,status,result_json)
            VALUES (10,17,'full',NULL,?,'completed',NULL)""",("f"*32,))
        manifest=json.dumps({"test_vmid":4301,"network_isolated":True,
                             "test_volumes":{"scsi0":"lab:clone-volume"}})
        con.execute("""INSERT INTO pra_lvm_lab_runs
            (id,protected_vm_id,mode,test_vmid,checkpoint_token,status,result_json)
            VALUES (11,17,'restore',4301,?,'completed',?)""",("f"*32,manifest))
    evidence.initialize()
    events=[]
    monkeypatch.setattr(evidence.db,"audit",lambda *a:events.append(a))
    monkeypatch.setattr(evidence.dr_protected_vms,"_one",lambda i:{
        "id":i,"engine":"qmp_live_incremental","state":"planned"})
    return events


def test_record_observation_and_never_grant_failover(setup):
    events=setup
    result=evidence.record(17,11,"admin","OS-LOGIN-VERIFIED",
                           "Connexion root réussie sur console Proxmox")
    assert result["status"]=="observed"
    assert result["application_verified"] is False
    assert result["production_failover_authorized"] is False
    rows=evidence.history(17)
    assert len(rows)==1
    assert rows[0]["test_vmid"]==4301
    assert rows[0]["checkpoint_token"]=="f"*32
    assert rows[0]["actor"]=="admin"
    assert evidence.current(17)["test_vmid"]==4301
    assert events[0][1]=="pra.lvm.guest.os_login_observed"


def test_historical_observation_does_not_validate_next_incremental(setup):
    evidence.record(17,11,"admin","OS-LOGIN-VERIFIED",
                    "Connexion root réussie sur console Proxmox")
    with evidence.db.session() as con:
        con.execute("""INSERT INTO pra_lvm_lab_runs
            (id,protected_vm_id,mode,test_vmid,checkpoint_token,status,result_json)
            VALUES (12,17,'incremental',NULL,?,'completed',NULL)""",("a"*32,))
    assert evidence.current(17) is None
    assert len(evidence.history(17))==1


def test_reject_duplicate_or_unverified_login(setup):
    with pytest.raises(ValueError,match="Confirmation"):
        evidence.record(17,11,"admin","", "Connexion root réussie")
    with pytest.raises(ValueError,match="8 à 400"):
        evidence.record(17,11,"admin","OS-LOGIN-VERIFIED","ok")
    evidence.record(17,11,"admin","OS-LOGIN-VERIFIED","Shell Debian opérationnel")
    with pytest.raises(ValueError,match="déjà enregistrée"):
        evidence.record(17,11,"admin","OS-LOGIN-VERIFIED","Shell Debian opérationnel")


def test_reject_non_isolated_or_unverified_clone(setup):
    with evidence.db.session() as con:
        con.execute("UPDATE pra_lvm_lab_runs SET result_json=? WHERE id=11",
            (json.dumps({"test_vmid":4301,"network_isolated":False,
                         "test_volumes":{"scsi0":"lab:clone-volume"}}),))
    with pytest.raises(ValueError,match="isolation"):
        evidence.record(17,11,"admin","OS-LOGIN-VERIFIED",
                        "Connexion root réussie sur console Proxmox")


def test_api_routes_are_explicitly_admin_protected():
    from pathlib import Path
    code=(Path(__file__).resolve().parents[1]/"app/main.py").read_text()
    assert "@app.post('/api/dr/vms/{vm_id}/lvm-lab/guest-evidence')" in code
    assert "security.require(request,role='admin',mutate=True)" in code
