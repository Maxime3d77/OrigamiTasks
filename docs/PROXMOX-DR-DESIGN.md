# OrigamiTasks DR — architecture Proxmox multi-datacenters (conception)

**Statut : conception, PAS un connecteur DR disponible en v0.2.** Aucune commande de réplication, promote, remote-migrate, HA/fencing, ni démarrage automatique de VM de secours n'est implémentée à ce stade.

## 1. Objectif produit

Une console Linux découvre plusieurs clusters Proxmox indépendants ; l'administrateur sélectionne une VM, un datacenter et un stockage cible, puis choisit un mode : `migration_planifiee`, `replication_delta`, `replication_continue` (si moteur sous-jacent la permet), `backup_pra`. Chaque politique enregistre RPO et RTO cibles, dernières exécutions, santé des disques, état du replica, historique et paramètres de récupération.

Dans les workflows, exposer des blocs : `Proxmox > Inventaire`, `Snapshot`, `Backup PBS`, `Migration PDM`, `DR > Vérifier la politique`, `Réplication`, `Tester le PRA`, `Failover`, `Failback`, `Rapport PRA`.

### Modèle déclaratif (concept)

```yaml
name: reprise-vm-app
source: {datacenter: AU, vmid: 106, storage: Pure_AU}
target: {datacenter: SD, vmid: 2106, storage: Pure_SD, network_profile: sd-dr-isolated}
mode: incremental
schedule: "0 */4 * * *" # toutes les 4 heures, adaptables 1 h, 15 min, etc.
recovery: {auto_start: false, require_source_fencing: true, require_two_approvers: true}
checks: {guest_agent: true, app_consistency: true, test_restore: weekly}
```

Le YAML est seulement une proposition de syntaxe. Ne pas le soumettre à l'API actuelle, qui ne dispose pas d'un moteur de politiques DR.

## 2. Moteurs de réplication à distinguer

| Profil | Stockage | Quoi reproduire | Limites |
|---|---|---|---|
| ZFS `pvesr` | ZFS local entre nœuds du MÊME cluster | Delta de snapshots ZFS | Non adapté directement à deux clusters Proxmox distincts |
| ZFS custom offsite | ZFS des deux côtés | `zfs send -i`/`recv` + VM config | Gestion custom complexe des snapshots, clés, nom des disques et cohérence |
| PBS | Tous stockages Proxmox pris en charge | Backups incrémentaux + sync PBS remote | Une sauvegarde n'est pas une VM prête à booter : restauration souvent nécessaire |
| Pure FlashArray ActiveDR | Pure FlashArray + pods/replica links adaptés | Deltas de blocs au niveau baie, near-zero RPO sous conditions | Ne réplique pas à elle seule l'identité Proxmox de la VM ; design multi-cluster indispensable |
| Pure FlashArray ActiveCluster | Pure FlashArray + réseau adapté, médiateur | Réplication synchrone, architecture étirée | Nécessite architecture validée ; ne pas présenter le LUN actif en lecture-écriture à deux clusters indépendants sans protocole/coordination |
| Pure protection-group snapshots | Pure FlashArray | Snapshots périodiques incrémentaux sous politique baie | RPO dépend fréquence ; clone/mapping/registration à orchestrer |
| Ceph RBD mirroring | Ceph sur les deux DC | Réplication asynchrone d'images RBD | Compatibilité/ownership/promotion/fencing à vérifier dans l'infra réelle |

### Point spécifique aux clusters sur Pure iSCSI / LVM

Si les disques des VMs reposent sur des volumes LVM portés par des LUNs iSCSI Pure, la réplication ZFS native PVE n'est **pas** l'option directe. Il faut vérifier le mode d'accès iSCSI, les WWID multipath, les VG/LV, le partitionnement, les snapshots de baie et la réplication entre FlashArrays. Les mêmes métadonnées de VG/LV répliquées peuvent créer des conflits d'identité si montées simultanément. Une réplication de la LUN entière peut répliquer plusieurs VMs, pas une seule : une isolation au niveau volume/consistency group doit être conçue.

Aucune action automatique sur les LUNs/VGs ne doit être générée à partir d'un simple VMID : prévisualiser le plan et exiger une validation des correspondances VM → disque → stockage → baie.

## 3. Réplication ≠ migration ≠ restauration

- **Migration PDM** : déplacement d'une VM (une seule active), à privilégier pour maintenance et réallocation planifiée. Elle ne produit pas forcément une VM miroir arrêtée mise à jour en permanence.
- **Réplication asynchrone** : les écritures source arrivent en retard sur cible. RPO > 0, borné par la santé et les retards du moteur. La VM standby est créée/actualisée en contrôlant la cohérence des disques et la configuration Proxmox.
- **Réplication synchrone** : données acquittées entre baies selon leur mécanisme ; RPO disque potentiellement 0, mais il faut encore orchestrer le compute, les votes, le réseau et les états applicatifs.
- **PBS** : copies durables de secours, idéalement avec tests de restauration automatisés ; les performances de récupération (RTO) dépendent notamment de la restauration complète ou du live restore selon contexte.

## 4. Flux de reprise obligatoire

1. Vérifier que la source est réellement indisponible ou arrêtée ; demander une **preuve de fencing** si le cluster/source peut encore écrire.
2. Confirmer la dernière version stable côté cible : horodatage, état de la réplication, intégrité et cohérence applicative.
3. Arrêter/mettre en quarantaine la réplication et réaliser les opérations de promotion/montage **seulement sur la baie/instance cible**.
4. Vérifier identité stockage : LUN/WWID, contrôleurs, mapping et VG ; refuser un clone dont l'identifiant est déjà visible ailleurs.
5. Créer/mettre à jour la définition de VM sur le cluster cible avec VMID distinct, BIOS/UEFI/TPM/EFI, disque et NIC mappés.
6. Garder la NIC isolée jusqu'à validation anti-doublon IP/MAC/AD ; vérifier DNS, VLAN, firewall et dépendances.
7. Démarrer après **deux validations humaines distinctes** (non intégré dans v0.2) ; exécuter tests applicatifs, remontée Zabbix, revue des journaux.
8. Marquer le site cible comme nouveau primaire ; interdire le redémarrage de l'ancien primaire.
9. Pour retour AU/SD (failback), réconcilier les données modifiées, ne jamais réactiver aveuglément une ancienne copie.

Ne pas confondre l'état 'offline' d'un endpoint de supervision avec la preuve de perte de l'ancienne source (réseau partitionné). Il faut un mécanisme indépendant empêchant deux primaires.

## 5. Écrans et API à réaliser

- **Datacenters** : découverte, connexion API et réconciliation d'inventaire (v0.2 : version basique de cet écran).
- **Cartographie inter-DC** : source→cible, profil réseau, latence, santé stockage, RPO mesuré, dernière réussite.
- **Politique par VM** : RPO (4 h, 1 h, 15 min, second-level si moteur), stockage, bande passante, fenêtres de copie, cohérence applicative.
- **Préflight migration/DR** : correspondances bridges, VLAN, CPU, disques, capacités, VMID, compatibilité version Proxmox, permissions, snapshot chain.
- **Recovery runbook** : dry-run, approbations, fencing, promotion, boot, tests, documentation du retour arrière.
- **Rapport PRA** : RPO réel, RTO mesuré, preuves de tests, écarts et alertes.
- **API idempotente** : `POST /dr/policies`, `POST /dr/policies/{id}/preflight`, `POST /dr/policies/{id}/sync`, `POST /dr/failovers/{id}/approve`, etc., sous RBAC par VM et audit signé.

## 6. Contrat de recette avant activation des écritures

- Une VM test Linux et une Windows avec QEMU Guest Agent, **données non sensibles**, snapshots cohérents.
- Tests de compatibilité storage LVM/Pure, ZFS si disponible, backup PBS, Proxmox/PDM versions installées.
- Réplication avec charge d'écriture et mesure du délai réel de propagation ; incident réseau, espace plein, storage cible absent.
- Test de panne source, échec de fencing (démarrage DOIT être refusé), source joignable (refus), split-brain simulé.
- Tests failover/failback en réseau isolé, vérification VM config, services et récupération des données.
- Tests de permissions : utilisateur viewer/opérateur ne peut ni promouvoir ni démarrer sans droits/approbations.

## 7. Sources

- Proxmox Datacenter Manager : https://pdm.proxmox.com/docs/introduction.html
- PDM features/roadmap : https://pdm.proxmox.com/docs/roadmap.html
- Proxmox réplication native : https://github.com/proxmox/pve-docs/blob/master/pvesr.adoc
- Proxmox Backup Server : https://pbs.proxmox.com/docs/managing-remotes.html
- FlashArray API : https://code.purestorage.com/swagger/redoc/fa2.41-api-reference.html
