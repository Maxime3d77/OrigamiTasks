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

## Read-only LVM storage capability discovery

The console discovers storage through the Proxmox API, without assuming a
storage vendor, VG name or transport. For `lvm` storage,
`snapshot-as-volume-chain=1` is reported as a **technology-preview QCOW2
snapshot-chain capability**, **not** as a working PRA replication receiver.
This setting does not automatically make pre-existing `raw` disks eligible;
each disk must be evaluated separately.

In v0.3.24, the QMP transport receiver still requires a writable Proxmox
`dir` storage. A future LVM receiver adapter must implement PVE-backed
allocation, per-VM independent volumes, atomic publication and cross-cluster
acceptance testing. Read-only capability discovery does not activate
replication, install agents on production or authorize failover.

Official reference: [Proxmox LVM storage](https://github.com/proxmox/pve-docs/blob/master/pve-storage-lvm.adoc).

## Generic LVM adapter: development stage (v0.3.25-rc1)

The new `agents/linux/pra_lvm_target.py` is a generic Proxmox-managed
block-volume staging component; it does not depend on any SAN vendor or VG
name. It detects `snapshot-as-volume-chain` without writing to storage,
rejects disabled pools, and calculates a conservative **thick LV** capacity
estimate for every generation, including incremental copies. Its
operator-approved staging method journals intent and outcomes and **never**
automatically frees a volume after an uncertain allocation.

Staging is **not connected** to the live replication API or to the
"Synchroniser à chaud" button. It still needs a durable, cluster-visible
checkpoint manifest, validated QCOW2 block-device backing chains, atomic
publication, verified recovery, and start fencing. Real LVM replication
remains deliberately **disabled** until these requirements have been
validated on non-production Proxmox hosts.

The new **Compatibilité stockage** action on a protected VM provides a
read-only report on the target pool, node, capacity, and source disks.
It never allocates a volume, copies data, or starts a VM. No new `.env`
setting is required.

## QMP checkpoint safety release (v0.3.26-rc1)

- After a QMP sync to a writable PVE `dir` store, the destination helper
  checks that the `REP_` standby is still OFF, the configured disks match
  the committed checkpoint, every backing image belongs to the VM, every
  QCOW2 backing path is inside the managed store, and `qemu-img check`
  succeeds. Only then may the run become `completed`.
  Any verification failure produces an `uncertain` run requiring operator
  reconciliation before another copy.
- **Historical checkpoints are never automatically garbage-collected.**
  `qm config --current` cannot prove that a disk is not referenced by a
  PVE snapshot, unused volume or other dependent image. Capacity monitoring
  is required until a proven reference-aware retention protocol is available.
- Previous completed replication runs are migrated as `unverified`, with
  history preserved. They are not trusted for auto-scheduling or incremental
  base selection. The first new replication needs a FULL reseed and updated
  host helpers at both sites.
- Both source and destination helpers must advertise safety protocol v2
  **before** allocation or transmission. Old helpers are rejected.
- OVMF, TPM, unused disks and PCI passthrough are blocked until a separately
  tested recovery profile exists. Source VMs are never stopped.
- Verification is **offline structural validation only**. It does not prove
  a bootable OS, application consistency, fencing or an independent witness.
  No automatic failover or failback is enabled.

The LVM receiver remains staging-only and is NOT wired to the live QMP
transport in this release.

## LVM capacity and cluster ownership checks (v0.3.27-rc1)

**Compatibilité stockage** now displays virtual disk sizes whenever the
PVE VM configuration exposes them as `size=` properties. For thick QCOW2
volume-chain LVM targets, it estimates **total virtual disk sizes plus at
least 5 % or 512 MiB reserved capacity** for each checkpoint generation.
Even a tiny incremental delta requires another thick LV generation.
Missing disk sizes are **unknown**, never assumed to be zero.

The dormant LVM staging adapter now checks real node-local PVE storage
availability and the VMID inventory across the **entire target cluster**.
It rejects VMIDs belonging to another node/CT and existing targets without
the matching protection marker, or with onboot enabled. Initial staging
for a VMID that does not exist yet is supported by the planning checks.

**Limit:** These checks do not enable LVM QMP replication, failover, or
failback. They are mock-tested only, pending Proxmox lab validation.

## Production DR acceptance matrix

Mock-based CI is not a substitute for the following tests on **two
non-production Proxmox clusters and an independent witness**:

1. Seed an online source VM without stopping it, then verify restored disk
   and guest data integrity.
2. Run successive incremental copies after writes and confirm only changed
   blocks are transmitted.
3. Migrate or restart the source: lost bitmap must force a FULL reseed.
4. Interrupt network, kill an agent, stop the target, exhaust storage:
   never publish an unverified checkpoint.
5. Test `dir`, NFS, LVM `raw`/QCOW2, LVM-thin, ZFS and RBD target
   adapters without overwriting any guest-owned disk.
6. Boot an **isolated copy** and validate OS and application health; test
   BIOS, UEFI and TPM cases separately.
7. Partition the source site: prove independent witness quorum and
   fencing before allowing any standby activation.
8. Perform controlled network mapping, promotion, reverse sync and
   failback while proving no concurrent writes occur.
9. Lose the central controller: agents must maintain local protection
   without unauthorized promotion.
10. Measure actual RPO/RTO, data throughput, free capacity and recovery
    or reconciliation time.

Until steps **6 through 9** are implemented and verified, failover/failback
remain simulations and are not enabled in production.

## Failover recovery readiness report (v0.3.28-rc1)

The **Préparation bascule** action on **VM protégées PRA** provides a
per-VM evidence checklist. It distinguishes offline QCOW2 structural checks
from the independent proofs required for safe recovery.

The read-only admin API
`GET /api/dr/vms/{id}/recovery-readiness` reports the most recent
structurally verified copy, destination adapter, storage availability,
missing evidence and recovery blockers.

This is intentionally **fail closed**: even when an offline image has been
verified, it will not authorize promotion without an isolated guest restore,
independent witness, confirmed fencing, an enforced single-writer start guard,
application validation and approved network mapping. Administrator-entered
flags never constitute proof of fencing or independent witness.

This version cannot start or stop a VM, does not require any new `.env`
setting and does not require Proxmox agent upgrades just to display the
report. Real failover and failback are still pending implementation and
non-production acceptance tests.

## Integrated PRA v1 development branch (not a production release)

The remaining PRA functionality is developed under
`feat/pra-v1-integrated-receiver-lab-20261120` to avoid piecemeal
production releases that do not compose into a recoverable system.

Read-only preflight on an **authorized lab Proxmox node** with this branch:

```bash
python3 scripts/pra_lab_preflight.py --storage LAB_STORAGE --vmid LAB_VM_ID
```

The command only uses `pvesh get` and `qm config`, never touches disks,
credentials or guest power states.

Real QEMU QCOW2 full/incremental/abort smoke tests use only pytest
temporary disk images:

```bash
python3 -m pytest -q tests/test_pra_real_qemu_checkpoints.py
```

Requires `pytest`, `qemu-img` and `qemu-io`. A test marked skipped due
to missing QEMU tools does not constitute validation.

`app/pra_recovery_transaction.py` contains the generic transaction
orchestration contract: signed/verified external approval and witness,
verified old-master fencing, new witness epoch, hypervisor-enforced start
guard and guarded target activation. **It is not connected to a real VM
start API**. Genuine witness, fencing, authenticated proof and start-guard
adapters remain mandatory before promotion can be enabled. Unit tests
with mocked providers do not prove safe inter-site behavior.

Do not merge this integrated development branch into production until
LVM QMP transfer, EFI/TPM, external witness/fencing, isolated guest
restore and two-cluster failure tests are complete.

