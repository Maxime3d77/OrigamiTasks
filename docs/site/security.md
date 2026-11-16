# Security and authorization

OrigamiTasks is an **automation control plane**. Running a script is a powerful privilege even if the browser never reveals the credential. Treat all users with edit access to privileged workflows as privileged on those targets.

## Current safeguards

- Argon2 password hashing, session cookies `HttpOnly; Secure; SameSite=Strict`, CSRF verification, login throttling.
- LDAP group role mapping, per-job ACLs for view/run/edit, administrator-only vault and datacenter management.
- Encrypted secret payloads (Fernet), TLS verification for LDAP, Matrix and Proxmox, strict SSH known hosts.
- Disabled local script execution by default; isolated host updater instead of a privileged Git command in the web application.
- Controlled transfer root and maximum log length. Known credential values are redacted from displayed logs (not an absolute guarantee).

## Known limits

- A workflow editor may exfiltrate a secret implicitly through script output/network behavior on an execution target.
- There are not yet scoped vault grants, per-tenant worker isolation, MFA/OIDC, certificate-backed agents or a HA audit store.
- Arbitrary user-configured HTTP endpoints can access networks reachable from the application container. Use egress firewall policies and trusted operators.
- Proxmox VM power actions are off by default and are not a substitute for safe DR fencing.
- Backup/restore and upgrade rollback must be tested before production.

Credential editing and cloning are server-side operations: existing secret values never return to the browser. Credentials referenced by a task or datacenter cannot be deleted. Creating credentials from a workflow step still encrypts them in the vault and stores only the credential ID in the workflow.

LDAP/AD administration settings are encrypted in SQLite; service-account passwords are never returned to the browser. The LDAP test performs read-only searches. SMTP testing never sends mail, verifies TLS certificates, and rejects unencrypted password authentication. Only administrators can test outbound connections; restrict their destinations with an egress firewall.

LDAP/AD settings saved via the admin UI are encrypted in SQLite. LDAP tests perform read-only bind/search operations and never return service credentials. SMTP tests do not send mail, verify TLS certificates and reject authentication without TLS. Only administrators can run connection tests; enforce network egress restrictions.

## Recommended deployment controls

Dedicated management VLAN, upstream allow-lists, no Docker socket mounts, TLS termination, mandatory code review, restricted SSH principals, no administrator/root service account for routine jobs, protected Git branch, audited production updates, secrets rotation and immutable external audit log.

See the detailed [security threat model](https://github.com/Maxime3d77/OrigamiTasks/blob/main/docs/SECURITY.md).

## Agent and directory task security

Preview agents use distinct revocable, long-lived tokens (no mTLS yet); enrollment tokens are single-use and expire after 15 minutes. Remote provisioning uses pinned SSH host keys and allow-listed targets. The Windows scheduled task runs as SYSTEM; Linux runs unprivileged. Script execution is still privileged trusted automation.

LDAP/AD writes are disabled by default, confined by base DN, TLS certificate validation and explicit step approval. Use least-privilege service credentials, a test directory and external domain controller audit logs.
