# Windows and Linux agents — architecture specification

**Status: designed, not implemented in v0.2.** The SSH runner supports initial Linux/Windows script execution without an agent. Native agents become useful when SSH cannot expose OS events and when inbound management ports must stay closed.

## Planned agent capabilities

| Linux | Windows |
|---|---|
| Bash, Python, `systemctl`, `journalctl`, package managers | PowerShell 5.1/7, Windows services, Event Logs |
| inotify/file watchers, process and disk checks | FileSystemWatcher, scheduled tasks, WMI/CIM |
| SSH/SFTP and controlled service elevation | SMB/UNC, Windows Credential Manager integration |
| Application user and service-account execution | Service session and optionally interactive session via separate broker |

## Trust and lifecycle

1. Admin generates a **single-use enrollment token** with expiry and assigned resource group.
2. Agent authenticates outbound over **TLS/mTLS** and obtains a distinct short-lived identity; never expose a master vault key.
3. Agent advertises signed/validated OS and capability inventory.
4. Control plane authorizes each job by agent group, action type, credential scope and approval level.
5. Agent polls/streams jobs, reserves durable leases, performs bounded execution without shell interpolation, returns structured stdout/stderr/exit code and audit identifiers.
6. Token/certificate rotation and revocation are supported. Updating agent binaries requires signed releases and rollback.

## Safety properties

- Use non-admin service identity by default; elevate via scoped policy on the target only.
- Authenticate commands and do not accept arbitrary internet requests on agent machines.
- Prevent duplicate execution using lease IDs and idempotency keys. Stop/reconcile expired leases rather than blindly replaying reboot or VM operations.
- Redact secrets at the source and keep logs short. Do not send unbounded artifacts into the control-plane database.
- Keep cross-platform packages separately versioned; test Windows Server and supported Linux distributions in CI/VM labs.

## Deployment roadmap

v0.4 is the first planned version for real agent enrollment, authenticated polling and native system actions. At this release, **do not install a placeholder agent** or treat this chapter as installation instructions for a finished binary.
