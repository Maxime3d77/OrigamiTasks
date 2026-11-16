# Changelog

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
