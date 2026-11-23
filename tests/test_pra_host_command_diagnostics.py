"""Stable, non-secret diagnostics for failed Proxmox LAB preparation."""
import json
import types
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"agents/linux"))
import pra_host_worker as host
from app import pra_live


class FakeStorage:
    def __init__(self,*args,**kwargs): pass
    def inspect(self): raise host.HostError("Host operation rejected: pvesh")
    def _node_capacity(self): raise AssertionError("Must stop on config error")


def test_storage_config_error_has_exact_stage(monkeypatch):
    monkeypatch.setattr(host,"PveLvmTarget",FakeStorage)
    with pytest.raises(host.HostError,match="PRA_STEP_STORAGE_CONFIG"):
        host.lvm_lab_prepare_standby({
            "lab_ack":"DISPOSABLE-LAB-ONLY","vmid":183,
            "protected_vm_id":1,"name":"REP_test","storage":"lab-lvm"
        })


def test_helper_error_exposes_only_allowlisted_command(monkeypatch):
    class Channel:
        def settimeout(self,_): pass
        def recv_exit_status(self): return 1
    class Stdout:
        channel=Channel()
        def read(self,n):
            return json.dumps({"ok":False,"error":
                "PRA_STEP_STORAGE_CONFIG : Host operation rejected: pvesh; secret=abc"}).encode()
    class SSH:
        def exec_command(self,*args,**kwargs): return None, Stdout(), None
    with pytest.raises(pra_live.LiveReplicationError) as raised:
        pra_live._run_helper(SSH(),"lvm_lab_prepare_standby",{"vmid":183})
    assert "PRA_STEP_STORAGE_CONFIG" in str(raised.value)
    assert "pvesh" in str(raised.value)
    assert "secret" not in str(raised.value)
