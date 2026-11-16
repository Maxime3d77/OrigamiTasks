# Référence de configuration

Le fichier `.env.example` constitue la liste de référence des paramètres. Redémarrer le conteneur après changement. **Ne jamais committer `.env`.**

| Variable | Rôle | Recommandation |
|---|---|---|
| `ADMIN_PASSWORD` | Mot de passe administrateur initial | Aléatoire long, rotation après installation |
| `VAULT_KEY` | Clé Fernet du coffre | Sauvegarde hors ligne ; ne pas changer sans rechiffrement |
| `COOKIE_SECURE` | Cookies réservés à HTTPS | `true` |
| `ALLOW_LOCAL_EXECUTION` | Shell/PowerShell dans le conteneur central | `false` |
| `ORIGAMI_EXECUTOR_WORKERS` | Nombre d'exécutions concurrentes | `2` au départ, une seule instance API |
| `MAX_STEP_SECONDS` | Limite d'une étape | `1800` |
| `MATRIX_ALLOWED_HOSTS` | Hôtes Matrix HTTPS autorisés | ex. `matrix.example.org` |
| `PROXMOX_ALLOWED_HOSTS` | API Proxmox autorisées | ex. `pve.example.org:8006` |
| `PROXMOX_CA_CERT` | CA PEM de confiance montée dans le conteneur | Si CA interne |
| `ALLOW_PROXMOX_ACTIONS` | Active les actions VM encadrées | `false` avant revue |
| `GIT_ALLOWED_ORIGIN` | Origine Git autorisée au service de mise à jour | Dépôt protégé uniquement |

## LDAP / Active Directory

Utiliser LDAPS (`ldaps://dc.example.org:636`) ou LDAP+STARTTLS ; éviter LDAP clair sur un réseau non fiable. Le compte de liaison doit être en lecture seule. Paramètres : `LDAP_URI`, `LDAP_BASE_DN`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_USER_ATTRIBUTE`, `LDAP_GROUP_BASE_DN`, groupes et `LDAP_CA_CERT` facultatif. Pour OpenLDAP, utiliser généralement `LDAP_USER_ATTRIBUTE=uid` (et non `sAMAccountName`) ; les groupes `posixGroup/memberUid` et `groupOfNames/member` sont recherchés sous `LDAP_GROUP_BASE_DN`. La fenêtre de sélection des ACL nécessite la lecture de ces groupes. Pour les groupes AD imbriqués, activer `LDAP_NESTED_GROUPS=true` et tester les comptes réels. Voir [Sécurité](security.md).

### Administration graphique LDAP / Active Directory

L'administrateur dispose du menu **Administration → LDAP / Active Directory**. Les valeurs initiales proviennent de `.env`, mais une configuration enregistrée dans l'interface prend priorité sans modifier ce fichier. Le formulaire permet de définir l'URI et le port, LDAPS ou STARTTLS, les DN de liaison/utilisateurs/groupes, `uid` (OpenLDAP) ou `sAMAccountName` (AD), les groupes d'accès et le chemin d'une CA de confiance (accessible dans le conteneur sous `/etc/ssl` ou `/app/data`).

**Tester la connexion** vérifie TLS/certificat, bind du compte de service, accès à la base DN, recherche des groupes et, facultativement, présence d'un compte par login. Test en lecture seule : aucun utilisateur n'est modifié et aucun mot de passe n'est affiché. La configuration est chiffrée dans SQLite avec `VAULT_KEY`. Conserver sa sauvegarde et limiter les droits de lecture. Le compte `admin` local reste indépendant du LDAP. Pour les tâches d'annuaire, les paramètres centraux sont les valeurs par défaut ; un compte de service séparé dans le coffre reste recommandé en cas d'écritures.

## Identifiants

Créer dans **Coffre-fort** un **profil par protocole** : SMTP (serveur, port, STARTTLS / SSL direct / aucun chiffrement, authentification activée ou non, login, mot de passe, expéditeur), SSH/SFTP (hôte, port, utilisateur, mot de passe ou clé), FTP/FTPS, LDAP, API, Matrix et Proxmox. Les champs changent selon le protocole. Le bouton **Tester SMTP** établit une connexion TLS et s'authentifie si nécessaire, **sans envoyer de courriel**. Pour l'annuaire lui-même, utiliser **Administration → LDAP → Tester la connexion**. Réserver l'édition des tâches privilégiées aux opérateurs de confiance. Les paramètres non sensibles sont consultables ; les mots de passe, jetons et clés privées ne sont jamais réaffichés. Modifier un champ secret en le laissant vide préserve l'ancien. Les anciennes connexions sans serveur configuré restent compatibles avec les tâches qui précisent l'hôte.

## SSH distant

Une entrée `known_hosts` vérifiée est obligatoire. Contrôler l'empreinte **par un canal indépendant** ; ne pas accepter automatiquement la première clé en production. Windows exige OpenSSH Server et un compte de service capable d'utiliser PowerShell ; Linux exige un compte SSH de service.

## Règles réseau

Limiter les sorties aux destinations explicitement autorisées : Matrix HTTPS, Proxmox HTTPS, SMTP submission, SSH/SFTP, FTPS. Restreindre les droits des tokens et l'accès au proxy.

## Agents et annuaire

| Variable | Rôle |
|---|---|
| `AGENT_SERVER_URL` | Origine HTTPS utilisée par les agents |
| `AGENT_DEPLOY_ALLOWED_HOSTS` | FQDN/IP de déploiement SSH autorisés |
| `ALLOW_AGENT_REMOTE_DEPLOY` | Déploiement SSH distant (false par défaut) |
| `ALLOW_AGENT_SCRIPTS` | Scripts agent (false) |
| `ALLOW_AGENT_MUTATIONS` | Actions d'écriture sur services (false) |
| `ALLOW_DIRECTORY_WRITES` | Écritures LDAP/AD (false) |

Pour la procédure complète et les précautions, consulter [Agents](agents.md) et [Tâches](tasks.md).
