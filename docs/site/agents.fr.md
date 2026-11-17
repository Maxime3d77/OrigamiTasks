# Agents Linux et Windows — préversion v0.3.0-rc1

## Paramètres HTTPS des agents et rôles Windows (0.3.18-rc1)

Avant de déployer un agent, aller dans **Agents Linux et Windows → Paramètres de connexion des agents**, renseigner **l'URL HTTPS publique** de la console puis cliquer **Enregistrer l'URL des agents**. L'adresse est préremplie avec l'origine HTTPS du navigateur si aucune URL n'est encore configurée. Exemple : `https://origamitasks.example.org` derrière Nginx, qui proxifie en interne vers `http://127.0.0.1:8091`. Ne pas inscrire `127.0.0.1:8091` dans la configuration des agents : cette adresse pointerait vers **chaque agent lui-même**, pas la console. L'URL enregistrée dans l'interface est effective immédiatement pour les **nouveaux déploiements** ; `AGENT_SERVER_URL` dans `.env` n'est plus qu'une valeur de repli facultative.

Le FQDN doit être joignable depuis les machines Linux/Windows, avec un certificat HTTPS dont la CA est reconnue par le système sur chaque machine. La vérification TLS du client n'est pas désactivée. L'adresse n'est pas automatiquement changée sur les agents déjà installés. Leur URL d'origine est mémorisée lors de la nouvelle installation et utilisée pour sécuriser la désinstallation. Pour un ancien agent installé avant cette version, le formulaire de désinstallation permet de renseigner manuellement son **ancienne URL HTTPS exacte**, que le serveur distant doit confirmer dans `config.json`.

Lors du déploiement depuis **DC Proxmox → Agents du cluster** ou **Sites PRA → Déployer sur les nœuds**, l'assistant affiche cette URL. Si elle est absente, il interdit le lancement et propose un raccourci vers sa configuration. Le module **PRA préparatoire** n'est **plus coché par défaut**. Il conserve uniquement un état local et un heartbeat passif ; **pas de réplication de VM, pas de restauration, pas de fencing ni de failover** à ce stade.

**Windows :** l'agent est **Standard uniquement**, pour exécuter des workflows Windows, scripts PowerShell, opérations sur les processus/services et autres actions de poste/serveur. Les rôles **Proxmox et PRA sont interdits par l'API et masqués dans l'interface**. La console peut déployer et désinstaller cet agent via SSH sur Windows avec les prérequis d'administration existants.

## Agent unique avec modules administrés (0.3.16-rc1)

L'installation OrigamiTasks devient **un seul agent par machine**, administrable depuis **Agents Linux et Windows → Configurer modules**. Trois fonctions sont visibles :
- **Standard**, toujours actif pour les commandes de workflows existantes.
- **Proxmox**, module d'affectation au site/cluster, sans copie de token PVE sur l'agent ; les fonctions Proxmox opérationnelles continuent à appeler l'API depuis la console.
- **PRA passif**, uniquement Linux : superviseur local préparatoire pour un futur moteur PRA, **sans réplication de VM et sans bascule**.

Pour activer Proxmox ou PRA, rattacher d'abord l'agent Linux à un **site PRA nommé** contenant au moins un cluster Proxmox. Pour PRA, le rôle du site doit être **principal** ou **secours**. Cocher ensuite les modules et enregistrer. L'agent version **0.3.2** reçoit la politique sur sa prochaine interrogation HTTPS, l'enregistre atomiquement en local et retourne un numéro de version effectivement appliquée. L'interface distingue **demande en attente**, **configuration appliquée**, **service PRA passif** et **agent non à jour**. Les agents déjà installés en 0.3.1 ou antérieure ne prennent pas automatiquement en charge le module PRA.

Sous Linux, le même déploiement via clé SSH installe `origamitasks-agent.service` (standard) et `origamitasks-agent-pra.service` (surveillance passive). Le deuxième service fonctionne de manière indépendante du processus standard et de la console après avoir reçu sa politique ; il ne dispose d'aucun mécanisme de promotion des VM. Il écrit uniquement son état local (`modules.json` et `pra-state.json` sous `/opt/origamitasks-agent`) : aucune réplication, aucune manipulation de stockage, aucune requête API Proxmox et aucune action de fencing. Même si la console est sur SD et tombe, le processus passif reste localement vivant sur AU, **mais il ne garantit pas encore la continuité PRA**. Les permissions actuelles sont des tokens HTTPS, pas encore mTLS ; les politiques ne sont pas encore signées : avant de permettre des actions sensibles, ajouter signature, témoin externe, preuve de fencing et tests de partition réseau.

Les deux services sont installés, mis à l'inventaire et désinstallés via la même identité d'agent. Le superviseur a une limite système de **64 Mio / 10 % d'un cœur**, et l'agent standard garde **192 Mio / 20 %**. Ces plafonds ne sont pas une mesure de consommation normale. Sous Windows, **Standard et Proxmox** sont configurables ; **PRA Linux seulement** pour cette première base. Les agents préexistants doivent être réinstallés de manière contrôlée pour obtenir la version 0.3.2 ; **il n'existe pas encore de bouton de mise à jour signée automatique**.

## Tableau de bord, consommation et désinstallation (v0.3.15)

La **Vue d'ensemble** dispose maintenant de trois onglets : **Tâches**, **Agents** et **DC Proxmox / PRA**. L'onglet Agents indique l'état de chaque agent (dernier heartbeat, version), ainsi que le CPU et la RAM du **processus agent uniquement** lorsque l'agent version 0.3.1 ou supérieure remonte ces métriques. Cela ne mesure pas l'utilisation des scripts enfants, des VM ou des services gérés. Des valeurs vides signifient « non remontées », pas zéro.

L'intervalle de polling des nouveaux agents est de **10 secondes** par défaut, configurable dans `config.json` avec `poll_interval_seconds` entre 5 et 60 secondes. En régime normal, un agent ne fait pas de travail intensif ; l'utilisation exacte dépend du serveur et des tâches déclenchées. Les nouveaux services Linux déployés avec cette version ont les directives `CPUQuota=20%`, `MemoryMax=192M` et `TasksMax=48` : ces **limites systemd concernent le groupe de processus du service** et peuvent interrompre ou ralentir des scripts lourds. Ce ne sont pas des mesures de consommation garanties. Les installations précédentes ne reçoivent pas ces limites tant qu'elles n'ont pas été remplacées/ajustées.

L'écran **Agents Linux et Windows** distingue **Révoquer le jeton** (l'agent reste installé) et **Désinstaller via SSH** (retirer le service/tâche et le dossier agent sur le serveur, puis révoquer le token). La désinstallation demande une identité SSH du coffre, le FQDN, le port, la saisie du **nom exact de l'agent** et confirmation. L'empreinte SSH doit être **déjà approuvée** et inchangée ; le hostname doit correspondre à celui de l'agent, et le fichier `config.json` distant doit contenir le même agent ID et l'URL de la console. En cas de refus ou échec SSH, l'opération échoue **sans annoncer une suppression réussie** ; la révocation reste disponible séparément. La suppression de l'agent Windows nécessite des droits administrateur et celle de Linux nécessite root ou sudo non interactif.

**Niveau de sécurité :** HTTPS sortant avec validation TLS, token d'enrôlement à usage unique puis token individuel révocable, coffre chiffré côté console ; Linux non privilégié avec restrictions systemd. En revanche, **pas encore de mTLS, mise à jour signée ni autonomie PRA** ; l'agent Windows actuel utilise une tâche SYSTEM et mérite un durcissement spécifique. Éviter l'autorisation de scripts arbitraires sur des hôtes critiques.

## Agents PRA autonomes : architecture cible, non encore disponible

Les agents actuels (Python Linux/PowerShell Windows) sollicitent la console OrigamiTasks en HTTPS pour recevoir leurs tâches. Si la VM de la console est sur SD et tombe, **ils ne peuvent plus recevoir de nouvelles commandes**. Ils ne sont pas encore des agents de réplication ni des arbitres de bascule.

Dans la nouvelle section **Serveurs & clusters Proxmox → Sites PRA et agents**, on peut **nommer les sites**, leur attribuer le rôle principal/secours, rattacher **plusieurs clusters Proxmox par site** (chaque cluster possède plusieurs nœuds) et lier les agents Linux existants. Le profil iSCSI + LVM est **déclaratif** : il ne valide ni les LUNs Pure ni les groupes de cohérence.

La cible future sera un **service PRA indépendant** de la console, installé par SSH avec une identité du coffre sur **au moins deux machines indépendantes par site** lorsque la haute disponibilité est recherchée (pas nécessairement un agent sur chaque nœud Proxmox). Les agents connaîtront localement leurs politiques signées, une identité **mTLS**, leurs derniers points de restauration et l'epoch de propriétaire. Ils devront pouvoir poursuivre la synchronisation si la console disparaît, **mais jamais décider seuls d'activer une VM** : témoin indépendant et preuve réelle de fencing de SD restent obligatoires.

La gestion des mises à jour visée : téléchargement d'un paquet **signé**, vérification de l'empreinte et de la signature, contrôle de compatibilité protocole, distribution **agent par agent**, vérification du service et rollback de la version précédente si elle ne démarre pas. Pendant un failover, les mises à jour doivent être gelées. Si la console tombe, les agents conservent leur version opérationnelle et n'acceptent aucun exécutable non signé.

**À ne pas confondre** : le bouton actuel **Déployer agent SSH** depuis le site PRA appelle l'installateur **standard**, qui utilise encore `AGENT_DEPLOY_ALLOWED_HOSTS` et les autorisations serveur existantes. Cela ne crée **pas** encore un agent PRA autonome, ni une chaîne de mise à jour indépendante. Cette fonctionnalité doit être développée et testée avant toute bascule automatique.

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

## Déploiement individuel et par liste de serveurs

Dans **Administration → Agents Linux / Windows**, les comptes proposés pour le déploiement sont **uniquement les identités SSH du coffre**. Les profils SMTP, LDAP et API ne sont jamais des comptes SSH valides.

- **Un serveur** : renseigner le FQDN, le port, le système Linux/Windows, le groupe d'agents et une identité SSH ; confirmer l'installation.
- **Plusieurs serveurs** : cliquer sur **Plusieurs serveurs**, coller les FQDN (un par ligne, port facultatif sous la forme `fqdn:2222`) ou utiliser **Ajouter** à partir d'un groupe de l'inventaire SSH. Choisir une identité SSH et la plateforme des cibles, puis confirmer le lot.
- Chaque serveur doit figurer dans `AGENT_DEPLOY_ALLOWED_HOSTS` ; le serveur de contrôle doit pouvoir établir une connexion SSH/SFTP et l'agent doit pouvoir joindre l'URL HTTPS de l'application.
- Les déploiements sont mis en file, traités **un serveur à la fois**, avec un statut indépendant par cible : en attente, en cours, installé, échec. Ils sont audités. **Après redémarrage, les opérations interrompues ne sont pas relancées sans confirmation.** Maximum : 100 FQDN par lot.
- **Proxmox** : les opérations sur les VM et l'inventaire des DC Proxmox utilisent directement l'API Proxmox ; un agent sur l'hyperviseur n'est **pas obligatoire**. Installer un agent Linux sur un nœud Proxmox seulement pour des besoins précis hors API, après recette, avec un compte au moindre privilège.

## Déploiement sur un ou plusieurs serveurs

Dans **Administration → Agents Linux / Windows**, le compte proposé pour déployer les agents est obligatoirement une **identité SSH du coffre**. Les profils SMTP et API ne sont pas affichés.

Pour **un serveur**, choisir son FQDN, son port, Linux/Windows, son groupe et le compte SSH, puis confirmer.

Pour **plusieurs serveurs**, utiliser **Plusieurs serveurs**, coller les FQDN un par ligne (port facultatif `fqdn:2222`) ou ajouter un groupe de serveurs depuis l'inventaire SSH. Sélectionner une plateforme commune et une identité SSH, puis confirmer le lot. **Maximum 100 machines par lot.** Chaque FQDN doit être présent dans `AGENT_DEPLOY_ALLOWED_HOSTS`. Les installations sont mises en file **séquentiellement** et les résultats sont affichés **par machine**. Un redémarrage interrompt les tâches en attente/en cours ; elles ne sont **pas relancées automatiquement**. Les erreurs ne dévoilent pas les secrets SSH.

**Pour Proxmox**, les opérations concernant les clusters, les VM et l'inventaire s'effectuent via l'API Proxmox : aucun agent sur les hyperviseurs n'est nécessaire. L'installation d'un agent Linux sur un nœud PVE reste facultative pour une tâche système précise, après recette.

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
