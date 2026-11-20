# Réplication QMP incrémentale à chaud — pilote de recette

!!! warning "Non validé pour la production"
    Le transport et l'orchestration sont codés, mais n'ont **pas été testés
    sur les clusters Proxmox AU/SD réels**. Laisser la PR en brouillon tant
    qu'une VM de recette n'a pas réussi deux copies et une restauration isolée.
    **Bascule et retour PRA restent désactivés.**

## Fonctionnement

- La VM source QEMU reste allumée pendant le transfert.
- Première copie complète par QMP `blockdev-backup`, puis blocs modifiés grâce
  aux dirty bitmaps.
- Le contrôleur transporte les blocs NBD via deux connexions SSH chiffrées
  issues du coffre OrigamiTasks. Aucune clé privée SSH n'est distribuée entre
  les deux datacenters ; les services NBD restent en écoute locale.
- Les blocs sont écrits dans un stockage Proxmox **DIR indépendant et
  inscriptible**, sous forme de checkpoints QCOW2 copy-on-write.
  **Aucune utilisation d'ActiveDR ni d'une LUN Pure en lecture seule.**
- Après validation de la copie, le disque est rattaché à une VM `REP_`
  **éteinte**. Une VM créée par le pilote n'a volontairement aucune interface
  réseau afin de prévenir les collisions.
- Une fois l'amorçage initial réussi, les copies suivantes sont programmées
  selon l'intervalle de protection. Après redémarrage du QEMU source, perte de
  bitmap, échec ou huit couches, une copie complète est imposée.
- Les erreurs restent visibles dans l'historique ; les états incertains
  nécessitent une **réconciliation explicite**.

## Prérequis

1. Deux clusters Proxmox distincts, associés aux sites principal et secours
   d'un même groupe PRA dans OrigamiTasks.
2. Agents Linux installés **depuis l'interface OrigamiTasks** sur les nœuds
   source et destination, rattachés à leurs sites. L'enregistrement du
   déploiement doit conserver l'identifiant SSH dans le coffre.
3. Empreintes SSH approuvées et identité permettant l'exécution de l'auxiliaire
   root (connexion root ou `sudo -n` autorisé).
4. Sur les hyperviseurs : `python3`, `qm`, `pvesm`, `qemu-img`,
   `qemu-nbd` et socket QMP local. Les services SSH doivent permettre les
   redirections de ports en boucle locale.
5. Sur le secours : un stockage Proxmox **dir** avec contenu `images`,
   inscriptible et suffisamment d'espace. Une LUN répliquée en lecture seule
   ou un VG/LVM classique partagé ne convient pas à ce pilote QCOW2.
6. Compatibilité actuelle : disques IDE/SATA/SCSI/VirtIO ordinaires. EFI/OVMF,
   TPM, Cloud-init, cohérence applicative et réseaux PRA spécifiques nécessitent
   encore un profil dédié.
7. **Aucune nouvelle variable `.env`** à ajouter.

## Utilisation

1. Configurer les sites, les clusters et les agents du groupe PRA.
2. Installer ou mettre à jour les agents Linux (v0.3.24+) via l'interface :
   le bundle comprend automatiquement les modules PRA.
3. Ajouter la VM protégée, choisir `QEMU à chaud : complet puis incrémental`,
   le stockage cible **DIR** et un VMID cible inutilisé.
4. Lancer **Prévol QMP**. Ce contrôle lit l'inventaire et les correspondances
   d'agents sans copier les disques.
5. Lancer **Synchroniser à chaud** et confirmer l'opération réelle. Surveiller
   l'historique. La source reste allumée, la VM `REP_` éteinte.
6. Vérifier `completed`, les volumes et la configuration dans Proxmox ;
   observer ensuite au moins un cycle `incremental`.
7. En cas d'état `uncertain`, ne pas relancer aveuglément : contrôler les
   jobs QMP/NBD, puis utiliser **Réconcilier**. La prochaine copie sera complète.
8. Tester un démarrage uniquement à la main, sur un réseau isolé et après
   contrôle indépendant du fencing.

## Architecture et sécurité

Modules installés avec l'agent, dans `/usr/local/lib/origamitasks-pra`
sous propriété root :

- `pra_host_worker.py` : commandes bornées QMP et stockage, aucun démarrage de VM.
- `pra_qmp_incremental.py` : transaction QMP multi-disques, dirty bitmaps,
  transfert à chaud et contrôle des erreurs.
- `pra_checkpoint.py` : points COW, manifeste atomique et nettoyage des seuls
  disques appartenant au moteur.

Serveur : `app/pra_live.py`. API :

- `GET /api/dr/vms/{id}/replication-preflight`
- `POST /api/dr/vms/{id}/replicate` avec `{"approved":true}`
- `GET /api/dr/vms/{id}/replications`
- `POST /api/dr/vms/{id}/replications/{run_id}/reconcile` avec `{"approved":true}`

**Une synchronisation réussie ne donne pas le droit de basculer.** Il manque
encore un témoin indépendant, le fencing, les protections contre le
split-brain, la gestion des réseaux, l'EFI/TPM et la validation réelle de la
restaurabilité et de la cohérence applicative.

## Limites

Les bitmaps QEMU des disques LVM/raw sont volatils : toute perte ou redémarrage
du processus QEMU exige un nouveau transfert complet. La cohérence fournie
est de type arrêt brutal, non une garantie applicative pour les bases de
données. Le RPO affiché correspond au **dernier point réellement validé**,
pas à une promesse de temps réel.

Documentation de référence : [QEMU dirty bitmap / sauvegarde incrémentale](https://www.qemu.org/docs/master/interop/bitmaps.html).

## Découverte des capacités LVM (pilote)

La console découvre les stockages via l'API Proxmox, sans supposer le
constructeur de la baie, le nom du VG ou le type de transport. Pour les
stockages `lvm`, `snapshot-as-volume-chain=1` est affiché comme une capacité
de **snapshots QCOW2 en chaîne en préversion**, et non comme un moteur de
réplication PRA opérationnel. L'option ne rend pas automatiquement éligibles
les anciens disques `raw` : chaque disque doit être inspecté individuellement.

La réception du transport QMP en v0.3.24 demeure limitée au stockage Proxmox
`dir` inscriptible. Le futur adaptateur LVM nécessitera allocation Proxmox,
gestion de volumes indépendants par VM, publication atomique et recette
source→destination sur clusters différents. Aucun changement de la politique
de bascule, du fencing ou des agents en production n'est impliqué par cette
simple découverte de capacités.

Documentation officielle : [Proxmox LVM](https://github.com/proxmox/pve-docs/blob/master/pve-storage-lvm.adoc).

## Adaptateur LVM générique : étape de développement (v0.3.25-rc1)

Un premier composant `agents/linux/pra_lvm_target.py` prépare l'utilisation
de volumes LVM gérés **par Proxmox**, indépendamment d'une marque de SAN ou du
nom du VG. Il détecte en lecture seule `snapshot-as-volume-chain`, refuse les
stockages désactivés et calcule la capacité minimale **épaisse** requise par
génération, même pour une copie incrémentale. Il inclut une procédure
d'allocation de staging nécessitant une approbation explicite et journalise
les commandes avant et après leur exécution. Un résultat incertain ne libère
**jamais** de volumes automatiquement.

Cette procédure de staging n'est **pas** accessible depuis le moteur de
réplication, ni depuis le bouton « Synchroniser à chaud ». Il manque un
manifeste de checkpoint durable visible entre les nœuds, la validation du
chaînage QCOW2 sur périphérique bloc, la publication atomique des disques,
le contrôle de restauration et la sécurisation du démarrage. Tant que ces
prérequis ne sont pas validés sur une plateforme de recette, l'adaptateur
LVM reste volontairement **désactivé pour les synchronisations réelles**.

Dans **VM protégées PRA**, le bouton **Compatibilité stockage** fournit un
rapport en lecture seule sur le stockage cible, le nœud, l'espace disponible
et les disques source. Il n'alloue pas de volume et ne déclenche ni
synchronisation ni bascule. Aucun ajout de paramètre `.env` n'est requis.

## Sécurisation des checkpoints QMP (v0.3.26-rc1)

- Après une synchronisation QMP vers un stockage `dir`, l'assistant de
  destination contrôle de nouveau que `REP_` est arrêtée, que les disques
  configurés correspondent au checkpoint, que chaque image appartient bien
  à la VM, que les chaînes QCOW2 restent dans son dossier et que `qemu-img
  check` réussit. La synchronisation est marquée `completed` **uniquement**
  après ce contrôle. Une erreur laisse le transfert `uncertain` : la
  récupération humaine est obligatoire avant toute nouvelle synchronisation.
- **Aucun ancien checkpoint n'est supprimé automatiquement.** Une suppression
  fondée sur `qm config --current` ne prouve pas l'absence de références dans
  les snapshots Proxmox, les sauvegardes ou `unusedN`. Prévoir de la place :
  un mécanisme de rétention ne pourra être activé qu'après une vérification
  explicite de toutes les dépendances.
- Les anciennes réplications terminées avant cette version sont
  `unverified` après migration **sans suppression de leur historique**.
  Elles ne peuvent ni déclencher une synchronisation planifiée ni servir de
  base à un incrémental. La première nouvelle synchronisation doit refaire
  une copie complète, après la mise à jour des assistants des deux nœuds.
- Le protocole des assistants PRA est désormais vérifié **avant** toute
  allocation : les deux assistants doivent annoncer le protocole de sûreté
  version 2. Un vieux module sur une machine Proxmox est refusé avant transfert.
- L'UEFI/OVMF, le TPM, les disques inutilisés et le passthrough PCI sont
  explicitement refusés tant qu'un profil de récupération éprouvé n'existe
  pas. Aucune conversion du disque ni arrêt de la source n'est effectué.
- Ce contrôle est **structurel et hors ligne**. Il ne prouve ni démarrage
  de l'OS, ni consistance applicative, ni fencing, ni droit de promotion.
  Les tests de bascule et de failback restent des simulations.

Le moteur de destination LVM n'est toujours pas raccordé au transfert QMP
et le stockage `lvm` ne doit pas être présenté comme un récepteur opérationnel.

## Capacité et propriété du stockage LVM (v0.3.27-rc1)

Le diagnostic **Compatibilité stockage** expose désormais la taille virtuelle
de chaque disque lorsque Proxmox la fournit sous la forme `size=` dans sa
configuration. Pour une cible LVM classique avec chaînes QCOW2, il estime
l'espace nécessaire à une génération : **somme des tailles virtuelles
+ réserve d'au moins 5 % (minimum 512 Mio)**. Une chaîne QCOW2 sur LVM utilise
des volumes logiques **épais**, y compris lorsqu'un incrément ne contient que
quelques blocs modifiés. L'absence d'une taille de disque est représentée
comme **inconnue**, jamais comme zéro.

L'adaptateur de préparation LVM vérifie directement le statut et l'espace
libre du stockage sur le nœud Proxmox cible, ainsi que les VMID du **cluster
entier**. Il refuse un VMID utilisé par un autre nœud/CT, une destination
active, dotée du démarrage automatique ou appartenant à une autre protection.
Le cas d'une première copie dont la VM cible n'existe pas est pris en compte.

**Limite :** ces vérifications n'activent ni le transfert QMP vers LVM ni
la bascule. Elles n'ont été évaluées qu'avec des tests automatisés simulés,
et doivent être confirmées en recette sur les versions Proxmox voulues.

## Matrice de qualification PRA avant mise en production

Aucun test de CI simulé ne remplace les essais suivants sur **deux clusters
Proxmox de recette et un témoin indépendant** :

1. Copie initiale d'une VM en activité, avec vérification du disque et de
   l'intégrité des données restaurées, sans arrêt de la source.
2. Deux synchronisations successives avec modifications de fichiers et
   vérification que seuls les blocs modifiés ont été transmis.
3. Source migrée ou redémarrée : perte de bitmap détectée, incrémental
   refusé, nouvelle copie complète imposée.
4. Interruption du réseau, arrêt brutal de l'agent, cible hors ligne,
   stockage saturé : aucun checkpoint invalide publié.
5. Stockages `dir`, NFS, LVM `raw`/QCOW2, LVM-thin, ZFS et RBD :
   adapter chaque cible selon ses capacités réelles, sans écrire dans un
   volume appartenant à une autre VM.
6. Démarrage d'une **copie isolée** avec validation des disques, du système
   et des services applicatifs. Tester séparément BIOS, UEFI et TPM.
7. Perte du site principal et partition réseau : témoin indépendant,
   preuve de fencing et interdiction des deux VM simultanées.
8. Promotion contrôlée, mappage réseau, résolution DNS, puis failback
   avec synchronisation inverse et absence d'écritures concurrentes.
9. Perte de la console OrigamiTasks : maintien des protections par les
   agents autonomes, sans promotion non autorisée.
10. Mesure réelle du RPO/RTO, de la bande passante, de la réserve de
    stockage et des délais de réconciliation.

Tant que les étapes **6 à 9** ne sont pas implémentées et validées, les
commandes de bascule et de retour restent des simulations interdites
d'exécution sur la production.

## Rapport de préparation à la bascule (v0.3.28-rc1)

Le nouvel écran **Préparation bascule** dans **VM protégées PRA** exploite
les résultats des contrôles précédents pour expliquer, VM par VM, ce qui
est vérifié et ce qui empêche une activation de la copie de secours.

L'API administrateur en lecture seule
`GET /api/dr/vms/{id}/recovery-readiness` indique la dernière copie
structurellement vérifiée, le type d'adaptateur, le statut de la destination,
les preuves manquantes et les blocages de récupération.

Ce diagnostic reste volontairement **fail-closed** : même avec une copie QCOW2
vérifiée et un stockage disponible, la bascule n'est jamais autorisée
sans restauration isolée éprouvée, témoin externe, fencing effectivement
prouvé, protection contre le double démarrage, tests applicatifs et
reconfiguration réseau contrôlée. Aucun booléen saisi par un administrateur
dans OrigamiTasks ne vaut preuve de fencing ou de témoin indépendant.

Cette version ne déclenche aucune opération sur un hyperviseur, ne change
aucun paramètre `.env` et ne nécessite pas une mise à jour des agents
Proxmox pour afficher ce rapport. Les moteurs de bascule réelle et failback
restent à développer et à qualifier sur une infrastructure de recette.

## Vérification QMP réelle automatisée — v0.3.29-rc1

Le workflow GitHub Actions `CI / Real QEMU QMP/NBD full + incremental
smoke test` installe de vrais exécutables QEMU et effectue les tests suivants
sur des fichiers **temporaires jetables**, et non sur les VM de production :

- Démarrage d'un processus QEMU avec deux disques QCOW2 distincts.
- Copie initiale via QMP `blockdev-backup` et deux serveurs NBD locaux.
- Commit réel des deux disques dans `CheckpointStore`, manifeste atomique.
- Écritures sur les disques de la source QEMU active, puis copie incrémentale
  avec dirty bitmap vers deux overlays QCOW2.
- Contrôle `qemu-img check` et lecture des motifs de données, vérification
  du chaînage et de l'immuabilité du checkpoint initial.
- Redémarrage de la source QEMU, perte des bitmaps volatils et refus d'un
  incrémental sans nouvel amorçage.
- Vérification que le mécanisme de rétention refuse toujours une suppression
  automatique non prouvée.

Les essais réels ont permis de corriger une **incompatibilité QMP** : la
transaction de copie initiale combine `block-dirty-bitmap-add` et
`blockdev-backup`, pour laquelle `completion-mode=grouped` n'est pas
autorisé par QEMU. Les copies incrémentales composées exclusivement de
jobs de sauvegarde conservent ce mode groupé.

L'assistant cible impose maintenant un arrêt **prouvé** de chaque processus
`qemu-nbd` avant de publier, d'abandonner ou de supprimer un checkpoint.
Un PID absent, un processus qui pointe vers un autre disque ou un arrêt
incertain préservent les images et obligent une réconciliation. Une erreur
d'initialisation NBD n'efface plus automatiquement la génération en cours.

### Limites des essais

Cette campagne teste une **copie technique QEMU/NBD réelle**, mais sans
hyperviseur Proxmox, sans deux nœuds SSH distincts, sans démarrage d'OS,
sans baie de stockage, sans adaptateur LVM actif et sans témoin/fencing.
Elle ne vaut donc **pas** qualification d'un PRA multi-clusters. Les essais
terrain sur deux clusters de recette restent obligatoires pour compléter
la matrice de qualification PRA.

## Version d'intégration PRA 0.3.29 — protocole de recette complet

Cette livraison est destinée à la **recette Proxmox** avant toute promotion
production, et rassemble le moteur QMP, la copie initiale et incrémentale,
les checkpoints atomiques DIR, les premières fonctions de destination LVM et
les tests automatisés sur de vrais QEMU/NBD. La copie source reste allumée.
La destination de secours demeure arrêtée.

### Ce qui est automatisé et vérifié en CI

- Deux disques de la source QEMU sauvegardés en pleine activité vers deux
  serveurs NBD locaux via le moteur QMP réel. Checkpoints DIR atomiques.
- Écritures de données puis réplication incrémentale sur deux overlays QCOW2 ;
  contrôles des motifs de données, des chaînes et de la génération initiale.
- Arrêt/redémarrage de QEMU, perte des bitmaps volatils et obligation d'un
  nouvel amorçage complet.
- Compatibilité QCOW2 sur véritables périphériques blocs temporaires
  (/dev/loop), sans utiliser de LUN ou de baie réelle.
- Protection contre la suppression d'un checkpoint tandis qu'un export NBD
  est encore actif, et garde anti-perte de référence de processus.
- Contrats de préparation LVM avec allocations via Proxmox, journalisation
  durable sur le nœud, vérification de chaînes et refus des volumes étrangers.

### Protocole manuel sur deux clusters Proxmox de test

**Préconditions impératives :** VM source sans données importantes, allumée
et sans EFI/TPM/PCI passthrough ; VM cible préexistante sur un **autre
cluster**, VMID distinct, arrêtée, `onboot=0`, **aucune carte réseau**
et description contenant les deux marqueurs
`ORIGAMITASKS_PRA_<id de protection>` et
`ORIGAMITASKS_LAB_ONLY`. La destination doit utiliser un stockage de
type `lvm` avec `snapshot-as-volume-chain=1` et disposer de la capacité
nécessaire pour des LV épaisses. Les deux nœuds doivent avoir un agent
OrigamiTasks Linux mis à jour et un accès SSH approuvé par le contrôleur.
Ne pas utiliser les pools PROD.

Depuis la console OrigamiTasks, une fois la protection QMP définie dans
l'interface avec le stockage LVM cible et les agents installés sur les
nœuds de **recette uniquement** :

```bash
docker exec origamitasks python -m app.pra_lvm_lab --vm-id 17
docker exec origamitasks python -m app.pra_lvm_lab --vm-id 17 --execute --lab-confirm DISPOSABLE-LAB-ONLY
docker exec origamitasks python -m app.pra_lvm_lab --vm-id 17 --incremental --execute --lab-confirm DISPOSABLE-LAB-ONLY
```

Remplacer `17` par l'identifiant **de la protection PRA** de la VM de
recette, et non par son VMID Proxmox. Le premier appel est **en lecture
seule** ; les appels avec `--execute` allouent réellement des LV et
transfèrent des données. Les checkpoints incertains sont conservés pour
réconciliation et empêchent les incréments suivants.

L'outil de diagnostic sur le nœud de test propose aussi :

```bash
python3 /usr/local/lib/origamitasks-pra/pra_lvm_lab_cli.py \
  --storage LAB-LVM --vmid 3301 --protection-id 17 inspect
```

**Attention :** le module `pra_lvm_restore_lab.py` prépare un clone
indépendant et arrêté à partir du dernier point LVM validé ; son interface
Python n'est pas encore une fonctionnalité exposée dans l'interface web,
et ne démarre jamais automatiquement la copie. Aucun retour d'état
`boot_verified`, cohérence applicative, témoin indépendant ou fencing n'est
attesté par les tests de copie.

### Limites bloquantes avant production

La journalisation LVM est encore **locale au nœud cible**, donc ne permet
pas de reprendre la propriété de la VM après perte du nœud. Les démarrages
isolés, profils BIOS modernes/UEFI/TPM, quorum de témoin indépendant,
fencing vérifié, droit de promotion et failback restent non qualifiés et
ne sont pas activés. **Une PR CI verte ne prouve pas un PRA multi-site.**

## Chantier PRA v1 intégré — branche de recette (non publiée)

Le développement des fonctions PRA restantes se poursuit dans la branche
`feat/pra-v1-integrated-receiver-lab-20261120` afin de ne pas livrer de
morceaux de moteur incompatibles les uns avec les autres.

### Première qualification exploitable sans arrêter de VM

Sur un **nœud Proxmox de recette** ayant accès au code de la branche :

```bash
python3 scripts/pra_lab_preflight.py --storage ID_DU_STOCKAGE --vmid VMID_DE_TEST
```

Ce script exécute exclusivement des consultations `pvesh get` et
`qm config`, sans toucher aux disques, VM ou clés privées. Il affiche les
versions d'outils présentes, les informations de stockage, les options BIOS
et les limitations de la pile actuelle, sans prétendre autoriser une
bascule réelle.

Pour vérifier des **chaînes QCOW2 avec les vrais outils QEMU** (uniquement
dans des répertoires temporaires de test) :

```bash
python3 -m pytest -q tests/test_pra_real_qemu_checkpoints.py
```

Nécessite `pytest`, `qemu-img` et `qemu-io`. Le test est ignoré si les
outils QEMU ne sont pas présents ; ne pas interpréter un `skip` comme un
résultat de validation. Ne jamais exécuter ces tests sur des images de VM
de production.

### Noyau bascule/failback

`app/pra_recovery_transaction.py` formalise un flux transactionnel
nécessitant : approbation externe, preuve de copie avec démarrage et
application validés, préparation par un témoin indépendant, fencing
effectivement vérifié de l'ancien maître, attribution d'une nouvelle
autorité avec epoch, installation d'une garde de démarrage exclusive sur
l'hyperviseur, puis démarrage sous cette garde.

Il n'est **pas relié à la production** : les adaptateurs de témoin, de
fencing et de démarrage doivent encore être implémentés avec des preuves
réelles, authentifiées et résistantes aux partitions réseau. La suite
automatisée vérifie l'ordre et les cas d'échec, mais ne constitue pas une
preuve de sécurité intersites.

**Ne pas fusionner cette branche tant que les chemins LVM QMP, EFI/TPM,
témoin/fencing et validation sur deux clusters de recette ne sont pas prêts.**

## v0.3.30 — Copie LVM réelle depuis la console (recette supervisée)

La page **VM protégées PRA** propose maintenant l'action **Copie LVM recette**.
Ce n'est plus seulement un diagnostic : la copie **FULL** ou
**INCREMENTAL** déclenche véritablement le moteur QMP/NBD par les agents
Proxmox déjà associés au groupe PRA. La VM source reste **allumée** et la
VM de destination **arrêtée** ; chaque génération LVM utilise de nouveaux LV
QCOW2. Les résultats et les échecs sont conservés dans
`pra_lvm_lab_runs`, séparément des réplications DIR.

### Première utilisation sur deux clusters de recette uniquement

1. Déclarer les clusters et les sites PRA et installer les agents Linux PRA
   **0.3.30** sur les deux nœuds de **recette** avec des accès SSH approuvés.
2. Depuis **VM protégées PRA**, créer la protection avec le moteur
   `qmp_live_incremental`, sélectionner la VM source **allumée**, le nœud
   cible et un stockage `lvm` compatible `snapshot-as-volume-chain=1`.
   Sélectionner un VMID cible **encore libre**.
3. Après création de la protection, relever son **identifiant de protection**
   (pas le VMID source). Sur le **nœud Proxmox cible de recette**, créer une
   VM vide, **sans disque et sans carte réseau**, avec le VMID cible :

   ```bash
   # EXEMPLE LAB SEULEMENT : adapter l'identifiant et le nom
   qm create 3301 --name REP_TEST_3301 --onboot 0 \
     --description 'ORIGAMITASKS_PRA_17 ORIGAMITASKS_LAB_ONLY' \
     --memory 2048 --cores 2
   qm status 3301
   qm config 3301
   ```

   Remplacer `17` par **l'identifiant de la protection OrigamiTasks**,
   et `3301` par le VMID réservé. Le VMID doit être libre sur **tout le
   cluster**. La VM doit rester `stopped`, sans entrée `net0` ou autre
   interface réseau, avec `onboot: 0`.
4. Dans **VM protégées PRA → Copie LVM recette**, exécuter le **prévol**.
   S'il réussit, saisir `DISPOSABLE-LAB-ONLY`, puis **Copie complète à chaud**.
5. Actualiser l'historique des runs jusqu'à `completed`. Cette réussite
   atteste uniquement des images et de leurs chaînes QCOW2 **hors ligne**.
   Après des écritures sur la VM source, choisir **Synchronisation incrémentale**.
   Si le bitmap QMP est perdu ou incohérent, refaire une copie complète.
6. Un run `uncertain` empêche toute relance automatique et nécessite une
   vérification manuelle des jobs QMP, des exports NBD et des LV attribués
   au checkpoint ; aucune suppression automatique n'est effectuée.

**Important :** ces opérations **allouent et remplissent de vrais volumes
LVM**. Chaque incrément occupe potentiellement l'espace virtuel complet
de chaque disque (LV épaisses). Utiliser un VG de recette jetable,
avec une réserve suffisante. N'utiliser ni la baie/les pools de production
ni une VM contenant des données importantes.

Le récepteur LVM de cette étape **n'attache pas automatiquement les disques**
à `REP_`, ne démarre aucune copie et n'autorise **ni bascule ni failback**.
Un clone indépendant préparé via le module `pra_lvm_restore_lab.py` reste
une opération de test distincte, à valider avant tout démarrage isolé.
Pas de nouvelle variable `.env` ; la migration de base SQLite est additive.

### Préparer une restauration réelle et isolée (sans démarrage automatique)

Après une copie LVM `completed` depuis la console, le même panneau
**Copie LVM recette** propose **Préparer un clone isolé (arrêté)**.
Choisir un **nouveau VMID libre sur tout le cluster de destination**, puis
confirmer `DISPOSABLE-LAB-ONLY`.

OrigamiTasks vérifie que le dernier checkpoint LVM est le dernier point
structurellement vérifié et que la VM `REP_` est toujours arrêtée.
L'assistant **copie et aplatit les images dans de nouveaux LV**, puis crée
une VM indépendante `PRA_TEST_<VMID>` avec `onboot=0` et sans carte réseau.
Le test est enregistré dans l'historique comme `restore`.

La VM `PRA_TEST_` est **réellement créée mais reste arrêtée**. Son
`boot_verified` reste faux. Après un contrôle de l'isolement réseau et des
disques sur l'hyperviseur de recette, l'administrateur pourra effectuer
**un test de démarrage manuel**, mesurer le temps de récupération et
contrôler les fichiers/services. Aucun test de démarrage ni de cohérence
applicative n'est automatique à cette étape. L'ancienne VM source n'est
jamais arrêtée, et la VM `REP_` n'est jamais promue.

**Échec ou perte de liaison** : le job passe en `uncertain`, aucun
nouvel essai ni nettoyage n'est lancé sans réconciliation manuelle.

