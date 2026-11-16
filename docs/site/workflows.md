# Workflows and scheduling

## Data model

A **job** contains ordered **steps**; each step has a unique ID, type, JSON configuration, optional condition, retry policy and `on_success`/`on_failure` target. `END` exits successfully; `FAIL` terminates with failure. Up to 80 steps and 200 graph transitions are accepted per run. The engine snapshots steps when a run is queued, preserving that run against later edits.

## Example: branch after a backup

1. **Run a backup** (`ssh` to a dedicated Linux runner); define `on_success: notify_ok`, `on_failure: notify_fail`.
2. **notify_ok** (`matrix` to an operations room), with `on_success: END`.
3. **notify_fail** (`email` to on-call), with `on_success: FAIL` so the job still registers as failed.

The editor can insert tasks and connect branches. Use template variables such as `{{ run.id }}`, `{{ var.ticket }}` or `{{ steps.backup.output }}` (step ID specific). Never embed credentials into templates.

## Triggers

- **Manual:** a permitted user clicks Run.
- **Once:** specify a local date/time and timezone. The workflow runs once and is not re-enqueued after firing.
- **Interval:** repeat every N minutes, hours, days, or weeks (for example every 5 minutes).
- **Advanced cron:** a five-field cron expression and IANA time zone for complex frequencies.
- **Webhook:** administrators generate a per-job token; inbound events can pass JSON variables. Keep tokens out of URLs, chat logs and public documentation.

**Not yet available:** parallel DAG joins, event-log/file watchers, IMAP triggers, native human approval gates and distributed high-availability scheduling.

## Folder tree, groups and secure connections

The **Workflows** page offers a persistent hierarchical folder explorer. Choose **Manage** to add subfolders, rename folders, delete folders and assign a workflow to a folder. Deleting a folder **moves its jobs to its parent**; it never deletes those jobs.

In **New workflow**, choose the **Trigger**: **Manual**, **Once**, **Repeat at interval**, or **Advanced cron**. Local dates use the specified timezone (default Europe/Paris) and are stored as UTC. Missed interval ticks during an outage are not backfilled automatically. Use cron, rather than intervals, for weekday-specific schedules.

**View / Run / Edit** permission groups can be selected via a searchable LDAP group picker, including OpenLDAP `posixGroup` / `memberUid`. The LDAP bind account needs read access to `LDAP_GROUP_BASE_DN`. When LDAP is unavailable, the UI warns and falls back to locally known groups.

For steps requiring credentials, **Search / manage** opens the vault without leaving the workflow. Administrators can **select, create, edit, clone or delete** a connection. Secret values are never re-displayed; empty sensitive fields keep existing values when editing. **Create from a step** still encrypts the credential in the vault and stores only its ID in the workflow — no plaintext secret in `steps_json`. Referenced credentials cannot be deleted until the reference is removed.

## Retry, timeout and idempotency

Each step can retry at most five times and set a timeout bounded by `MAX_STEP_SECONDS`. Retries can repeat side effects (sending a message, restarting a VM); design idempotent steps, use unique transaction IDs and verify target state. Following an unclean process restart, running jobs become `interrupted` rather than silently replaying effects.

## Role-based access

Admins manage jobs, vault and datacenters. Operators may run/edit jobs only when job group ACL permits it; a viewer can read permitted jobs. An operator cannot assign a new vault credential or modify Proxmox steps; nevertheless a workflow editor running scripts with existing credentials has significant privileges.
