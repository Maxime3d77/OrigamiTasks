"""Lab-only LVM backend contract tests; no real PVE volumes are touched."""
import importlib.util
import json
from pathlib import Path
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1] / "agents/linux"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("pra_lvm_checkpoint_lab", ROOT / "pra_lvm_checkpoint.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
from pra_lvm_target import PveLvmTarget


class FakePveTarget(PveLvmTarget):
    def __init__(self, root):
        super().__init__("lab-lvm", 321, lambda *args: "", journal_root=root,
                         protected_vm_id=17)
        self.files = {}
        self.calls = []
        self.root = Path(root)
    def plan(self, sizes, mode, previous=None):
        self.calls.append(("plan",mode))
        self.validate_sizes(sizes)
        if mode == "incremental" and not previous:
            raise AssertionError("Expected committed base")
        return {"storage":self.storage,"vmid":self.vmid,"mode":mode,
                "disks":dict(sorted(sizes.items()))}
    def stage_allocations(self, plan, *, approved=False, previous=None):
        assert approved is True
        self._journal_path()
        token=uuid.uuid4().hex
        assigned={}
        for index,(slot,size) in enumerate(sorted(plan["disks"].items())):
            volid=f"{self.storage}:vm-{self.vmid}-disk-{int(token[:12],16)+index}.qcow2"
            dest=self.root / ("disk-" + str(len(self.files)) + ".qcow2")
            dest.touch()
            self.files[volid]=dest
            assigned[slot]={"volid":volid,"size":size}
        self.calls.append(("pvesm","alloc",len(assigned)))
        mod._atomic(self.journal / "pending.json",
                    {"token":token,"status":"staged_only","allocated":assigned})
        return {"token":token,"allocated":assigned}
    def resolve_existing(self, volid, *, verified_block=True):
        return str(self.files[volid])


@pytest.fixture
def setup(tmp_path):
    target=FakePveTarget(tmp_path)
    chains={}
    def fake_qemu(*args):
        if args[:2]==("qemu-img","create"):
            path=args[-2] if args[-1].isdigit() else args[-1]
            backing=args[args.index("-b")+1] if "-b" in args else None
            chains[path]=[path]+(chains[backing] if backing else [])
            return "created"
        if args[:2]==("qemu-img","info"):
            path=args[-1]
            return json.dumps([{"filename":p,"format":"qcow2"} for p in chains[path]])
        if args[:2]==("qemu-img","check"):
            assert args[-1] in chains
            return "no errors"
        raise AssertionError(args)
    return mod.LvmCheckpointStore(target,command=fake_qemu),target,chains


def test_full_to_incremental_volumes_never_reuse_old_lv(setup):
    store,target,chains=setup
    sizes={"scsi0":4096,"scsi1":8192}
    seed=store.begin(mode="full",sizes=sizes,source_vm_uuid="lab-vm-one",
                     operator_approved=True)
    assert seed["status"]=="staging"
    assert not store.current()
    assert seed["cluster_visible"] is False
    assert not seed["failover_enabled"]
    for slot in sizes:
        assert Path(store.image_path(slot,seed["token"])).exists()
    with pytest.raises(mod.LvmCheckpointError,match="proof is uncertain"):
        store.commit(seed["token"])
    commit=store.commit(seed["token"],writers_closed=True,source_jobs_verified=True)
    assert commit["status"]=="committed" and commit["depth"]==1
    delta=store.begin(mode="incremental",sizes=sizes,source_vm_uuid="lab-vm-one",
                      operator_approved=True)
    assert delta["previous_token"]==seed["token"]
    for slot in sizes:
        assert delta["disks"][slot]["volid"]!=seed["disks"][slot]["volid"]
    committed=store.commit(delta["token"],writers_closed=True,source_jobs_verified=True)
    assert committed["depth"]==2
    assert store.current()["token"]==delta["token"]
    for slot in sizes:
        path=str(target.files[delta["disks"][slot]["volid"]])
        previous=str(target.files[seed["disks"][slot]["volid"]])
        assert chains[path]==[path,previous]
    assert not any(call[0]=="pvesm" and call[1]=="free" for call in target.calls)
    assert all(p.exists() for p in target.files.values())
    ledger=json.loads(store.owned.read_text())
    assert len(ledger["generations"])==2
    assert {x["token"] for x in ledger["generations"]}=={seed["token"],delta["token"]}


def test_no_allocation_without_explicit_approval(setup):
    store,target,chains=setup
    with pytest.raises(mod.LvmCheckpointError,match="approval"):
        store.begin(mode="full",sizes={"scsi0":4096},
                    source_vm_uuid="lab-vm-one")
    assert not target.files


def test_incremental_requires_a_matching_committed_base(setup):
    store,target,_=setup
    with pytest.raises(mod.LvmCheckpointError,match="FULL reseed required"):
        store.begin(mode="incremental",sizes={"scsi0":4096},
                    source_vm_uuid="lab-vm-one",operator_approved=True)
    assert not target.files


def test_quarantine_preserves_all_existing_volumes(setup):
    store,target,_=setup
    first=store.begin(mode="full",sizes={"scsi0":4096},
                      source_vm_uuid="lab-vm-one",operator_approved=True)
    result=store.abort(first["token"])
    assert result=={"quarantined":True,"freed":0,"last_checkpoint_preserved":True}
    assert store._read(store.pending)["status"]=="uncertain"
    assert len(target.files)==1 and all(p.exists() for p in target.files.values())
    with pytest.raises(mod.LvmCheckpointError,match="reconciliation"):
        store.begin(mode="full",sizes={"scsi0":4096},
                    source_vm_uuid="lab-vm-one",operator_approved=True)


def test_missing_qa_evidence_fails_closed(setup):
    store,target,_=setup
    first=store.begin(mode="full",sizes={"scsi0":4096},
                      source_vm_uuid="lab-vm-one",operator_approved=True)
    for writers_closed, jobs in ((True,False),(False,True),(False,False)):
        with pytest.raises(mod.LvmCheckpointError):
            store.commit(first["token"],
                         writers_closed=writers_closed,
                         source_jobs_verified=jobs)
    assert store.current() is None


def test_source_generation_mismatch_blocks_incremental(setup):
    store,target,_=setup
    first=store.begin(mode="full",sizes={"scsi0":4096},
                      source_vm_uuid="lab-vm-one",operator_approved=True)
    store.commit(first["token"],writers_closed=True,source_jobs_verified=True)
    with pytest.raises(mod.LvmCheckpointError,match="FULL reseed"):
        store.begin(mode="incremental",sizes={"scsi0":4096},
                    source_vm_uuid="lab-vm-new",operator_approved=True)


def test_failed_qcow2_init_is_uncertain_not_auto_rolled_back(setup):
    store,target,chains=setup
    def broken(*args):
        raise mod.LvmCheckpointError("image format rejected")
    store.command=broken
    with pytest.raises(mod.LvmCheckpointError,match="rejected"):
        store.begin(mode="full",sizes={"scsi0":4096},
                    source_vm_uuid="lab-vm-one",operator_approved=True)
    record=store._read(store.pending)
    assert record["status"]=="uncertain"
    assert record["checkpoint_committed"] is False
    assert not store.current()
    assert len(target.files)==1
    assert all(p.exists() for p in target.files.values())
