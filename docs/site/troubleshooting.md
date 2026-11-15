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
