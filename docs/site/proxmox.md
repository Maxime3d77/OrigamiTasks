# Proxmox VE: discovery and disaster recovery

### Manually protected VMs and safe DR rehearsals (0.3.21)

Under **Administration → Protected DR VMs**, choose **Add VM**, select a DR group (e.g., Staging or Production) and a primary and standby cluster belonging to that **same** group. Select **Discover VMs and storage** to retrieve actual QEMU guests, candidate destination nodes and VM-image storages over the Proxmox API.

Explicitly choose the source VM, unused target VMID, destination node/storage, desired repetition interval, and **proposed** transport. OrigamiTasks records a proposed `REP_<vm-name>` identifier; **it does NOT create a target guest or copy disk blocks**. Shared iSCSI/LVM volume groups may contain multiple guests on one LUN: no per-VM LUN isolation or consistency is inferred. The interval is a planning preference, **not** an active job schedule.

**Test replication** performs read-only Proxmox preflight checks on both clusters; **Simulate failover** generates an audited, always-blocked decision until a verified consistent recovery point, external witness, effective source fencing and enforceable source restart suppression exist. Network-partition and controller-offline simulations are available too. None of these invokes a write, snapshot, remote migration, backup, restore, source shutdown or target boot. No fake replication lag or last-successful-sync timestamp is presented.

A future real copy engine for PBS backup/sync/restore or validated storage-array replication will require site-specific storage mapping, free-capacity validation, backup/consistency group verification, real RPO/RTO evidence, target isolation, independent witness and effective fencing. Proxmox remote migration **moves** VMs rather than maintaining standbys. `pvesr` does not solve arbitrary independent-cluster iSCSI+LVM replication. Never copy a live shared LVM disk using raw rsync/dd as a general DR mechanism.

PP and Production group boundaries are enforced. Protected sites cannot be silently deleted or moved across groups. Pausing or deleting a plan record changes only OrigamiTasks metadata. Agent refresh retrieves the most recently received heartbeat—it does not send an unsolicited remote command.

### Automatic SSH first-contact pinning on Proxmox nodes (0.3.20)

The SSH **vault identity** is the user account and its **private login key**, which can be reused from existing deployment workflows. The SSH **server host key** is a separate public key identifying each Proxmox node. Previously, deployment stopped until every node had been manually approved.

For an explicit admin-approved rollout under **Proxmox DC → Cluster agents**, OrigamiTasks now **automatically pins the first server host key** to persistent `known_hosts` (**TOFU / accept-new**). No mandatory fingerprint approval is required for previously unknown nodes. The second, authenticated SSH connection verifies the same stored key before presenting any user credentials. If a stored host key changes, installation remains blocked; no automatic replacement occurs. **View SSH fingerprint (optional)** is still available.

**TOFU caveat:** initial SSH connection interception can cause the wrong host key to be pinned, so independently verifying keys is recommended on critical hypervisors. This behavior is limited to explicitly authorized nodes in a managed Proxmox cluster; it does not expand permission for arbitrary remote hosts, and does not bypass SSH user authentication, SFTP or sudo checks.

Prior failed batches do not retry automatically: create a new batch on a single noncritical node after upgrading. Further issues with the selected vault account, noninteractive sudo, Python 3 or systemd are separate from server-key trust.

### Keep staging and production DR environments isolated (0.3.19)

Under **Proxmox DC → Independent DR Groups**, create any number of custom protection scopes, e.g. **Staging** and **Production**, then associate primary and standby **sites** with each scope. Sites can contain multiple registered clusters; a cluster belongs to only one site. Simulations can only use sites within the **same DR group**, enforced both by the UI and backend.

DR groups are logical protection boundaries, not physical cluster names. Site names currently remain globally unique; use distinct site names such as *Staging primary* and *Production primary*. Existing sites are preserved in **Unassigned sites** until an administrator explicitly classifies them. The migration does not silently move workloads or configure replication.

### Troubleshoot agent installation on Proxmox nodes (0.3.19)

**Agents → Bulk deployments** now shows actionable but sanitized errors per node: untrusted SSH host fingerprint, refused SSH identity, unreachable host/DNS/network/firewall, or failed remote prerequisite such as root/noninteractive sudo, Python 3 and systemd. No raw remote stderr or SSH secrets are displayed.

Before retrying, approve the server fingerprint after out-of-band verification, check SSH and SFTP connectivity and verify `sudo -n true` if not using root. Retry on **one non-critical PVE node** before provisioning a fleet. Previously failed batches cannot retrospectively recover error detail that was never recorded.

### Agent-facing HTTPS controller URL for PVE rollouts (0.3.18)

Proxmox node rollout requires every agent to contact the controller via its reachable HTTPS DNS name, **not** the internal Nginx/Docker binding `127.0.0.1:8091`. Set **Agents → Connection settings** once, e.g. `https://origamitasks.example.org`; the wizard displays it and links to settings if missing. The legacy `AGENT_SERVER_URL` in `.env` is not required for managed cluster rollouts after saving this value in the UI.

Passive DR is optional and **unchecked by default**: this does not replicate VMs, prove fencing, or start recovery guests. Trusted SSH host keys, vault credentials and agent-side HTTPS CA trust remain necessary.

### Deploy the unified agent to all PVE cluster nodes directly from Proxmox (0.3.17-rc1)

On a Proxmox cluster card choose **Cluster agents**, or under a named DR site choose **Deploy to nodes**, then select the cluster if the site contains several.

1. OrigamiTasks queries the actual PVE cluster API for nodes, derives their SSH FQDN from the Proxmox API DNS suffix (for example `pve01.infra.example.org`) and lets the administrator correct that suffix before proceeding.
2. Review and select the desired node list, then **Create / Synchronize SSH group**. This creates managed SSH inventory servers and a persistent `PVE-<id>-<name>` group **linked to the datacenter**, without installing agents. Explicit resynchronization replaces managed group membership; servers can also be members of other inventory groups.
3. Per node use **SSH host fingerprint** and compare the presented SHA256 to a reliable console-side fingerprint. Explicitly type the matching fingerprint to approve; unknown or changed keys are never silently trusted.
4. Choose a **vault SSH identity** with root/noninteractive sudo rights, select nodes without existing agents, approve the batch and click **Deploy selected agents**. A bounded background worker installs one node at a time, using the **same unified Linux agent package** with the passive DR worker.
5. Monitor results under **Agents → Bulk deployments**. Agents are auto-linked to the site if the cluster has one. Check **Proxmox** and **passive DR** in the same deployment wizard to apply those roles automatically after enrollment, or adjust them later under **Agents → Configure modules**. Passive DR still performs no replication or failover. **No VM replication or failover is triggered.**

**Security:** this cluster-bound workflow requires no per-host `.env` allowlist; the cluster API discovery, managed group and administrator confirmation define its scope. The worker validates node membership and already pinned SSH host keys before every installation. Generic/unbound SSH deployment keeps its legacy allowlist protections. Start with a test node before deploying across production PVE hypervisors.

### Named DR sites, managed agents and safe simulations (0.3.14)

Under **Proxmox servers & clusters → DR sites and agents**, choose **Create site**, name it (e.g. SD or AU), assign its primary/standby role, associate **one or more previously registered Proxmox clusters** and declare the storage profile. The **iSCSI + LVM** choice is currently an **operator-supplied profile**, not confirmed storage discovery. A shared LVM VG can hold disks for many VMs on the same LUN, so replicating one Pure volume does not yield independent per-VM standbys.

Attach existing Linux agents if desired. The **Deploy SSH agent** button links to the existing standard agent installer and vaulted SSH key; existing deployment prerequisites still apply, and the agent **does not become an autonomous DR arbitrator**. Future independent site agents must handle all nodes of a cluster, maintain trusted local policies and checkpoints across controller downtime, use mutually authenticated connections, implement rolling signed updates with rollback, and require witness and effective source fencing before any failover.

**Simulate an outage (no action)** models a lost source cluster API, SD/AU network partition, or a failed OrigamiTasks console. It produces an auditable precondition report without any actual VM, node, storage or agent mutation. It does **not** constitute a live fencing or disaster-recovery test.

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
