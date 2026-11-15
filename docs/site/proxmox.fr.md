# Proxmox VE : découverte et reprise d'activité

**Actuellement :** enregistrement de plusieurs API Proxmox indépendantes, découverte des nœuds/VM QEMU/LXC/stockages et, si explicitement autorisé, `start/shutdown/reboot` encadrés. Une **évaluation PRA en lecture seule** identifie les types de stockage de chaque disque VM et propose les voies de reprise à étudier. **Pas de réplication en temps réel, de bascule ni de migration inter-clusters dans la v0.2.**

## Ajouter un datacenter

1. Créer un token API Proxmox de **lecture seule** pour l'inventaire (`user@realm!tokenid` et sa valeur).
2. Déclarer `https://pve01.example.org:8006` et `PROXMOX_ALLOWED_HOSTS=pve01.example.org:8006`.
3. Faire confiance à sa CA ; ne jamais désactiver la validation TLS. Pour une CA interne, monter un PEM et paramétrer `PROXMOX_CA_CERT`.
4. Ouvrir **Datacenters → Ajouter**, sélectionner le token et lancer **Découvrir**. Sur une VM QEMU, utiliser **Évaluer PRA** : le diagnostic interroge la configuration disque et `/storage` sans modifier les volumes.
5. Pour les opérations, autoriser explicitement `ALLOW_PROXMOX_ACTIONS=true` ; chaque bloc d'action doit aussi porter `approved=true` et utiliser un token de droits limités.

## Quel moteur de réplication ?

| Stockage | Mécanisme | Résultat | Limite |
|---|---|---|---|
| ZFS local dans **un même** cluster | `pvesr` Proxmox | Réplication incrémentale entre nœuds | Pas une réplication générique iSCSI inter-clusters |
| Stockage VM supporté par Proxmox | Sauvegarde PVE → PBS → sync distante → restauration | Copie PRA incrémentale/dédupliquée | Le backup n'est **pas** une VM de secours prête à démarrer |
| Deux baies SAN iSCSI | Snapshots et réplication natives des baies | Copie par blocs, fréquente voire quasi synchrone | Dépend du fournisseur, de la latence, de l'ordre d'écriture et du fencing |
| Ceph RBD | RBD mirroring inter-clusters | Réplication par blocs | Topologie supportée et procédure de bascule à primaire unique |
| Deux clusters indépendants | Migration PDM/PVE si supportée | Déplacement de VM active | Ce n'est pas un miroir maintenu en permanence |

### Pourquoi pas un rsync temps réel ?

Sur une **VM allumée** et un disque iSCSI/LVM, `rsync` d'une image ou d'un périphérique bloc lit des secteurs qui évoluent pendant la copie. Il ne garantit ni point de restauration atomique cohérent, ni ordre des écritures, et peut produire un clone non démarrable. `rsync` convient à certains disques **à l'arrêt** ou à des données applicatives, pas comme moteur universel de réplication de VM active. Ne jamais présenter en écriture la même LUN aux deux clusters indépendants sans gestion de propriété du stockage.

### Workflow PRA cible

**Découverte → évaluation de compatibilité → contrôle capacité cible → snapshot cohérent → transfert/synchro → vérification → enregistrement standby → test de restauration → rapport.** La bascule est un autre workflow à approbation : attestation du fencing source, gel de réplication, disponibilité des volumes cibles, réseau isolé, démarrage unique, tests applicatifs et publication réseau/DNS. Ne jamais démarrer le clone seulement parce que l'API source est injoignable : cela peut être une partition réseau.

## RPO et RTO

Le `RPO` (perte de données maximale acceptable) dépend du **dernier point de reprise cohérent et vérifié**. Le `RTO` inclut la restauration, le boot, le réseau et les tests métiers. Une réplication horaire **ne garantit pas** une heure de RPO si un job échoue ; mesurer RPO et RTO en exercice de PRA.

## Généralisation

OrigamiTasks doit représenter un DC par un cluster, ses capacités de stockage, ses correspondances réseaux et ses credentials, sans imposer un fournisseur de baies. Des connecteurs Pure Storage, PBS, Ceph, ZFS et SAN tiers pourront être ajoutés. Pour iSCSI, la découverte devra identifier les LUN, snapshots, consistency groups, modes de réplication et fencing **avant** toute automatisation.

Sources : [réplication PVE](https://github.com/proxmox/pve-docs/blob/master/pvesr.adoc), [stockages PVE](https://github.com/proxmox/pve-docs/blob/master/pvesm.adoc), [sync PBS](https://pbs.proxmox.com/docs/managing-remotes.html), [sauvegardes incrémentales PBS](https://pbs.proxmox.com/docs/technical-overview.html).
