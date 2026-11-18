# Linux and Windows agents — v0.3.0-rc1 preview

## Autonomous DR site agents: design target, not yet available

Current Linux Python/Windows PowerShell agents poll the OrigamiTasks controller over outbound HTTPS. If its VM is on SD and becomes unavailable, **they cannot receive new jobs**. These are not yet replication agents or failover arbitrators.

Under **Proxmox servers and clusters → DR sites and agents**, administrators can **name sites**, set primary/standby roles, link **multiple Proxmox clusters per site** (with multiple nodes inside each cluster), and associate existing Linux agents. The shared iSCSI + LVM storage profile is **operator-declared**, not verification of Pure LUNs or consistency groups.

The future design requires **standalone DR services**, SSH-installed using vault credentials onto **at least two independent servers per site** when redundancy is required; not necessarily on each PVE cluster node. Agents maintain locally signed policies, **mTLS** identity, durable checkpoints and ownership epochs. They should be able to continue replication during controller downtime, **but cannot promote a standby on their own**: an independent witness and verifiable effective SD fencing remain mandatory.

Planned version management: **signed package** delivery and verification, compatibility checks, **one agent at a time** upgrades, service health checks and version rollback on failure. Freeze updates during a failover. If the controller is down, agents continue their last verified version and reject unsigned updates.

**Important:** the existing DR site **Deploy SSH agent** button invokes only the **legacy agent installer**, which still uses `AGENT_DEPLOY_ALLOWED_HOSTS` and server approval settings. This does **not** create an autonomous PRA agent, or provide independent rolling updates; these remain separate engineering and validation milestones.

OrigamiTasks includes **portable script agents**, not signed native binaries: Python 3.9+ on Linux with systemd (`agents/linux/agent.py`) and Windows PowerShell 5.1+ on Windows 10/11 or Server 2016+ (`agents/windows/agent.ps1`). Test on dedicated machines first. Agents make **outbound HTTPS** connections; they do not listen for inbound connections.

## Server configuration

Set these variables in `.env` and recreate the application container:

```dotenv
AGENT_SERVER_URL=https://tasks.example.org
ALLOW_AGENT_REMOTE_DEPLOY=true
AGENT_DEPLOY_ALLOWED_HOSTS=linux-test.example.org,win-test.example.org
ALLOW_AGENT_SCRIPTS=false
ALLOW_AGENT_MUTATIONS=false
ALLOW_DIRECTORY_WRITES=false
```

All three enable flags default to **false**. `AGENT_SERVER_URL` must be a trusted HTTPS origin. `AGENT_DEPLOY_ALLOWED_HOSTS` is an exact hostname/IP list. Agent enrollment uses a **one-use 15-minute token** and then a distinct long-lived per-agent bearer token that can be revoked; mTLS and signed packages are not implemented.

## Single-server and bulk deployment

Under **Administration → Linux / Windows agents**, deployment credential choices are restricted to **SSH identities in the Vault**. SMTP/LDAP/API profiles cannot be used for SSH installation.

- **Single server:** enter target FQDN/port, Linux or Windows, agent group and a vault SSH identity, then explicitly approve.
- **Multiple servers:** choose **Multiple servers**, paste one FQDN per line (optional `fqdn:2222` port) or add an SSH inventory group. Choose the platform and SSH identity, then approve the entire batch.
- Each host must be present in `AGENT_DEPLOY_ALLOWED_HOSTS`, reachable over SSH/SFTP, and able to reach the trusted OrigamiTasks HTTPS endpoint.
- Installation is **queued and sequential**, with an independent status and error for each host. Restarted/abandoned attempts are never silently replayed. Maximum 100 target FQDNs per batch.
- **Proxmox:** cluster/VM inventory and operations use the **Proxmox API**, so an agent on a PVE host is **not required**. Only install a Linux agent directly on a hypervisor for a separate, validated system-management use case.

## Single-host and bulk deployment

In **Administration → Linux / Windows agents**, only **SSH vault identities** are offered for installation; SMTP and API credentials are excluded.

For **one host**, choose FQDN, SSH port, Linux/Windows, agent group and SSH identity, then confirm.

For **multiple hosts**, select **Multiple servers**, paste one FQDN per line (optional `fqdn:2222`) or add a group from the SSH inventory. Choose one target platform and SSH identity, then approve the batch. **Up to 100 hosts per batch.** Every host must be listed in `AGENT_DEPLOY_ALLOWED_HOSTS`. Installation is **queued and sequential**, with **independent per-host results**. Interrupted/pending jobs are **not silently replayed** after a restart. Raw SSH secrets are never returned.

**Proxmox** cluster/VM inventory and operations use the Proxmox API, so a PVE node agent is **not required**. Installing a Linux agent on a hypervisor is optional for a separately validated host-management use case.

## Deploy from the UI

1. Add a scoped SSH credential in **Vault**. Prepare a trusted `SSH_KNOWN_HOSTS` file (default `data/known_hosts`) in the control-plane container. Unknown or changed SSH keys are rejected.
2. Choose **Administration → Agents Linux / Windows → Deploy via SSH**. Set the target hostname from the allow-list, Linux/Windows, group, SSH port and credential, and explicitly confirm installation.
3. The server pushes the matching agent and `config.json` by SFTP, creates the enrollment, and installs the service. Linux requires Python 3, systemd and root or noninteractive `sudo -n`; the installed service runs as unprivileged `origamitasks-agent`. Windows requires administrative OpenSSH Server/SFTP and PowerShell 5.1; its installed Scheduled Task runs as **SYSTEM**.
4. The agent calls the HTTPS API, enrolls and starts sending heartbeats. Refresh the admin page; **online** means a heartbeat within 90 seconds.

If the machine lacks SSH, select **Manual enrollment** and copy the token immediately. Place the agent script and a restricted `config.json` in the same folder, e.g.:

```json
{"server":"https://tasks.example.org","agent_id":12,"enrollment_token":"ONE_USE_TOKEN"}
```

For an interactive smoke test, run `python3 agent.py` on Linux or `powershell.exe -NoProfile -File agent.ps1` on Windows. To persist a manual install, configure your own systemd unit or Scheduled Task. On enrollment, the token is replaced in `config.json` with the operational token. Revocation in the UI invalidates access but does **not** uninstall remote files/services.

## Workflow blocks

Select **Agent Linux / Windows** in the workflow editor and choose `agent_id`, `action`, optional `script` or `service`, `timeout`, `approved`. Actions: `inventory`, `disk`, `processes`, `service_status` (read only); `script` (requires `ALLOW_AGENT_SCRIPTS=true`); `service_start`, `service_stop`, `service_restart` (require `ALLOW_AGENT_MUTATIONS=true` and `approved=true` plus OS permissions). Linux uses Bash and Windows uses PowerShell.

Each task has a unique lease, bounded execution time and capped output. **Never automatically retry a non-idempotent action:** on a timeout, the agent may have executed it but failed to deliver the result. Disable workflow retries for such actions. The preview has no binary signing, auto-update, GPO deployment, multi-tenant sandbox, OS event listener, interactive Windows popup or mTLS certificate rotation.

## Troubleshooting

Linux: `systemctl status origamitasks-agent`, `journalctl -u origamitasks-agent -n 100`. Windows: `Get-ScheduledTask OrigamiTasksAgent`, Task Scheduler Operational log. Check HTTPS trust, outbound 443, SSH known-hosts, restricted `config.json`, token expiration and agent identity. See [Tasks](tasks.md) and [Security](security.md).
