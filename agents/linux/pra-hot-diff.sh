#!/usr/bin/env bash
# Live-source differential, immutable AU candidate.
# Uses a complete QEMU snapshot on a DEDICATED scratch filesystem, then
# rsync compares unchanged data locally and sends changed blocks over SSH.
# Not QEMU CBT. Does not stop the source or modify the old AU standby.
set -Eeuo pipefail
umask 077
die(){ echo "PRA HOT REFUSED: $*" >&2; exit 2; }
[[ $# == 10 ]] || die 'usage: VMID TARGET_HOST BASE_VMID STORAGE --confirm-hot SCRATCH PROTECTION TARGET_USER KEY_FD PORT'
sid=$1; host=$2; base=$3; storage=$4; approval=$5
scratch=$6; protection=$7; dstuser=$8; key=$9; port=${10}
[[ $EUID -eq 0 && $approval == --confirm-hot ]] || die 'Explicit confirmation and root required'
[[ $sid =~ ^[1-9][0-9]{1,8}$ && $base =~ ^[1-9][0-9]{1,8}$ ]] || die 'Invalid VMIDs'
[[ $protection =~ ^[1-9][0-9]{0,8}$ ]] || die 'Invalid protection ID'
[[ $host =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$ && $dstuser =~ ^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$ ]] || die 'Invalid target'
[[ $storage =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$ ]] || die 'Invalid storage'
[[ $port =~ ^[0-9]{1,5}$ ]] || die 'Invalid SSH port'
[[ $key =~ ^/proc/self/fd/[0-9]+$ && -r $key ]] || die 'Ephemeral SSH key required'
[[ -n $PRA_PINNED_KNOWN_HOSTS && $PRA_PINNED_KNOWN_HOSTS =~ ^/proc/self/fd/[0-9]+$ ]] || die 'Pinned host file required'
[[ $scratch =~ ^/[A-Za-z0-9_./-]+$ && $scratch != / && -d $scratch && ! -L $scratch ]] || die 'Dedicated scratch directory required'
[[ $(findmnt -T "$scratch" -n -o TARGET) != / ]] || die 'Scratch filesystem cannot be root filesystem'
[[ -f /etc/pve/nodes/$(hostname -s)/qemu-server/$sid.conf ]] || die 'Run on current owner node'
running(){
 [[ -f /etc/pve/nodes/$(hostname -s)/qemu-server/$sid.conf ]] || die 'Source migrated to another node'
 [[ $(qm status "$sid") == 'status: running' ]] || die 'Source SD must remain running'
}
running
for bin in vzdump vma rsync ssh qm pvesm blockdev sha256sum flock; do command -v "$bin" >/dev/null || die "Missing $bin"; done
rsync --help | grep -q -- '--write-devices' || die 'Rsync --write-devices unavailable'
exec 9>"/run/lock/origami-pra-hot-$sid.lock"
flock -n 9 || die 'Another source transfer active'
sshopts=(-i "$key" -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes
         -o "UserKnownHostsFile=$PRA_PINNED_KNOWN_HOSTS" -o GlobalKnownHostsFile=/dev/null
         -o ForwardAgent=no -o ClearAllForwardings=yes -o ConnectTimeout=15)
target="$dstuser@$host"
remote(){
  local cmd="$1"
  if [[ $dstuser != root ]]; then printf -v cmd 'sudo -n sh -c %q' "$cmd"; fi
  ssh "${sshopts[@]}" "$target" "$cmd"
}
guard="set -eu; test -f /etc/pve/nodes/\$(hostname -s)/qemu-server/$base.conf; test \"\$(qm status '$base')\" = 'status: stopped'; if ha-manager config 2>/dev/null | grep -Eq '^vm:$base([[:space:]]|$)'; then exit 44; fi; rsync --help | grep -q -- '--write-devices'"
remote "$guard" || die 'AU base must be stopped, node-local, no HA, rsync-compatible'
if qm config "$sid" | grep -Eq '^(efidisk|tpmstate|unused|hostpci|usb|ide[0-9]+: .*cloudinit)'; then
 die 'Special disks need dedicated adapter'
fi
slots=$(qm config "$sid" | awk -F ': ' '/^(scsi|virtio|sata|ide)[0-9]+: /{print $1}' | sort)
[[ -n $slots ]] || die 'No supported disks'
base_slots=$(remote "qm config '$base' | awk -F ': ' '/^(scsi|virtio|sata|ide)[0-9]+: /{print \$1}' | sort")
[[ "$slots" == "$base_slots" ]] || die 'Disk topology changed since last verified generation'
need=0
for slot in $slots; do
 vol=$(qm config "$sid" | sed -n "s/^$slot: \([^,]*\).*/\1/p")
 [[ $vol =~ ^[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]+$ ]] || die 'Invalid PVE source volume'
 dev=$(pvesm path "$vol")
 [[ $dev == /dev/* && -b $dev ]] || die 'Source disk not a block volume'
 size=$(blockdev --getsize64 "$dev")
 need=$((need+size))
done
available=$(df -B1 --output=avail "$scratch" | tail -1 | tr -d ' ')
[[ $available =~ ^[0-9]+$ ]] || die 'Cannot check scratch size'
((available >= need + need/10)) || die 'Insufficient scratch free space for complete snapshot'
# Never launch a new full-clone generation without enough measured capacity
# on the AU Proxmox image storage. pvesm status reports available KiB.
target_free_kib=$(remote "pvesm status | awk -v s='$storage' '\$1==s && \$3==\"active\" {print \$6}'")
[[ $target_free_kib =~ ^[0-9]+$ ]] || die 'Target storage free capacity cannot be verified'
target_free_bytes=$((target_free_kib*1024))
((target_free_bytes >= need + need/10)) || die 'Insufficient AU storage for a full candidate clone'
work=$(mktemp -d "$scratch/origami-$sid-XXXXXXXX")
unpacked="$work/unpacked"
cleanup(){
 rc=$?
 if [[ -n $work && -d $work && $work == "$scratch"/origami-"$sid"-* ]]; then rm -rf -- "$work"; fi
 exit "$rc"
}
trap cleanup EXIT
echo "PRA HOT: extracting point-in-time backup while source remains running"
vzdump "$sid" --mode snapshot --stdout --compress 0 | vma extract - "$unpacked"
running
[[ -f "$unpacked/qemu-server.conf" ]] || die 'Snapshot config missing'
for slot in $slots; do
 [[ -f "$unpacked/disk-drive-$slot.raw" && ! -L "$unpacked/disk-drive-$slot.raw" ]] || die "Snapshot missing disk $slot"
done
candidate=$(remote "pvesh get /cluster/nextid --output-format=json" | tr -d '"[:space:]')
[[ $candidate =~ ^[1-9][0-9]{1,8}$ && $candidate != "$base" ]] || die 'Invalid candidate VMID'
remote "set -eu; test \"\$(qm status '$base')\" = 'status: stopped'; ! qm config '$candidate' >/dev/null 2>&1; qm clone '$base' '$candidate' --full 1 --storage '$storage' --name 'REP-$protection-G$candidate'; qm set '$candidate' --onboot 0; test \"\$(qm status '$candidate')\" = 'status: stopped'" || die 'Unable to clone offline candidate'
echo "ORIGAMI_HOT_CANDIDATE vmid=$candidate"
scanned=0; sent=0; count=0
for slot in $slots; do
 running
 remote "$guard" || die 'Base VM changed state'
 remote "test \"\$(qm status '$candidate')\" = 'status: stopped'" || die 'Candidate was started'
 raw="$unpacked/disk-drive-$slot.raw"
 vol=$(remote "qm config '$candidate' | sed -n 's/^$slot: \([^,]*\).*/\1/p'")
 [[ $vol =~ ^[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]+$ ]] || die 'Candidate volume invalid'
 dev=$(remote "pvesm path '$vol'")
 [[ $dev =~ ^/dev/[A-Za-z0-9_./-]+$ ]] || die 'Candidate not block-backed'
 size=$(stat -c %s "$raw")
 remote "test -b '$dev'; test \"\$(blockdev --getsize64 '$dev')\" = '$size'" || die 'Disk size mismatch'
 options=(--checksum --no-whole-file --inplace --write-devices --block-size=65536 --stats)
 if [[ $dstuser != root ]]; then options+=(--rsync-path='sudo -n rsync'); fi
 logfile="$work/$slot.log"
 rsync "${options[@]}" -e "ssh -i $key -p $port -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$PRA_PINNED_KNOWN_HOSTS -o GlobalKnownHostsFile=/dev/null -o ForwardAgent=no -o ClearAllForwardings=yes" "$raw" "$target:$dev" | tee "$logfile"
 bytes=$(awk '/^Total bytes sent:/ {gsub(/,/,"",$4);print $4}' "$logfile" | tail -1)
 [[ $bytes =~ ^[0-9]+$ ]] || die 'Missing rsync transfer statistics'
 local_hash=$(sha256sum "$raw" | cut -d' ' -f1)
 remote_hash=$(remote "sha256sum '$dev' | cut -d' ' -f1")
 [[ $local_hash == "$remote_hash" ]] || die "Disk checksum mismatch: $slot"
 remote "test \"\$(qm status '$candidate')\" = 'status: stopped'" || die 'Candidate started during verification'
 scanned=$((scanned+size));sent=$((sent+bytes));count=$((count+1))
 echo "ORIGAMI_HOT_DISK_VERIFIED slot=$slot bytes=$size transferred=$bytes"
done
running
remote "test \"\$(qm status '$candidate')\" = 'status: stopped'" || die 'Candidate no longer stopped'
echo "ORIGAMI_HOT_GENERATION_READY candidate_vmid=$candidate disks=$count scanned_bytes=$scanned transferred_bytes=$sent"
