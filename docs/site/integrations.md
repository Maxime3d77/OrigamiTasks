# Integrations

## Matrix / Element

1. Create a dedicated Matrix bot and join it to an **unencrypted** room.
2. Store an access token in Vault with key `token`.
3. Add `MATRIX_ALLOWED_HOSTS=matrix.example.org` to `.env` and restart.
4. Add a `matrix` step with `homeserver`, full `room_id` (`!room:server`), `message`, `credential_id`.

Messages are sent via `PUT /_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}` with deterministic transaction IDs. **No E2EE, encrypted attachments or interactive approvals yet.** Retry may still encounter room permission errors.

## Ansible

Use a **dedicated SSH runner**, not the API container, with Ansible installed. Place approved playbooks under `/opt/origamitasks/ansible/playbooks/` and inventories under `/opt/origamitasks/ansible/inventories/` on that runner. Register its verified SSH host key and service credential. Workflow step `ansible` accepts relative `playbook` and `inventory`, `limit`, `extra_vars` JSON, and `check` for dry-run. Restrict runner's user, filesystem access and sudo policy.

Example configuration:

```json
{"host":"runner.example.org","playbook":"linux/patch.yml","inventory":"prod/hosts.yml",
 "limit":"webservers","check":true,"extra_vars":{"reboot":false},"credential_id":4}
```

## Email / SMTP

Configure `host`, `port`, sender, recipient, subject, body and vault credentials. Use port 587 with STARTTLS or 465 with implicit TLS. SMTP supports an optional HTML alternative (`html_body`) and up to ten attachments (`attachments`) totalling 10 MB from `data/transfers`. Advanced templating, IMAP triggers and S/MIME are not yet available.

## HTTP and file transfer

HTTP REST calls support headers, bearer token and request body. FTP defaults to FTP over TLS. SFTP authenticates over strict known-host SSH and transfers only into/out of `data/transfers`. For large or untrusted transfers, use an isolated runner.

## Proxmox

See [Proxmox & Disaster Recovery](proxmox.md) for discovery, security and detailed replication decisions.
