# OrigamiTasks — inter-site disaster recovery SD → AU (architecture RFC)

**Status: design proposal, NOT implemented.** This document does not activate VM replication, fencing, automatic failover or failback. These require integration tests, an independent witness, and an operationally verified fencing mechanism before production use.

## Intent

- **SD primary site:** an active Proxmox QEMU VM, potentially on shared iSCSI/LVM block storage.
- **AU recovery site:** a continuously refreshed **powered-off** standby named REP_ORIGINAL_NAME; it may have a distinct VMID. Protect the mapping using stable site/cluster/source-VM identifiers, destination VMID, and generation (epoch), **never only the name prefix**.
- Report a measured **RPO** (age of last validated and durable recovery point) and observed **RTO** (time to application recovery). Do not promise synchronous recovery or "zero latency" without confirming the storage engine and topology.
- When SD comes back, **the source VM must stay down** until an explicitly approved failback has succeeded.

## User interface / discovery

The Proxmox discovery view gains free-text search by name/VMID/node/state, type filters (**QEMU VMs, LXC, nodes, storage**), sorting and pagination. A subsequent release should add VM selection to a **protection group**, with mapping from source to target and storage/network policies. LXC is out of scope for the initial replication engine.

The planned DR workspace has **Sites**, **Agents**, **Protected VMs**, **Replications**, **Simulations**, **Failover**, **Failback**, and **Audit / alerts**. Per-VM status displays source and REP_ target, last consistent recovery point, replication lag, integrity, resources, RPO, measured RTO, boot order and exception conditions.

## Architecture

1. **OrigamiTasks control plane:** administer policies, inventory, API credentials, jobs, approvals and audit logs. Its continued availability during an SD disaster cannot be assumed.
2. **SD / AU site agents** (preferably redundant) with per-agent mTLS identities, renewals, least-privilege Proxmox/Pure/PBS API integrations, idempotent operations and secure transfer checkpoints. Agents **do not elect the active site on their own**.
3. **Independent third-failure-domain witness/arbitrator:** durable ownership state for each protected VM, monotonically increasing epoch, signed and replay-resistant authorization to promote or resume. This is not a Corosync qdevice attached to the two separate PVE clusters.
4. **Enforced fencing:** independently verified inability of the source to write or start (hypervisor power fencing/BMC/PDU, local start guard, storage I/O ownership). Lost pings, failed API probes, or an expired heartbeat do NOT establish that the original VM stopped. A lease alone cannot shut down a VM already running.

### Non-negotiable safety invariants

- **Exactly one active, writable instance per protected VM.** A replicated disk is not exposed read-write to two separate copies.
- The AU agent may not start REP_ without both **witness authorization** and **verified effective source fencing**. Missing witness/ambiguous source status/old checkpoints lead to NEEDS_OPERATOR, with no automatic promotion.
- Source onboot, Proxmox HA, and other restart paths must be controlled by safeguards stronger than a single agent process. When SD reappears and AU is running, SD remains fenced and its VM stays off, regardless of stale prior local configuration.
- Standby identifiers, disks, VLANs, bridges, IP/MAC, application dependencies and storage consistency must be mapped and versioned; REP_ is only an operator-friendly label.
- Reverse replication may never overwrite a newer AU state with stale SD writes. Failback is explicitly authorized and audited, with validation and destination fencing.

### Proposed state machine

- Preparation: DISCOVERED → SEEDING → PROTECTED_AU_OFF → SYNCED; else REPLICATION_LAG / REPLICATION_FAILED.
- Disaster: SD_SUSPECT → DECISION_PENDING → SD_FENCED → AU_PROMOTING → ACTIVE_AU; otherwise NEEDS_OPERATOR.
- SD return: ACTIVE_AU → SD_RETURNED_LOCKED; **no automatic SD restart**.
- Approved failback: FAILBACK_REQUESTED → REVERSE_SYNC → AU_FENCED → SD_PROMOTING → ACTIVE_SD → RESUME_REPLICATION; failure gives FAILBACK_BLOCKED.
- Every state transition carries an owner and epoch, independent proof, timestamp, idempotency key and traceable audit entry; stale commands cannot reacquire ownership.

### Detection, promotion and return

1. Correlate different sources of evidence (multiple PVE API endpoints, site-agent heartbeat, storage, witness and independent probes) to tell network partitions and API outages from genuine site failures.
2. Ensure an intact, consistent, sufficiently fresh recovery point exists on AU and storage/network mapping is ready.
3. Fence the SD workload **effectively**, then request exclusive AU ownership from the witness. Start the standby only after both validations.
4. Validate network isolation or intentional route/IP promotion, boot dependency order, guest/application readiness, and publish the change.
5. When SD returns, keep source start paths blocked. A manual failback must sync new writes AU→SD, stop/fence AU, verify integrity/exclusivity, switch ownership, and only then start SD.

## Replication engines (select after storage assessment)

- **Native Proxmox pvesr** sends incremental ZFS local snapshots within a cluster; it is **not** generic cross-cluster replication of shared iSCSI/LVM disks.
- **Proxmox Backup Server** incremental backups and restore provide a sensible recovery baseline, but restore time and recovery point age must be measured; this is not a guaranteed continuously ready AU VM.
- **Pure Storage FlashArray:** evaluate asynchronous Protection Groups and snapshot consistency, or synchronous ActiveCluster only if the actual versions, licenses, connectivity and mediator support it. A large shared LVM LUN may hold many VMs; array-level LUN replication does **not** automatically implement independent per-VM copies. Validate ownership, site presentation, iSCSI mapping, snapshot consistency and fencing.
- Control-plane mTLS and replication data paths must remain separate; do not assume SD↔AU iSCSI is routable.

## Safe development phases

1. Read-only discovery search/type filtering/sorting/paging.
2. Managed site agents, mTLS enrollments, witness prototype, VM-to-volume mapping, simulated and **network-isolated** recovery.
3. Pilot one non-critical VM with verified consistent snapshots and measured RPO/RTO.
4. **Manual** fencing-based AU activation, observed SD return lockout and explicitly approved failback.
5. **Automatic** failover only after independent fencing and witness safety is proven under repeated partition/failure scenarios.

## Mandatory test scenarios

- SD↔AU connectivity lost while source continues running: **no AU start**.
- SD API or agent fails but source VM still runs: **no AU start**.
- Witness unavailable/partitioned: **no unauthorized failover**.
- SD truly offline and verifiably fenced with witness approval: AU may promote.
- SD unexpectedly recovers while AU active, including historic onboot/HA: SD VM remains **off**.
- Replication lag beyond RPO, corrupt checkpoint, missing storage, or multi-disk inconsistent snapshot: reject promotion.
- Failback sends AU changes to SD, fences AU, and only then starts SD.
- Isolated DR drill cannot impact production VLANs or IP/MAC assignments.

**Release criterion:** no double-start/double-write, no automatic SD restart after AU takeover, complete audit and measured RPO/RTO under tested failures.
