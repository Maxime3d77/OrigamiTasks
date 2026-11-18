# PRA inter-clusters : maître logique, témoin et bascule (proposition)

> **Statut : conception + garde de rôles unitaire, NON connecté au moteur de réplication ni aux démarrages Proxmox.** Ne pas activer le failover automatique à ce stade.

## Modèle

- Chaque protection possède un identifiant stable, un cluster primaire et secondaire, deux VMID, un cluster maître, un epoch croissant et la génération de copie vérifiée.
- Le **maître est un cluster, jamais un nœud**. Rechercher le nœud courant depuis `GET /cluster/resources?type=vm` à chaque opération, et revalider emplacement/VMID immédiatement avant la tâche. Un déplacement local de la VM ne change pas le maître.
- Ne jamais sélectionner un hôte statique pour assurer l'envoi de blocs : un agent sur le nœud actif coordonne le flux, puis passe le relais quand la VM migre.
- Les réseaux iSCSI source/destination restent locaux à chaque datacenter. Le transfert utilise un réseau IP de réplication routé **entre agents/serveurs**, protégé par mTLS et autorisation explicite, sans exposer les LUN brutes à l'autre cluster.
- Toute copie standby doit rester arrêtée, avec démarrage interdit par **contrôle imposé sur tous les chemins de démarrage** (API, UI, CLI, HA, auto-start), et stockage non-écrivable lorsque non maître. Une simple interdiction dans l'UI OrigamiTasks n'est pas du fencing.

## Moteur de copie à choisir

**Piste initiale réaliste** : sauvegarde incrémentale PVE → PBS accessible depuis les deux sites (ou synchronisation PBS entre sites) → restauration vérifiée sur un stockage *local au cluster cible*. Déployer des contrôles de cohérence, un RPO mesuré et un test de démarrage isolé. C'est de la réplication périodique et non du temps réel. En cas de restauration après chaque nouvelle sauvegarde, vérifier les limites de durée/charge avant de promettre un standby prêt à démarrer.

**Piste avancée** : snapshots cohérents applicativement et flux de blocs modifiés via fournisseur compatible (QEMU backup access / dirty bitmap, snapshot SAN, ou backend dédié). Nécessite suivi de génération de disque, transfert initial intégral, deltas atomiques, reprise des erreurs, protection contre l'overflow, surveillance des écritures et rebaselining. Ni `rsync` en direct ni copie à chaud d'un LV brut n'offrent ces garanties. **Ne pas afficher « temps réel » tant que ce chemin n'est pas développé et testé.**

## Bascule / retour

1. Demander un **témoin indépendant** des deux clusters et maintenir un epoch monotone par protection.
2. Suspendre la synchro et obtenir une preuve technique de **fencing de l'ancien maître** (hors tension, watchdog/gestion hors bande, révocation de son droit d'écriture et de démarrage). Une indisponibilité API ne prouve rien.
3. Vérifier la génération cohérente et la présence de tous les disques du standby, les snapshots, le stockage et le réseau isolé.
4. Transférer l'autorité avec journal d'audit, vérifier la nouvelle barrière côté hôte et stockage, puis autoriser un seul démarrage.
5. Au retour du site primaire, **ne pas redémarrer l'ancienne copie**. Effectuer une resynchronisation inverse depuis le nouveau maître, vérifier l'intégrité, fence le maître courant et seulement alors repromouvoir le site d'origine.
6. Un bouton de déverrouillage d'urgence exige deux validations, motif, ticket, preuve de fencing et audit. Il ne contourne jamais la prévention du split-brain.

**Arbitrage indisponible :** toute bascule automatique est refusée. Le maître déjà actif peut continuer seulement si sa validité et la politique du témoin le permettent; aucun site ne doit s'auto-promouvoir uniquement sur timeout.

## Travaux d'intégration à réaliser

- Brancher `app/pra_authority.py` dans une base transactionnelle avec compare-and-swap de l'epoch et quorum/témoin, validations cryptographiques des preuves et verrou distribué.
- Installer des gardes de démarrage **en dehors de l'interface**, couvrir Proxmox HA et les tâches externes. Sur clusters indépendants, concevoir explicitement les barrières et vérifier qu'elles résistent à la panne du contrôleur OrigamiTasks.
- Créer transport sécurisé, snapshots, moteur delta, reprise de copie, contrôle de génération et tests de restauration.
- Corriger la collecte exhaustive des VM, les groupes SSH renommables, le badge Standard et la rétention de l'historique dans **la branche applicative plus récente contenant ces pages** ; ces vues n'existent pas dans `main` au moment de cette préparation.
- Tests à prévoir : migration source de nœud pendant synchro, coupure réseau inter-DC, panne témoin, erreur de disque secondaire, timeout pendant promotion, double demande de promotion, retour arrière, panne totale de site, opérations concurrentes.
