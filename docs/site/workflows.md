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
- **Cron:** configure an expression and timezone as provided by the scheduler UI.
- **Webhook:** administrators generate a per-job token; inbound events can pass JSON variables. Keep tokens out of URLs, chat logs and public documentation.

**Not yet available:** parallel DAG joins, event-log/file watchers, IMAP triggers, native human approval gates and distributed high-availability scheduling.

## Retry, timeout and idempotency

Each step can retry at most five times and set a timeout bounded by `MAX_STEP_SECONDS`. Retries can repeat side effects (sending a message, restarting a VM); design idempotent steps, use unique transaction IDs and verify target state. Following an unclean process restart, running jobs become `interrupted` rather than silently replaying effects.

## Role-based access

Admins manage jobs, vault and datacenters. Operators may run/edit jobs only when job group ACL permits it; a viewer can read permitted jobs. An operator cannot assign a new vault credential or modify Proxmox steps; nevertheless a workflow editor running scripts with existing credentials has significant privileges.
