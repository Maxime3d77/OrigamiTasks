# v0.3.29 — Offline incremental sync and per-VM monitoring

> **WARNING v0.3.31: the offline PRA pilot is DISABLED. The primary SD VM must stay running. The older instructions below are historical reference only and no longer executable.**

The Protected VMs panel now displays the latest incremental sync state, last successful timestamp, processed disks, scanned bytes, actual rsync bytes sent, duration and errors. Incremental history shows 30 records per VM.

The scripts/pra-incremental-cold.sh pilot uses rsync with --copy-devices --write-devices --checksum --no-whole-file --inplace over direct SSH between two independently iSCSI-backed Proxmox nodes. An initial direct seed and matching existing LVM disk volumes are required. Only changed blocks are transferred, but the full devices are scanned.

**Both source and destination guests MUST REMAIN STOPPED.** The script rejects HA-managed guests, incorrect owner nodes, special EFI/TPM devices, differing disk topology, mismatched block device sizes and unsupported non-block volumes. It cannot prevent unrelated administrators from starting a guest concurrently and is not production-safe fencing or live replication.

Run on the VM's current source node after copying both scripts into /root and making sure the enrolled agent belongs to the source PRA site:

~~~bash
bash /root/pra-incremental-cold.sh 100 pve-dr.example.org 900100 \
  --confirm-offline /root/.ssh/id_pra 22 1
~~~

The final number is the OrigamiTasks protected-VM ID. The existing enrolled agent token reports synchronization progress; no new .env variable. The source-to-target SSH technical key remains on the source node for this standalone pilot, not copied from the controller's vault.

**Not implemented:** live QEMU changed-block streaming, automatic scheduler, launching from the UI using vault SSH identities, global hypervisor fencing, production failover/failback.
