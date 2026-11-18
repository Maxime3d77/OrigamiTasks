# PRA à chaud : copies différentielles directes — v0.3.32 (recette)

Le moteur hot_differential protège une VM SD QEMU qui reste **allumée en permanence**. La VM de secours AU reste arrêtée. Aucun PBS n'est requis, aucun routage iSCSI entre baies, et les identités SSH proviennent du coffre OrigamiTasks. Le composant est distribué automatiquement par la mise à jour de l'agent Linux 0.3.32 ; aucun scp ni nouveau paramètre .env.

## Fonctionnement réel

1. Une copie initiale VMA/QEMU snapshot doit déjà avoir créé une VM de secours AU arrêtée.
2. L'administrateur choisit Différentiel à chaud et le chemin d'une **partition de travail dédiée** sur le nœud SD (ex. /mnt/pra-scratch). Le répertoire doit être monté ailleurs que sur / et avoir au moins 110 % de la **taille virtuelle totale** des disques de la VM. Les images temporaires contiennent des données en clair et sont supprimées en fin d'exécution.
3. Le moteur vérifie l'hôte source courant et que la VM SD est **running**, et que l'ancienne VM AU est **stopped** et hors HA. Aucune commande d'arrêt de la source n'est envoyée.
4. Le nœud SD exécute un vzdump snapshot en sortie standard et extrait le point de sauvegarde cohérent dans un répertoire de travail privé via vma extract. Cela relit et écrit la totalité des données à sauvegarder, mais n'arrête pas le guest.
5. AU réalise un **full clone** du dernier standby cohérent dans un **nouveau VMID**, éteint. L'ancienne VM AU reste intacte et sert de point de reprise précédent.
6. Les images extraites SD sont comparées par blocs aux disques du **nouveau clone uniquement** via rsync --checksum --no-whole-file --write-devices : seuls les blocs différents traversent le réseau SSH. Aucun disque source actif n'est lu directement avec rsync.
7. Une empreinte SHA-256 complète de chaque disque SD extrait est comparée à celle du nouveau disque AU. La génération est marquée success seulement après toutes les vérifications et le contrôle que la VM AU demeure arrêtée.
8. Chaque tentative conserve son état et ses octets lus/transférés sur la fiche VM. Un transfert interrompu ou incertain n'est **jamais repris automatiquement**, n'est pas considéré comme prêt et demande une inspection AU.

## Limitations importantes

**Ce n'est pas du CBT QEMU.** Le moteur effectue à chaque génération une sauvegarde/extraction complète sur SD, une **copie intégrale locale du dernier standby sur AU**, puis un transfert réseau différentiel. Il peut donc consommer beaucoup d'I/O et de capacité stockage : espace de scratch SD >= taille virtuelle VM + 10 %, ainsi qu'au moins une copie VM complète supplémentaire sur AU pour chaque génération non purgée. Il n'existe actuellement **aucune politique de rétention/purge automatique**, ni planificateur de réplication récurrente validé, afin de ne pas détruire une génération par inadvertance. Ce mode est manuel en recette.

Les VM avec disques EFI, TPM, passthrough ou volumes non bloc ne sont pas encore prises en charge par le moteur de transfert. Le moteur copie les données des disques, mais ne met pas à jour automatiquement toutes les modifications de configuration matérielle de la VM ; à vérifier avant une reprise.

Le snapshot de sauvegarde QEMU donne un point de reprise cohérent au niveau des disques, généralement crash-consistent ; la cohérence transactionnelle des bases nécessite un agent invité/gel applicatif approprié. Le moteur n'applique aucune politique d'interdiction globale de démarrer une VM par un administrateur Proxmox tiers. Le **fencing**, la bascule automatique, le failback, le transfert QEMU CBT natif et la planification restent à implémenter et tester. Ne pas utiliser sur une VM de production critique tant qu'une recette complète n'a pas validé le moteur avec les versions de Proxmox et rsync présentes sur les deux sites.

## Procédure de recette sans copie de scripts

Mettre à jour OrigamiTasks, puis l'agent Linux depuis l'administration web en 0.3.32. Dans VM protégées PRA : sélectionner la VM source, effectuer la copie initiale, puis **Différentiel à chaud**. Choisir les deux identités SSH du coffre et le chemin de scratch SD. Confirmer. Ensuite consulter **Historique des générations** ; une génération en succès est un VMID AU éteint distinct. Aucun démarrage automatique.

La suppression d'une ancienne VM sur AU doit rester une opération de maintenance manuelle, après vérification qu'une copie plus récente existe et qu'elle est valide.
