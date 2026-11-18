# Correctifs 0.3.22 — inventaire, agents et première sauvegarde PBS réelle

> **Statut : branche de recette, non déployée en production.** Les démarrages/arrêts PRA automatiques, la restauration de VM, le témoin, le fencing d'hyperviseur et le streaming delta inter-clusters restent non intégrés. Ne pas les considérer opérationnels.

## Corrections utilisateurs

- Un groupe SSH synchronisé depuis Proxmox peut être **renommé** et recevoir une nouvelle description ; les membres sont toujours gérés par la découverte Proxmox.
- L'agent **Standard** est intégré, sans faux badge de module optionnel. Les rôles Proxmox et PRA restent visibles séparément.
- La liste des déploiements groupés offre un nettoyage administrateur : 20 lots terminés maximum et 30 jours de rétention. Les lots en cours ne sont pas supprimés ; le journal d'audit reste intact. Ce nettoyage est **manuel**, pas encore un cron automatique.
- La sélection de protection VM utilise la découverte de tout le cluster via `/cluster/resources`. Le compteur distingue les VM QEMU visibles des conteneurs LXC exclus. Une VM manquante peut résulter des droits ACL du token PVE.

## Sauvegarde réelle PBS (première étape vérifiable)

Pour activer un **backup réel sur le cluster source** :
1. Installer une version de recette issue de cette branche et sauvegarder la base SQLite.
2. Monter/autoriser un stockage `pbs` avec capacité de contenu `backup` **sur le cluster source**, et fournir à l'identité Proxmox les permissions nécessaires.
3. Activer `ALLOW_PROXMOX_ACTIONS=true` uniquement après validation de l'environnement et contrôle des droits.
4. Déclarer la VM protégée avec la stratégie **PBS : sauvegarde réelle manuelle** ; ne pas la mettre en pause.
5. Dans « VM protégées », cliquer **Sauvegarder sur PBS**, saisir l'identifiant exact du stockage PBS du cluster source, confirmer.
6. Utiliser **Vérifier le résultat**. Le backend suit le `UPID` Proxmox et marque la sauvegarde terminée seulement lorsque la tâche s'arrête avec `exitstatus=OK`. Les reprises ambiguës doivent être reconciliées dans les tâches du cluster.

**Important :** cette opération déclenche réellement `POST /api2/json/nodes/{nœud_courant}/vzdump` avec `mode=snapshot`. Elle ne crée pas `REP_...` sur le cluster de secours. PBS fournit de la déduplication et peut réutiliser les blocs déjà sauvegardés, mais ce n'est **pas du temps réel** et cela n'atteste pas d'une restauration utilisable.

## Verrou de démarrage

L'API de tâches OrigamiTasks refuse désormais **start/reboot sur un VMID cible déclaré comme secours**. Ce contrôle ne s'applique pas aux démarrages directement réalisés par la CLI/UI/API Proxmox, à HA, aux redémarrages d'hôtes ou à d'autres outils. Il **ne remplace pas** un agent de fencing sur l'hyperviseur, un témoin indépendant ni un bail de stockage. Les bascules réelles restent interdites.

## Étapes nécessaires avant la réplication intersites opérationnelle

- Qualifier un transport PBS distant et une restauration vers le stockage cible, avec vérification de l'intégrité et réseaux de test isolés.
- Ajouter le gestionnaire de propriété par cluster (epochs et témoin réellement indépendant), les contrôles de démarrage imposés dans PVE/HA et le fencing technique.
- Pour réduire le RPO à quelques secondes, développer un moteur de suivi de blocs modifiés cohérent, avec resynchronisation inverse après bascule. Un backup PBS périodique ne remplace pas cela.
- Tester sur VM sacrifiable : migration intra-cluster pendant sauvegarde, coupure réseau, stockage inaccessible, témoin perdu, bascule et failback contrôlés. Aucune bascule automatique sans preuve de fencing.

Voir [architecture du PRA](https://github.com/Maxime3d77/OrigamiTasks/blob/feat/dr-protected-vms-simulations-0.3.21/docs/ARCHITECTURE-PRA-INTERSITES.fr.md).

## Restauration réelle d'une copie sur le cluster de secours (0.3.27)

La nouvelle action **Restaurer sur site de secours** utilise l'API Proxmox `POST /nodes/{node}/qemu` avec `archive`, `vmid`, `storage`, `start=0`, `onboot=0`, `ha-managed=0` et `unique=1`. Elle crée les disques et la configuration d'une VM arrêtée sur le cluster cible, **sans écraser une VM préexistante**. OrigamiTasks contrôle le job UPID, la présence d'une VM QEMU arrêtée et le paramètre onboot désactivé avant d'afficher la réussite.

**Précondition obligatoire :** les clusters source et cible doivent pouvoir consulter le **même snapshot PBS** : soit même serveur/datastore PBS configuré sur les deux clusters, soit PBS distant synchronisé avec la même sauvegarde disponible. Aucun routage iSCSI intersite n'est nécessaire ; les transferts se font via PBS et l'API réseau Proxmox.

Procédure pour une VM sacrifiable :
1. Vérifier le stockage PBS accessible aux deux clusters et les ACL `Datastore.Backup` / `Datastore.Read` et droits de restauration Proxmox.
2. Ajouter la protection avec moteur PBS, VMID cible libre, nœud cible et stockage images local à AU PP.
3. **Sauvegarder sur PBS**, puis **Vérifier le résultat** jusqu'à `completed/OK`. Noter l'identifiant numérique du job.
4. **Restaurer sur site de secours**, entrer l'identifiant du job et le nom du PBS sur le cluster cible. Sélectionner explicitement le snapshot source correspondant, confirmer l'écriture.
5. Utiliser **Vérifier le résultat** jusqu'à `completed`. Sur le cluster de secours, contrôler `qm status <VMID>`, `qm config <VMID>`, et `pvesm status`.

**Limitations de sécurité :** ne PAS démarrer la VM source et la copie simultanément sur un réseau connecté. L'action restaure une seule génération de sauvegarde et ne met pas à jour automatiquement une copie déjà créée. Aucune synchronisation continue, bascule automatique, témoin indépendant ou fencing côté hyperviseur ne sont fournis. Ne pas utiliser ce pilote comme plan PRA production.
