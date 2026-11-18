# Proxmox VE: discovery and disaster recovery

**Current functionality:** register several independent Proxmox API endpoints, read node/QEMU/LXC/storage inventory, and optionally start/shutdown/reboot a VM through tightly gated API calls. A **read-only DR assessment** can inspect each VM disk storage type and list viable recovery technologies requiring further validation. **No cross-cluster replication, automated failover or migration engine is implemented in v0.3.13-rc1.**

## Register a node or cluster and discover VMs

1. In **Administration → Proxmox servers & clusters**, choose **Add a server / cluster**; provide the type, HTTPS URL, name and new API token (or select an existing vault connection).
2. Upload an internal CA in PEM/CRT/CER/DER. For a self-signed certificate without an available CA, administrators may **skip certificate validation for this endpoint only**, accepting endpoint impersonation risks. **No .env file edits** are needed for Proxmox site registration.
3. Click **Discover nodes/VMs**, search by name/VMID/node/status, and filter **QEMU VMs / LXC / Nodes / Storage**. Sorting and 100-item paging are supported.
4. Use **Assess DR** on a QEMU VM for a read-only assessment of disks and storage types; this does not create a replica or start a VM.
5. Workflow operations currently cover inventory, start, shutdown and reboot only; mutations retain explicit server and workflow permission gates. Dynamic VM/node workflow pickers are planned but **not yet available**.

### Planned SD → AU agent-managed DR

Future recovery design uses **SD active → AU powered-off REP_ standby**, mTLS-paired site agents, an **independent witness**, **verified fencing** against concurrent VM starts and **explicit AU→SD failback**. Lost pings, failed API calls or agents disappearing are never sufficient to promote the standby. See the design file docs/ARCHITECTURE-PRA-INTERSITES.md. This is not a deployed auto-failover engine.

## Which replication engine can work?

| Source storage | Example pathway | Result | Caveat |
|---|---|---|---|
| Local ZFS within **one** cluster | Proxmox `pvesr` | Incremental local node replication | Not a general cross-cluster iSCSI replication solution |
| Any Proxmox-supported VM storage | PVE backup → PBS → remote sync → restore | Incremental/deduplicated DR copy | Backup is **not** a boot-ready standby VM; RPO = last successful recovery point |
| Two SAN arrays (iSCSI) | Array-native snapshots/replication | Block-level copy, potentially frequent or near-sync | Depends on vendor, latency, write-order consistency, safe presentation and fencing |
| Ceph RBD | RBD mirroring configured across clusters | Block-level replication | Requires supported topology and an explicit single-writer failover process |
| Separate clusters and storage | PDM/PVE remote migration when supported | Transfer/move active VM | Migration is not a continuously maintained standby replica |

### Why not real-time rsync of the VM disk?

For an **online** VM backed by iSCSI/LVM, `rsync` copying the disk image or raw block device can read changing sectors at different moments. It does not supply a consistent atomic recovery point or write-ordering and can yield an unbootable clone. `rsync` is suitable for selected **offline** disk files or application data, not a general live VM block replicator. Never mount the same writable LUN on two independent active clusters without appropriate clustered storage ownership.

### Designing the DR workflow

**Discovery → compatibility assessment → target capacity check → storage-consistent snapshot → transfer/sync → integrity check → standby registration → test restore → reporting.** Failover is a separate, approval-gated sequence: attest source fencing, freeze replication, ensure target disks are writable and isolated, start once, verify services and update routing. Never auto-start the clone purely because the source API is unreachable; a network partition could cause split brain.

## RPO and RTO

`RPO` (maximum tolerated data loss) depends on the **last verified consistent recovery point**. `RTO` (time to service restoration) includes restore, start, network and application checks. A one-hour sync interval **does not guarantee** a one-hour RPO under failed jobs or inconsistent snapshots; measure both in DR drills.

## Generality

OrigamiTasks will model a DC as a cluster + storage capabilities + network mappings + credentials, not hardcode a specific SAN vendor. Connectors for Pure Storage, PBS, Ceph, ZFS and vendor SAN APIs can be added independently. For iSCSI, discovery must identify target LUN access, snapshots, replication, consistency groups and fencing methods **before** enabling automation.

Sources: [PVE storage replication](https://github.com/proxmox/pve-docs/blob/master/pvesr.adoc), [PVE storage model](https://github.com/proxmox/pve-docs/blob/master/pvesm.adoc), [PBS sync](https://pbs.proxmox.com/docs/managing-remotes.html), [PBS changed-block backups](https://pbs.proxmox.com/docs/technical-overview.html).
