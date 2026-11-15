# Référence de l'API HTTP

L'API web est sous `/api`. Les sessions interactives utilisent `origami_session` et `origami_csrf` ; toute mutation doit envoyer `X-CSRF-Token` égal au cookie CSRF. Les accès anonymes sont limités à health, login et aux webhooks possédant leur token.

| Méthode | Endpoint | Fonction | Rôle |
|---|---|---|---|
| GET | `/api/health` | Santé/version | Public |
| POST | `/api/login` | Ouverture de session | Public |
| POST | `/api/logout` | Fin de session | Connecté |
| GET | `/api/me` | Utilisateur courant | Connecté |
| GET | `/api/catalog` | Catalogue des blocs disponibles | Connecté |
| GET | `/api/jobs` | Workflows filtrés par ACL | Connecté |
| POST | `/api/jobs` | Créer un workflow | Admin |
| PUT | `/api/jobs/{id}` | Modifier un workflow | Opérateur/admin autorisé |
| POST | `/api/jobs/{id}/run` | Enfiler une exécution | Opérateur/admin autorisé |
| GET | `/api/runs` | Historique d'exécution | Selon les droits |
| GET | `/api/credentials` | Métadonnées du coffre seulement | Admin |
| POST | `/api/credentials` | Stocker un secret chiffré | Admin |
| GET/POST | `/api/datacenters` | Lister/déclarer Proxmox | Admin |
| GET | `/api/datacenters/{id}/discover` | Inventaire du cluster | Admin |
| GET | `/api/datacenters/{id}/vms/{vmid}/dr-assessment` | Évaluation PRA en lecture seule | Admin |
| POST | `/api/updates/request` | Demander contrôle/installation Git | Admin |

Les erreurs HTTP sont JSON avec `detail`. Conserver le lien vers le ticket de changement si nécessaire. L'API n'est pas encore considérée comme contractuellement stable ; les garanties de compatibilité commenceront à la v1.0.

## Authentification

```bash
# Exemple de recette sur environnement protégé ; éviter les secrets en historique shell.
# curl -c cookies.txt -H 'Content-Type: application/json' \
#   -d '{"username":"admin","password":"..."}' https://tasks.example.org/api/login
```

Préférer un gestionnaire de secrets ou une saisie sécurisée.
