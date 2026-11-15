# Sécurité — OrigamiTasks v0.2

## Limites et modèle de confiance

OrigamiTasks est un ordonnanceur **privilégié** : un utilisateur ayant `edit` sur une tâche capable d'exécuter du code peut agir sur la cible de cette tâche. Même avec un coffre chiffré, un éditeur de scripts peut accéder à des secrets dans l'environnement du processus qu'il contrôle si cette machine contient ces secrets. En production, considérez l'éditeur de code comme un rôle de confiance élevé.

**Par défaut, les étapes `shell` et `powershell` locales sont désactivées** (`ALLOW_LOCAL_EXECUTION=false`) pour empêcher l'exécution de scripts dans le conteneur qui contient la clé du coffre, la base et les variables LDAP. Utilisez des connexions SSH vers des runners de service dédiés et isolés avec des comptes aux permissions minimales. Activer l'exécution locale signifie volontairement abandonner cette barrière logique. Cette version n'inclut pas de séparation de privilèges entre workers par utilisateur ni de confinement de scripts non fiables.

## Mesures implémentées

- Le coffre chiffre les payloads avec Fernet (`VAULT_KEY` distincte de la DB, jamais committée) ; seul l'admin peut créer/supprimer des credentials ; leurs valeurs ne sont pas renvoyées par l'API.
- Les sessions sont aléatoires et stockées uniquement en hash SHA-256 côté base ; cookie session HttpOnly, Secure en production, SameSite=Strict ; contrôles CSRF à chaque mutation.
- Les mots de passe locaux sont hashés avec Argon2 ; tentatives de connexion limitées (par processus).
- LDAP exclusivement LDAPS validé (CA système ou personnalisée) ou STARTTLS ; droits dérivés des groupes LDAP et ACL de jobs.
- Les clés hôtes SSH sont vérifiées via `known_hosts`, politique `RejectPolicy` ; pas d'acceptation aveugle.
- Le stockage des transferts est limité à `data/transfers` ; chemins canonisés.
- SMTP impose TLS (SSL ou STARTTLS), FTP non chiffré explicitement opt-in.
- Le conteneur est non-root, sans privilèges Linux, read-only, sans socket Docker ; le worker Git est séparé.
- API avec CSP, X-Frame-Options et no-store ; audit des connexions et mutations.
- Les logs tronqués et les valeurs de credentials connus sont remplacées par `[SECRET]` quand détectées ; ne pas transmettre d'autres secrets en clair dans stdout/stderr.

## Points à traiter avant production critique

1. **Isolation des tâches** : conteneurs workers séparés, compte spécifique par runner, politique de secrets avec autorisations par credential et exécution non privilégiée. La version actuelle ne garantit pas que deux opérateurs pourront être isolés lorsqu'ils modifient du code exécuté sur la même cible.
2. **Secrets** : support de Vault externe, rotation automatisée, révocation d'accès par workflow, chiffrement enveloppe et log redaction exhaustive (impossible à garantir dans les sorties arbitraires).
3. **Audit/observabilité** : journaux centralisés immuables, métriques, alertes, rétention configurée, rotation SQLite/sauvegardes chiffrées hors serveur.
4. **Connexion** : MFA/SSO OIDC ou Keycloak en amont, rate limit distribué et protection reverse proxy, en-têtes HSTS au reverse proxy, contrôle d'adresse IP.
5. **Réseau** : segmenter runners Windows/Linux, autoriser uniquement les ports nécessaires, revue de conformité des appels HTTP internes (SSRF volontaire via tâches privilégiées).
6. **Mises à jour** : révisions signées ou tags signés, approbation à deux personnes, image Docker signée, migration de schéma réversible, tests de rollback sur copie.
7. **HA** : base PostgreSQL, locks distribués et workers découplés ; le scheduler actuel impose exactement un processus API.
8. **Robustesse** : sortie SSH volumineuse, timeout FTP, erreurs systèmes et LDAP doivent être validés en intégration selon les versions des équipements.

## Gestion des permissions

- **viewer** : visibilité seulement si appartenance à un groupe autorisé sur le workflow.
- **operator** : run/edit uniquement si groupe explicitement autorisé ; ne peut pas modifier l'ACL.
- **admin** : accès total au coffre, workflows, audit, mises à jour.

Un mot de passe root Windows ou un SSH root ne doit jamais être utilisé pour des scripts ordinaires. Préférer un compte de service dédié avec le minimum de droits.

## Connecteurs v0.2 — garde-fous complémentaires

- `MATRIX_ALLOWED_HOSTS` et `PROXMOX_ALLOWED_HOSTS` imposent une liste d'hôtes réseau explicitement autorisés (HTTPS seulement) ; aucune redirection des tokens n'est suivie.
- Proxmox : les datacenters sont déclarés uniquement par un administrateur, les secrets sont résolus côté serveur, et les actions VM sont globalement désactivées sauf approbation explicite. Les opérateurs ne peuvent pas modifier une étape Proxmox.
- **Attention** : la confirmation `approved` reste une option de configuration du workflow, et **n'est pas une validation humaine par exécution**. La solution v0.2 n'est donc pas adaptée aux actions de secours ou de destruction sans garde-fou externe.
- Ansible : le playbook et l'inventaire sont confinés nominalement à des chemins prévus sur le runner SSH, les arguments sont échappés. Veiller aux liens symboliques, aux permissions des chemins et à la restriction du compte SSH (sinon un auteur de workflows peut toujours disposer de droits larges sur ce runner).
- Matrix : le token du bot n'est pas exposé dans la réponse, mais les contenus de messages et logs peuvent contenir des données métier. Vérifier la classification des rooms.
- Les appels distants gardent une surface SSRF si un administrateur configure des DNS hostiles pouvant changer de résolution. Réseau egress et DNS contrôlés restent nécessaires.
- Le projet doit rester en **recette** tant que les droits Proxmox, les certificats internes, la journalisation des secrets et le PRA n'ont pas été validés.
