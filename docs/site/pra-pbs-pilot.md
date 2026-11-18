# 0.3.22 staging fixes — inventory, agents and first real PBS backup

> **Status: staging branch only.** Live VM replication, standby restoration, independent witness, actual Proxmox host-level fencing, auto-failover and failback are not implemented.

## User fixes

- Proxmox-managed SSH groups can be renamed and re-described, while membership stays discovery-managed.
- Standard agent functionality is built in and no longer presented as an optional module badge.
- Admins may manually clean finished bulk deployment batches (keep up to 20 recent batches, retention 30 days). Active batches and the audit log are not purged.
- Protected VM discovery reads the cluster-wide resources API and displays visible QEMU VM count and excluded LXC count. Missing VM records may indicate insufficient Proxmox API token ACLs.

## Real PBS backup pilot

1. Deploy this branch in staging and back up SQLite.
2. Configure a source-side Proxmox `pbs` storage with `backup` content and appropriately scoped PVE backup API privileges.
3. After review, set `ALLOW_PROXMOX_ACTIONS=true`.
4. Register a non-paused protected VM with `pbs_backup_restore` engine.
5. In **Protected VMs**, choose **Sauvegarder sur PBS**, enter the source cluster's exact PBS storage ID, approve and wait for the returned UPID.
6. Poll **Vérifier le résultat**. Only PVE task `exitstatus=OK` means backup task completion. Ambiguous network outcomes stay blocked pending manual reconciliation.

This uses a real source-cluster `POST /nodes/{current-node}/vzdump`, `mode=snapshot`. The current node is discovered every run. **It does not create a standby VM, prove restore integrity, provide real-time changed-block streaming or implement failover.** PBS deduplication/incremental backup behavior depends on the configured PBS/PVE backend.

## Partial OrigamiTasks start guard

OrigamiTasks' own workflow PVE `start/reboot` operation now blocks protected standby VMIDs. It does **not** intercept any independent PVE API, UI, CLI, HA or host boot. This is not fencing, and **must not** be advertised as operational split-brain protection.

## Remaining implementation

- PBS remote availability/sync and target restore with isolated networking plus recoverability checks
- Independent witness with transactional epoch management and real host/storage fencing
- Real coherent changed-block inter-DC engine, reverse resync, safe switchover and failback
- Staging acceptance exercises (node migration mid-job, partition, failed backup, stale epoch, double start)

See [PRA architecture](https://github.com/Maxime3d77/OrigamiTasks/blob/feat/dr-protected-vms-simulations-0.3.21/docs/ARCHITECTURE-PRA-INTERSITES.md).

## Real stopped-standby restoration on a second cluster (0.3.27)

**Restore on standby site** issues actual Proxmox `POST /nodes/{node}/qemu` with a validated PBS `archive`, target `vmid`, destination image `storage`, and explicit `start=0`, `onboot=0`, `ha-managed=0`, `unique=1`. The controller tracks task UPID and confirms a stopped QEMU guest with autostart disabled. Existing destination VMIDs are never overwritten.

**Prerequisite:** both PVE clusters must see the **same PBS backup snapshot**, either a shared PBS datastore configured as storage on both or a separately synchronized PBS remote exposing an identical snapshot. SAN iSCSI remains unrouted; actual guest disk data is read from PBS over the configured storage network.

For a disposable VM:
1. Configure PBS for both clusters and review datastore and PVE restore permissions.
2. Select the PBS strategy, a free target VMID, destination node and target image storage.
3. Run **Save to PBS** and poll its UPID until completed; note the numerical backup job ID.
4. Open **Restore on standby site**, supply the completed backup job ID and target PBS storage ID, select a matching source VM backup snapshot and explicitly confirm.
5. Poll restore UPID until completion and verify the guest remains stopped with autostart disabled in target PVE.

**Not provided:** repeated changed-block application to a preexisting standby, near-real-time streaming, independent witness, host-wide PVE fencing, safe automated failover or failback. Never boot both copies together on connected networks. This is a manual backup/restore proof-of-concept, not a production-ready high-availability solution.
