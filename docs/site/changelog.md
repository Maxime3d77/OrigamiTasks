# Changelog

## 0.3.15-rc1 — 2026-11-17

- **Overview:** *Tasks & runs*, *Active agents* and *Proxmox DC / DR* tabs. Existing task metrics, agent online/offline/revoked/uninstalled states and process CPU/RAM telemetry when reported, linked Proxmox clusters and sites. Real DR replication lag remains explicitly unavailable until an actual engine is deployed.
- **Remote removal:** SSH uninstall for enrolled Linux and Windows agents, requiring a vault SSH identity, pinned and trusted host key, matching enrolled hostname and an on-host check of agent ID and controller HTTPS URL. Removes only OrigamiTasks's service/task and agent folder, then revokes its token. Failed remote removal never silently revokes; manual revoke stays separate.
- **Resource usage:** newly deployed Linux units apply `MemoryMax=192M`, `CPUQuota=20%`, `TasksMax=48`. New agent script versions default to configurable 10-second polling (5–60 s) and report their own process CPU/RAM. Existing agents are not automatically upgraded.
- **Security limits:** preview-grade agents use HTTPS and individual revocable tokens, but no mTLS, signed update bundles or autonomous DR runtime yet. Windows scheduled agent still runs as SYSTEM and needs further hardening.

## 0.3.14-rc1 — 2026-11-17

- **DR sites:** manage freely named sites directly from the Proxmox screen, with **primary / standby / other** role, multiple registered Proxmox clusters, existing Linux agents, and declared storage profiles (auto, shared iSCSI+LVM, ZFS, Ceph, PBS, other).
- **Outage simulation:** **cluster unavailable**, **site network partition**, and **OrigamiTasks console unavailable**. All scenarios are virtual, never touching VMs, hypervisors, agents or storage. Failure checks and outcomes are auditable.
- **Agents:** links to existing SSH deployment and shows existing agent versions and heartbeats. Current agents still depend on the central controller; they are **not autonomous DR agents**. Rolling updates, an independent witness, fencing and replication require later implementation and validation.
- **Fail closed:** no REP_ standby can start from a simulation.

## 0.3.13-rc1 — 2026-11-17

- **Proxmox discovery:** instant search by name, VMID, node, ID, status and storage; QEMU VM/LXC/node/storage filters, sort by name/type/VMID/node/status in either direction, 100-item pagination, API refresh. Existing per-VM DR assessment remains accessible.
- **SD→AU DR architecture:** bilingual design for managed site agents, powered-off REP_ standbys, storage-specific replication, independent witness and effective fencing, no double-start, controlled failover and explicit failback. **This version implements neither replication nor automatic failover.**

## 0.3.12-rc1 — 2026-11-17

- **Proxmox:** per-node/per-cluster administrator checkbox **Skip TLS certificate verification (self-signed certificate)**, also available in vault Proxmox credentials. Disabled by default, stored persistently, reversible and scoped to the selected endpoint. HTTPS remains encrypted, but server identity is not authenticated in bypass mode.
- **Diagnostics:** distinguish certificate validation, TLS handshake, DNS/port/firewall, token and permission errors. Explicit warning when a diagnostic skipped certificate validation.
- **UI:** responsive Proxmox cards with two-column actions, unobstructed add-server button below heading, and fixed FQDN search.
- **Compatibility:** nondestructive SQLite migration; existing inventory/workflows remain, no `.env` editing.

## 0.3.11-rc1 — 2026-11-17

- **Fully web-managed Proxmox registration:** add or edit a cluster or standalone node, issue a vault-backed encrypted API token profile, upload a trusted CA from the browser and assign it per target, without editing `.env` or restarting Docker.
- **Certificates:** PEM/CRT/CER/DER imports, private persistent store, select different CA chains per datacenter or vault profile. Reject arbitrary filesystem paths or private keys; HTTPS remains verified.
- **Fleet navigation:** searchable nodes and clusters, direct API diagnostics and discovery of cluster nodes, VMs, containers and storage.
- **Compatibility and safety:** migrate existing SQLite entries, preserve encrypted tokens, enforce matching vault and DC URLs, retain explicit gates for VM actions.

## 0.3.10-rc1 — 2026-11-16

- **Proxmox:** register and manage datacenters and API tokens in the web UI without `PROXMOX_ALLOWED_HOSTS` in `.env`; searchable datacenter list, ensure vault token and DC URL match, actionable HTTP 401/403/TLS/network diagnostics.
- **LDAP:** accept standard OpenSSL PEM certificate exports with Bag Attributes/friendlyName/subject/issuer metadata; reject arbitrary content and private keys.
- **Email:** per-workflow HTML/plaintext completion reports with SUCCESS/FAILURE subject, encrypted SMTP vault profile, conditional sends, per-step results and redacted error excerpts; delivery status retained.
- **Reliability:** backward-compatible SQLite migrations, regression tests, bilingual documentation.

## 0.3.9-rc1 — 2026-11-16

- **SSH:** the default host-key policy is now persistent trust-on-first-use (`SSH_HOST_KEY_POLICY=accept-new`): new public host keys are stored automatically; changed keys are blocked. `strict` remains available for manual approval.
- **LDAP:** schema-neutral read-only queries eliminate client-side `LDAPObjectClassError` from mixed AD/OpenLDAP group filters. The imported CA continues to verify LDAPS/STARTTLS; PEM files with a UTF-8 BOM are supported.
- **UI/tests:** no mandatory per-server fingerprint entry, public CA import clears the TLS bypass checkbox in the form; regression tests and English/French documentation updated.

## 0.3.8-rc1 — 2026-11-16

- **SSH:** browser-assisted public server host-key pinning, requiring independently verified SHA256 fingerprint confirmation. Changed existing keys remain blocked. User SSH private keys and vault identities are never altered by host-key approval.
- **LDAP:** import mixed CRT/PEM chains containing CA and server-leaf certificates; store CAs only and report excluded non-CA certificates.
- **UI:** readable import status, explanation of disabled TLS verification bypass, SSH/LDAP regression tests and bilingual documentation.



## 0.3.7-rc1 — 2026-11-16

- **SSH:** local OpenSSH/PEM user-private-key import; distinguish unreadable key, unknown host (SHA-256 fingerprint), changed host key, rejected authentication, and incompatible handshake; no automatic trust of unknown hosts.
- **LDAP:** explicit certificate-verification exception via Administration → LDAP, allowed only with host-level ALLOW_INSECURE_LDAP_TLS=true, off by default and prominently warned. Prefer CRT/DER/PEM public CA import.
- **Updates:** in-browser non-installation preflight, host updater heartbeat and Git/Docker Compose/data readiness checks.
- **GitHub:** automatic version-tag release workflow for vX.Y.Z or vX.Y.Z-rcN, running version validation, tests, and documentation before publishing; ordinary commits never publish releases.
- **Privacy:** anonymized server examples, bilingual security documentation and regression tests.


## 0.3.6-rc1 — 2026-11-16

- **SSH test** against a manually supplied FQDN and port without registering a server; strict host-key verification remains enforced.
- **LDAP CA** import from binary `.crt`, `.cer`, `.der` or PEM, converted to trusted PEM without disabling TLS.
- **Workflow graph**: 8–250% zoom, Ctrl-wheel, drag-to-pan, fit width, overview and viewport preservation.
- API and UI regression tests and bilingual documentation updates.


## 0.3.5-rc1 — 2026-11-16

- Bulk Linux/Windows agent installation from a **host FQDN list** or SSH inventory group, with a persistent per-host status queue (100 targets maximum).
- Filtered deployment credential choices to **SSH identities**; typed connection pickers no longer show unrelated protocols.
- **Updates** screen compares installed/latest version, latest remote Git commit date, commits behind, and verified/unverified state.
- **LDAP** public PEM CA import from the browser, preserving strict TLS certificate validation.
- Renamed **DC Proxmox**; Proxmox API inventory and VM actions do not require PVE node agents.
- Workflow source selector (**vault or manual network settings**), concise once/assistant/cron schedules and a one-click **Add on failure** branch.
- Additional regression tests for bulk deployment, LDAP TLS trust, SMTP modes and failure handling.


## 0.3.4-rc1 — 2026-11-16

- Decoupled **SSH identities** (login, password or private key, optional passphrase) from server addresses in the encrypted vault.
- Added **Administration → SSH servers & groups** with FQDN inventory, ports, labels, reusable group membership, bulk FQDN entry and search.
- SSH credential test now accepts an explicit inventory server target and verifies host key plus the saved identity.
- SSH, SFTP and Ansible runner workflow steps can select an inventory server without duplicating secrets.
- Preserved legacy host-bound SSH credentials and protected removal of inventory servers still referenced by jobs.
- Added regression tests and updated bilingual documentation.


## 0.3.3-rc2 — 2026-11-16

- Fixed protocol diagnostic checkboxes inheriting the global 100% text input width.
- Constrained checkboxes to 18 px, preserved readable labels, and prevented horizontal dialog overflow.
- Improved mobile layout and added a UI regression test.
- Bumped CSS and JavaScript asset versions to refresh stale browser caches.

## 0.3.3-rc1 — 2026-11-16

- Added a **per-protocol credential diagnostic wizard** accessible from the vault and workflow editor.
- **SMTP:** optional real test email with recipient, subject/body, Message-ID, clear SMTP-accepted vs inbox-delivered distinction, send rate limiting.
- **SSH/SFTP:** optional metadata read and fixed `whoami` probe; **FTPS:** optional read-only PWD.
- **LDAP:** optional user/group lookup; **Matrix:** room membership and explicit test message; **Proxmox:** node listing/status; **API:** GET/HEAD and expected status.
- Explicit opt-in for sends, audited administrator-only actions, injection/secret protections and extended regression coverage.


## 0.3.2-rc2 — 2026-11-16

- Added **Test** action for all vault connection types, with a detailed read-only result dialog and per-check status.
- SSH: strict known-host verification and authentication without remote commands. FTPS: TLS and login without file operations; plaintext FTP only checks reachability, never sending a password.
- LDAP: TLS, bind, optional base read; Matrix: authenticated `whoami`; Proxmox: protected permission read. Generic HTTPS API: allowlisted GET only and explicit warning that HTTP 2xx does not necessarily prove token identity.
- Added `CONNECTION_TEST_ALLOWED_HOSTS` for generic API diagnostics, protocol-specific regression tests, and updated English/French guidance.


## 0.3.2-rc1 — 2026-11-16

- Added **Administration → LDAP / Active Directory** with encrypted live settings and read-only bind/group diagnostics.
- Added protocol-specific vault forms for SMTP, SSH/SFTP, FTP/FTPS, LDAP, API, Matrix and Proxmox.
- SMTP profiles now include host, port, TLS mode, optional authentication, default sender and no-send connection testing.
- Email steps reuse the vault's SMTP settings; legacy credentials remain supported.
- Added automated security/integration tests and updated EN/FR documentation.


## 0.3.2-rc1 — 2026-11-16

- Added **Administration → LDAP / Active Directory** with encrypted stored settings, URI/port, TLS, base DNs, user attribute, role groups and read-only connection diagnostics.
- Added protocol-specific connection profiles for SMTP, SSH/SFTP, FTP/FTPS, LDAP, API, Matrix, Proxmox and generic secrets.
- SMTP profiles configure server/port, implicit TLS or STARTTLS, optional authentication, default sender and no-send connection test.
- SMTP tasks inherit vault connection settings while preserving optional step-level overrides.
- Preserved legacy credential compatibility and added API/security tests.


## 0.3.1-rc2 — 2026-11-16

- Fixed login form unintentionally sending credentials via GET when the main JavaScript handler was unavailable.
- Added independent POST/JSON login script, versioned assets and early submit-handler registration.
- Added regression checks and guidance for handling passwords exposed in access logs and browser history.

## 0.3.1-rc1 — 2026-11-16

- Fixed ignored sidebar clicks (`data-page` buttons now reach delegated navigation).
- Replaced sidebar glyphs with consistent SVG icons and refined responsive editor layout.
- Added persistent nested workflow folders with create/rename/delete (without deleting jobs), filtering and assignment.
- Added graphical once-only and repeating minute/hour/day/week schedules alongside advanced cron.
- Added searchable LDAP/AD group selector and OpenLDAP `posixGroup/memberUid` and `groupOfNames/member` resolution.
- Added credential vault search/select/create/edit/clone/protected-delete flows inside task configuration.
- Added regression tests and bilingual documentation; real-browser and real-directory acceptance is still required.


## 0.3.0-rc1 — 2026-11-15

- Restored the original official logo in README and bilingual documentation.
- Added Linux Python/systemd and Windows PowerShell portable outbound HTTPS agents with enrollment, revocation, heartbeat, queue and leases.
- Added SSH/SFTP installation from the administration page with explicit host allow-list and SSH key pinning.
- Added `agent` and `directory` workflow task adapters; LDAP/AD changes require opt-in approval.
- Updated English/French documentation and prepared test coverage. Real Windows/LDAP lab acceptance remains required.

## 0.2.0 — 2026-11-15

- Matrix plain text message task with encrypted token.
- Ansible playbook invocation through dedicated SSH runner; playbook/inventory path restrictions.
- Confined file operations and HTML SMTP attachments.
- Read-only Proxmox storage-based VM DR assessment.
- Proxmox datacenter registration, discovery of node/QEMU/LXC/storage resources, gated power actions.
- Expanded searchable task catalogue UI and feature-roadmap inventory.
- Added **English-default, French-translated, multipage documentation** with CI consistency check.

**Outstanding:** No native Windows/Linux agent, full VisualCron parity, real-time cross-cluster replication, PBS/PDM DR automation or production compatibility guarantee.

## 0.1.0 — 2026-11-15

- First scheduler, web UI with dark/light/cyber themes, execution graph, local/SSH scripts, SMTP/HTTP/FTP/SFTP, vault and LDAP roles.
- Git-based host updater, installer, initial tests and security documentation.
