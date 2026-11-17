# Proxmox VE : découverte et reprise d'activité

### Installer l'agent sur tous les nœuds du cluster depuis Proxmox (0.3.17-rc1)

Sur la fiche du cluster, cliquer **Agents du cluster**, ou depuis un site PRA sur **Déployer sur les nœuds**, puis choisir le cluster si le site en comporte plusieurs.

1. OrigamiTasks découvre via l'API Proxmox les nœuds du cluster. Il construit leur FQDN SSH (ex. `pve01.infra.example.org`) à partir du suffixe DNS de l'URL API, avec possibilité de le corriger dans le formulaire. Le suffixe choisi doit correspondre aux FQDN retournés par Proxmox et au réseau SSH réellement utilisé.
2. Vérifier la liste, cocher les nœuds, puis **Créer / Synchroniser le groupe SSH**. Cela crée des entrées d'inventaire SSH et le groupe `PVE-<id>-<nom>`, lié durablement au cluster, **sans installer de logiciel**. Une synchronisation explicite remplace les membres du groupe géré. Les mêmes serveurs peuvent appartenir à d'autres groupes SSH indépendants.
3. Pour chaque nœud non encore approuvé, utiliser **Empreinte SSH**. Comparer le SHA256 affiché à celui obtenu directement sur la console du nœud et saisir l'empreinte exacte pour l'approuver. Les nouveaux hôtes non vérifiés **ne sont jamais autorisés automatiquement**.
4. Choisir l'identité SSH du **coffre** (clé privée, droits root ou `sudo -n` sur les nœuds), sélectionner seulement les nœuds non encore équipés, cocher l'approbation, puis **Déployer les agents sélectionnés**. La file persistante réalise une installation à la fois ; chaque nœud reçoit l'**agent unique Linux** standard + superviseur PRA passif.
5. Suivre le résultat sous **Agents → Déploiements groupés**. Quand le cluster appartient à un site PRA, l'agent nouvellement enregistré lui est associé automatiquement. Les modules Proxmox et PRA passif peuvent ensuite être activés dans **Agents → Configurer modules**. **Aucune réplication de VM** n'est déclenchée.

**Sécurité :** pas de liste de FQDN à modifier dans le `.env` pour ce parcours. La sélection des cibles passe par le cluster déclaré, la découverte Proxmox, le groupe lié et la confirmation administrateur. Avant chaque installation, le worker recoupe le FQDN, le port, le membre du cluster et la clé d'hôte SSH approuvée. Les déploiements génériques non liés à un cluster continuent d'exiger leurs restrictions de configuration. Il s'agit d'une action privilégiée : tester sur un nœud non critique avant de traiter la totalité des nœuds.

### Sites PRA nommés, agents et simulation sans risque (0.3.14)

Sous **Serveurs & clusters Proxmox → Sites PRA et agents**, cliquer **Déclarer un site**. Donner son nom (ex. SD, AU), son rôle, sélectionner **un ou plusieurs clusters Proxmox déjà enregistrés** et indiquer le profil de stockage. La valeur **iSCSI + LVM** est pour l'instant une **déclaration à vérifier**, pas une découverte confirmée du stockage. Le groupe de volumes LVM partagé peut héberger plusieurs disques de VM sur une même LUN : la réplication du volume Pure ne produit pas automatiquement une copie indépendante par VM.

Associer éventuellement des agents Linux existants. Le bouton **Déployer agent SSH** réutilise le déploiement standard OrigamiTasks et sa clé SSH du coffre ; ce mécanisme nécessite encore ses prérequis actuels, et l'agent installé **ne devient pas un arbitre autonome**. Les véritables agents PRA devront être gérés par cluster/site (plusieurs nœuds possibles), disposer de leur propre configuration locale sécurisée, continuer quand la console SD disparaît, et être mis à jour en rolling update avec vérification et rollback. Une liaison mTLS, un témoin indépendant et un fencing source sont obligatoires avant toute promotion automatique.

La commande **Simuler une panne (sans action)** propose cluster principal inaccessible, coupure du lien entre les sites, ou VM de la console OrigamiTasks indisponible. Elle montre les prérequis manquants et trace la simulation ; **aucun nœud ni VM ne sont arrêtés**, et **aucune copie REP_ n'est démarrée**. Cela ne remplace pas une véritable campagne de tests d'interruption/fencing en laboratoire.

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
