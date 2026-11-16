# Agents Linux et Windows — préversion v0.3.0-rc1

OrigamiTasks intègre des **agents portables sous forme de scripts**, pas des exécutables natifs signés : Python 3.9+ sous Linux/systemd (`agents/linux/agent.py`) et PowerShell 5.1+ sous Windows 10/11 et Server 2016+ (`agents/windows/agent.ps1`). Faire la recette sur des machines dédiées. Les agents contactent le serveur en **HTTPS sortant** ; aucun port entrant ne leur est nécessaire.

## Configuration du serveur

Renseigner dans `.env`, puis recréer le conteneur :

```dotenv
AGENT_SERVER_URL=https://tasks.example.org
ALLOW_AGENT_REMOTE_DEPLOY=true
AGENT_DEPLOY_ALLOWED_HOSTS=linux-test.example.org,win-test.example.org
ALLOW_AGENT_SCRIPTS=false
ALLOW_AGENT_MUTATIONS=false
ALLOW_DIRECTORY_WRITES=false
```

Les trois options d'activation sont sur **false** par défaut. `AGENT_SERVER_URL` doit être une origine HTTPS fiable ; les FQDN/IP autorisés à l'installation SSH sont exacts. L'enrôlement utilise un **jeton unique valable 15 minutes**, ensuite remplacé par un token d'agent individuel, durable et révocable. Pas encore de mTLS ou de paquets signés.

## Déploiement depuis OrigamiTasks

1. Enregistrer un compte SSH dédié dans le **Coffre-fort**. Préparer les empreintes dans `SSH_KNOWN_HOSTS` (par défaut `data/known_hosts` du conteneur). Les clés inconnues ou modifiées sont refusées.
2. Aller à **Administration → Agents Linux / Windows → Déployer par SSH**, renseigner nom, FQDN autorisé, plateforme, groupe, port et identifiant, puis confirmer explicitement l'installation.
3. Le serveur transfère code et `config.json` en SFTP, crée un jeton d'enrôlement et installe le service. Linux exige Python 3, systemd et root ou `sudo -n` non interactif ; le service tourne sous le compte **non privilégié** `origamitasks-agent`. Windows exige OpenSSH Server/SFTP en administrateur et PowerShell 5.1 ; la tâche planifiée tourne en **SYSTEM**.
4. L'agent contacte l'API HTTPS, s'enrôle et émet ses heartbeats. Actualiser la page : état **online** si contact dans les 90 dernières secondes.

Sans SSH, utiliser **Enrôlement manuel** puis copier immédiatement le jeton. Placer script et fichier `config.json` protégé dans le même dossier :

```json
{"server":"https://tasks.example.org","agent_id":12,"enrollment_token":"JETON_UNIQUE"}
```

Pour une recette ponctuelle : `python3 agent.py` (Linux) ou `powershell.exe -NoProfile -File agent.ps1` (Windows). Installer ensuite manuellement une unité systemd ou une tâche planifiée pour persister. Après l'enrôlement, le jeton devient un token opérationnel stocké sur la machine. La révocation dans l'interface **n'efface pas** le service distant.

## Blocs de workflow agents

Sélectionner **Agent Linux / Windows**, renseigner `agent_id`, `action`, éventuellement `script` ou `service`, `timeout`, `approved`. Les actions `inventory`, `disk`, `processes`, `service_status` sont en lecture. `script` exige `ALLOW_AGENT_SCRIPTS=true` ; `service_start`, `service_stop`, `service_restart` exigent `ALLOW_AGENT_MUTATIONS=true`, `approved=true` et les permissions locales. Le script est du Bash sous Linux, PowerShell sous Windows.

Chaque travail a un lease unique, un timeout et une sortie limitée. **Ne pas relancer automatiquement une opération non idempotente** : elle a pu s'exécuter avant une coupure de réseau. Garder retries=0 pour ces actions. Cette préversion ne propose ni binaire signé, ni auto-update, GPO, isolation multi-tenant, événement OS, popup Windows interactif, ni renouvellement mTLS.

## Dépannage

Linux : `systemctl status origamitasks-agent` et `journalctl -u origamitasks-agent -n 100`. Windows : `Get-ScheduledTask OrigamiTasksAgent` et événements Planificateur de tâches. Vérifier HTTPS/certificats, TCP 443 sortant, empreinte SSH, permissions de `config.json`, expiration du jeton et identité de l'agent. Voir [Tâches](tasks.md) et [Sécurité](security.md).
