# Cross-cluster PRA: logical primary, witness and failover (proposal)

> **Status: design + isolated role guard only. NOT wired to Proxmox start paths or replication.** Automated failover must remain disabled.

## Identity and routing

- Each protection tracks a stable ID, primary and standby cluster, both VMIDs, master cluster, monotonic fencing epoch and last verified copy generation.
- **Master means a cluster, not a node**. Resolve current node through `GET /cluster/resources?type=vm` before each operation and re-check immediately before executing; an intra-cluster live migration does not change mastership.
- Let an agent on the current owner node coordinate the transfer. Never bind the stream permanently to one Proxmox node.
- Both SAN iSCSI fabrics remain local/unrouted. Move data using an authenticated routed replication transport between hosts, not by presenting the remote LUN to the other independent cluster.
- Standby is stopped and must be prevented from starting through **every** path (GUI, CLI, API, autostart, HA); enforcing only in the OrigamiTasks UI is not sufficient.

## Copy engine

**First viable option:** incremental PVE backup to PBS, optionally PBS remote sync, followed by verified restore to target cluster-local storage and isolated startup testing. This is periodic recovery, **not real-time replication**. Actual RPO/RTO and restore cost must be measured.

**Advanced option:** consistent snapshots plus application/backend-specific changed-block stream (QEMU access/dirty bitmap, vendor SAN replication or another proven block replication adapter), with full seed, ordered atomic deltas, generation tracking, checkpointing and rebaseline. Live `rsync` over a changing LVM block device is not safe.

## Failover/failback

1. Independent witness and per-VM monotonic fencing epoch.
2. Stop replication, obtain **verified old-master fencing** (power, watchdog/out-of-band, and/or enforced storage/start revocation). Loss of API connectivity is not proof of power-off.
3. Verify every disk, latest consistent generation, target storage and isolated networking.
4. Persist an atomic authority transfer and audit, enforce new cluster-side barriers, then allow exactly one live instance.
5. For failback, do not boot stale original. Reverse-sync from current master, verify, fence it, then promote original with a new epoch.
6. Emergency unlock requires dual approval, reason, ticket, evidence of fencing, and audit, **never overrides split-brain protection**.

If the witness is unreachable or fencing is uncertain, automatic promotion must fail closed. A current master must abide by the approved lease/fencing policy.

## Integration still required

- Wire `app/pra_authority.py` into transactional persistent state, atomic compare-and-swap epochs, real witness and cryptographically verifiable fencing evidence.
- Enforce remote start locks at all actual PVE start paths, account for HA, fail closed if controller unavailable.
- Implement transport, snapshot/delta, reconciliation and restore verification.
- Integrate group rename, module display, deployment retention and complete VM inventory in the **newer deployed UI branch**; those pages do not yet exist on current `main`.
- Exercise split-brain, simultaneous promotion, node migration mid-stream, failed witness, broken block stream and both failover/failback disaster drills.
