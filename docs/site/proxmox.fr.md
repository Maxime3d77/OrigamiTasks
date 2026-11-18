# Proxmox VE : découverte et reprise d'activité

**Actuellement :** enregistrement de plusieurs API Proxmox indépendantes, découverte des nœuds/VM QEMU/LXC/stockages et, si explicitement autorisé, `start/shutdown/reboot` encadrés. Une **évaluation PRA en lecture seule** identifie les types de stockage de chaque disque VM et propose les voies de reprise à étudier. **Pas encore de moteur de réplication, de bascule automatique ni de migration inter-clusters dans la v0.3.13-rc1.**

## Ajouter un serveur ou cluster et découvrir les VM

1. Dans **Administration → Serveurs & clusters Proxmox**, choisir **Ajouter un serveur / cluster**, puis renseigner son type, FQDN HTTPS, nom et token API (ou sélectionner une connexion du coffre).
2. Importer une CA interne au format PEM/CRT/CER/DER si possible ; pour un certificat autosigné sans CA importable, l'administrateur peut **ignorer la vérification TLS pour ce seul serveur**, avec un risque d'usurpation à accepter. Aucune modification du fichier .env n'est nécessaire.
3. **Découvrir les nœuds/VM** puis rechercher par nom/VMID/nœud/état. Filtres **VM / LXC / Nœuds / Stockages**, tri et pagination de 100 résultats.
4. Sur une VM QEMU, utiliser **Évaluer PRA** pour vérifier la configuration disque et le type de stockage. Le diagnostic ne réplique ni ne démarre de VM.
5. Les seules actions actuellement implémentées dans une tâche Proxmox sont inventaire, start, shutdown et reboot ; les mutations requièrent toujours les autorisations serveur et workflow spécifiques. Les VMID/nœuds ne sont pas encore proposés comme sélecteurs dynamiques de workflow.

### Agents inter-sites et stratégie de reprise SD → AU

Le futur module de réplication doit gérer **SD actif → AU copie REP_ arrêtée**, des agents appairés en mTLS, un **témoin indépendant**, du **fencing vérifié** pour éviter tout double démarrage, et un **failback demandé explicitement** après synchronisation AU→SD. Une panne d'agent, un ping absent ou une API source injoignable **ne suffisent jamais** à démarrer la copie AU. **Architecture de référence** : fichier docs/ARCHITECTURE-PRA-INTERSITES.fr.md. Cette architecture reste au stade de la conception.

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
