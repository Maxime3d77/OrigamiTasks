PRA à chaud — v0.3.31

Exigence absolue : VM principale SD toujours ALLUMÉE pendant la copie initiale et toutes les futures synchronisations. Seule la VM AU reste arrêtée. Aucune connexion des réseaux iSCSI intersites, aucun PBS requis.

Implémenté : copie initiale à chaud par sauvegarde snapshot QEMU streamée directement par SSH vers AU ; bouton Tester la réplication à chaud depuis VM protégées PRA avec identité SSH du coffre. Prétest en lecture seule sur QMP : vérifie le statut allumé du guest SD, les disques, les dirty bitmaps et les commandes QMP, notamment backup-access-setup et backup-access-teardown lorsque présentes. Vérification API que le clone AU reste arrêté. Aucun guest n'est arrêté ou suspendu par le prétest.

Sécurité : l'ancien moteur offline_incremental est désormais rejeté par l'API et le worker. Son exécutable embarqué est remplacé par un refus permanent ; les historiques restent consultables. La mise à jour agent 0.3.31 fournit le prétest QMP automatiquement sans scp ni nouvelle variable .env.

ATTENTION : l'incrémental à chaud n'est PAS encore opérationnel. Il manque un export fiable des blocs QEMU modifiés, leur application atomique sur une génération AU cohérente, les points de restauration, la rebaseline après migration/invalidations, un fencing intercluster et une vérification des données. L'API fournisseur externe Proxmox est une voie à intégrer. Les bitmaps raw/LVM sont non persistants comme ceux qcow2. La présence de commandes QMP ne prouve ni synchronisation ni aptitude à une bascule.

Test sans effet : VM protégées PRA → Tester la réplication à chaud → identité SSH SD. Ce test ne modifie ni les disques, ni les bitmaps, ni l'état de la source.
