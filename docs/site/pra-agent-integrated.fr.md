# PRA direct depuis l'interface — v0.3.30

## Déploiement sans scripts manuels

Le paquet Linux OrigamiTasks 0.3.30 embarque les trois composants PRA : copie initiale, incrémental à froid et remontée d'état. Le déploiement initial et le bouton **Mettre à jour l'agent** installent automatiquement ces fichiers dans /usr/local/lib/origamitasks/pra, répertoire accessible à root uniquement. L'agent sortant (service origamitasks-agent) reste non privilégié et ne peut pas modifier les exécutables PRA. L'agent garde ses ID, token et modules.

Aucune commande scp ni nouvelle variable .env n'est nécessaire. Ne pas placer une clé privée SSH durable sur les hyperviseurs.

## Configurer les VM

1. Dans la console, ajouter le cluster SD et le cluster AU, chacun avec son inventaire Proxmox et les nœuds associés à des serveurs SSH (association cluster ↔ nœud ↔ FQDN).
2. Associer les agents Linux du site SD au site PRA principal ; installer et mettre à jour l'agent du nœud qui héberge la VM source en version **0.3.30**. Activer le module PRA dans sa configuration.
3. Créer la VM protégée avec moteur **Copie directe + incrémental à froid**. Choisir le VMID source, un VMID libre pour AU, le nœud AU et le stockage de disques cible.
4. Dans les actions de la VM, cliquer **Copie initiale directe**, sélectionner une identité SSH du coffre pour joindre le nœud SD (root ou utilisateur avec sudo -n) et une pour joindre AU (root ou utilisateur autorisé par sudo -n), confirmer.
5. Lorsque la copie est terminée et vérifiée éteinte, on peut réaliser un nouveau point à froid par **Synchronisation incrémentale à froid**. **Arrêter la source ET le secours** et supprimer leur participation HA avant ce test. Ils doivent rester arrêtés tout le long.
6. Consulter **Journal des transferts**, le statut dans la fiche VM et **Suivi incrémental** pour les métriques détaillées des disques.

Les clés et leurs phrases secrètes restent chiffrées au coffre. OrigamiTasks ouvre deux connexions SSH contrôlées, puis transmet la clé temporairement déchiffrée au nœud source **uniquement dans un canal SSH chiffré**, dans un fichier anonyme en mémoire Linux (memfd). La clé est retirée à la fin du processus. Le flux de disques circule directement source → secours. Cela évite tout fichier de clé privée durable sur les nœuds ; **root sur le nœud source pourrait toutefois lire cette clé en mémoire pendant le transfert**. Utiliser des identités techniques dédiées et restreintes au PRA plutôt qu'une clé personnelle.

La première copie peut utiliser le mode snapshot QEMU sans arrêter la VM source ; elle crée un clone avec de nouvelles adresses MAC, éteint et sans démarrage automatique. Elle ne garantit pas à elle seule la cohérence transactionnelle d'applications complexes.

L'incrémental utilise une comparaison de périphériques bloc via rsync, qui nécessite les options --copy-devices et --write-devices sur les nœuds. Il compare le contenu complet des disques et transfère uniquement les différences. Les VM doivent être arrêtées. Les VM avec EFI, TPM, passthrough ou disques non bloc sont refusées dans le pilote.

## Statuts et sécurité

Les jobs sont enregistrés séparément par VM avec l'auteur, les dates, l'état, la durée et les erreurs. L'agent source, associé au site principal, remonte la progression par disque, les octets parcourus et les octets réellement envoyés. Une migration intra-cluster de la VM avant un job nécessite une nouvelle confirmation : le nœud est découvert via l'API Proxmox. En cas de redémarrage du contrôleur durant une copie, le statut passe à **incertain** et la copie n'est jamais relancée automatiquement.

Aucune action de démarrage, de bascule, de failback ou de suppression de disque n'est exécutée automatiquement. Le contrôleur n'implémente pas encore de fencing total entre les deux clusters, ni de réplication continue des VM allumées. **Pilote de recette uniquement**, sur des VM non critiques, car un démarrage manuel externe de la source ou du secours pendant une synchronisation peut invalider les données. Vérifier le résultat réel sur Proxmox avant de considérer une copie exploitable.

## Mettre à jour OrigamiTasks

~~~bash
cd /opt/OrigamiTasks
git status --short
git fetch origin
git switch -c recette-0330 --track origin/feat/pra-agent-bundled-0330
docker compose up -d --build --force-recreate
docker compose exec origamitasks python -c "from app.config import APP_VERSION; print(APP_VERSION)"
docker compose ps
docker compose logs --tail=100 origamitasks
~~~

Si la branche recette-0330 existe déjà, utiliser git switch recette-0330 puis git pull --ff-only. Mettre ensuite à jour les agents Linux depuis la console avec leur identité SSH du coffre. **Ne pas recopier manuellement de scripts sur SD ou AU.**
