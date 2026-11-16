# Changelog

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
