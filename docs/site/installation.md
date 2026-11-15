# Installation

## Prerequisites

- Linux host (Debian 12/13 or Ubuntu LTS recommended); Docker Engine and Docker Compose v2.
- DNS and synchronized time (NTP); HTTPS reverse proxy and firewall segmentation.
- Enough disk for SQLite database, logs, transfer staging; plan backups independently.
- A service account with minimum privileges on every remote execution target.

## Initial setup

```bash
sudo apt update
# Install Docker Engine and Docker Compose plugin from your trusted repository.
git clone https://github.com/Maxime3d77/OrigamiTasks.git
cd OrigamiTasks
sudo bash ops/bootstrap.sh
sudo docker compose up -d --build
curl -fsS http://127.0.0.1:8080/api/health
```

`bootstrap.sh` creates `.env`, a random admin password and a Fernet `VAULT_KEY`. Store the secrets in an **offline password manager**; neither belongs in Git. Inspect `.env` before deploying. Compose binds HTTP to loopback, not a public interface.

## HTTPS example: Nginx

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

Replace domains/certificates with yours; do not expose :8080 directly. Keep `COOKIE_SECURE=true` in production.

## First login and health

1. Open `https://tasks.example.org`.
2. Sign in as `admin` with the one-time password printed by bootstrap; rotate it after installation using `ops/change-admin-password.py`.
3. Check **Settings → Vault**, **Workflows** and **Audit**.
4. Register DNS names and outbound allow-lists before adding remote credentials.
5. Run the [acceptance checklist](https://github.com/Maxime3d77/OrigamiTasks/blob/main/docs/DEPLOYMENT-CHECKLIST.md).

## Updating installed packages

Use the [Operations](operations.md) procedure. Do not blindly run `git pull` over a modified working tree or share the application data directory with a public web server.
