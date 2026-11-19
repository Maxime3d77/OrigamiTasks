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

