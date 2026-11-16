# Sécurité et autorisations

OrigamiTasks est un **poste de pilotage d'automatisation**. Exécuter du code reste un privilège important même si le navigateur ne voit jamais le credential. Un éditeur de scripts privilégiés est privilégié sur les cibles correspondantes.

## Protections actuelles

- Hash Argon2, cookie `HttpOnly; Secure; SameSite=Strict`, contrôle CSRF et limitation des connexions.
- Rôles par groupe LDAP, ACL par workflow lecture/exécution/édition ; coffre et datacenters réservés aux admins.
- Coffre chiffré Fernet, TLS vérifié pour LDAP/Matrix/Proxmox, clés SSH vérifiées.
- Exécution locale désactivée par défaut ; worker Git séparé de l'application web.
- Chemins de transferts encadrés et taille des logs limitée. Masquage des valeurs de secrets connus (ne garantit pas l'absence totale de fuite).

## Limites connues

- Un éditeur peut exfiltrer indirectement un secret par une sortie ou un script sur la cible.
- Pas encore de droits par credential, isolation multi-tenant des workers, MFA/OIDC, agents à certificats ou audit HA.
- Les endpoints HTTP librement configurés peuvent joindre les réseaux accessibles depuis le conteneur : imposer un filtrage réseau sortant et des opérateurs de confiance.
- Les opérations d'alimentation des VM Proxmox sont coupées par défaut et ne remplacent pas un mécanisme de fencing PRA.
- La sauvegarde/restauration et le rollback doivent être testés avant la production.

Le gestionnaire de connexions permet la modification et le clonage côté serveur : aucune ancienne valeur secrète ne quitte le serveur. La suppression est refusée lorsqu'une tâche ou un datacenter référence le secret. La fenêtre de création depuis une tâche chiffre les secrets dans le coffre, sans les stocker en clair dans le workflow.

La configuration LDAP/AD enregistrée via l'administration est chiffrée en SQLite ; le mot de passe du bind n'est pas renvoyé au navigateur. Le test LDAP n'exécute que des opérations de lecture. Le test SMTP ne transmet aucun message et vérifie les certificats TLS ; l'authentification SMTP sans chiffrement est refusée. Les tests de connexion sont réservés aux administrateurs et doivent être limités aux hôtes approuvés par le pare-feu sortant.

La configuration LDAP/AD enregistrée via l’administration est chiffrée en SQLite. Le test LDAP réalise uniquement un bind et des recherches en lecture seule, sans retourner le mot de passe. Le test SMTP ne transmet aucun message, vérifie les certificats TLS et refuse l’authentification sans TLS. Les tests sont réservés aux administrateurs ; le pare-feu sortant doit limiter les hôtes joignables.

Les tests des connexions sont réservés aux administrateurs et audités. Aucun test ne lance de commande SSH, ne transfère de fichier, n’envoie d’e-mail ou ne modifie l’annuaire. Pour FTP en clair, aucun mot de passe n’est transmis. Les tests d’API génériques nécessitent une liste d’hôtes explicitement autorisés (`CONNECTION_TEST_ALLOWED_HOSTS`) et utilisent uniquement GET, sans suivre les redirections. Un succès HTTP ne garantit pas toujours la validité du jeton. Le filtrage réseau sortant reste indispensable.

## Mesures recommandées

VLAN d'administration dédié, listes d'autorisation réseau, aucune socket Docker montée, terminaison TLS, revue de code des workflows, identités SSH limitées, aucun compte root/admin pour les tâches courantes, branche Git protégée, mises à jour PROD auditées, rotation des secrets et audit externe immuable.

Lire le [modèle de sécurité détaillé](https://github.com/Maxime3d77/OrigamiTasks/blob/main/docs/SECURITY.md).

## Sécurité des agents et de l'annuaire

En préversion, les jetons agents sont uniques et révocables, mais restent de longue durée (pas de mTLS). Les jetons d'enrôlement sont à usage unique et expirent après 15 min. Le déploiement distant repose sur SSH avec empreintes vérifiées et liste de cibles. La tâche Windows tourne sous SYSTEM et l'agent Linux sous un compte non privilégié. Les scripts restent des exécutions de confiance.

Les écritures LDAP/AD sont désactivées par défaut, limitées par base DN, certificat TLS et confirmation explicite par bloc. Utiliser un compte de service à privilèges minimaux, un annuaire de test et conserver l'audit des contrôleurs de domaine.
