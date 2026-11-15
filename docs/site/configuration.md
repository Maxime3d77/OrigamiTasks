# Configuration reference

The `.env.example` file is the normative list of settings. Restart the container after changing environment variables. **Never commit `.env`.**

| Variable | Meaning | Recommended |
|---|---|---|
| `ADMIN_PASSWORD` | Initial local administrator credential | Strong random value; rotate after bootstrap |
| `VAULT_KEY` | Fernet encryption key for secrets | Offline backup; never rotate without re-encryption |
| `COOKIE_SECURE` | HTTPS-only browser sessions | `true` |
| `ALLOW_LOCAL_EXECUTION` | Execute shell/Pwsh inside control-plane container | `false` |
| `ORIGAMI_EXECUTOR_WORKERS` | Concurrent workflow workers | Start with `2`; single API instance only |
| `MAX_STEP_SECONDS` | Upper bound for one step | `1800` |
| `MATRIX_ALLOWED_HOSTS` | Explicit Matrix homeserver hostnames | e.g. `matrix.example.org` |
| `PROXMOX_ALLOWED_HOSTS` | Proxmox REST API endpoints | e.g. `pve.example.org:8006` |
| `PROXMOX_CA_CERT` | Trusted PEM CA file inside container | Optional if system CA trusted |
| `ALLOW_PROXMOX_ACTIONS` | Allow gated VM power actions | `false` until reviewed |
| `GIT_ALLOWED_ORIGIN` | Updater's exact permitted Git URL | Protected origin only |

## LDAP / Active Directory

Use LDAPS (`ldaps://dc.example.org:636`) or LDAP+STARTTLS; never use plain LDAP on untrusted networks. Bind account must be read-only. Configure `LDAP_URI`, `LDAP_BASE_DN`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_USER_ATTRIBUTE`, `LDAP_GROUP_BASE_DN`, group names and optional `LDAP_CA_CERT`. For nested AD groups, set `LDAP_NESTED_GROUPS=true` and test memberships with actual users. See [Security](security.md).

## Credentials

Create secrets under **Vault**: SSH (`username`, `password` or `private_key`), Matrix (`token`), SMTP (`username`, `password`), Proxmox (`username=user@realm!tokenid`, `token=secret`). Grant workflow editing rights only to trusted operators. Vault contents are not returned to browsers.

## Remote SSH

A system-verified `known_hosts` entry is required. Add the fingerprint **after independently verifying it**; do not automatically trust the first key seen in production. Windows targets need Windows OpenSSH Server and an authorized PowerShell-capable service account; Linux targets need an SSH service account.

## Network policy

Allow outbound traffic only to explicit integration endpoints (Matrix HTTPS, Proxmox HTTPS, SMTP submission, SSH/SFTP, FTPS). Limit API token privileges and restrict reverse-proxy access where possible.
