# Feature coverage and roadmap

**Do not confuse the catalogue with implementation.** VisualCron has extensive native actions and triggers; our implementation is currently an early pre-production subset. The [detailed original feature inventory](https://github.com/Maxime3d77/OrigamiTasks/blob/main/docs/FEATURE-CATALOG.md) tracks VisualCron categories and additional infrastructure-specific modules.

| Capability | v0.2 status | Next milestone |
|---|---|---|
| Job editing, search, tags, folders, manual run, cron, webhook | Implemented | Time windows and event triggers |
| Success/failure flow, conditions, retries, variable templates, audit | Implemented (sequential graph) | DAG parallelism, approvals, revisioning |
| SSH/Windows PowerShell, confined file operations, SFTP/FTPS, HTTP, SMTP HTML/attachments | Implemented; integration tests outstanding | More native task categories |
| Matrix plain messages | Implemented | Threads/attachments/E2EE/approvals |
| Ansible over dedicated SSH runner | Implemented | Isolated Ansible Runner worker + structured events |
| Proxmox registration/discovery, read-only DR assessment and gated VM power | Implemented | Read-only DR assessment, PBS and PDM |
| Windows/Linux native agents | **Not implemented** | Enrollment, mTLS, durable leases, OS tasks |
| Cross-cluster VM replication and failover | **Not implemented** | Storage-aware DR design, safety tests |
| Full VisualCron feature parity | **Not implemented** | Multi-release roadmap |

## Proposed releases

- **v0.3**: expanded task catalogue, templated email and richer integrations, per-credential permissions and documentation tests.
- **v0.4**: native cross-platform agents with authenticated enrollment; isolated execution; approval and workflow versioning.
- **v0.5**: Proxmox/PDM migration planning and monitored operations; PBS backup/sync/restore tasks.
- **v0.6**: storage-aware DR plugins, consistent snapshots, test restores and failover guardrails.
- **v1.0**: operational hardening, HA design, thorough integration/security tests, stable public APIs.

Implementations must ship with a bilingual change to these docs, tests, and a changelog entry.
