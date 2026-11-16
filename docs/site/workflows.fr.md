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
- **Une seule fois :** choisir une date/heure locale et un fuseau ; le workflow ne se réenfile pas après le déclenchement.
- **Répéter :** toutes les N minutes, heures, jours ou semaines (par exemple toutes les 5 minutes ou toutes les heures).
- **Cron avancé :** expression à 5 champs et fuseau IANA pour les fréquences personnalisées.
- **Webhook :** token propre au workflow créé par un administrateur ; les événements peuvent transmettre des variables JSON. Ne pas mettre le token dans une URL ou des logs publics.

**Non disponibles :** jonctions DAG parallèles, surveillance des fichiers et Event Logs, réception IMAP, approbation humaine native, ordonnanceur HA distribué.

## Dossiers, groupes et connexions

Le menu **Workflows** contient une arborescence : **Gérer** ouvre les dossiers pour créer des sous-dossiers, renommer ou supprimer un dossier, et sélectionner le dossier d'un workflow. Les workflows d'un dossier supprimé sont **déplacés vers son parent**, jamais effacés.

Dans **Nouveau workflow**, sélectionner **Déclenchement** : **Manuel**, **Une seule fois**, **Répéter à intervalle**, ou **Cron avancé**. Les horaires sont saisis dans le fuseau indiqué (par défaut Europe/Paris) et persistés en UTC. Une tâche déclenchée à intervalle ne rejoue pas automatiquement toutes les occurrences manquées pendant un arrêt du serveur. Le cron peut cibler des jours précis, contrairement aux intervalles.

Les ACL **Lecture / Exécution / Modification** ouvrent une fenêtre **Rechercher** dans les groupes existants de l'annuaire (y compris les groupes OpenLDAP `posixGroup` / `memberUid`). Le compte LDAP de lecture doit avoir accès à `LDAP_GROUP_BASE_DN`. Si la connexion LDAP est indisponible, seuls les groupes déjà connus sont proposés et un avertissement apparaît ; vérifier ces choix avant sauvegarde.

Les blocs **SSH**, **SFTP** et **Ansible** disposent d'un sélecteur de serveur enregistré dans **Administration → Serveurs & groupes SSH**. L'identité SSH du coffre est réutilisable sur plusieurs cibles. Les groupes organisent et filtrent la liste des serveurs ; ils ne déclenchent pas automatiquement une exécution sur tous leurs membres. Les paramètres de tâche `host` et `port` demeurent pour compatibilité avec les anciens workflows.

Les profils de connexion stockent désormais aussi les paramètres du protocole. Dans un bloc SMTP, le serveur, le port, la sécurité et l’authentification sont repris automatiquement du coffre ; les champs de tâche sont facultatifs lorsqu’un profil complet est choisi. Les tâches d’annuaire peuvent utiliser la configuration LDAP d’administration par défaut.

Pour les blocs utilisant une connexion, **Rechercher / gérer** ouvre le coffre depuis la tâche. Il est possible de **sélectionner**, **créer**, **modifier**, **cloner** ou **supprimer** une connexion. Les valeurs secrètes ne sont jamais réaffichées ; modifier une connexion en laissant un champ sensible vide préserve sa valeur. **Créer depuis la tâche** stocke également les secrets dans le coffre chiffré, puis référence leur identifiant dans le workflow — aucun mot de passe en clair dans `steps_json`. La suppression d'une connexion référencée par un workflow ou un datacenter est refusée.

## Reprises et idempotence

Jusqu'à cinq reprises par étape, avec timeout plafonné par `MAX_STEP_SECONDS`. Toute reprise peut répéter des effets : envoyer un message, relancer une VM, etc. Privilégier l'idempotence, des identifiants de transaction et la vérification de l'état cible. Après redémarrage non propre, les runs en cours sont marqués `interrupted` et ne sont pas relancés automatiquement.

## Accès

L'administrateur gère les workflows, le coffre et les datacenters. L'opérateur exécute/édite uniquement selon les ACL de groupe ; le lecteur consulte uniquement. L'opérateur ne peut pas attribuer un nouveau credential ni modifier les étapes Proxmox, mais un script éditable avec des credentials existants reste privilégié.
