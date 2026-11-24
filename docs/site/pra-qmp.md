# Live incremental QMP replication — laboratory pilot

## Automated QMP → LVM laboratory workflow (PR #51)

!!! warning "LAB ONLY — no production failover"
    This branch orchestrates hot QMP/LVM transfers and an OFF standby entirely
    from OrigamiTasks, but **is not production-ready disaster recovery**.
    EFI/TPM guests, OS boot, application consistency, exclusive promotion
    authority and reverse failback are not qualified. Use disposable lab VMs.

### Automatic enrollment

1. Deploy or update the Linux PRA agents using **Agents** and associate each
   hypervisor with the appropriate DR site.
2. Under **Protected DR VMs**, choose a running source VM, the destination
   node and Proxmox LVM `images` storage with QCOW2 volume chains enabled.
   A free target VMID is suggested automatically.
3. Select QMP, retain **Create REP_ and start the initial FULL hot backup**,
   type `DISPOSABLE-LAB-ONLY` for lab authorization and save. No manual
   `qm create` or `scp` commands are required.
4. The controller validates both clusters, creates an OFF, networkless,
   diskless `REP_` with `onboot=0`, then runs hot QMP/NBD backup.
5. After structural verification, the checkpoint is attached to the OFF
   `REP_` configuration. **Configured does not mean guest boot tested.**
6. Scheduled incrementals require a verified previous copy and a confirmed
   publish; failures or uncertain states stop automatic replay.
7. The **LVM Replication** screen provides activation, publication,
   scheduler, copy and isolated boot-test histories.

### Test a recovery clone

Create an independent `PRA_TEST_` clone from the latest checkpoint.
**Test isolated clone boot** briefly starts this networkless clone, verifies
QEMU is `running` and stops it. This is **not** OS-boot or application
health proof; an unconfirmed stop is persistently marked uncertain.

### Hard safety limits

- LVM generations allocate **thick** LVs and may consume the entire virtual
  disk capacity per incremental. The lab pilot caps allocations at **8
  generations** pending a safe, verified flatten/retention system.
- Checkpoint journals are **node-local**, not distributed promotion authority.
  Cross-hypervisor standby recovery cannot be guaranteed.
- Production failover, reverse sync and failback are **disabled** pending an
  independent witness, verified fencing, host-enforced start guards and
  split-brain/crash fault-injection tests.
- No new `.env` settings are required; controller **and agents** must be
  upgraded together.

### Acceptance testing

Run CI and then qualify a sacrificial guest across real lab Proxmox clusters:
FULL + incremental, bitmap loss, VG exhaustion, network disconnection,
controller restart, guest hardware changes, isolated restore, clone shutdown
and complete run logs. CI alone is not a Proxmox certification.

---


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

## Real QMP acceptance tests — v0.3.29-rc1

The new `CI / Real QEMU QMP/NBD full + incremental smoke test` job
installs actual QEMU tools and runs on **disposable local test images**,
never real production guests.

It starts QEMU with two QCOW2 disks, performs an actual QMP full backup
to NBD exports, atomically commits the resulting images with
`CheckpointStore`, writes changed data through the source QEMU process,
then transfers incremental dirty bitmap backups to fresh QCOW2 COW
overlays. It verifies payload contents, both backing chains, the
immutability of initial checkpoints, and bitmap loss after QEMU restart.
No obsolete checkpoint can be automatically deleted.

The tests revealed a real QMP incompatibility: bitmap-add actions cannot
share a grouped-completion transaction with backup jobs. Full seed
transactions now omit that property, while incremental transactions
containing only backup jobs keep grouped completion.

The destination helper additionally requires a **verified shutdown** of
the exact `qemu-nbd` process exporting each checkpoint image. Missing PID
files, unexpected process identities and ambiguous shutdowns fail closed,
preserving the checkpoint for manual reconciliation. Failed NBD launches
no longer automatically erase staging data.

### Not yet covered

This tests real QEMU/NBD, but **not** two independent Proxmox clusters,
SSH forwarding between actual agents, guest OS boot, application
consistency, LVM/iSCSI receiver integration, witness, fencing, failover
or failback. These still require implementation and real acceptance tests
on non-production infrastructure.

## Integrated PRA 0.3.29 — end-to-end laboratory qualification

This integration release brings together real QMP/NBD full and incremental
replication, atomic DIR COW checkpoints, and a guarded, experimental
Proxmox-managed LVM receiver. The source remains ON and the standby OFF.

The CI suite executes two-disk full/delta backups against a real temporary
QEMU process, checks data integrity and immutable QCOW2 generations,
enforces FULL reseeding after bitmap loss and exercises QCOW2-on-block via
disposable /dev/loop devices. LVM receiver control tests are mock-based:
**two actual Proxmox clusters have not been tested**.

A manual **lab-only** command on the OrigamiTasks controller can replicate
to a Proxmox LVM destination, with actual fresh PVE-allocated LVs and
SSH-forwarded QMP/NBD transport:

```bash
docker exec origamitasks python -m app.pra_lvm_lab --vm-id 17
docker exec origamitasks python -m app.pra_lvm_lab --vm-id 17 --execute --lab-confirm DISPOSABLE-LAB-ONLY
docker exec origamitasks python -m app.pra_lvm_lab --vm-id 17 --incremental --execute --lab-confirm DISPOSABLE-LAB-ONLY
```

The target QEMU VM must exist, be stopped, have onboot disabled, **zero network
interfaces**, and a description with both
`ORIGAMITASKS_PRA_<protection id>` and `ORIGAMITASKS_LAB_ONLY`.
Use *only disposable non-production volumes and VMs*. The first command is
read-only; executed commands allocate real LVs. Lost acknowledgment results
in manual reconciliation, never automatic deletion.

The `pra_lvm_restore_lab.py` module can prepare an independent, flattened
QCOW2 clone on new LVs for future boot tests; it intentionally keeps the clone
OFF, has no web API and must not be considered proof of OS or application
recovery.

**Not production-ready:** LVM metadata is node-local; there is no durable
shared witness authority, source fencing, proven isolated guest boot,
UEFI/TPM recovery, real promotion or safe reverse sync/failback. No production
agents are installed automatically, and no new .env feature switches
are required.

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

## v0.3.30 — Real QMP-to-LVM replication through the UI (supervised LAB)

**VM protégées PRA → Copie LVM recette** now launches the existing
**real QMP/NBD full and incremental** data plane, rather than only listing
storage capabilities. Source VM stays **ON** and target standby remains
**OFF**. Runs are asynchronously tracked in the SQLite
`pra_lvm_lab_runs` history and kept separate from DIR receiver runs.

### Use on disposable non-production Proxmox clusters only

1. Register both clusters/sites and deploy PRA-capable **Linux agent 0.3.30**
   to the authorized **lab** source and target nodes, with approved SSH.
2. Create a protected VM policy using `qmp_live_incremental`; source must
   be running, target storage must be PVE `lvm` with
   `snapshot-as-volume-chain=1`, target node online and target VMID **free**.
3. After creating the policy, create an **empty, stopped, networkless,
   auto-start-disabled** VM on the destination lab node, with ownership
   markers for the OrigamiTasks **protection ID**:

   ```bash
   # EXAMPLE LAB ONLY — replace the VMID and protection ID
   qm create 3301 --name REP_TEST_3301 --onboot 0 \
     --description 'ORIGAMITASKS_PRA_17 ORIGAMITASKS_LAB_ONLY' \
     --memory 2048 --cores 2
   qm status 3301
   qm config 3301
   ```

   `17` is the **OrigamiTasks protection ID**, not the VMID. `3301` is
   the unused destination Proxmox VMID. The destination VM must have **no
   network adapters, no disks, onboot=0 and status stopped**.
4. In the UI, open **Copie LVM recette**, verify read-only preflight,
   type `DISPOSABLE-LAB-ONLY` and click **Copie complète à chaud**.
5. Refresh the run history until completed. Then write new data to the
   source guest and use **Synchronisation incrémentale**. Missing/unsafe
   dirty bitmap requires a new full seed.
6. An `uncertain` result blocks all replay pending manual QMP/NBD/LV
   reconciliation. Existing LVs are **never automatically freed**.

This performs **real disk writes**, one thick PVE-managed LVM generation
per run, including increments, and may consume the *entire virtual size*
of each disk per generation. Use disposable, adequately sized lab storage.
Do not target production arrays, production VGs or important VMs.

No auto-attach, guest start, production fencing, promotion, witness or
failback is provided. The separate `pra_lvm_restore_lab.py` component
can prepare an isolated independent clone, but requires a distinct
authorized test and never automatically boots a guest. No new `.env`
flag is needed; the SQLite migration preserves existing records.

### Prepare a real independent, networkless restore test (never auto-start)

After a `completed` LVM lab full/incremental transfer, the same
**Copie LVM recette** panel offers **Préparer un clone isolé (arrêté)**.
Choose a **free VMID across the whole destination cluster** and confirm
`DISPOSABLE-LAB-ONLY`.

OrigamiTasks requires a matching committed verified LVM checkpoint,
checks that the standby is still OFF, flattens each disk into **fresh
PVE-allocated LVs** and creates an independent, stopped
`PRA_TEST_<VMID>` guest with `onboot=0` and no network interfaces.
The operation is tracked in the LAB run history as mode `restore`.

This is a **real VM clone preparation, not a boot test**: the machine
is created but never started by OrigamiTasks; `boot_verified=false`.
The operator must inspect disk attachments and isolation, then manually
test guest boot and applications on authorized lab infrastructure.
The online source and original standby are never started/stopped by the
restore operation.

A failed or ambiguously acknowledged clone remains `uncertain`; no
blind retry, LV deletion or automatic promotion is attempted.



### 0.3.44-rc1 LAB: one-time consolidation of legacy LVM chains

On the **destination** Proxmox node with the REP_ VM **stopped**, Linux
agent 0.3.44 ships the root-only tool
`/usr/local/lib/origamitasks-pra/pra_lvm_consolidation.py`.

Example **only for LAB VM 183 / protection #1**, after an independent
backup of PVE VM configuration and local PRA metadata:

```bash
PRA=/usr/local/lib/origamitasks-pra/pra_lvm_consolidation.py
python3 "$PRA" plan --vmid 183 --storage sd-PP-Proxmox --protected-id 1
# Review the report before proceeding.
python3 "$PRA" prepare --vmid 183 --storage sd-PP-Proxmox --protected-id 1
python3 "$PRA" state --vmid 183 --storage sd-PP-Proxmox --protected-id 1
qm config 183
# Verify the standalone checkpoint and preferably test an isolated
# LAB recovery boot before authorizing physical deletion.
python3 "$PRA" prune --vmid 183 --storage sd-PP-Proxmox \
  --protected-id 1 --confirm DELETE-OLD-PRA-CHAIN
```

`plan` makes no changes or LV activations. `prepare` locally activates
only the LAB disks, allocates **one** new thick LV, performs a one-time
full local flatten, verifies no backing chain, runs `qemu-img check`
and a full logical compare, and then switches `scsi0`. All old volumes
remain attached as `unusedN` until explicitly confirmed `prune`.
Before each physical deletion, `prune` re-verifies VM shutdown, absence
of snapshots or cross-VM references, and that the new active image is
standalone. Proxmox performs the physical unlink; deletion is irreversible.
Pre-consolidation metadata remains archived.

This is **one-time legacy migration**, **not** the missing long-term
delta-only transactional incremental engine. After consolidation, the
checkpoint is marked `requires_full_reseed`: an uncertain or stale QMP
bitmap must never be silently used as an incremental base. If interrupted
or a QCOW2 backing path is missing (for instance an obsolete
`/dev/dm-N`), the tool fails closed; retain the LVs for manual
reconciliation. Not qualified for production.
