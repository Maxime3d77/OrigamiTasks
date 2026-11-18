Live PRA replication — v0.3.31

Mandatory invariant: source SD VM remains RUNNING during initial seed and every future synchronization; only the AU standby is stopped. iSCSI SANs are independent; no PBS or new .env setting is needed.

Implemented: hot initial QEMU snapshot export streamed over SSH to AU; read-only Test hot replication button using the source-node SSH vault identity, QMP query of running source, block devices, dirty bitmaps, and supported commands including Proxmox external backup provider setup/teardown when available; destination API check confirms standby is stopped. Agent updates bundle the preflight in v0.3.31, no scp.

The old offline_incremental job is refused by API/worker, and its agent executable is replaced with a fail-closed stub. Historical status remains visible.

NOT implemented: live delta exporter, generation-safe transactional application to standby, consistent restore checkpoints, bitmap invalidation/migration recovery, global fencing and automatic failover. Raw/LVM bitmaps are not persistent like qcow2 metadata. QMP command discovery is not evidence of a usable replication chain. The read-only test changes neither the VM state nor its bitmaps/disks.
