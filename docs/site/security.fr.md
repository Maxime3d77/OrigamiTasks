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

## Mesures recommandées

VLAN d'administration dédié, listes d'autorisation réseau, aucune socket Docker montée, terminaison TLS, revue de code des workflows, identités SSH limitées, aucun compte root/admin pour les tâches courantes, branche Git protégée, mises à jour PROD auditées, rotation des secrets et audit externe immuable.

Lire le [modèle de sécurité détaillé](https://github.com/Maxime3d77/OrigamiTasks/blob/main/docs/SECURITY.md).

## Sécurité des agents et de l'annuaire

En préversion, les jetons agents sont uniques et révocables, mais restent de longue durée (pas de mTLS). Les jetons d'enrôlement sont à usage unique et expirent après 15 min. Le déploiement distant repose sur SSH avec empreintes vérifiées et liste de cibles. La tâche Windows tourne sous SYSTEM et l'agent Linux sous un compte non privilégié. Les scripts restent des exécutions de confiance.

Les écritures LDAP/AD sont désactivées par défaut, limitées par base DN, certificat TLS et confirmation explicite par bloc. Utiliser un compte de service à privilèges minimaux, un annuaire de test et conserver l'audit des contrôleurs de domaine.
