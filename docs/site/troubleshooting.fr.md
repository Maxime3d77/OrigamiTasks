# Dépannage

## Le service ne démarre pas

Consulter `docker compose logs app` ; l'absence ou l'invalidité de `VAULT_KEY` empêche volontairement le démarrage. Exécuter bootstrap, vérifier les permissions UID 10001, l'espace disque et l'image. Ne jamais recréer une clé Fernet sur une base contenant déjà des credentials.

## Échec d'authentification navigateur

Contrôler HTTPS/reverse proxy, `COOKIE_SECURE`, cookies, NTP, mot de passe local, URI LDAP, chaîne CA, compte de liaison et appartenance aux groupes. Plusieurs échecs peuvent activer une limitation temporaire ; regarder l'audit.

## Erreur SSH/SFTP

Vérifier accès réseau/port, compte de service, fingerprint, format de clé privée et absence de mot de passe dans le JSON. Windows doit fournir OpenSSH Server et `powershell.exe` pour le compte utilisé.

## Erreur Matrix

Contrôler `MATRIX_ALLOWED_HOSTS`, HTTPS, token du bot, appartenance à la room, identifiant complet, permissions d'envoi et éventuel E2EE (non supporté actuellement).

## Erreur de découverte Proxmox

Vérifier le FQDN/port autorisé, syntaxe token (`user@realm!tokenid`), permissions API, chaîne CA et `/api2/json/cluster/resources`. Un token de lecture seule ne peut pas agir sur l'alimentation des VM.

## Aucun lancement cron

Vérifier activation du workflow, expression cron, horloge du serveur, unicité de l'instance scheduler, prochaine date et logs du moteur. Ne pas répliquer l'API SQLite sur plusieurs instances.

## Mise à jour bloquée en attente

Le worker côté hôte peut être absent ou ne pas joindre l'origine Git autorisée. Examiner systemd/journal, branche, permissions et propreté du checkout. Ne pas donner au worker root des origines Git arbitraires.
