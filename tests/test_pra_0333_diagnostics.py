"""Regression contracts for the Nov 2026 PRA diagnostic/UI patch.

These checks never touch Proxmox. They guard the controller action allowlist,
version parity, status drill-down and safe resumption policy.
"""
from pathlib import Path

import pytest

from app import config, pra_live, pra_lvm_activation, agent_updates

ROOT = Path(__file__).resolve().parents[1]


def test_supported_lab_host_actions_are_explicitly_whitelisted():
    # A rejected action is a local error: it must not open an SSH channel.
    class ForbiddenChannel:
        def exec_command(self,*args,**kwargs):
            raise AssertionError('Never invoke unlisted host command')
    with pytest.raises(pra_live.LiveReplicationError,match='Action PRA non autorisée'):
        pra_live._run_helper(ForbiddenChannel(),'qm start',{})
    source=(ROOT/'app/pra_live.py').read_text()
    for action in ('lvm_lab_prepare_standby','lvm_lab_publish_standby','lvm_lab_test_boot'):
        assert '\"'+action+'\"' in source


def test_manager_agent_bundle_versions_are_aligned():
    assert config.APP_VERSION == '0.3.33-rc1'
    versions=agent_updates.available_versions()
    assert versions=={'linux':'0.3.33','windows':'0.3.33'}
    assert not agent_updates.outdated('0.3.34','0.3.33')


def test_protection_ui_search_and_diagnostics_are_connected():
    ui=(ROOT/'static/app.js').read_text()
    for phrase in ('praVMSearch','dr-vm-check-vmid','dr-vm-lvm-lab-open',
                   'protection_note','reference','auditCategory','auditSearch',
                   'retry-local-error','lvm_effective_status'):
        assert phrase in ui


def test_known_pre_ssh_rejection_only_can_be_retried(monkeypatch):
    vm_id=17
    class C:
        def __init__(self,dbrow,hascopy=False):
            self.dbrow=dbrow
            self.hascopy=hascopy
            self.queries=[]
        def __enter__(self):return self
        def __exit__(self,*exc):return False
        def execute(self,sql,args=()):
            self.queries.append(sql)
            class R:
                def __init__(self,value):self.value=value
                def fetchone(self):return self.value
            if sql.startswith('SELECT id,status,phase,details'):
                return R(self.dbrow)
            if sql.startswith('SELECT 1 FROM pra_lvm_lab_runs'):
                return R({'id':7} if self.hascopy else None)
            return R(None)
    good={'id':8,'status':'uncertain','phase':'blocked','replication_run_id':None,
          'details':'Activation non confirmée : LiveReplicationError: Action PRA non autorisée'}
    logs=[]
    monkeypatch.setattr(pra_lvm_activation.db,'audit',lambda *args:logs.append(args))
    monkeypatch.setattr(pra_lvm_activation.db,'now',lambda:'2026-11-23T08:38:00+01:00')
    for patch,hascopy in [
        ({**good,'details':'Socket timeout'},False),
        ({**good,'replication_run_id':5},False),
        (good,True)
    ]:
        monkeypatch.setattr(pra_lvm_activation.db,'session',lambda p=patch,h=hascopy:C(p,h))
        with pytest.raises(ValueError,match='Reprise automatique interdite|vérification manuelle'):
            pra_lvm_activation.retry_authorization_bug(vm_id,'admin','DISPOSABLE-LAB-ONLY')
    monkeypatch.setattr(pra_lvm_activation.db,'session',lambda:C(good))
    started=[]
    monkeypatch.setattr(pra_lvm_activation,'start',lambda *args:started.append(args) or {'status':'queued'})
    assert pra_lvm_activation.retry_authorization_bug(vm_id,'admin','DISPOSABLE-LAB-ONLY')['status']=='queued'
    assert started==[(vm_id,'admin','DISPOSABLE-LAB-ONLY')]
