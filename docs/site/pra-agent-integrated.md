# GUI-driven direct PRA — v0.3.30

The Linux agent 0.3.30 now bundles the full-seed, offline differential and authenticated reporting scripts. Deployment and **Update agent** install them automatically in a root-owned private /usr/local/lib/origamitasks/pra directory. The outbound agent service remains unprivileged. There is no manual scp and no new .env toggle.

## Setup and use

1. Register both independent Proxmox clusters, map cluster nodes to SSH inventory hosts, assign the current source-node Linux agent to the primary PRA site, and update it to 0.3.30.
2. Create a protected VM using the **Direct copy + offline incremental** engine with a free standby VMID, selected standby node and target image storage.
3. Click **Direct initial seed**. Select the controller-vault SSH identity for the SD source node (root or sudo -n), and the vault identity for the AU target node (root for this pilot). Confirm.
4. Review the target VM in Proxmox. It is created stopped with new MACs and no autostart, without PBS or shared SAN.
5. For subsequent OFFLINE updates, STOP BOTH VMs and remove them from HA first, then click **Offline incremental synchronization**. The script checks source owner, standby state, block volume topology and sizes, and transfers only changed rsync blocks.
6. Use the VM's transfer history and incremental history for each job's status, duration, scanned bytes, sent bytes and errors.

The controller connects to both systems with vault credentials, passing the destination SSH private key and pinned host-key material via an encrypted SSH channel only into anonymous Linux memory descriptors on the current source node. Neither the source nor the destination stores a permanent copy on disk. Root on the source can nevertheless access the in-memory private key during the job; use a narrowly scoped technical identity, not a personal key. Disk traffic goes directly from source to target.

This is a **staging-only** implementation: source snapshots may not be transactionally consistent for all applications; rsync offline transfer requires device support and both guests kept stopped. There is no live dirty-bitmap engine, cluster-wide fencing, automatic failover/failback or unattended periodic replication. A third-party Proxmox administrator could start a guest during offline sync, invalidating safety. Controller restart marks in-flight jobs uncertain and does not auto-retry.

Deploy console: run git fetch and switch to branch feat/pra-agent-bundled-0330; rebuild with docker compose up -d --build --force-recreate. Upgrade existing Linux agents from the web UI, preserving their IDs and configuration.
