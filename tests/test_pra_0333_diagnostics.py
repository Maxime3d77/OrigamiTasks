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
    # Regression from the real audit: the exception formatter adds a space
    # before the colon. Both spellings are a pre-SSH controller rejection.
    for diagnostic in (
        'LiveReplicationError: Action PRA non autorisée',
        'LiveReplicationError : Action PRA non autorisée',
    ):
        variant={**good,'details':'Activation non confirmée : '+diagnostic+
                 '. Contrôler les hôtes et volumes avant tout nouvel essai'}
        monkeypatch.setattr(pra_lvm_activation.db,'session',lambda row=variant:C(row))
        monkeypatch.setattr(pra_lvm_activation,'start',
                            lambda *args:{'status':'queued','activation_id':9})
        assert pra_lvm_activation.retry_authorization_bug(
            vm_id,'admin','DISPOSABLE-LAB-ONLY')['status']=='queued'
    monkeypatch.setattr(pra_lvm_activation.db,'session',lambda:C(good))
    started=[]
    monkeypatch.setattr(pra_lvm_activation,'start',lambda *args:started.append(args) or {'status':'queued'})
    assert pra_lvm_activation.retry_authorization_bug(vm_id,'admin','DISPOSABLE-LAB-ONLY')['status']=='queued'
    assert started==[(vm_id,'admin','DISPOSABLE-LAB-ONLY')]


def test_lvm_provision_available_on_controller_and_on_host():
    from app import pra_live
    source=(ROOT/'agents/linux/pra_host_worker.py').read_text()
    assert source.count('"lvm_lab_prepare_standby": lvm_lab_prepare_standby')==1
    assert '"lvm_lab_prepare_standby"' in (ROOT/'app/pra_live.py').read_text()
    assert 'def lvm_lab_prepare_standby(data):' in source


def test_update_errors_report_safe_stage_not_remote_secrets():
    source=(ROOT/'app/agent_updates.py').read_text()
    for phase in ('ORIGAMI_STAGE_VALIDATE','ORIGAMI_STAGE_BACKUP',
                  'ORIGAMI_STAGE_INSTALL','ORIGAMI_STAGE_RESTART',
                  'ORIGAMI_STAGE_HEALTH'):
        assert phase in source
    assert "str(exc)[:400]" in source
    assert 'Remplacement distant non confirmé' not in source


def test_uncertain_remote_provision_retries_only_if_target_vmid_absent(monkeypatch):
    from app import pra_lvm_activation
    vm={'id':17,'engine':'qmp_live_incremental','state':'planned',
        'target_dc_id':2,'target_node':'sd-bearnaise','target_vmid':183}
    last={'id':2,'status':'uncertain','phase':'blocked','replication_run_id':None,
          'details':('Activation non confirmée : LiveReplicationError : '
                     'Échec commande agent PRA: lvm_lab_prepare_standby '
                     '(vérifier root helper)')}
    class C:
        def __enter__(self): return self
        def __exit__(self,*a): return False
        def execute(self,sql,args=()):
            class Res:
                def __init__(self,one):self.one=one
                def fetchone(self):return self.one
            if 'FROM pra_lvm_activations' in sql:
                return Res(last)
            return Res(None)
    monkeypatch.setattr(pra_lvm_activation.db,'session',lambda:C())
    monkeypatch.setattr(pra_lvm_activation.db,'now',lambda:'2026-11-23')
    monkeypatch.setattr(pra_lvm_activation.db,'audit',lambda *args:None)
    monkeypatch.setattr(pra_lvm_activation.dr_protected_vms,'_one',lambda _:vm)
    resources=[{'type':'node','node':'sd-bearnaise','status':'online'}]
    monkeypatch.setattr(pra_lvm_activation.dr_protected_vms,'_inventory',
                        lambda _:({}, {}, resources))
    calls=[]
    monkeypatch.setattr(pra_lvm_activation,'start',
                        lambda *args:(calls.append(args) or {'status':'queued'}))
    assert pra_lvm_activation.reconcile_failed_provision(
        17,'admin','DISPOSABLE-LAB-ONLY')['status']=='queued'
    assert len(calls)==1
    resources.append({'type':'qemu','node':'sd-bearnaise','vmid':183,
                      'status':'stopped'})
    with pytest.raises(ValueError,match='VMID cible existe'):
        pra_lvm_activation.reconcile_failed_provision(17,'admin','DISPOSABLE-LAB-ONLY')
    assert len(calls)==1


def test_uncertain_provision_will_not_retry_after_transfer(monkeypatch):
    from app import pra_lvm_activation
    monkeypatch.setattr(pra_lvm_activation.dr_protected_vms,'_one',
                        lambda _: {'engine':'qmp_live_incremental',
                                   'state':'planned'})
    class C:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def execute(self,sql,args=()):
            class R:
                def fetchone(self):
                    return {'id':2,'status':'uncertain','phase':'blocked',
                            'replication_run_id':3,'details':'Échec commande agent PRA'}
            return R()
    monkeypatch.setattr(pra_lvm_activation.db,'session',lambda:C())
    with pytest.raises(ValueError,match='préparation de VM identifiable'):
        pra_lvm_activation.reconcile_failed_provision(17,'admin','DISPOSABLE-LAB-ONLY')
