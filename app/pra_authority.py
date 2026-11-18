"""Fail-closed, pure PRA role decisions.

This module is an isolated guard building block. It is NOT connected to Proxmox
start hooks, agents, HA, network fencing or replication yet. Never use this
module's return values alone as proof that a source cluster has been fenced.
"""
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional


class PRAError(ValueError):
    """A PRA state transition was rejected."""


class Phase(str, Enum):
    INITIAL = "initial"
    SYNCING = "syncing"
    READY = "ready"
    PROMOTING = "promoting"
    FAILED_OVER = "failed_over"
    FAILING_BACK = "failing_back"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Protection:
    """Logical identity survives a node migration inside a PVE cluster."""

    protection_id: str
    source_cluster_id: str
    source_vmid: int
    destination_cluster_id: str
    destination_vmid: int
    master_cluster_id: str
    epoch: int = 1
    phase: Phase = Phase.INITIAL
    last_verified_generation: Optional[str] = None

    def __post_init__(self):
        if not self.protection_id or not self.source_cluster_id or not self.destination_cluster_id:
            raise PRAError("Protection and cluster identifiers are required")
        if self.source_cluster_id == self.destination_cluster_id:
            raise PRAError("PRA requires two distinct clusters")
        if self.master_cluster_id not in (self.source_cluster_id, self.destination_cluster_id):
            raise PRAError("Master must be one of the configured clusters")
        if self.source_vmid <= 0 or self.destination_vmid <= 0 or self.epoch < 1:
            raise PRAError("Invalid VMID or epoch")

    def is_master(self, cluster_id: str) -> bool:
        return cluster_id == self.master_cluster_id

    def node_moved(self, cluster_id: str, new_node: str) -> "Protection":
        """A node move does not transfer authority to the other cluster."""
        if cluster_id not in (self.source_cluster_id, self.destination_cluster_id) or not new_node:
            raise PRAError("Unknown cluster or node")
        return self


@dataclass(frozen=True)
class PromotionEvidence:
    """External attestations, verified by a real fencing/witness adapter."""

    witness_quorum: bool
    old_master_fenced: bool
    destination_copy_verified: bool
    replication_stopped: bool
    independent_witness: bool
    current_epoch: int
    generation: str


def start_allowed(protection: Protection, cluster_id: str, witnessed_epoch: int,
                  writable_storage_owner: bool, fencing_guard_installed: bool) -> bool:
    """Conservative local decision; MUST be enforced on every start path."""
    return (
        protection.is_master(cluster_id)
        and witnessed_epoch == protection.epoch
        and writable_storage_owner
        and fencing_guard_installed
        and protection.phase not in (Phase.PROMOTING, Phase.FAILING_BACK, Phase.BLOCKED)
    )


def promote(protection: Protection, destination_cluster_id: str,
            evidence: PromotionEvidence) -> Protection:
    """Role handover; never auto-promote after heartbeat or API loss alone."""
    if destination_cluster_id not in (
        protection.source_cluster_id, protection.destination_cluster_id
    ):
        raise PRAError("Unknown destination cluster")
    if protection.master_cluster_id == destination_cluster_id:
        raise PRAError("Destination is already master")
    if evidence.current_epoch != protection.epoch:
        raise PRAError("Stale witness epoch")
    if not all((
        evidence.witness_quorum,
        evidence.independent_witness,
        evidence.old_master_fenced,
        evidence.destination_copy_verified,
        evidence.replication_stopped,
        bool(evidence.generation),
    )):
        raise PRAError("Promotion requires independent witness, fencing and verified copy")
    return replace(
        protection,
        master_cluster_id=destination_cluster_id,
        epoch=protection.epoch + 1,
        phase=(Phase.FAILED_OVER if destination_cluster_id == protection.destination_cluster_id
               else Phase.READY),
        last_verified_generation=evidence.generation,
    )


def override_request_allowed(admin_approved: bool, reason: str, ticket: str,
                             witness_quorum: bool, old_master_fenced: bool) -> bool:
    """A break-glass request never bypasses exclusivity or verified fencing."""
    return all((admin_approved, bool(reason.strip()), bool(ticket.strip()),
                witness_quorum, old_master_fenced))
