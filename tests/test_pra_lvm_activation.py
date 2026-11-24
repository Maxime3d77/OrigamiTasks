"""LAB activation contracts: no manual terminal step and fail-closed queue."""
import pytest
from app import pra_lvm_activation


class Session:
    def __init__(self): self.queries=[]
    def __enter__(self): return self
    def __exit__(self,*exc): return False
    def execute(self,sql,args=()):
        self.queries.append((sql,args))
        class Cursor:
            lastrowid=73
            def fetchone(self): return None
        return Cursor()


def test_activation_checks_explicit_lab_ack_before_db(monkeypatch):
    with pytest.raises(ValueError,match="recette"):
        pra_lvm_activation.start(3,"admin","")
    

def test_activation_enqueues_one_supervised_background_pipeline(monkeypatch):
    vm={"id":3,"engine":"qmp_live_incremental","state":"planned"}
    monkeypatch.setattr(pra_lvm_activation.dr_protected_vms,"_one",lambda _:vm)
    session=Session()
    monkeypatch.setattr(pra_lvm_activation.db,"session",lambda:session)
    monkeypatch.setattr(pra_lvm_activation.db,"now",lambda:"2026-11-20T00:00:00+00:00")
    monkeypatch.setattr(pra_lvm_activation.db,"audit",lambda *a:None)
    started=[]
    class Thread:
        def __init__(self,**kwargs): self.data=kwargs
        def start(self): started.append(self.data)
    monkeypatch.setattr(pra_lvm_activation.threading,"Thread",Thread)
    result=pra_lvm_activation.start(3,"admin","DISPOSABLE-LAB-ONLY")
    assert result["status"]=="queued" and result["lab_only"] is True
    assert len(started)==1
    assert started[0]["target"]==pra_lvm_activation._work
    assert started[0]["args"]==(73,3)


def test_activation_worker_does_not_start_copy_after_failed_provision(monkeypatch):
    session=Session()
    class Cursor:
        def fetchone(self): return {"actor":"admin"}
    session.execute=lambda *args:Cursor()
    monkeypatch.setattr(pra_lvm_activation.db,"session",lambda:session)
    stages=[]
    monkeypatch.setattr(pra_lvm_activation,"_step",lambda *args:stages.append(args))
    monkeypatch.setattr(pra_lvm_activation.pra_lvm_standby,"prepare",lambda *args:{
        "target_stopped":False,"network_isolated":False})
    calls=[]
    monkeypatch.setattr(pra_lvm_activation.pra_lvm_jobs,"start",lambda *args,**kw:calls.append(args))
    pra_lvm_activation._work(73,3)
    assert not calls
    assert stages[-1][1]=="uncertain"
