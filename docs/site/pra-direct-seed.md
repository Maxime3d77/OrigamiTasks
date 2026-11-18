# PRA: direct SD → AU VM copy without PBS (0.3.28 prototype)

The one-shot `scripts/pra-direct-seed.sh` sends actual QEMU VM backup data from its **current source Proxmox node** directly to the destination via `vzdump --stdout | ssh target qmrestore -`. The LVM/iSCSI arrays remain independent and unrouted. A routed, SSH-secured server-to-server network is required. PBS is not involved.

For a disposable VM, execute on the VM's current source node:

```bash
bash scripts/pra-direct-seed.sh 100 au-styx.infra.aday.fr 900100 local-zfs --confirm root /root/.ssh/<AUTHORIZED_TECHNICAL_KEY>
```

The destination VMID must be free. Use a restricted, preapproved technical SSH identity on the source that can authenticate the destination, and pin the destination server host key. Do **not** copy an administrator's private vault key to a Proxmox node. The script rejects existing target VMs, performs a direct backup stream, restores to a **stopped** VM with fresh MAC addresses, disables autostart and does not use destructive force flags.

This is a **full seed only**, NOT CBT/delta synchronization, failover/failback or HA fencing. Network failure can leave an incomplete target requiring manual inspection. A running VM can be snapshotted, but guest application consistency is not automatically guaranteed. This is a CLI prototype; the control-plane integration with vault SSH identities and the VM protection UI is still required and not claimed as implemented. No new `.env` switch is needed for this prototype.
