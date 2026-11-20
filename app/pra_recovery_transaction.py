"""Generic PRA failover/failback transaction kernel (NOT wired to production).

This is an orchestration contract for independently implemented, authenticated
services. No arbitrary boolean or controller-local DB flag can grant a VM
power-on: the external start_guard MUST revalidate an exclusive witness lease
at the hypervisor's start boundary, including manual/HA starts.

IMPORTANT: no real Proxmox, Redfish/IPMI or witness adapter is installed here.
This module has no production endpoint and is tested with failure-injection
fakes. It must not be enabled until external witness, fencing and per-host
start guards exist and have passed fault-injection testing.
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class DRUnsafe(RuntimeError):
    """The requested recovery transition cannot prove exclusive writes."""


@dataclass(frozen=True)
class Protection:
    ident: str
    primary_cluster: str
    secondary_cluster: str
    primary_vmid: int
    secondary_vmid: int
    master_cluster: str
    epoch: int

    def __post_init__(self):
        if (not self.ident or
                not self.primary_cluster or not self.secondary_cluster or
                self.primary_cluster == self.secondary_cluster or
                self.master_cluster not in (self.primary_cluster, self.secondary_cluster) or
                any(type(v) is not int or v < 100 for v in
                    (self.primary_vmid, self.secondary_vmid, self.epoch))):
            raise DRUnsafe("Invalid PRA protection identity")


@dataclass(frozen=True)
class CopyProof:
    generation: str
    destination_cluster: str
    verified_disks: bool
    isolated_boot_verified: bool
    application_verified: bool
    source_writes_stopped: bool = False


@dataclass(frozen=True)
class FenceProof:
    source_cluster: str
    vmid: int
    confirmed_off: bool
    verified_identity: str


@dataclass(frozen=True)
class Lease:
    protection_id: str
    cluster_id: str
    epoch: int
    witness_receipt: str


@runtime_checkable
class OperatorApproval(Protocol):
    def require_approval(self, protection_id: str, action: str, actor: str) -> None: ...


@runtime_checkable
class Witness(Protocol):
    def prepare(self, protection_id: str, old_cluster: str,
                new_cluster: str, expected_epoch: int) -> str: ...
    def verify_prepared(self, token: str, protection_id: str,
                        expected_epoch: int) -> None: ...
    def commit(self, token: str, fence: FenceProof, generation: str) -> Lease: ...
    def verify_current(self, lease: Lease) -> None: ...


@runtime_checkable
class Fence(Protocol):
    def fence_and_verify(self, cluster_id: str, vmid: int, transfer: str) -> FenceProof: ...
    def verify_proof(self, proof: FenceProof, transfer: str) -> None: ...


@runtime_checkable
class VMRuntime(Protocol):
    def verify_copy(self, to_cluster: str, vmid: int, generation: str) -> CopyProof: ...
    def require_stopped(self, cluster_id: str, vmid: int) -> None: ...
    def arm_exclusive_start_guard(self, cluster_id: str, vmid: int, lease: Lease) -> None: ...
    def guarded_start(self, cluster_id: str, vmid: int, lease: Lease) -> None: ...
    def verify_running_with_guard(self, cluster_id: str, vmid: int, lease: Lease) -> None: ...


class RecoveryController:
    """One conservative, supervised failover OR failback attempt.

    The adapters must implement exclusive distributed fencing and an
    independently enforced VM start guard. If any step fails, do NOT start
    the former master or attempt rollback automatically. Witness/host agents
    must persist their own progress; loss of this Python process is NOT a
    substitute for a distributed transaction.
    """

    def __init__(self, approvals: OperatorApproval, witness: Witness,
                 fence: Fence, vms: VMRuntime):
        for provider, protocol in (
                (approvals, OperatorApproval), (witness, Witness),
                (fence, Fence), (vms, VMRuntime)):
            if not isinstance(provider, protocol):
                raise DRUnsafe("Missing required independent recovery adapter")
        self.approvals = approvals
        self.witness = witness
        self.fence = fence
        self.vms = vms

    def promote(self, protection: Protection, to_cluster: str,
                generation: str, actor: str) -> Lease:
        if to_cluster not in (protection.primary_cluster, protection.secondary_cluster):
            raise DRUnsafe("Destination is not part of this protection")
        if to_cluster == protection.master_cluster:
            raise DRUnsafe("Target is already the master")
        if not generation or not actor:
            raise DRUnsafe("Missing immutable checkpoint generation or actor")

        old_cluster = protection.master_cluster
        old_vmid = (protection.primary_vmid if old_cluster == protection.primary_cluster
                    else protection.secondary_vmid)
        new_vmid = (protection.primary_vmid if to_cluster == protection.primary_cluster
                    else protection.secondary_vmid)

        # All checks preceding a witness change are read-only. No generic
        # copy proof may be constructed by the caller; runtime obtains it.
        self.approvals.require_approval(protection.ident, "promote", actor)
        copy = self.vms.verify_copy(to_cluster, new_vmid, generation)
        if (not isinstance(copy, CopyProof) or
                copy.destination_cluster != to_cluster or
                copy.generation != generation or
                copy.verified_disks is not True or
                copy.isolated_boot_verified is not True or
                copy.application_verified is not True):
            raise DRUnsafe("Recovery copy lacks verified boot and application evidence")
        self.vms.require_stopped(to_cluster, new_vmid)

        # The independent witness freezes the old epoch BEFORE fencing;
        # a real implementation must refuse concurrent transfers.
        transfer = self.witness.prepare(
            protection.ident, old_cluster, to_cluster, protection.epoch)
        if not transfer or not isinstance(transfer, str):
            raise DRUnsafe("No independent witness transfer receipt")
        self.witness.verify_prepared(transfer, protection.ident, protection.epoch)

        # The old master may be running during normal synchronous or QMP
        # replication. It must be positively fenced, not merely unreachable.
        proof = self.fence.fence_and_verify(old_cluster, old_vmid, transfer)
        if (not isinstance(proof, FenceProof) or
                proof.source_cluster != old_cluster or
                proof.vmid != old_vmid or
                proof.confirmed_off is not True or
                not proof.verified_identity):
            raise DRUnsafe("Source fence could not be independently verified")
        self.fence.verify_proof(proof, transfer)
        self.vms.require_stopped(to_cluster, new_vmid)

        lease = self.witness.commit(transfer, proof, generation)
        if (not isinstance(lease, Lease) or
                lease.protection_id != protection.ident or
                lease.cluster_id != to_cluster or
                lease.epoch != protection.epoch + 1 or
                not lease.witness_receipt):
            raise DRUnsafe("Witness issued unexpected authority")
        self.witness.verify_current(lease)

        # Both arm_exclusive_start_guard and guarded_start MUST enforce the
        # witness token on the host, not rely on the controller's assertion.
        self.vms.arm_exclusive_start_guard(to_cluster, new_vmid, lease)
        self.witness.verify_current(lease)
        self.vms.guarded_start(to_cluster, new_vmid, lease)
        self.vms.verify_running_with_guard(to_cluster, new_vmid, lease)
        return lease
