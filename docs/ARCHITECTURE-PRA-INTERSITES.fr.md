# OrigamiTasks — PRA inter-sites SD → AU (conception)

**État : RFC / architecture proposée, non implémentée.** Aucun démarrage de secours, failback, fencing ou moteur de réplication interclusters n'est actuellement activé par ce document.

## Objectif fonctionnel

- SD est **actif** ; AU dispose d'une copie **arrêtée**, nommée REP_NOM, d'un point de restauration consistant. Le VMID cible peut être différent.
- Un groupe de protection lie l'identité stable de la VM source (site + cluster + VMID + génération) à la VM cible et au stockage/réseau, sans se baser sur le simple préfixe REP_.
- Montrer le **RPO réellement observé** (ancienneté du dernier snapshot transféré et validé), le retard de réplication, l'intégrité et le **RTO mesuré** lors des exercices. Ne jamais promettre le temps réel sans confirmation des capacités du moteur de stockage.
- Quand SD revient après une bascule sur AU, **SD ne reprend pas automatiquement le rôle actif**. Le retour nécessite une procédure explicite de failback.

## Découverte et interface

Le modal de découverte propose recherche (nom, VMID, nœud, statut), filtres **VM, LXC, nœuds, stockage**, tris et pagination. En phase ultérieure, les VMs QEMU pourront être cochées pour créer un groupe de protection. LXC nécessitera une stratégie distincte.

L'onglet PRA visé propose : **Sites**, **Agents**, **VM protégées**, **Réplications**, **Simulations**, **Basculement**, **Failback**, **Historique / alertes**. Pour chaque VM : source, destination, REP_, réplication, délai, snapshot, stockage, réseau, état de santé, restrictions et priorité de redémarrage.

## Architecture cible

1. **OrigamiTasks** : console de configuration, coffre, orchestration, audit, permissions et politique de bascule. Son indisponibilité ne doit pas supprimer les mesures de sécurité.
2. **Agents sur SD et AU**, idéalement redondants : identité par certificat mTLS, renouvellement, appels PVE/Pure/PBS à privilèges minimaux, reprises idempotentes, transfert et vérification de points de restauration.
3. **Arbitre / témoin situé hors des deux domaines de panne SD et AU** : état durable, propriétaire actif pour chaque VM, génération monotone (epoch), contrôle anti-rejeu, autorisation signée. Ce témoin est distinct d'un qdevice interne au cluster Proxmox.
4. **Fencing réellement capable de bloquer les écritures / redémarrages SD** : accès hyperviseur ou BMC/PDU hors bande, accès au stockage et garde locale de démarrage. Le fencing est vérifié par des preuves indépendantes.

### Invariants non négociables

- **Une seule copie active et inscriptible.** Une simple perte de ping, de l'API ou de l'agent source ne prouve pas que la VM source est arrêtée.
- AU ne démarre pas REP_ avant **accord du témoin ET preuve vérifiée de fencing source**. Témoin perdu, contradiction ou fencing incertain : mode NEEDS_OPERATOR, aucune bascule.
- Les VMs protégées doivent avoir leurs options HA/onboot et leurs mécanismes de redémarrage maîtrisés côté SD ; au retour de SD, un garde-fou doit empêcher **toute** remise en route tant que AU détient le rôle actif. Un agent qui perd son bail ne garantit pas l'arrêt d'une VM qui tournait déjà.
- Le préfixe REP_ sert à identifier visuellement les copies, pas à garantir l'exclusivité ; maintenir un mapping global et des journaux de décisions.
- Ne jamais recopier vers SD un état devenu obsolète après l'activation d'AU. Les écritures produites à AU doivent être resynchronisées dans le bon sens avant failback ; toute divergence bloque la procédure.

### Machine à états proposée

- Initiale : DISCOVERED → SEEDING → PROTECTED_AU_OFF → SYNCED ; sinon REPLICATION_LAG ou REPLICATION_FAILED.
- Sinistre : SD_SUSPECT → DECISION_PENDING → SD_FENCED → AU_PROMOTING → ACTIVE_AU ; toute preuve manquante mène à NEEDS_OPERATOR.
- Retour SD : ACTIVE_AU → SD_RETURNED_LOCKED (SD reste passif), puis seulement après **demande de retour** : FAILBACK_REQUESTED → REVERSE_SYNC → AU_FENCED → SD_PROMOTING → ACTIVE_SD → RESUME_REPLICATION. Une erreur donne FAILBACK_BLOCKED.
- Chaque transition doit être idempotente, corrélée à une génération/epoch, tracée et contrôlée par conditions explicites.

### Détection et failover

1. Distinguer **panne du site**, **perte réseau**, **agent indisponible** et **panne VM** via plusieurs signaux : Proxmox, agents, stockage, témoin et sondes externes.
2. Vérifier le dernier point de restauration consistant à AU, sa fraîcheur RPO, les disques et le réseau de secours.
3. Fencer SD de façon effective, établir un propriétaire AU auprès du témoin, puis seulement autoriser la promotion de la VM REP_ sur AU.
4. Contrôler les VLAN/bridge, IP/MAC (collision potentielle), dépendances et accès applicatifs ; tester l'état invité avant de déclarer la reprise.
5. Au retour SD : **ne pas** lancer la VM source. Bloquer la relance automatique à plusieurs niveaux indépendants de l'agent. La reprise SD n'est permise qu'après resynchronisation depuis AU, arrêt/fencing AU, validation exclusive et nouvelle autorisation du témoin.

### Moteurs de réplication à étudier

- **pvesr / ZFS local** : incrémental entre nœuds du même cluster ; pas un moteur générique de réplication entre SD et AU indépendants, notamment pour iSCSI/LVM.
- **PBS** : snapshots/sauvegardes incrémentales et restauration, solution de base pour le PRA (RTO et durée de restauration à mesurer, pas une image AU instantanément synchronisée).
- **Pure Storage FlashArray** : Protection Groups / réplication asynchrone cohérente, ou ActiveCluster synchrone **si compatible avec versions, topologie, médiateur et licences**. Sur les grandes LUN iSCSI/LVM hébergeant plusieurs VMs, le groupe de cohérence/volume n'est pas automatiquement une copie indépendante par VM. Tester l'ownership, les chemins iSCSI, le mapping et le risque de double écriture.
- La liaison de synchronisation des données et la messagerie mTLS sont séparées ; ne pas supposer que les réseaux iSCSI sont routés.

## Livraison progressive

1. Recherche/filtres/tris de la découverte : sans action sur les VMs.
2. Agents pilotables depuis l'interface, appairage mTLS, identités, heartbeat, droits, stockage/volume mapping, simulations **sans production**.
3. Réplication expérimentale d'une VM de recette et test de reprise isolée ; suivi réel des temps/RPO/RTO.
4. Basculement **manuel** avec fencing testé, contrôle des deux sites, blocage strict des redémarrages SD et failback explicite.
5. Basculement automatique **seulement** après démonstration répétée de la sûreté en cas de partitions réseau et pertes du témoin.

## Tests indispensables avant toute automatisation

- Lien SD↔AU coupé mais SD fonctionne : aucun démarrage AU.
- API SD / agent SD indisponible, VM SD toujours active : aucun démarrage AU.
- Crash agent AU ou du plan de contrôle ; panne partielle du témoin : aucun failover non autorisé.
- SD arrêté réellement, témoin et fencing validés : promotion AU conforme.
- SD revient pendant qu'AU est actif : VM SD reste **éteinte**, même avec onboot ou HA historiques.
- Transfert incomplet, snapshot corrompu, RPO dépassé, volume iSCSI indisponible : failover bloqué.
- Demande de failback : vérifier transfert des écritures AU→SD, fencing AU, puis seul démarrage SD.
- Test réseau isolé : aucune collision IP/MAC avec les VMs de production.

**Critère de livraison :** aucune double écriture ni double démarrage, aucune reprise silencieuse sur SD, journal complet des décisions, RPO/RTO mesurés, tests reproductibles.
