# Configuration reference

### Proxmox discovery: search and sort resources

Under **Proxmox servers and clusters → Discover nodes/VMs**, use free-text search (name, VMID, node, status, resource ID, storage), filter **All / Nodes / VMs / LXC / Storage**, sort by **Name / Type / VMID / Node / Status** in ascending or descending order, and browse **100 results per page**. Counts and an **API refresh** are provided; the existing QEMU **Assess DR** action remains available. This feature is **read-only**.

The proposed SD→AU managed DR agents, powered-off **REP_** standbys, independent witness, fencing and explicit failback are specified in **docs/ARCHITECTURE-PRA-INTERSITES.md**. This is a design document, not an active automatic failover engine. Lost pings or unavailable agents cannot independently authorize a standby start.

### Self-signed Proxmox HTTPS: per-server TLS verification exception (0.3.12)

Open **Administration → Proxmox servers and clusters**, click **Edit** for the target. If uploading the trusted internal CA is not possible, select **Skip TLS certificate verification (self-signed certificate)**, **Save**, then **Test API**. This administrator-only exception applies to that node/cluster only, is reversible and requires **no `.env` edits or Docker restart**.

The transport remains HTTPS encrypted, but the endpoint certificate and hostname are **not authenticated**, introducing man-in-the-middle risk. For durable operations, import the PVE internal CA and disable the exception. Diagnostics warn when certificate validation is disabled and distinguish TLS errors from DNS/port 8006/firewall failures and token permissions.

### Add a Proxmox server or cluster entirely in the browser (0.3.11)

1. Open **Administration → Proxmox servers and clusters**, then **Add a server / cluster**.
2. Choose **Proxmox cluster** (discover member nodes) or **Standalone Proxmox node**, enter a descriptive name and an HTTPS API URL (for example `https://pve.example.org:8006`).
3. Either select an existing vault token, or enter **user@pve!tokenname** and its secret directly in the wizard. OrigamiTasks creates the encrypted vault profile automatically.
4. For an internal CA, **upload the public CA file** (PEM/CRT/CER/DER), or select a previously uploaded CA. Its SHA-256 identifier is linked to this target; **no `.env` change or Docker restart** is needed.
5. Use **Test API**, **Discover nodes/VMs**, or **Edit** to change the target's settings.

Public CA bundles are stored in `data/proxmox-cas`, separately from LDAP. TLS certificate and hostname verification remain mandatory. Legacy `PROXMOX_CA_CERT` is only a fallback when no CA is selected in the web interface. Destructive VM actions retain separate authorization controls; registering a cluster does not enable those actions by itself.

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
| `PROXMOX_ALLOWED_HOSTS` | Legacy external allow-list for other connector paths | **Not required for Proxmox DC/token profiles stored in the web UI** |
| `CONNECTION_TEST_ALLOWED_HOSTS` | Exact allow-listed HTTPS hosts for generic API credential GET diagnostics | Empty by default; set only trusted hosts |
| `PROXMOX_CA_CERT` | Trusted PEM CA file inside container | Optional if system CA trusted |
| `SSH_HOST_KEY_POLICY` | SSH server public key enrollment | `accept-new` by default; `strict` for manual verification |
| `ALLOW_PROXMOX_ACTIONS` | Allow gated VM power actions | `false` until reviewed |
| `GIT_ALLOWED_ORIGIN` | Updater's exact permitted Git URL | Protected origin only |

## Manage Proxmox datacenters without editing `.env`

Create a **Proxmox API** vault profile with an HTTPS endpoint such as `https://pve.example.org:8006`, `user@realm!tokenname` and the API token secret. Under **Administration → DC Proxmox**, register a datacenter and select that profile with **the same HTTPS URL**. The registered endpoint is authorized immediately, regardless of `PROXMOX_ALLOWED_HOSTS`; the web UI supports searching large fleets.

Mismatched token and datacenter URLs are rejected to prevent sending credentials to a different host. HTTPS server certificate verification is still mandatory; if an internal CA is not trusted, configure `PROXMOX_CA_CERT` or the system trust store. Sensitive VM operations remain subject to PVE token permissions **and** OrigamiTasks action controls; registering a DC never elevates its token.

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


### Automatic trust-on-first-use for new SSH servers

An SSH identity stored in the Vault can be used unchanged on every Linux or Windows server where its matching **user public key** is authorized. The private SSH key is **not tied to a server**.

With `SSH_HOST_KEY_POLICY=accept-new` (default), OrigamiTasks **automatically records** each new server public host key in persistent `/app/data/known_hosts` during a connection test, workflow, or agent deployment. Subsequent connections require the same key and reject unexpected changes. There is no per-server fingerprint entry.

First-use auto trust does not authenticate the server independently: an attacker controlling that first connection could present a false key. For strict environments, set `SSH_HOST_KEY_POLICY=strict` in `.env` and restart. Manual browser host-key inspection and independently verified SHA256 approval remain available.

### Import LDAP CA chains from CRT

A `.crt` file may contain several X.509 certificates. The importer accepts PEM and DER; for a PEM bundle containing both CAs and server-leaf certificates, it **keeps only CA certificates** and reports any non-CA certificates ignored. A `CA:TRUE` CA is retained, while a leaf-only bundle is rejected. After import, **save the LDAP configuration** with the resulting CA path and test LDAPS/STARTTLS. A greyed-out skip-verification checkbox means the host-level `ALLOW_INSECURE_LDAP_TLS` permission is disabled. With the correct CA, leave this bypass turned off.


### SSH diagnostics: user private key versus server host key

The Vault stores a **user authentication private key** (OpenSSH/PEM, optionally passphrase protected). The matching user public key must be in each target's `authorized_keys`; never import a server's `/etc/ssh/ssh_host_*` private key.

A manual FQDN connection test does not register the server in inventory. In `accept-new` mode, it only registers the **public server host key** in `known_hosts`. The optional fingerprint inspection button can still be used to investigate a host-key change.

### Test SSH credentials against a temporary manual host

In **Vault → SSH identity → Test**, select **Manual FQDN**, enter a server hostname (such as `serveur.fr`) and SSH port (22 by default). The vault private key or password is tested against the specified host, without creating an inventory entry or modifying the identity. **Inventory SSH** and **Legacy host** are still available. SSH host-key pinning through `SSH_KNOWN_HOSTS` remains mandatory.

### LDAP public CA certificates in CRT, DER, or PEM

In **Administration → LDAP**, import a `.crt`, `.cer`, `.der`, or `.pem` public CA file. A `.crt` may be text **PEM** or binary **DER**; both are recognized and normalized to PEM. Private keys and non-CA server certificates are rejected. **Save** the LDAP settings after importing, then run **Test LDAP**. LDAPS/STARTTLS certificate validation remains mandatory, without a global insecure skip option.

### SSH identities and reusable server groups

An **SSH** vault entry is a **reusable identity**, not a host configuration: profile name, login, password **or** OpenSSH private key, with an optional key passphrase. Secrets remain encrypted and are never revealed after saving. Prefer individual least-privilege service keys.

Open **Administration → SSH servers & groups** to:

- Create a server **group** by entering FQDNs one per line, e.g. `au-app01.infra.example.org` or `sd-app01.infra.example.org:2222` for a non-standard SSH port.
- Register individual hosts with their FQDN, port and label; assign a host to **multiple groups**.
- Search/filter the inventory by group and FQDN. Deleting a group **never deletes its servers**.
- Open **Vault → SSH identity → Test**, choose a **specific inventoried host**, and verify credentials or the private key against that target. Optional SFTP and `whoami` checks are still available.

**SSH**, **SFTP** and **Ansible runner** workflow steps can select an inventoried server while referencing a reusable vault identity. The selected host and port override any legacy host stored in the old credential. Groups **organize and filter targets**; they do not silently run a task across every member. Legacy host-bound SSH credentials remain supported.

The first host key is automatically pinned in `known_hosts` under `accept-new`, while any unexpected later change is rejected. Use `strict` to require manual verification. Basic authentication tests do not run remote commands.

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

In `accept-new`, new server keys are automatically stored and later substitutions are blocked. Set `strict` to require independent host-key verification for higher-assurance environments. Windows targets need Windows OpenSSH Server and an authorized PowerShell-capable service account; Linux targets need an SSH service account.

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


## Importing an LDAP TLS CA certificate

Open **Administration → LDAP / Active Directory**, select **Import CA** and upload the public PEM certificate(s) for the authority that signed your LDAP server certificate (`.crt` / `.pem`, up to 128 KiB). The validated PEM bundle is stored in the persistent `/app/data/ldap-cas` directory and its path is inserted into **TLS CA path**. **Save settings**, then **Test LDAP connection**.

LDAPS:636 with strict TLS certificate verification is preferred; LDAP:389 with verified STARTTLS is also supported. There is **no skip-certificate switch** because it would expose LDAP service-account credentials to interception. If your LDAP endpoint has a self-signed chain, import the corresponding trusted **CA certificate**, not a private key, and verify the server certificate's DNS SAN matches the FQDN.

## Installed and available versions

The **Updates** screen compares the running container `APP_VERSION` with the version stored in the latest commit on the pinned `GIT_BRANCH` from the trusted `GIT_ALLOWED_ORIGIN`. The separate host-side `ops/update-worker.py` checks once on start and at most hourly, reporting installed/latest version, **latest commit date** (not necessarily a release date), commit count behind and last checked time.

`GIT_ALLOWED_ORIGIN` must match exactly `git remote get-url origin`. When deploying a recipe branch, set `GIT_BRANCH` accordingly. If the trusted origin is not configured, the worker is missing, or the local commit does not fast-forward to the tracked branch, the UI reports **Check unavailable**, never a false up-to-date status. Applying updates always requires explicit administrator approval.

## LDAP trusted CA import

In **Administration → LDAP / Active Directory**, upload the **public CA** that signed the LDAPS server certificate (`.crt` / `.pem`, maximum 128 KiB). The PEM certificates are validated and saved with restricted permissions under the persistent `/app/data/ldap-cas` directory. The CA path is populated automatically: **save the LDAP settings**, then **test the connection**. The server certificate's DNS SAN must match the configured FQDN.

Certificate verification remains mandatory for **LDAPS:636** or **STARTTLS:389**. There is no skip-validation toggle, because bypassing verification could expose LDAP bind credentials to interception. **Never import a private key.**

## Installed and latest versions

**Updates** shows the running version, the latest commit's application version from the trusted `GIT_BRANCH` under `GIT_ALLOWED_ORIGIN`, remote **commit date**, commits behind, and the last check time. The host-side `ops/update-worker.py` checks on startup and approximately hourly; an admin may also request a check.

Set `GIT_ALLOWED_ORIGIN` to the exact result of `git remote get-url origin` and `GIT_BRANCH` to the branch actually deployed. If the worker is absent, the origin is not trusted, or local and remote branches diverge, the UI reports **Check unavailable**, not a false up-to-date status. Applying updates always requires explicit approval. The displayed date is a commit date, not necessarily a release date.
