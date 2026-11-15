# Agents Windows et Linux — cahier d'architecture

**Statut : spécification, non intégré en v0.2.** Le runner SSH exécute déjà des scripts Linux/Windows sans agent. Un agent natif devient utile pour remonter les événements OS et quand les ports de gestion entrants doivent rester fermés.

## Capacités cibles

| Linux | Windows |
|---|---|
| Bash, Python, `systemctl`, `journalctl`, gestionnaires de paquets | PowerShell 5.1/7, services Windows, Event Logs |
| inotify, événements de fichiers, processus et disques | FileSystemWatcher, tâches planifiées, WMI/CIM |
| SSH/SFTP, élévation contrôlée | SMB/UNC, Credential Manager Windows |
| Compte applicatif et compte de service | Session service, interaction utilisateur via broker séparé |

## Identité et cycle de vie

1. L'administrateur émet un **token d'enrôlement à usage unique** avec expiration et groupe de ressources.
2. L'agent s'authentifie en sortie en **TLS/mTLS** et reçoit une identité propre courte durée ; jamais de clé maîtresse du coffre.
3. Il annonce ses capacités et son inventaire OS contrôlés.
4. Le serveur vérifie l'autorisation de chaque job selon groupe d'agent, type d'action, credential et approbation.
5. L'agent récupère les travaux via polling/flux, réserve des leases persistants, exécute avec timeout sans interpolation shell implicite et renvoie sortie, code retour et identifiants d'audit.
6. Rotation/révocation de certificats et tokens. Mises à jour des binaires signées, avec rollback.

## Sécurité

- Compte de service non administrateur par défaut ; élévation ciblée par politique locale.
- Authentification obligatoire des ordres, aucun endpoint public non authentifié sur les machines.
- Prévenir les doublons par lease et clé d'idempotence ; ne pas répéter aveuglément un reboot ou une opération VM.
- Masquer les secrets à la source et plafonner les logs. Les gros artefacts restent en stockage externe.
- Versionner séparément les agents et tester les distributions Linux/versions Windows réellement supportées.

## Planning

La v0.4 est la première version prévue avec enrôlement, polling authentifié et actions natives. Pour l'instant, **ne pas installer d'agent fictif** et ne pas considérer cette page comme procédure de déploiement d'un binaire existant.
