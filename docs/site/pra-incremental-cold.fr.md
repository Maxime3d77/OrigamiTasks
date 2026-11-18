# v0.3.29 — Synchronisation incrémentale à froid et suivi par VM

Le panneau **VM protégées PRA** affiche maintenant le dernier état, la dernière synchronisation réussie, les disques traités, le volume parcouru, les octets envoyés par rsync, la durée et les erreurs. Le bouton **Suivi incrémental** montre les trente derniers essais par VM.

## Moteur réel sans PBS

Le script scripts/pra-incremental-cold.sh synchronise les périphériques bloc LVM déjà créés sur le site secondaire en utilisant rsync avec les options --copy-devices, --write-devices, --checksum, --no-whole-file et --inplace via SSH. Les réseaux iSCSI restent séparés. Les octets modifiés sont transmis, mais les disques complets sont examinés pour calculer les différences.

**ATTENTION : source et secours doivent être ARRÊTÉS pendant toute la synchronisation.** Le pilote refuse les VM HA, les nœuds incorrects, les topologies/taille de disques différentes, les fichiers au lieu de disques bloc, le TPM et les disques EFI. Il ne garantit pas l'exclusion d'un redémarrage externe pendant le transfert et ne constitue pas un PRA de production. Ne démarre ni ne supprime les VM.

## Recette sur une VM non critique

Après une copie initiale vers le VMID de secours et avec des identités SSH techniques autorisées (la clé reste sur le nœud source) :

~~~bash
# Sur les deux nœuds
rsync --help | grep -E 'copy-devices|write-devices'

# Sur le nœud qui héberge actuellement la VM source, après arrêt des DEUX VM
bash /root/pra-incremental-cold.sh 100 pve-dr.example.org 900100 \
  --confirm-offline /root/.ssh/id_pra 22 1
~~~

Le dernier nombre est l'identifiant de la protection dans OrigamiTasks. Installer dans /root les deux fichiers scripts/pra-incremental-cold.sh et scripts/pra-sync-report.py, et vérifier que l'agent du nœud source appartient au site PRA principal. L'outil récupère le jeton de l'agent déjà enrôlé depuis son installation ; aucune nouvelle variable .env n'est nécessaire.

Le rapport utilise l'agent authentifié et vérifie que le nœud est l'hôte actuel de la VM sur le cluster principal. La synchronisation manuelle depuis l'interface, l'automatisation récurrente, les clés SSH du coffre pour le trajet source-cible, les deltas QEMU à chaud, le fencing et le failback ne sont pas encore implémentés. Une VM qui migre nécessite de relancer le script sur son nœud actuel.
