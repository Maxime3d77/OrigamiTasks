# Changelog

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
