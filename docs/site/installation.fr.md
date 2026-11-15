# Installation

## Prérequis

- Serveur Linux (Debian 12/13 ou Ubuntu LTS conseillé) avec Docker Engine et Docker Compose v2.
- DNS, NTP, reverse proxy HTTPS et segmentation réseau.
- Espace disque pour la base SQLite, les journaux et les transferts, sauvegardé séparément.
- Un compte de service aux droits minimaux sur chaque cible d'exécution.

## Déploiement initial

```bash
sudo apt update
# Installer Docker Engine et le plugin Docker Compose depuis un dépôt de confiance.
git clone https://github.com/Maxime3d77/OrigamiTasks.git
cd OrigamiTasks
sudo bash ops/bootstrap.sh
sudo docker compose up -d --build
curl -fsS http://127.0.0.1:8080/api/health
```

Le script `bootstrap.sh` crée `.env`, un mot de passe admin aléatoire et une clé Fernet `VAULT_KEY`. Conserver ces secrets dans un **coffre de mots de passe hors serveur**, jamais dans Git. Vérifier `.env` avant lancement. Compose n'expose HTTP que sur la boucle locale.

## Exemple de reverse proxy Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name tasks.example.org;
    ssl_certificate     /etc/letsencrypt/live/tasks.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tasks.example.org/privkey.pem;
    add_header Strict-Transport-Security "max-age=31536000" always;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }
}
```

Adapter le FQDN et les certificats. Ne pas exposer directement le port 8080. Garder `COOKIE_SECURE=true` en production.

## Première connexion

1. Ouvrir `https://tasks.example.org`.
2. Se connecter avec `admin` et le mot de passe généré ; le changer avec `ops/change-admin-password.py`.
3. Vérifier **Coffre-fort**, **Workflows** et **Journal d'audit**.
4. Déclarer les hôtes autorisés avant d'ajouter les identifiants distants.
5. Suivre la [checklist de recette](https://github.com/Maxime3d77/OrigamiTasks/blob/main/docs/DEPLOYMENT-CHECKLIST.md).

Pour les mises à jour, suivre [Exploitation](operations.md), et ne pas exécuter `git pull` aveuglément sur un répertoire modifié.

## Premier test des agents

Après avoir préparé le HTTPS et la liste des hôtes SSH, suivre [Agents](agents.md) pour déployer sur une VM Linux puis une VM Windows de recette. Garder `ALLOW_AGENT_SCRIPTS`, `ALLOW_AGENT_MUTATIONS` et `ALLOW_DIRECTORY_WRITES` sur false pendant les tests de lecture seule.
