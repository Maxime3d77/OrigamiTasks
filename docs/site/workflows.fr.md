# Workflows et planification

## Modèle

Un **workflow** contient des **étapes ordonnées** avec ID unique, type, configuration JSON, condition facultative, politique de reprise et destinations `on_success`/`on_failure`. `END` termine avec succès ; `FAIL` termine en erreur. Limites : 80 étapes et 200 transitions par exécution. Une copie des étapes est sauvegardée lors de l'enfilement ; les modifications ultérieures n'affectent pas ce run.

## Exemple : embranchement après sauvegarde

1. **Sauvegarder** (`ssh` sur un runner Linux dédié), `on_success: notify_ok`, `on_failure: notify_fail`.
2. **notify_ok** (`matrix` dans une room exploitation), avec `on_success: END`.
3. **notify_fail** (`email` à l'astreinte), avec `on_success: FAIL` afin de conserver le statut d'échec du workflow.

L'éditeur permet de créer les blocs et de relier les branches. Variables disponibles : `{{ run.id }}`, `{{ var.ticket }}`, `{{ steps.backup.output }}` (ID d'étape exact). Ne jamais mettre les secrets dans les variables.

## Déclencheurs

- **Manuel :** un opérateur autorisé clique sur Exécuter.
- **Cron :** expression cron et fuseau gérés par l'ordonnanceur.
- **Webhook :** token propre au workflow créé par un administrateur ; les événements peuvent transmettre des variables JSON. Ne pas mettre le token dans une URL ou des logs publics.

**Non disponibles :** jonctions DAG parallèles, surveillance des fichiers et Event Logs, réception IMAP, approbation humaine native, ordonnanceur HA distribué.

## Reprises et idempotence

Jusqu'à cinq reprises par étape, avec timeout plafonné par `MAX_STEP_SECONDS`. Toute reprise peut répéter des effets : envoyer un message, relancer une VM, etc. Privilégier l'idempotence, des identifiants de transaction et la vérification de l'état cible. Après redémarrage non propre, les runs en cours sont marqués `interrupted` et ne sont pas relancés automatiquement.

## Accès

L'administrateur gère les workflows, le coffre et les datacenters. L'opérateur exécute/édite uniquement selon les ACL de groupe ; le lecteur consulte uniquement. L'opérateur ne peut pas attribuer un nouveau credential ni modifier les étapes Proxmox, mais un script éditable avec des credentials existants reste privilégié.
