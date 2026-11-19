# OrigamiTasks PRA — moteur QMP incrémental (prototype technique)

**Statut au 19 novembre 2026 : composants de transport et checkpoint développés,
tests unitaires ajoutés, MAIS aucun déploiement automatique ni synchronisation
de VM de secours fonctionnelle de bout en bout.**

Ce document décrit les parties déjà écrites et le travail restant. Il ne faut
pas confondre un bloc QMP réussi avec une VM de secours effectivement à jour.

## Exigences confirmées

- VM source QEMU Proxmox **toujours allumée** pendant les copies.
- VM de secours **arrêtée**, sans démarrage ou promotion automatique.
- Copie initiale complète, suivie des **blocs modifiés** via les dirty bitmaps QEMU.
- Prise en charge de plusieurs disques en un groupe de jobs QMP.
- Aucune réplication Pure Storage / ActiveDR ; ne jamais écrire sur un volume
  de stockage répliqué en lecture seule.
- Aucune nouvelle variable `.env` requise pour ce moteur.
- Pilotage futur via les agents OrigamiTasks, avec permissions et états en base.

## Code disponible dans cette branche

### `agents/linux/pra_qmp_incremental.py`

`QMP` connecte le socket Unix local du processus QEMU. `backup()`
valide chaque disque, attache un export NBD **local via tunnel SSH**, puis
soumet une **transaction QMP groupée** utilisant
`block-dirty-bitmap-add` / `blockdev-backup`.

- Mode `full` : recrée un bitmap propre et copie l'image entière dans un
  staging indépendant, sans arrêt de la VM.
- Mode `incremental` : utilise `sync=incremental` avec le bitmap dédié
  pour copier les changements depuis le dernier checkpoint accepté.
- Bitmap absent, incohérent ou disque remplacé : refuser l'incrémental et
  exiger un nouveau `full`, notamment après un redémarrage QEMU sur disque raw.
- Une erreur de job interdit de valider un checkpoint.
- Aucun `qm start`, `qm stop`, promotion, modification de LUN ou HA.

ATTENTION : ce composant attend des exports NBD préparés par un coordinateur
cible ; il ne sait **pas** encore ouvrir le tunnel, commander les deux agents
ou changer la VM de secours. Il n'est pas exposé à l'API publique.

### `agents/linux/pra_checkpoint.py`

Gestion d'un **stockage de staging inscriptible** sur le site de secours.

- Première image qcow2 complète.
- Incréments dans des *overlays qcow2* dont la base est le dernier point validé.
- `pending.json` pour la génération en cours et `current.json` pour le point
  validé ; publication atomique du manifeste après vérification de tous les
  disques par `qemu-img check`.
- En cas d'échec, abandon possible du staging sans modifier le dernier
  checkpoint publié.
- Pas de démarrage de VM.

IMPORTANT : ce composant ne saurait utiliser un LVM raw ou une copie Pure
**en lecture seule** comme répertoire qcow2. Il exige une cible de fichiers
**inscriptible**, sur le datacenter de secours. Cela peut être un stockage
Proxmox de type `dir` sur une unité dédiée, **distincte de la réplique RO**,
ou un autre système de fichiers prévu pour la reprise. La VM cible ne doit
pas être attachée aux fichiers staging tant qu'ils ne sont pas validés.

## Flux bout en bout à intégrer avant activation

1. Prévol API Proxmox : nœuds, source allumée, secours arrêté, agent/root
   helper présent, permissions, disques, tailles, destination inscriptible.
2. Agent cible : création des images de staging et des exports NBD
   uniquement sur `127.0.0.1` avec identité d'exécution liée au job.
3. Tunnel chiffré et authentifié source → cible ; les ports NBD ne doivent
   jamais être exposés sur le réseau inter-sites.
4. Agent source : `QMP.backup` avec tous les disques du groupe et contrôle
   de complétion, sans interrompre la VM source.
5. Fermeture des exports NBD ; contrôle `qemu-img check` sur la destination.
6. **Accusé de réception durable** du point et publication du manifeste sur
   la cible ; reprise idempotente d'un état « indéterminé ». Toute incertitude
   bitmap / accusé de réception déclenche un full, jamais un delta risqué.
7. Mise à jour contrôlée des disques **de la VM de secours arrêtée** dans la
   configuration Proxmox, avec retour arrière si une étape échoue.
8. Sauvegarde de la configuration VM (CPU, EFI/TPM, disques, boot, réseau),
   contrôle anti-HA/onboot et isolement réseau pour les exercices PRA.
9. Historique par VM, progression, planification, reprise, fréquence, RPO
   calculé depuis le point vérifié, alertes et documentation FR/EN.
10. Tests de bout en bout sur deux clusters de **recette**, crashs réseau,
    absence de bitmap, espace plein, multi-disques et restaurabilité réelle.

**Règle fondamentale** : tant que les étapes 1–10 ne sont pas intégrées et
testées, le bouton public `/api/dr/vms/{id}/replicate` reste volontairement
bloqué. Un mode « moteur disponible » serait mensonger et dangereux.

## Limitations

- Sur les disques raw LVM partagés, un dirty bitmap QEMU non persistant est
  perdu au redémarrage du processus QEMU : la prochaine réplication doit
  refaire une copie initiale complète, sauf technologie de suivi durable
  indépendante démontrée.
- `blockdev-backup` fournit une cohérence de type crash du point QEMU,
  pas une garantie applicative pour une base de données : prévoir
  fsfreeze/VSS ou hooks applicatifs selon VM.
- Une chaîne qcow2 doit être contrôlée (longueur, espace libre, consolidation
  périodique) et conservée jusqu'à libération de ses dépendances.
- Le basculement PRA reste un flux distinct avec fencing et arbitrage ;
  aucune bascule automatique n'est autorisée ici.

## Sources

- QEMU : https://www.qemu.org/docs/master/interop/bitmaps.html
- QEMU : https://www.qemu.org/docs/master/interop/live-block-operations.html
- Proxmox : https://pve.proxmox.com/pve-docs/
