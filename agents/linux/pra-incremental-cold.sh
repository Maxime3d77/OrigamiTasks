#!/usr/bin/env bash
# OrigamiTasks PRA: the old offline volume synchronizer is deliberately disabled.
# Never stop the primary VM or read a live raw LVM disk using rsync.
set -Eeuo pipefail
echo 'PRA REFUSÉ : la VM source doit rester allumée. Le moteur incrémental à froid est désactivé. Un fournisseur QEMU de deltas cohérents est requis pour le PRA à chaud.' >&2
exit 78
