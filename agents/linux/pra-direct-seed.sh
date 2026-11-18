#!/usr/bin/env bash
# Run only on the CURRENT source Proxmox node as root.
# Real direct seed: vzdump stdout -> SSH -> qmrestore stdin.
# Does not update a pre-existing VM or implement incremental deltas.
set -Eeuo pipefail
umask 077
if [[ $# -lt 5 || $# -gt 7 ]]; then
  echo 'Usage: pra-direct-seed.sh SOURCE_VMID TARGET_HOST TARGET_VMID TARGET_STORAGE --confirm [TARGET_USER] [SSH_PRIVATE_KEY_PATH]' >&2
  exit 2
fi
vmid=$1; targethost=$2; targetvmid=$3; storage=$4; confirm=$5
user=${6:-root}; key=${7:-}
[[ $confirm == --confirm ]] || { echo 'Confirmation required' >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo 'Run as root on source Proxmox node' >&2; exit 2; }
[[ $vmid =~ ^[1-9][0-9]{1,8}$ && $targetvmid =~ ^[1-9][0-9]{1,8}$ ]] || exit 2
[[ $targethost =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$ ]] || exit 2
[[ $storage =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$ ]] || exit 2
[[ $user =~ ^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$ ]] || exit 2
[[ -f "/etc/pve/nodes/$(hostname -s)/qemu-server/${vmid}.conf" ]] || { echo 'VM must be local to the source Proxmox node' >&2; exit 3; }
qm config "$vmid" >/dev/null || { echo 'Source VM cannot be read' >&2; exit 3; }
sshargs=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=12 -o ForwardAgent=no)
if [[ -n $key ]]; then
  if [[ "$key" == /proc/self/fd/* ]]; then
    [[ "$key" =~ ^/proc/self/fd/[0-9]+$ && -r "$key" ]] || exit 3
  else
    [[ $key == /* && -f $key && ! -L $key ]] || exit 3
  fi
  sshargs+=(-o IdentitiesOnly=yes -i "$key")
fi
if [[ -n "${PRA_PINNED_KNOWN_HOSTS:-}" ]]; then
  [[ "$PRA_PINNED_KNOWN_HOSTS" =~ ^/proc/self/fd/[0-9]+$ ]] || exit 3
  sshargs+=(-o "UserKnownHostsFile=$PRA_PINNED_KNOWN_HOSTS" -o GlobalKnownHostsFile=/dev/null)
fi
dest="$user@$targethost"
preflight="set -eu; command -v qmrestore; pvesm status | awk '\$1 == \"$storage\" {found=1} END {exit !found}'; if qm config '$targetvmid' >/dev/null 2>&1; then echo 'TARGET VMID EXISTS' >&2; exit 40; fi"
remote_command(){
  if [[ "$user" == root ]]; then
    printf '%s' "$1"
  else
    printf 'sudo -n sh -c %q' "$1"
  fi
}
ssh "${sshargs[@]}" "$dest" "$(remote_command "$preflight")" || { echo 'Destination preflight failed' >&2; exit 4; }
exec 9>"/run/lock/origami-seed-${vmid}.lock"
flock -n 9 || { echo 'Backup already in progress on source node' >&2; exit 5; }
# Restore refuses existing VMID, unique network MACs, no autostart.
restore="set -eu; if qm config '$targetvmid' >/dev/null 2>&1; then exit 40; fi; qmrestore - '$targetvmid' --storage '$storage' --unique 1 --start 0 --ha-managed 0; qm set '$targetvmid' --onboot 0; test \"\$(qm status '$targetvmid')\" = 'status: stopped'; echo ORIGAMI_SEED_COMPLETED"
echo 'Starting initial direct SD -> AU backup stream; no PBS used'
vzdump "$vmid" --mode snapshot --stdout --compress 0 | ssh "${sshargs[@]}" "$dest" "$(remote_command "$restore")"
echo 'Seed complete, standby is stopped. This is NOT continuous replication.'
