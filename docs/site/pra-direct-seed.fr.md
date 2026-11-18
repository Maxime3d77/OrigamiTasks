# PRA : copie directe SD → AU sans PBS (prototype 0.3.28)

**But** : transférer réellement une première copie des disques QEMU depuis le nœud qui héberge la VM sur SD vers un nœud AU, avec `vzdump --mode snapshot --stdout --compress 0 | ssh ... qmrestore -`. Le stockage iSCSI reste **local à chaque datacenter**. Le flux emprunte le réseau IP/SSH intersites.

Cette première copie est un **seed VMA complet**, pas une réplication incrémentale ni une synchronisation temps réel. Elle ne met pas à jour une VM déjà restaurée et n'autorise ni bascule ni failback. Les changements après la copie ne sont PAS reproduits. Le PBS existant reste indépendant et peut continuer ses sauvegardes du soir.

## Commande de recette

Sur le **nœud qui héberge actuellement** la VM source QEMU (et uniquement pour une VM sacrifiable) :

```bash
# Depuis le dépôt de recette de l'outil :
bash scripts/pra-direct-seed.sh 100 au-styx.infra.aday.fr 900100 local-zfs --confirm root /root/.ssh/<CLE_AUTORISEE>
```

Les arguments sont : VMID source, FQDN SSH cible, VMID cible **libre**, stockage images AU, confirmation, utilisateur SSH cible et chemin de la clé SSH présente sur le **nœud source**. Ne pas copier une clé privée personnelle du coffre vers les nœuds : pour ce prototype CLI, employer une identité technique préconfigurée et restreinte pour le lien source→cible. La clé d'hôte cible doit être préalablement approuvée dans `known_hosts` du compte source. Les nœuds doivent pouvoir joindre le SSH de l'autre site.

Le script vérifie une VM sur le nœud courant, l'accès SSH et le stockage cible, refuse un VMID existant, puis effectue la copie en **flux direct** (pas de fichier PBS temporaire). Il demande `qmrestore --start 0 --unique 1`, garde `onboot=0`, et n'envoie jamais `--force`.

## Important

- Une VM en fonctionnement peut faire l'objet d'un backup snapshot de QEMU sans arrêt, mais la cohérence applicative dépend de sa configuration et des invités.
- Une panne réseau peut laisser un **clone incomplet** et des volumes côté AU. Vérifier manuellement l'état avant de réessayer. Aucun nettoyage destructif automatique.
- L'absence de verrouillage imposé à tout Proxmox/HA interdit de démarrer le secours en réseau de production.
- Ce script est une **preuve du moteur de transfert**, pas encore un job intégré à la page « VM protégées PRA » et ne réutilise pas encore directement les identités chiffrées du coffre depuis l'interface. L'intégration des identités via délégation SSH temporaire sécurisée est requise avant déploiement groupé.
- La prochaine étape est un moteur de **changed-block tracking** adapté à QEMU et à LVM iSCSI, sans accès en écriture concurrent aux disques, avec génération de snapshots cohérents, journaux de deltas, resynchronisation inverse et fencing.
- Aucun nouveau paramètre `.env` n'est nécessaire pour ce prototype. La sélection des clés et les validations doivent être gérées dans les futurs écrans d'administration, pas avec des interrupteurs d'environnement.
