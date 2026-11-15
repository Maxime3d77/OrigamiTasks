# Available task reference (v0.2)

This page lists **implemented adapters**, not all VisualCron features. All task settings are entered in the step configuration; most strings support variable interpolation.

| Type | Core settings | Notes |
|---|---|---|
| `ssh` | `host`, `port`, `platform=linux/windows`, `script`, `credential_id`, `timeout` | Host fingerprint must be known; Windows uses encoded PowerShell. |
| `shell` | `script`, `timeout` | Local execution **disabled** by default. |
| `powershell` | `script`, `timeout` | Local `pwsh`, disabled by default. |
| `ansible` | `host`, `credential_id`, `playbook`, `inventory`, `limit`, `extra_vars`, `check` | Invokes `ansible-playbook` on a dedicated SSH runner. |
| `matrix` | `homeserver`, `room_id`, `message`, `credential_id` | Plain Matrix messages using bot token; no E2EE. |
| `email` | `host`, `port`, `ssl`, `from`, `to`, `subject`, `body`, `credential_id` | SMTP with STARTTLS or TLS; plain text or optional `html_body` plus up to 10 attachments / 10 MB from transfer storage. |
| `http` | `method`, `url`, `headers`, `body`, `credential_id` | Uses request-level bearer token. Treat endpoints as trusted. |
| `ftp` | `host`, `port`, `direction`, `local_path`, `remote_path`, `credential_id` | FTPS default; unencrypted FTP opt-in. |
| `sftp` | `host`, `port`, `direction`, `local_path`, `remote_path`, `credential_id` | Local paths confined to transfer storage. |
| `proxmox` | `datacenter_id`, `action=inventory/start/shutdown/reboot`, `node`, `vmid` | Power actions require global gate and per-step approval. |
| `file` | `action=list/read/write/copy/move/mkdir/delete`, `path`, `source`, `content` | Confined to `data/transfers`; delete requires `approved=true`. |
| `set_variable` | `name`, `value` | Sets a per-run variable. |
| `delay` | `seconds` | Sleeps up to 300 seconds. |

## Example JSON configuration

```json
{"type":"matrix","name":"Notify operations","id":"matrix1",
 "config":{"homeserver":"https://matrix.example.org","room_id":"!ops:example.org",
 "message":"Backup run {{ run.id }} completed","credential_id":2},
 "on_success":"END","on_failure":"FAIL","retries":1}
```

**Credentials** are numeric references to entries stored in Vault, never cleartext values. `ssh` uses strict SSH host-key verification. SMTP `ssl=true` is implicit TLS, `ssl=false` uses STARTTLS. FTP `allow_plaintext=true` must be explicitly set for insecure legacy endpoints. A Proxmox `inventory` action is read-only; its destructive/power-related operations are separately gated.
