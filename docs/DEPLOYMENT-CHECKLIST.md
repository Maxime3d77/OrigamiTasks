# Checklist d'installation / recette

## Avant installation

- [ ] Dépôt Git privé, protection de `main`, Git origin autorisé et accès de publication séparé.
- [ ] Serveur Linux à jour, Docker Engine + Compose installés.
- [ ] DNS et NTP fonctionnels, reverse proxy HTTPS, sauvegarde de VM disponible.
- [ ] VLAN/règles de pare-feu vers AD LDAP, SMTP, FTP(S), SSH et cibles métiers.
- [ ] Service account dédié pour les tâches distantes (Windows / Linux).

## Installation

- [ ] `ops/bootstrap.sh` exécuté ; mot de passe et clé Fernet sauvés hors serveur.
- [ ] Permissions UID10001 sur `data/` ; `docker compose up -d --build` sans erreur.
- [ ] `curl http://127.0.0.1:8080/api/health` retourne `status: ok`.
- [ ] HTTPS public valide ; `COOKIE_SECURE=true` ; login et logout fonctionnels.
- [ ] Fichier `data/known_hosts` peuplé avec empreintes vérifiées.
- [ ] Bind LDAP TLS vérifié, CA importée si besoin, groupes imbriqués testés.
- [ ] Rôles viewer/operator/admin testés avec de **vrais comptes**.

## Tests fonctionnels de bout en bout

- [ ] Webhook externe : token correct accepté, erroné rejeté, variables JSON injectées.
- [ ] Workflow à 3 étapes : réussite, erreur simulée et branche de remédiation.
- [ ] Politique de reprise activée, nombre de tentatives et logs vérifiés.
- [ ] Cron `*/5 * * * *` : observer l'exécution et l'absence de double lancement.
- [ ] Windows OpenSSH : tâche distante PowerShell `Write-Output` et code retour d'échec.
- [ ] Linux SSH : Bash distant `echo`, sortie et code retour.
- [ ] SFTP upload/download, hôte connu et compte de service ; test FTP TLS.
- [ ] SMTP STARTTLS envoi de mail à une boîte dédiée.
- [ ] HTTP 2xx/4xx, timeout et conditions liées au code de sortie.
- [ ] Coffre : ajout, absence de réexposition des secrets, suppression.
- [ ] Groupes LDAP : ne voir que les jobs autorisés ; interdire l'exécution d'un job hors ACL.
- [ ] Tests de panne : kill/restart du conteneur pendant une tâche => run marqué `interrupted` sans relance implicite.
- [ ] Test de backup/restauration SQLite et de la clé Fernet.
- [ ] Mise à jour Git vérifiée sur environnement PP avant PROD ; worker systemd et rollback.

## Limites v0.1

Les triggers externes de type filesystem watcher et email entrant, l'exécution parallèle de branches, les variables créées pendant un run, le SSO OIDC natif et le cluster HA ne sont pas disponibles. L'exécution locale est désactivée par défaut. La vérification d'intégration sur domaine Windows/AD réel n'a pas été exécutée lors de la génération du projet.
