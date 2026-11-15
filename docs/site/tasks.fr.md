# Référence des tâches disponibles (v0.2)

Cette page liste les **adaptateurs réellement intégrés**, pas toute la liste VisualCron. La configuration de chaque étape accepte de nombreuses chaînes interpolées.

| Type | Paramètres | Précisions |
|---|---|---|
| `ssh` | `host`, `port`, `platform=linux/windows`, `script`, `credential_id`, `timeout` | Empreinte hôte vérifiée ; Windows via PowerShell encodé. |
| `shell` | `script`, `timeout` | Exécution locale **désactivée** par défaut. |
| `powershell` | `script`, `timeout` | `pwsh` local, désactivé par défaut. |
| `ansible` | `host`, `credential_id`, `playbook`, `inventory`, `limit`, `extra_vars`, `check` | Appelle `ansible-playbook` sur un runner SSH dédié. |
| `matrix` | `homeserver`, `room_id`, `message`, `credential_id` | Message en clair avec un bot ; pas de support E2EE. |
| `email` | `host`, `port`, `ssl`, `from`, `to`, `subject`, `body`, `credential_id` | SMTP STARTTLS ou TLS, texte ou `html_body` facultatif et jusqu’à 10 pièces jointes / 10 Mo depuis transfers/. |
| `http` | `method`, `url`, `headers`, `body`, `credential_id` | Token Bearer possible. Hôtes à considérer de confiance. |
| `ftp` | `host`, `port`, `direction`, `local_path`, `remote_path`, `credential_id` | FTPS par défaut, FTP clair sur autorisation explicite. |
| `sftp` | `host`, `port`, `direction`, `local_path`, `remote_path`, `credential_id` | Chemins locaux confinés au dossier transferts. |
| `proxmox` | `datacenter_id`, `action=inventory/start/shutdown/reboot`, `node`, `vmid` | Actions VM conditionnées par verrou global et confirmation. |
| `file` | `action=list/read/write/copy/move/mkdir/delete`, `path`, `source`, `content` | Limité à `data/transfers` ; delete exige `approved=true`. |
| `set_variable` | `name`, `value` | Variable pendant l'exécution. |
| `delay` | `seconds` | Pause de 300 secondes maximum. |

## Exemple JSON

```json
{"type":"matrix","name":"Notifier l'exploitation","id":"matrix1",
 "config":{"homeserver":"https://matrix.example.org","room_id":"!ops:example.org",
 "message":"Sauvegarde {{ run.id }} terminée","credential_id":2},
 "on_success":"END","on_failure":"FAIL","retries":1}
```

**Credentials :** références numériques au coffre, jamais de valeur en clair. `ssh` vérifie strictement les clés hôtes. SMTP `ssl=true` correspond à TLS implicite, `ssl=false` à STARTTLS. FTP `allow_plaintext=true` n'est utilisable qu'en cas de nécessité. Pour Proxmox, `inventory` est en lecture seule ; les autres actions sont soumises à deux validations.
