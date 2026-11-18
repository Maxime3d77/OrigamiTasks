# OrigamiTasks

![OrigamiTasks — official logo](static/logo.png)

[![CI](https://github.com/Maxime3d77/OrigamiTasks/actions/workflows/ci.yml/badge.svg)](https://github.com/Maxime3d77/OrigamiTasks/actions/workflows/ci.yml)

**OrigamiTasks** is an open-source, self-hosted Linux workflow scheduler and automation control plane with an editable web UI, a credential vault with full per-protocol diagnostics (including opt-in real SMTP test messages), LDAP role mapping, and remote integrations. Inspired by the **workflow concepts** of VisualCron, it is an independent implementation, not a compatible VisualCron clone.

> **Pre-production v0.3.27 staging.** Single-agent and bulk SSH updates are now available with per-host verification and preserved credentials/modules.  Production deployment requires integration/security review. SSH group rename, agent cleanup and the explicitly approved **source-side PBS backup test** are included. **Manual cross-cluster PBS restore is available for a stopped standby VM when both clusters can access the same PBS snapshot.** No continuous changed-block streaming, host-level fencing or automatic failover is implemented. See [PBS pilot (EN)](docs/site/pra-pbs-pilot.md) / [Pilote PBS (FR)](docs/site/pra-pbs-pilot.fr.md).

![OrigamiTasks dark dashboard](docs/screenshots/dashboard-dark.png)

## Full documentation — English (default) / Français

The maintained, **multipage** guides are in `docs/site/` and use MkDocs Material with a bilingual language selector. English is the default (`/`), French is under `/fr/`.

| English | Français |
|---|---|
| [Documentation home](docs/site/index.md) | [Accueil](docs/site/index.fr.md) |
| [Install](docs/site/installation.md) | [Installer](docs/site/installation.fr.md) |
| [Configure TLS, LDAP, Vault](docs/site/configuration.md) | [Configuration TLS, LDAP, coffre](docs/site/configuration.fr.md) |
| [Workflows and cron](docs/site/workflows.md) | [Workflows et cron](docs/site/workflows.fr.md) |
| [Task types](docs/site/tasks.md) | [Catalogue des tâches](docs/site/tasks.fr.md) |
| [Matrix, Ansible, SMTP](docs/site/integrations.md) | [Matrix, Ansible, SMTP](docs/site/integrations.fr.md) |
| [Proxmox/DR architecture](docs/site/proxmox.md) | [Proxmox/PRA](docs/site/proxmox.fr.md) |
| [Agents and deployment](docs/site/agents.md) | [Agents et déploiement](docs/site/agents.fr.md) |
| [Operations and updates](docs/site/operations.md) | [Exploitation/mises à jour](docs/site/operations.fr.md) |
| [Security](docs/site/security.md) | [Sécurité](docs/site/security.fr.md) |
| [API reference](docs/site/api.md) | [Référence API](docs/site/api.fr.md) |
| [Troubleshooting](docs/site/troubleshooting.md) | [Dépannage](docs/site/troubleshooting.fr.md) |
| [Feature coverage](docs/site/roadmap.md) | [Couverture des fonctions](docs/site/roadmap.fr.md) |
| [Changelog](docs/site/changelog.md) | [Historique des versions](docs/site/changelog.fr.md) |
| [Inter-site DR safety design](docs/ARCHITECTURE-PRA-INTERSITES.md) | [Conception PRA SD → AU](docs/ARCHITECTURE-PRA-INTERSITES.fr.md) |

### Preview or build the documentation

```bash
python -m pip install -r requirements-docs.txt
mkdocs serve          # documentation at http://127.0.0.1:8000
mkdocs build --strict # static HTML, English root and /fr/
```

See `mkdocs.yml` for navigation, languages, search, and theme. Do not confuse documentation build port 8000 with the API's internal port 8000.

**Documentation maintenance rule:** every changed configuration variable, task type, API route, UI procedure or security model must update **both** language pages, the [changelog](docs/site/changelog.md), and tests. `python tools/check_docs.py` enforces translation parity and task coverage; CI also builds the site strictly.

## Implemented today

- Browser dashboard, searchable nested folder tree with CRUD, tags, accessible SVG navigation icons, three visual themes (melon-yellow/anthracite, white/melon and cyberpunk purple/neon).
- Workflow graph with sequential success/failure branches, condition sets, variables, retries, timeouts, manual/once/interval/cron/webhook triggers and run history/audit.
- Remote Linux Bash / Windows PowerShell through SSH; optional local scripts **disabled by default**.
- FTPS/SFTP transfer, SMTP with HTML/attachments, HTTP, file operations restricted to transfer storage, Matrix room messages, Ansible playbooks via a dedicated SSH runner.
- Proxmox inventory/discovery across independently configured clusters, **read-only DR/storage assessment**, explicitly gated power actions.
- Protocol-specific encrypted connection profiles (SMTP, SSH, FTP, LDAP, API, Matrix, Proxmox), SMTP handshake diagnostics, editable LDAP/AD administration with read-only test, and Fernet-encrypted credential vault with in-task picker/create/edit/clone, searchable LDAP groups, LDAPS/STARTTLS with group access control, admin/operator/viewer job ACLs, host-side Git updater.
- Preview Linux (Python) and Windows (PowerShell) HTTPS polling agents; SSH deployment from the UI and single-use enrollment.
- Directory workflow actions for LDAP and Active Directory search, user creation, groups, account state and password resets, with opt-in write gates.

## Not yet implemented

Complete VisualCron task parity (100+ categories), IMAP event triggers, signed/mTLS native agent packages, full parallel DAG, document conversion, HA scheduler, Proxmox/PDM cross-cluster migration, PBS orchestration and **live/near-real-time inter-DC VM replication**. See the [feature catalogue](docs/FEATURE-CATALOG.md) for a detailed implementation roadmap.

### Quick install (Linux)

```bash
git clone https://github.com/Maxime3d77/OrigamiTasks.git
cd OrigamiTasks
sudo bash ops/bootstrap.sh       # generates .env and protected secrets
sudo docker compose up -d --build
curl -fsS http://127.0.0.1:8080/api/health
```

Only `127.0.0.1:${ORIGAMI_PORT:-8080}` is exposed on the host. Publish the UI through your own **HTTPS reverse proxy**, with `COOKIE_SECURE=true`. Do not expose the control plane directly on the internet. Back up `.env`, the Fernet key and the SQLite database consistently before upgrades. Full instructions: [Installation](docs/site/installation.md), [Security](docs/site/security.md), [Operations](docs/site/operations.md).

### Testing

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
python tools/check_docs.py
```

External systems (Windows AD/LDAP, Matrix, SMTP, SAN/PVE/PBS and agents) need independent staging acceptance tests; passing mocks does not certify production connectivity.

## Français — présentation rapide

OrigamiTasks est une plateforme d'automatisation auto-hébergée sur Linux : interface web moderne, moteur de workflows conditionnels, coffre d'identifiants, LDAP/AD, scripts Windows/Linux via SSH, Matrix, Ansible, email, FTP/SFTP, fichiers et découverte Proxmox.

**La v0.2.0 est une préversion :** les agents natifs signés/mTLS, toutes les tâches VisualCron et la réplication inter-datacenters de VM ne sont pas encore réalisés. Pour deux clusters Proxmox sur baies iSCSI, un `rsync` de blocs d'une VM allumée **n'est pas un mécanisme de réplication cohérente** : voir le [guide Proxmox/PRA](docs/site/proxmox.fr.md).

La documentation française intégrale se trouve dans [docs/site/index.fr.md](docs/site/index.fr.md). Toute modification du code devra mettre à jour les pages EN **et** FR ainsi que l'historique.

## License

MIT — see [LICENSE](LICENSE). OrigamiTasks is not affiliated with VisualCron or Proxmox.
