#!/usr/bin/env bash
# OFFLINE changed-block sync between two existing Proxmox VMs on LVM.
# NOT for running guests, HA, automatic failover, or live incremental backup.
set -Eeuo pipefail
umask 077
die(){ echo "PRA REFUSED: $*" >&2; exit 2; }
[[ $# == 8 ]] || die "Usage: $0 SOURCE_VMID TARGET_HOST TARGET_VMID --confirm-offline /absolute/key PORT PROTECTION_ID TARGET_USER"
sid=$1; host=$2; did=$3; approve=$4; key=$5; port=$6; protection=$7; dstuser=$8
[[ $EUID -eq 0 && $approve == '--confirm-offline' ]] || die 'Root and explicit approval required'
[[ $sid =~ ^[1-9][0-9]{1,8}$ && $did =~ ^[1-9][0-9]{1,8}$ && $protection =~ ^[1-9][0-9]{0,8}$ ]] || die 'Invalid IDs'
[[ $host =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ ]] || die 'Invalid target host'
[[ $dstuser =~ ^[A-Za-z_][A-Za-z0-9_-]{0,63}$ ]] || die 'Invalid target SSH user'
[[ $port =~ ^[0-9]{1,5}$ ]] && ((10#$port>=1 && 10#$port<=65535)) || die 'Invalid port'
if [[ "$key" == /proc/self/fd/* ]]; then
  [[ "$key" =~ ^/proc/self/fd/[0-9]+$ && -r "$key" ]] || die 'Invalid ephemeral key'
else
  [[ $key =~ ^/[A-Za-z0-9_./-]+$ && -f $key && ! -L $key ]] || die 'Missing source-local key'
fi
ssh_pins=()
if [[ -n "${PRA_PINNED_KNOWN_HOSTS:-}" ]]; then
  [[ "$PRA_PINNED_KNOWN_HOSTS" =~ ^/proc/self/fd/[0-9]+$ ]] || die 'Invalid pinned host file'
  ssh_pins=(-o "UserKnownHostsFile=$PRA_PINNED_KNOWN_HOSTS" -o GlobalKnownHostsFile=/dev/null)
fi
target="$dstuser@$host"
remote(){
  if [[ "$dstuser" == root ]]; then
    cmd=$1
  else
    printf -v cmd 'sudo -n sh -c %q' "$1"
  fi
  ssh -i "$key" -p "$port" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes "${ssh_pins[@]}" "$target" "$cmd"
}
source_guard(){
  [[ -f "/etc/pve/nodes/$(hostname -s)/qemu-server/$sid.conf" ]] || die 'VM migrated: rerun on its current Proxmox node'
  [[ "$(qm status "$sid")" == 'status: stopped' ]] || die 'Source VM running'
  if ha-manager config 2>/dev/null | grep -Eq "^vm:$sid([[:space:]]|$)"; then die 'Source VM controlled by HA'; fi
}
target_guard="set -eu; test -f /etc/pve/nodes/\$(hostname -s)/qemu-server/$did.conf; test \"\$(qm status '$did')\" = 'status: stopped'; test \"\$(qm config '$did' | sed -n 's/^onboot: //p')\" != 1; if ha-manager config 2>/dev/null | grep -Eq '^vm:$did([[:space:]]|$)'; then exit 49; fi; rsync --help | grep -q -- '--write-devices'"
source_guard
remote "$target_guard" || die 'Standby must be stopped, node-local, autostart disabled, HA disabled'
for flag in copy-devices write-devices; do
  rsync --help | grep -q -- "--$flag" || die "Local rsync missing --$flag"
done
qm config "$sid" | grep -Eq '^(efidisk|tpmstate|unused|hostpci|usb)' && die 'Special disk/device adapter not supported'
source_slots=$(qm config "$sid" | awk -F ': ' '/^(scsi|virtio|sata|ide)[0-9]+: / {print $1}' | sort)
target_slots=$(remote "qm config '$did' | awk -F ': ' '/^(scsi|virtio|sata|ide)[0-9]+: / {print \$1}' | sort")
[[ -n $source_slots && "$source_slots" == "$target_slots" ]] || die 'Disk topology differs'
tmp=$(mktemp)
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
reporter="$script_dir/pra-sync-report.py"
[[ -f $reporter && -f /opt/origamitasks-agent/config.json ]] || die 'Agent and PRA report helper required'
run_uuid=$(cat /proc/sys/kernel/random/uuid)
done_success=false
report(){
 python3 "$reporter" "$protection" "$sid" "$did" "$run_uuid" "$1" "$total_disks" "$count" "$scanned" "$sent" "$(($(date +%s)-started))" "$2"
}
cleanup(){
 rc=$?
 if [[ $done_success != true && -n ${run_uuid:-} && ${report_started:-false} == true ]]; then
   report failed 'Synchronisation froide interrompue : vérifier la copie sur AU' || true
 fi
 rm -f "$tmp"
 exit "$rc"
}
trap cleanup EXIT
printf '%s\n' "$source_slots" > "$tmp"
exec 9>"/run/lock/origami-pra-cold-$sid.lock"
flock -n 9 || die 'Sync already running on source node'
started=$(date +%s); count=0; scanned=0; sent=0
total_disks=$(wc -l < "$tmp")
report_started=false
report running '' || die 'Tracking report refused: verify agent site and current source node'
report_started=true
while IFS= read -r slot; do
  [[ $slot =~ ^(scsi|virtio|sata|ide)[0-9]+$ ]] || die 'Invalid slot'
  source_guard; remote "$target_guard" || die 'Standby state changed'
  srcvol=$(qm config "$sid" | sed -n "s/^$slot: \([^,]*\).*/\1/p")
  dstvol=$(remote "qm config '$did' | sed -n 's/^$slot: \([^,]*\).*/\1/p'")
  [[ $srcvol =~ ^[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]+$ ]] || die 'Invalid source volume'
  [[ $dstvol =~ ^[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]+$ ]] || die 'Invalid standby volume'
  srcdev=$(pvesm path "$srcvol")
  dstdev=$(remote "pvesm path '$dstvol'")
  [[ $srcdev == /dev/* && -b $srcdev ]] || die 'Source not an LVM block device'
  [[ $dstdev =~ ^/dev/[A-Za-z0-9_./-]+$ ]] || die 'Standby not an LVM block path'
  size=$(blockdev --getsize64 "$srcdev")
  remote "test -b '$dstdev' && test \"\$(blockdev --getsize64 '$dstdev')\" = '$size'" || die 'Block size mismatch'
  echo "OFFLINE incremental synchronization: $slot ($size bytes)"
  disk_log=$(mktemp)
  rsync --checksum --no-whole-file --copy-devices --write-devices \
    --rsync-path="$(if [[ "$dstuser" == root ]]; then echo rsync; else echo 'sudo -n rsync'; fi)" \
    --inplace --block-size=65536 --stats \
    -e "ssh -i $key -p $port -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes${PRA_PINNED_KNOWN_HOSTS:+ -o UserKnownHostsFile=$PRA_PINNED_KNOWN_HOSTS -o GlobalKnownHostsFile=/dev/null}" \
    "$srcdev" "$target:$dstdev" | tee "$disk_log"
  sent_this=$(awk '/^Total bytes sent:/ {gsub(/,/,"",$4);print $4}' "$disk_log" | tail -1)
  rm -f "$disk_log"
  [[ $sent_this =~ ^[0-9]+$ ]] || die 'Rsync did not report network bytes'
  source_guard; remote "$target_guard" || die 'A VM changed state during transfer'
  count=$((count+1)); scanned=$((scanned+size)); sent=$((sent+sent_this))
  report running '' || die 'Progress report refused'
done < "$tmp"
report success '' || die 'Final status report refused'
done_success=true
echo "ORIGAMI_INCREMENTAL_COLD_OK vmid=$sid target_vmid=$did disks=$count scanned_bytes=$scanned transferred_bytes=$sent elapsed_seconds=$(($(date +%s)-started))"
