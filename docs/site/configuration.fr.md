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
| `PROXMOX_ALLOWED_HOSTS` | API Proxmox autorisées et tests de connexion | ex. `pve.example.org:8006` |
| `CONNECTION_TEST_ALLOWED_HOSTS` | Hôtes HTTPS autorisés pour les tests de jetons API génériques | Vide par défaut ; ajouter seulement des hôtes approuvés |
| `PROXMOX_CA_CERT` | CA PEM de confiance montée dans le conteneur | Si CA interne |
| `ALLOW_PROXMOX_ACTIONS` | Active les actions VM encadrées | `false` avant revue |
| `GIT_ALLOWED_ORIGIN` | Origine Git autorisée au service de mise à jour | Dépôt protégé uniquement |

## LDAP / Active Directory

Utiliser LDAPS (`ldaps://dc.example.org:636`) ou LDAP+STARTTLS ; éviter LDAP clair sur un réseau non fiable. Le compte de liaison doit être en lecture seule. Paramètres : `LDAP_URI`, `LDAP_BASE_DN`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_USER_ATTRIBUTE`, `LDAP_GROUP_BASE_DN`, groupes et `LDAP_CA_CERT` facultatif. Pour OpenLDAP, utiliser généralement `LDAP_USER_ATTRIBUTE=uid` (et non `sAMAccountName`) ; les groupes `posixGroup/memberUid` et `groupOfNames/member` sont recherchés sous `LDAP_GROUP_BASE_DN`. La fenêtre de sélection des ACL nécessite la lecture de ces groupes. Pour les groupes AD imbriqués, activer `LDAP_NESTED_GROUPS=true` et tester les comptes réels. Voir [Sécurité](security.md).

### Administration graphique LDAP / Active Directory

L'administrateur dispose du menu **Administration → LDAP / Active Directory**. Les valeurs initiales proviennent de `.env`, mais une configuration enregistrée dans l'interface prend priorité sans modifier ce fichier. Le formulaire permet de définir l'URI et le port, LDAPS ou STARTTLS, les DN de liaison/utilisateurs/groupes, `uid` (OpenLDAP) ou `sAMAccountName` (AD), les groupes d'accès et le chemin d'une CA de confiance (accessible dans le conteneur sous `/etc/ssl` ou `/app/data`).

**Tester la connexion** vérifie TLS/certificat, bind du compte de service, accès à la base DN, recherche des groupes et, facultativement, présence d'un compte par login. Test en lecture seule : aucun utilisateur n'est modifié et aucun mot de passe n'est affiché. La configuration est chiffrée dans SQLite avec `VAULT_KEY`. Conserver sa sauvegarde et limiter les droits de lecture. Le compte `admin` local reste indépendant du LDAP. Pour les tâches d'annuaire, les paramètres centraux sont les valeurs par défaut ; un compte de service séparé dans le coffre reste recommandé en cas d'écritures.

### Administration LDAP / Active Directory

Le menu **Administration → LDAP / Active Directory** permet de configurer l’URI et le port, LDAPS ou STARTTLS, le DN du compte de liaison et son mot de passe, les bases DN, l’attribut utilisateur (`uid` sous OpenLDAP), les groupes d’accès et le chemin de la CA. Les valeurs initiales proviennent de `.env`. Une configuration enregistrée dans l’interface prend le dessus sans toucher à ce fichier. Les paramètres et le mot de passe de liaison sont **chiffrés dans SQLite** à l’aide de `VAULT_KEY` ; aucun secret n’est réaffiché.

**Tester la connexion LDAP** réalise un bind et des recherches en lecture seule sur la base, les groupes et, facultativement, un identifiant utilisateur. La CA doit être accessible dans le conteneur sous `/etc/ssl` ou `/app/data`. Le compte `admin` local demeure indépendant de l’annuaire.

## Identifiants

Créer dans **Coffre-fort** un **profil par protocole** : SMTP (serveur, port, STARTTLS / SSL direct / aucun chiffrement, authentification activée ou non, login, mot de passe, expéditeur), SSH/SFTP (hôte, port, utilisateur, mot de passe ou clé), FTP/FTPS, LDAP, API, Matrix et Proxmox. Les champs changent selon le protocole. Le bouton **Tester SMTP** établit une connexion TLS et s'authentifie si nécessaire, **sans envoyer de courriel**. Pour l'annuaire lui-même, utiliser **Administration → LDAP → Tester la connexion**. Réserver l'édition des tâches privilégiées aux opérateurs de confiance. Les paramètres non sensibles sont consultables ; les mots de passe, jetons et clés privées ne sont jamais réaffichés. Modifier un champ secret en le laissant vide préserve l'ancien. Les anciennes connexions sans serveur configuré restent compatibles avec les tâches qui précisent l'hôte.


### Diagnostic SSH : clé privée utilisateur et clé d'hôte

La connexion SSH du coffre contient **la clé privée du compte utilisateur** (OpenSSH, RSA ou EC en PEM), pas la clé d'identité du serveur. La clé publique correspondante doit être dans \`authorized_keys\` côté serveur. Une clé \`/etc/ssh/ssh_host_*_key\` ne doit jamais être importée comme identité du compte.

Le fichier de confiance des serveurs est \`data/known_hosts\` sur l'hôte OrigamiTasks, monté dans le conteneur sous \`/app/data/known_hosts\`. Pour \`serveur.fr\` (exemple fictif), afficher l'empreinte avec \`ssh-keyscan -T 5 -p 22 -t ed25519 serveur.fr 2>/dev/null | ssh-keygen -lf - -E sha256\`. **Comparer cette empreinte à celle obtenue directement sur la console du serveur** avec \`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256\` avant d'ajouter la clé d'hôte. Le diagnostic différencie clé inconnue (SHA-256 affichée, jamais acceptée automatiquement), clé remplacée, authentification refusée et protocole incompatible.


### Test SSH ponctuel sans inventaire

Dans **Coffre-fort → Identité SSH → Tester**, choisir **Saisie manuelle**, indiquer le FQDN du serveur (par exemple `serveur.fr`) et son port SSH, 22 par défaut. La clé ou le mot de passe du coffre sont testés sur ce serveur, sans inscrire le FQDN dans l’inventaire ni l’enregistrer dans l’identité. Les modes **Inventaire SSH** et **Ancien hôte** restent disponibles. Les clés d’hôte SSH doivent toujours être vérifiées dans `SSH_KNOWN_HOSTS` pour empêcher les attaques de type usurpation de serveur.

### Import de certificats LDAP CRT, DER ou PEM

Dans **Administration → LDAP**, le bouton d’import accepte une CA publique `.crt`, `.cer`, `.der` ou `.pem`. Un fichier `.crt` peut contenir un certificat texte **PEM** ou binaire **DER** : les deux sont reconnus et convertis en fichier PEM de confiance. Les clés privées et les certificats serveur non-CA sont rejetés. **Enregistrer** la configuration LDAP après import, puis cliquer sur **Tester LDAP**. La vérification LDAPS/STARTTLS reste obligatoire ; il n’y a pas d’option globale « ignorer le certificat ».

### Identités SSH et inventaire des serveurs

Les connexions **SSH** du coffre sont des **identités réutilisables**, sans serveur associé : nom de profil, login, mot de passe **ou** clé privée OpenSSH, et phrase secrète facultative pour cette clé. Le mot de passe et la clé restent chiffrés dans le coffre et ne sont jamais réaffichés. Il est préférable d'utiliser une clé individuelle de service avec droits minimaux.

Le menu **Administration → Serveurs & groupes SSH** permet de :

- Créer un **groupe de serveurs** et saisir une liste de FQDN, un par ligne (par exemple `au-app01.infra.example.org` ou `sd-app01.infra.example.org:2222` si le port SSH diffère de 22).
- Enregistrer des serveurs individuels avec leur FQDN, port et libellé ; rattacher une machine à **plusieurs groupes**.
- Filtrer et retrouver les serveurs par groupe ou recherche de FQDN. Supprimer un groupe **ne supprime pas** les serveurs associés.
- Depuis **Coffre-fort → Identité SSH → Tester**, choisir **un serveur de l'inventaire**, puis tester le compte et la clé privée sur cet hôte précis. Les vérifications optionnelles SFTP et `whoami` restent disponibles.

Les tâches **SSH**, **SFTP** et le runner **Ansible** proposent un sélecteur de **serveur cible enregistré** : elles utilisent le compte SSH du coffre, mais le FQDN et le port du serveur sélectionné. Un groupe sert à **organiser et filtrer les cibles**, pas à exécuter implicitement une tâche sur tous les serveurs. Les anciennes connexions SSH contenant encore `host`/`port` continuent de fonctionner ; la sélection d'un serveur de l'inventaire les remplace pour cette tâche ou ce test.

La vérification de la **clé d'hôte SSH** reste stricte (`known_hosts`) afin de prévenir les connexions à un serveur inconnu ou usurpé. Vérifier l'empreinte par un canal fiable avant de l'ajouter au fichier `SSH_KNOWN_HOSTS`. Aucune commande SSH n'est lancée lors du simple test d'authentification.

### Assistant de diagnostic des connexions

Dans **Coffre-fort → Tester** ou directement depuis le sélecteur d'un workflow, chaque profil ouvre un assistant propre à son protocole :

- **SMTP :** connexion/TLS/authentification. Activer **Envoyer un e-mail de test réel**, saisir **un destinataire**, un objet et un message, puis cliquer sur **Envoyer le message de test**. OrigamiTasks effectue un véritable envoi via le profil enregistré, puis affiche le **Message-ID** pour rechercher le message. Le succès indique que le serveur SMTP a **accepté** le message ; il ne confirme **pas** la réception par la boîte. Vérifier boîte de réception, spams et journaux du système mail. Un envoi est limité à un destinataire et une tentative réussie toutes les 30 secondes par connexion.
- **SSH / SFTP :** vérification de l'empreinte connue et de l'authentification. En option, ouverture du sous-système SFTP et lecture des métadonnées du répertoire, ou exécution explicite de la commande fixe `whoami` (Linux et Windows). Aucun transfert ni commande arbitraire.
- **FTPS :** connexion, TLS et authentification ; lecture optionnelle du répertoire courant via `PWD`. Jamais d'upload ni de suppression. **FTP sans TLS :** connectivité uniquement, sans transmettre le mot de passe.
- **LDAP / AD :** connexion chiffrée, bind, recherche ciblée optionnelle d'un login ou groupe sous les bases DN du profil. Filtres échappés, lecture seule.
- **Matrix :** validation du token avec `whoami`, vérification optionnelle de l'appartenance du bot à une room, ou **envoi explicite** d'un message de test dans cette room (limité à un envoi toutes les 30 secondes par connexion). Vérifier ensuite son apparition dans la room. Aucune prise en charge E2EE.
- **Proxmox :** permissions API, inventaire optionnel des nœuds et statut d'un nœud nommé via GET ; aucune opération sur les VM.
- **API HTTPS :** méthode de lecture `GET` ou `HEAD`, chemin de diagnostic, statut HTTP attendu (`2xx` ou un code précis). Réservé à `CONNECTION_TEST_ALLOWED_HOSTS` ; aucune redirection suivie. Un HTTP 2xx ne démontre pas forcément la validité du token.
- **Identifiant générique :** nécessite un protocole et une destination pour pouvoir être testé.

Par défaut, les diagnostics **n'envoient aucun message** et ne modifient aucune donnée distante. Les envois SMTP/Matrix exigent un choix explicite dans l'assistant. Les tests sont réservés aux administrateurs, journalisés sans secrets et limités à des délais courts. Pour des destinations hors des listes autorisées, configurer le `.env` et filtrer les sorties réseau.

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

## Certificat CA LDAP et vérification TLS

Dans **Administration → LDAP / Active Directory**, importer la **CA publique** qui a signé le certificat du serveur LDAPS depuis un fichier `.pem` ou `.crt` (128 Kio maximum). Les certificats PEM sont validés et conservés dans `/app/data/ldap-cas` (volume persistant avec permissions restreintes). Le chemin est renseigné dans le formulaire ; **enregistrer la configuration**, puis **tester la connexion**. Le SAN du certificat serveur doit correspondre au FQDN utilisé.

La vérification du certificat reste obligatoire pour **LDAPS:636** ou **STARTTLS:389**. Il n'y a pas d'option « ignorer le certificat », car elle rendrait possible l'interception des identifiants du compte de liaison. **Ne jamais importer de clé privée.**

## Statut de mise à jour

Le menu **Mises à jour** présente la version installée, la version du dernier commit de la branche `GIT_BRANCH` sur l'origine autorisée par `GIT_ALLOWED_ORIGIN`, la date du **commit** distant, le nombre de commits en retard et la date de vérification. Le service hôte `ops/update-worker.py` vérifie au démarrage puis toutes les heures environ. Le contrôle peut aussi être demandé manuellement.

Configurer `GIT_ALLOWED_ORIGIN` avec l'URL exacte obtenue via `git remote get-url origin` et `GIT_BRANCH` avec la branche réellement utilisée. Quand le worker est absent, que l'origine n'est pas autorisée ou que la branche diverge, l'écran indique **Vérification indisponible**, jamais un faux « À jour ». Une installation reste une action administrateur manuelle ; la date affichée est celle du dernier commit, pas nécessairement d'une release.
