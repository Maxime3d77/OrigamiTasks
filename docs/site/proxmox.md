# Proxmox VE: discovery and disaster recovery

**Current functionality:** register several independent Proxmox API endpoints, read node/QEMU/LXC/storage inventory, and optionally start/shutdown/reboot a VM through tightly gated API calls. A **read-only DR assessment** can inspect each VM disk storage type and list viable recovery technologies requiring further validation. **There is no cross-cluster live replication, failover or migration engine in v0.2.**

## Connect a datacenter

1. Create a Proxmox API token with read-only permissions for discovery (`user@realm!tokenid`, token secret).
2. Provide a validated HTTPS API endpoint such as `https://pve01.example.org:8006`; set `PROXMOX_ALLOWED_HOSTS=pve01.example.org:8006`.
3. Trust its signing CA. Never disable certificate verification. If necessary, mount a PEM CA and configure `PROXMOX_CA_CERT`.
4. Open **Datacenters → Add**, link the token, then **Discover**. Click **Assess DR** next to a QEMU VM to inspect disk configuration and `/storage` without modifying VM resources.
5. Only if explicitly approved for operations, set `ALLOW_PROXMOX_ACTIONS=true`; action steps must additionally set `approved=true` and use a scoped API token.

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
