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
| `PROXMOX_ALLOWED_HOSTS` | Proxmox REST API endpoints and read-only credential tests | e.g. `pve.example.org:8006` |
| `CONNECTION_TEST_ALLOWED_HOSTS` | Exact allow-listed HTTPS hosts for generic API credential GET diagnostics | Empty by default; set only trusted hosts |
| `PROXMOX_CA_CERT` | Trusted PEM CA file inside container | Optional if system CA trusted |
| `ALLOW_PROXMOX_ACTIONS` | Allow gated VM power actions | `false` until reviewed |
| `GIT_ALLOWED_ORIGIN` | Updater's exact permitted Git URL | Protected origin only |

## LDAP / Active Directory

Use LDAPS (`ldaps://dc.example.org:636`) or LDAP+STARTTLS; never use plain LDAP on untrusted networks. Bind account must be read-only. Configure `LDAP_URI`, `LDAP_BASE_DN`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_USER_ATTRIBUTE`, `LDAP_GROUP_BASE_DN`, group names and optional `LDAP_CA_CERT`. For OpenLDAP, `LDAP_USER_ATTRIBUTE=uid` is typically correct (not `sAMAccountName`); `posixGroup/memberUid` and `groupOfNames/member` memberships are resolved under `LDAP_GROUP_BASE_DN`. The ACL picker requires read access to the group subtree. For nested AD groups, set `LDAP_NESTED_GROUPS=true` and test memberships with actual users. See [Security](security.md).

### Graphical LDAP / Active Directory administration

Administrators can open **Administration → LDAP / Active Directory**. Initial settings are read from `.env`; settings saved in the UI override them without editing that file. Configure URI and port, LDAPS or STARTTLS, bind/user/group bases, `uid` (OpenLDAP) or `sAMAccountName` (AD), mapped role groups, and an optional trusted CA file accessible in the container under `/etc/ssl` or `/app/data`.

**Test connection** performs certificate-verified TLS negotiation, service-account bind, base DN and group searches, and optional user lookup. This is **read-only**: no directory objects change and no password is returned. Settings are encrypted in SQLite using `VAULT_KEY`; retain backups and restrict database access. The local `admin` account is independent of LDAP. Directory steps can default to central settings; use a separate vault service account for write actions when possible.

### LDAP / Active Directory administration

Use **Administration → LDAP / Active Directory** to configure URI and port, LDAPS or STARTTLS, bind DN/password, user/group base DNs, login attribute (`uid` for OpenLDAP), mapped permission groups and CA path. The initial values come from `.env`; settings saved in the web console override them without changing the file. These settings, including the bind secret, are **encrypted in SQLite** using `VAULT_KEY` and never returned to the browser.

**Test LDAP connection** performs read-only bind, base and group searches plus optional login lookup. Trusted CA files must be available inside `/etc/ssl` or `/app/data` in the container. The local `admin` account remains independent of LDAP.

## Credentials

Create **protocol-specific vault profiles** for SMTP (server, port, STARTTLS / implicit SSL / unencrypted relay, optional authentication and default sender), SSH/SFTP, FTP/FTPS, LDAP, API, Matrix and Proxmox. The editor changes fields when you select a connection type. **Test SMTP** verifies TLS and optional credentials without sending mail. Use the **Administration → LDAP** test for the directory itself. Grant workflow editing rights only to trusted operators. Non-sensitive settings can be reviewed by administrators; passwords, tokens and SSH keys are never re-displayed. Blank secret fields retain their existing encrypted values. Old hostless credentials remain compatible with tasks that define their own host.

### SSH identities and reusable server groups

An **SSH** vault entry is a **reusable identity**, not a host configuration: profile name, login, password **or** OpenSSH private key, with an optional key passphrase. Secrets remain encrypted and are never revealed after saving. Prefer individual least-privilege service keys.

Open **Administration → SSH servers & groups** to:

- Create a server **group** by entering FQDNs one per line, e.g. `au-app01.infra.example.org` or `sd-app01.infra.example.org:2222` for a non-standard SSH port.
- Register individual hosts with their FQDN, port and label; assign a host to **multiple groups**.
- Search/filter the inventory by group and FQDN. Deleting a group **never deletes its servers**.
- Open **Vault → SSH identity → Test**, choose a **specific inventoried host**, and verify credentials or the private key against that target. Optional SFTP and `whoami` checks are still available.

**SSH**, **SFTP** and **Ansible runner** workflow steps can select an inventoried server while referencing a reusable vault identity. The selected host and port override any legacy host stored in the old credential. Groups **organize and filter targets**; they do not silently run a task across every member. Legacy host-bound SSH credentials remain supported.

SSH host-key verification remains strict via `known_hosts` to prevent server impersonation. Independently verify a host fingerprint before adding it to `SSH_KNOWN_HOSTS`. Basic authentication tests do not run remote commands.

### Connection diagnostic wizard

From **Vault → Test** or the workflow's credential selector, each typed profile opens its own diagnostic wizard:

- **SMTP:** connection, TLS and optional authentication. Enable **Send a real test email**, enter **one recipient**, subject and body, then explicitly submit. OrigamiTasks sends a real message using the saved profile and returns its **Message-ID** for verification. Success means **accepted by the SMTP server**, not **delivered to the inbox**; check inbox, spam and mail server traces. One recipient per test; a successful send is rate-limited to once every 30 seconds per credential.
- **SSH/SFTP:** pinned host key and authentication; optional read-only SFTP metadata check or explicitly selected fixed `whoami` command (Linux/Windows). No arbitrary command or file transfer.
- **FTPS:** TLS, login and optional read-only `PWD`; no upload, deletion or directory dump. **Plain FTP:** connectivity only, never transmit passwords without TLS.
- **LDAP/AD:** encrypted transport, bind and optional scoped username/group lookup with escaped filters; no directory changes.
- **Matrix:** authenticated `whoami`, optional room membership check, or **explicit** test message to that room (one successful send every 30 seconds per credential). Verify it appears in the room. E2EE is not supported.
- **Proxmox:** authenticated API permissions, optional node listing and specific node status via GET. No VM actions.
- **HTTPS API:** read-only `GET` or `HEAD`, configurable diagnostic path and expected HTTP status (`2xx` or an exact code). Restricted to `CONNECTION_TEST_ALLOWED_HOSTS`, without redirects. HTTP 2xx does not necessarily prove token validation.
- **Generic secrets:** no meaningful network diagnostic until associated with a protocol and destination.

By default, diagnostics **never send messages** or change remote data. Email/Matrix sends are explicit actions in the wizard. Tests require administrator privileges, are audited without disclosing secrets, and use bounded timeouts. Enforce outbound firewall rules and allow-lists.

## Remote SSH

A system-verified `known_hosts` entry is required. Add the fingerprint **after independently verifying it**; do not automatically trust the first key seen in production. Windows targets need Windows OpenSSH Server and an authorized PowerShell-capable service account; Linux targets need an SSH service account.

## Network policy

Allow outbound traffic only to explicit integration endpoints (Matrix HTTPS, Proxmox HTTPS, SMTP submission, SSH/SFTP, FTPS). Limit API token privileges and restrict reverse-proxy access where possible.

## Agents and directory automation

| Variable | Purpose |
|---|---|
| `AGENT_SERVER_URL` | HTTPS origin for outbound agents |
| `AGENT_DEPLOY_ALLOWED_HOSTS` | Allow-listed SSH deployment targets |
| `ALLOW_AGENT_REMOTE_DEPLOY` | Remote SSH deployment (default false) |
| `ALLOW_AGENT_SCRIPTS` | Agent scripts (false) |
| `ALLOW_AGENT_MUTATIONS` | Mutating agent service actions (false) |
| `ALLOW_DIRECTORY_WRITES` | LDAP/AD changes (false) |

For setup and safety, refer to [Agents](agents.md) and [Tasks](tasks.md).
