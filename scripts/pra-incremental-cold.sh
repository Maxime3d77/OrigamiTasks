#!/usr/bin/env bash
# OFFLINE changed-block sync between two existing Proxmox VMs on LVM.
# NOT for running guests, HA, automatic failover, or live incremental backup.
set -Eeuo pipefail
umask 077
die(){ echo "PRA REFUSED: $*" >&2; exit 2; }
[[ $# == 5 || $# == 6 ]] || die "Usage: $0 SOURCE_VMID TARGET_HOST TARGET_VMID --confirm-offline /absolute/key [PORT]"
sid=$1; host=$2; did=$3; approve=$4; key=$5; port=${6:-22}
[[ $EUID -eq 0 && $approve == '--confirm-offline' ]] || die 'Root and explicit approval required'
[[ $sid =~ ^[1-9][0-9]{1,8}$ && $did =~ ^[1-9][0-9]{1,8}$ ]] || die 'Invalid VMIDs'
[[ $host =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ ]] || die 'Invalid target host'
[[ $port =~ ^[0-9]{1,5}$ ]] && ((10#$port>=1 && 10#$port<=65535)) || die 'Invalid port'
[[ $key =~ ^/[A-Za-z0-9_./-]+$ && -f $key && ! -L $key ]] || die 'Missing source-local key'
target="root@$host"
remote(){ ssh -i "$key" -p "$port" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes "$target" "$1"; }
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
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$source_slots" > "$tmp"
exec 9>"/run/lock/origami-pra-cold-$sid.lock"
flock -n 9 || die 'Sync already running on source node'
started=$(date +%s); count=0; scanned=0
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
  rsync --checksum --no-whole-file --copy-devices --write-devices \
    --inplace --block-size=65536 --stats \
    -e "ssh -i $key -p $port -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes" \
    "$srcdev" "$target:$dstdev"
  source_guard; remote "$target_guard" || die 'A VM changed state during transfer'
  count=$((count+1)); scanned=$((scanned+size))
done < "$tmp"
echo "ORIGAMI_INCREMENTAL_COLD_OK vmid=$sid target_vmid=$did disks=$count scanned_bytes=$scanned elapsed_seconds=$(($(date +%s)-started))"
