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

## Connexion administrateur : redirection contenant un mot de passe

Si le navigateur affiche `/?username=...&password=...` ou si les logs montrent un `GET /` avec `password=`, **arrêter les tentatives immédiatement**. Cela signifie que l'événement JavaScript du formulaire n'a pas été intercepté : le serveur n'a pas reçu de demande `POST /api/login`, quel que soit le mot de passe stocké en base. Le défaut a été corrigé en **v0.3.1-rc2** :

- Le formulaire HTML utilise `method="post"` ; le mot de passe ne peut plus être envoyé dans une URL lors d'une soumission native.
- Un fichier `/assets/login.js` autonome gère la connexion POST en JSON même si le reste de l'application échoue à s'initialiser.
- Les URLs JavaScript/CSS sont versionnées pour forcer le navigateur à récupérer les nouvelles ressources.
- Les listeners `submit` du cœur de l'application sont enregistrés avant l'initialisation.

Pour confirmer : `curl -fsS http://127.0.0.1:8091/api/health` doit afficher `0.3.1-rc2`. Dans l'onglet Réseau du navigateur, la connexion doit être un **POST /api/login** et non un GET avec mot de passe. Faire `Ctrl+Maj+R` après mise à jour.

**Incident de confidentialité** : un mot de passe déjà présent dans une URL est potentiellement conservé dans l'historique du navigateur, les logs d'accès du serveur et du reverse proxy, et leurs sauvegardes. Faire tourner les identifiants concernés, limiter l'accès aux journaux et traiter leur conservation selon la politique d'incident. Ne pas publier les URLs ou logs bruts contenant des secrets.

Un changement de `ADMIN_PASSWORD` dans `.env` ne modifie pas un hash existant en SQLite ; consulter la procédure de récupération du compte local si un véritable `POST /api/login` échoue.
