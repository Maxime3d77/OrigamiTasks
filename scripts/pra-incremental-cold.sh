#!/usr/bin/env bash
# DEPRECATED: Never synchronize live LVM disks with rsync.
# The PRA source must remain running; coherent QEMU block backup is required.
set -Eeuo pipefail
echo 'PRA REFUSÉ : la VM SD reste toujours allumée. Ancien incrémental à froid désactivé. Utiliser le diagnostic QMP à chaud dans OrigamiTasks.' >&2
exit 78
