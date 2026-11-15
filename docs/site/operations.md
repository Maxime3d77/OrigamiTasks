# Operations, upgrades and recovery

## Health and diagnostics

```bash
curl -fsS http://127.0.0.1:8080/api/health
docker compose ps
docker compose logs --tail=200 -f app
```

Observe job history and audit records in the UI. Keep the API on one instance: current SQLite queue and in-process scheduler do **not** support horizontal multi-instance scheduling.

## Update from Git (UI)

The **Updates** page sends `check` or `apply` requests to the database; it does not run Git inside the API container. A separately installed `ops/update-worker.py` on the host performs tightly scoped Git operations. Read `ops/origamitasks-updater.service`, set `GIT_ALLOWED_ORIGIN` and an approved branch, then test in staging.

**Safe release checklist:** protected Git branch, reviewed commit, CI green, backup of database and `.env`/Vault key, check update, apply to pre-production, test, then production. Avoid automatic updates of arbitrary untrusted refs or workflows without approval.

## Backup

Stop the app cleanly or take SQLite's **consistent backup** API; do not only copy the main `.db` file while WAL is active. Back up `data/` including transfer assets/known_hosts as needed, `.env`, SSL configuration and an **independent protected copy** of `VAULT_KEY`. Never store backup secrets alongside publicly accessible archives.

Example when the service is stopped:

```bash
docker compose down
sudo tar -czf /root/origamitasks-backup-$(date +%F).tar.gz data .env
sudo chmod 600 /root/origamitasks-backup-*.tar.gz
docker compose up -d
```

## Restore and rollback

1. Stop services, capture diagnostics, preserve current failed state.
2. Check restore media integrity and retrieve the **matching** Vault key.
3. Restore data and environment with correct ownership; checkout the last tested Git release.
4. Rebuild/restart containers, verify health, log in and test a non-destructive workflow.
5. Review interrupted or queued runs manually before resumption; side effects may already have happened.

## Monitoring

Monitor API health, container restarts, filesystem space, failed jobs, oldest queued run, LDAP connectivity, token expiration and integration availability. Export to Zabbix/Prometheus is a roadmap item; until then poll health and use central syslog for host logs.
