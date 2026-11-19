# Live incremental QMP replication — laboratory pilot

!!! warning "Not production-certified"
    This feature is implemented as an experimental transport and controller, but
    has **not been tested against the actual AU/SD Proxmox clusters**.
    Keep this PR as a draft until a sacrificial VM completes two real replications
    and a disconnected, isolated restore. **Failover and failback remain disabled.**

## What it does

- Keep the primary QEMU guest running while copying its disks.
- Seed once with a full QMP blockdev-backup.
- Copy only the dirty bitmap changes on subsequent runs.
- Send the QMP->NBD transfer through two SSH connections already managed by
  OrigamiTasks. The controller relays SSH-encrypted traffic; there is no direct
  SSH private key on the source node and NBD is loopback-only.
- Store destination writes in an **independent writable Proxmox DIR storage**
  (QCOW2 copy-on-write checkpoints). Array/Pure ActiveDR is **not used**.
- Publish a verified checkpoint and attach it to a **powered-off** REP_ VM.
  For safety, the pilot creates no network interfaces on a new standby.
- Track status and last successful point, and schedule subsequent cycles after
  the approved initial seed. Reseed fully if source QEMU restarted, a bitmap
  disappeared, a copy failed, or a chain reached eight levels.
- Allow manual reconciliation of an uncertain run, checking QMP job state
  before discarding unfinished destination overlays.

## Requirements

1. Two distinct Proxmox clusters linked to primary and standby sites in a
   single OrigamiTasks DR protection group.
2. Linux agents installed **using OrigamiTasks** on the exact source and
   target hypervisors, associated with their sites. The original successful
   deployment record must retain the SSH credential in the vault.
3. These SSH identities must be approved by the host-key registry and have
   permission to run the root-owned PRA helper (root SSH or passwordless sudo).
4. `/usr/bin/python3`, `qm`, `pvesm`, `qemu-nbd`, `qemu-img` and local
   QMP sockets must be available. The source SSH daemon must support
   **loopback remote port forwarding**; the target SSH daemon must allow
   loopback TCP forward channels.
5. A **writable** Proxmox directory storage, with content `images`, on the
   standby node, with free space sufficient for a full seed. A read-only
   replicated LUN and a shared classic LVM storage are not supported as
   checkpoint destinations by this pilot.
6. Initially supported disk set: ordinary IDE/SATA/SCSI/VirtIO disks
   (no dedicated EFI/OVMF, TPM-state, or Cloud-init recovery handling yet).
   The initial replica is created with no NICs to avoid network collisions.
7. No new `.env` variables. The control-plane admin explicitly approves
   the first data transfer from the UI.

## Operator workflow

1. In the DR UI, create/select a protection group and both sites.
2. Deploy/update the Linux agents (v0.3.24+) through the interface, then
   associate them with their corresponding site. Do not SCP standalone PRA scripts.
3. Add a protected VM; choose the **QMP live incremental** engine and a
   **DIR** target storage. Assign an unused target VMID.
4. Click **QMP preflight**. It checks inventory, target storage type, and
   the associated SSH agent deployment records without writing guest data.
5. Click **Synchronize live** and approve the operation. Observe run history
   and confirm that the REP_ guest remains stopped. The first transfer may
   require space comparable to the entire VM.
6. Wait for **completed** and check the checkpoint and guest configuration
   from Proxmox; then observe at least one **incremental** run.
7. If status is **uncertain**, do not retry blindly. Click **Reconcile** only
   after inspecting the QMP/NBD jobs and accepting that the next copy must
   be a full reseed.
8. Run any boot experiment manually in an **isolated network** after separate
   fencing verification, not through this automatic transport.

## Design & guardrails

Agent-deployed Python modules:

- `pra_host_worker.py`: root-only fixed actions for source probe/copy and
  destination staging/commit/publish/abort; installed under a root-owned
  `/usr/local/lib/origamitasks-pra` path, not under the writable agent data dir.
- `pra_qmp_incremental.py`: QMP protocol, atomic multi-disk backup transaction,
  bitmap consistency checks and job success verification.
- `pra_checkpoint.py`: QCOW2 COW chains, atomic manifests, retention of a
  previous committed point through failure and owned-file cleanup.

Controller: `app/pra_live.py`; API endpoints:

- `GET /api/dr/vms/{id}/replication-preflight`
- `POST /api/dr/vms/{id}/replicate` with `{"approved":true}`
- `GET /api/dr/vms/{id}/replications`
- `POST /api/dr/vms/{id}/replications/{run_id}/reconcile` with `{"approved":true}`

The client never starts or stops the primary VM or powers on the replica.
A successful sync **does not grant permission to fail over**. Independent
fencing, quorum/witness, split-brain prevention, application consistency, EFI/TPM
handling, throughput and restore tests remain required.

## Important limitations

The QMP dirty bitmap is volatile for raw LVM source disks; a QEMU restart
requires a full reseed. Backups provide crash consistency, not necessarily
transactional database consistency. This is a testable pilot, not a claim of
near-zero RPO or production readiness. Validate QMP command compatibility,
storage layout, NIC/boot mapping, and chain size on actual PVE versions.

Official background: [QEMU dirty bitmap and incremental backup](https://www.qemu.org/docs/master/interop/bitmaps.html).
