# Troubleshooting

## Service does not start

Check `docker compose logs app`; a missing or invalid `VAULT_KEY` prevents startup intentionally. Run the bootstrap script, verify permissions for UID 10001, verify data disk capacity and image compatibility. Do not regenerate a Vault key for an existing encrypted database.

## Browser login fails

Check HTTPS/reverse proxy, `COOKIE_SECURE`, browser cookies, NTP, local admin password, LDAP URI, CA chain, bind credentials and expected group membership. Repeated failed logins can trigger throttling; check audit entries.

## SSH/SFTP task fails

Verify endpoint reachability and port, service account, host fingerprint, SSH private key format and that no password is embedded in workflow JSON. Windows must have OpenSSH Server enabled and the intended `powershell.exe` accessible to that user.

## Matrix message fails

Check `MATRIX_ALLOWED_HOSTS`, HTTPS, bot token, room membership, full room ID, room permissions and whether E2EE is enabled (not supported currently).

## Proxmox discovery returns an error

Check HTTPS hostname and port allow-list, token syntax (`user@realm!tokenid`), token privileges, API health, CA trust and access to `/api2/json/cluster/resources`. A read-only token may discover but cannot power-cycle VMs.

## No runs after cron

Verify job is enabled, schedule is valid, server clock, scheduler instance count (exactly one), next-run timestamp, and engine logs. Avoid multiple API replicas with SQLite.

## Update request remains queued

The host-side update worker may be absent or unable to reach its allowed Git origin. Check systemd status, worker journal, allowed branch, permissions and whether the repository checkout is clean. Do not run a root update worker with unrestricted Git sources.

## Admin login submits a password in a URL

If the browser navigates to `/?username=...&password=...` or access logs show `GET /` with `password=`, **stop attempting login**. The login JavaScript handler was not attached: the server has not received a `POST /api/login` request, irrespective of the password stored in the database. This was fixed in **v0.3.1-rc2**:

- The HTML form uses `method="post"`, so a native submit cannot include passwords in a URL.
- A standalone `/assets/login.js` handles JSON POST login even if the main SPA fails to initialize.
- JavaScript/CSS URLs are versioned to force a fresh browser asset request.
- The main application installs its submit listener before other initialization work.

Confirm `curl -fsS http://127.0.0.1:8091/api/health` returns `0.3.1-rc2`; browser Network tools should show **POST /api/login**, never GET with credentials. Refresh with `Ctrl+Shift+R` after upgrading.

**Credential exposure**: any password embedded in a URL may be stored in browser history, application/reverse-proxy access logs, or their backups. Rotate all affected credentials, restrict access to logs and handle retention using your incident-response policy. Never publish raw URLs or logs containing passwords.

Updating `ADMIN_PASSWORD` in `.env` does not reset an existing SQLite admin hash; only investigate admin credential recovery after a real `POST /api/login` fails.
