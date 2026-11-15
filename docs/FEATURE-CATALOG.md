# OrigamiTasks — catalogue fonctionnel, inspiré de VisualCron

Version cible : **v1.0+**. Ce document est un **inventaire de fonctionnalités à réaliser**, PAS une déclaration que toutes ces fonctions sont déjà implémentées.

Sources de référence :
- VisualCron : https://www.visualcron.com/tasks
- Documentation VisualCron : https://help.visualcron.com/
- Proxmox Datacenter Manager : https://pdm.proxmox.com/docs/introduction.html
- Proxmox réplication ZFS : https://github.com/proxmox/pve-docs/blob/master/pvesr.adoc
- Proxmox Backup Server : https://pbs.proxmox.com/docs/managing-remotes.html
- Matrix Client-Server API : https://spec.matrix.org/latest/client-server-api/
- Ansible Runner : https://docs.ansible.com/projects/runner/en/stable/

## Statuts

- **MVP** : types de tâches disponibles dans le produit actuel. Ils restent à valider sur systèmes réels.
- **P1** : priorité de développement ; conception et tests à créer.
- **P2** : extensions avancées.
- **R&D** : faisabilité, sécurité ou dépendances techniques à valider.

## 1. Orchestration (P1)

- [x] Création, classement par dossiers, tags, recherche globale, exécution manuelle.
- [x] Branches succès/échec, conditions simples, retries, délais, historique.
- [x] Planification cron et webhooks avec token.
- [ ] Éditeur DAG drag & drop, sous-workflows, variables typées et portée, références de sortie.
- [ ] Parallélisme fork/join, boucles foreach, matrices d'exécution, map/reduce.
- [ ] Calendriers métier, jours fériés, fenêtres de maintenance, exclusions, dépendances inter-jobs.
- [ ] Déclencheurs : présence/événements fichiers locaux/distants, email reçu, log/event Windows, SNMP trap, port/HTTP/SQL, changement de statut Proxmox/Zabbix/NetBox.
- [ ] Annulation propre, pause/reprise, timeout au niveau workflow, file prioritaire, quotas, verrous de concurrence.
- [ ] Simulations/dry-run, rollback/compensation (saga), workflows versionnés, révisions/diffs et validation avant publication.
- [ ] Approbation humaine, 4-eyes, validation d'impact et demande de changement liée à un ticket.
- [ ] Agents Windows/Linux avec certificat, gestion des capacités, routage et file persistante (à concevoir séparément).
- [ ] Haute disponibilité et planification centralisée Postgres/Redis, leader election, workers distincts, sauvegarde et reprise.

## 2. Processus, OS et langages

- [x] Bash et PowerShell local (désactivés par défaut), SSH, PowerShell via OpenSSH.
- [x] Ansible via runner SSH avec inventaire/playbook restreints (v0.2).
- [ ] Python, PHP, Node, Ruby, Perl, VBScript/JScript (via agents Windows), exécutable .NET/C#.
- [ ] Démarrer/arrêter/redémarrer services systemd/Windows, gestion de processus, remote kill, gestion tâches planifiées.
- [ ] Gestion packages APT/DNF/Pacman/WinGet, inventaire logiciel, redémarrages orchestrés.
- [ ] Exécution par utilisateur de service, politiques d'élévation, journal des commandes et restriction des actions.

## 3. Réseau, transferts, fichiers et stockage

- [x] HTTP/REST, FTP/FTPS, SFTP, SSH.
- [ ] SCP, WebDAV, SMB, NFS, rsync, S3, Azure Blob, SharePoint, OneDrive.
- [ ] Reprise de transferts, transferts multipart, checksum, comparaison, débit limité, règles de rétention.
- [ ] TCP/UDP, ping, check port, DNS, SNMP get/walk/trap, syslog, AMQP/RabbitMQ, MQTT.
- [ ] Copier/synchroniser/déplacer/list/read/write/touch/rename/delete, création et suppression de dossiers.
- [ ] Archive ZIP/7z/TAR/GZIP, chiffrement PGP, certificats, signatures, CRC/SHA, permissions ACL/ownership.
- [ ] Déclencheurs File System Watch et réconciliation d'événements après interruption.

## 4. Communication

- [x] SMTP texte (MVP), Matrix message non chiffré via bot token (v0.2).
- [ ] SMTP HTML/pièces jointes, distribution/copie, modèles, DKIM/PGP/S-MIME et politiques d'envoi.
- [ ] IMAP/POP3 fetch/move/delete/attachments/filters, déclencheur réception email.
- [ ] Matrix : markdown, pièces jointes, mentions, réactions, boutons d'approbation, réponse au thread et support E2EE via client adapté.
- [ ] Teams, Slack, Telegram, Discord, Signal via intégration officiellement maintenue, SMS/push.
- [ ] Webhooks signés entrants/sortants, paging d'astreinte et corrélation d'incidents.

## 5. Données, documents et entreprise

- [ ] SQL (Postgres, MySQL, MariaDB, MSSQL, Oracle), transactions, procédures stockées, exports.
- [ ] JSONPath/JQ, CSV/Excel, XML/XPath/XSLT, validation XML, PDF, génération rapports, images et OCR.
- [ ] SAP, PeopleSoft, SSIS, SSRS, Informatica, IBM Cognos, Microsoft Dynamics CRM (connecteurs spécifiques).
- [ ] Active Directory, LDAP, Exchange, Microsoft 365/Graph, Teams et SharePoint.
- [ ] AWS EC2, DynamoDB, SimpleDB, SNS ; Azure VM/Functions/Blob/Entra ; Google BigQuery.
- [ ] ITSM/CMDB : Redmine, GLPI, NetBox, Jira, ServiceNow, GitLab et GitHub.

## 6. Infrastructure en plus de VisualCron

- [x] Découverte Proxmox VE : nodes, QEMU, LXC et storages ; déclaration des datacenters via UI (v0.2).
- [x] Tâche Proxmox inventaire ; démarrer, shutdown, reboot sous verrou global + option validée (v0.2).
- [ ] Proxmox Datacenter Manager : découverte multi-clusters, état et historique des migrations.
- [ ] Migration planifiée intra/inter-clusters via API officielles, dry-run, mappage bridges/storage, suivi UPID et reprise sur échec.
- [ ] Proxmox Backup Server : backups à la demande, synchronisation remote, vérification, restauration-test et PRA.
- [ ] Proxmox DR : ZFS send/receive, baies synchronisées, Ceph RBD mirroring, politique de réplication par VM.
- [ ] Pure Storage : inventaire volumes, snapshots/réplication, alertes de capacité, API stockage et comparaison de LUN.
- [ ] Zabbix : lire/filtrer événements, maintenance, acquittements, escalades, déclencher remédiations sous approbation.
- [ ] NetBird : groupes/ACL peers, état des gateways, lancement de vérifications réseau.
- [ ] Docker/Podman/Kubernetes : déploiement, update et rollback, suivi readiness/health.
- [ ] Keycloak : événements identité, comptes, groupes, accès SSO/OIDC ; SSH CA / Vault entreprise.
- [ ] Tableaux de bord sur métriques, coûts et empreinte des workflows.

## 7. Sécurité et exploitation

- [x] Coffre chiffré, LDAP/AD, rôles, ACL par workflow, audit, mise à jour Git surveillée.
- [ ] RBAC par datacenter et ressource ; séparation administrateur de workflows / admin infrastructures.
- [ ] MFA/OIDC, limitation de sessions, export SIEM, clé de chiffrement rotative, coffre externe Vault.
- [ ] Secrets temporaires, brokers de credentials, blocage des sorties sensibles, scan de secrets/PII.
- [ ] Signature des plugins, permission manifest, isolation worker, sandbox, politique egress/hosts.
- [ ] SSO Keycloak, politique de moindre privilège, révision périodique des permissions et approbation des actions destructrices.
- [ ] Update signé + canary + backup + rollback testé ; observabilité OpenTelemetry/Prometheus.

## Priorisation proposée

1. **v0.2** (socle livré) : Matrix texte, Ansible SSH, datacenters Proxmox + inventaire, actions restreintes.
2. **v0.3** : tâches enregistrées comme plugins + UI catalogue, connecteurs Zabbix/NetBox/GLPI/Redmine + SMTP HTML et IMAP.
3. **v0.4** : agent d'exécution Windows/Linux, Ansible Runner isolé, workflow drag & drop et approbation.
4. **v0.5** : Proxmox/PDM pilotage migrations avec simulation, monitoring UPID ; PBS orchestration.
5. **v0.6** : moteur DR à intervalle, contrôle des snapshots et tests de restauration ; aucune promesse de RPO 0.
6. **v1.0** : HA, exploitation sécurisée, tests de sécurité et tests PRA réels, documentation d'exploitation.

## Proxmox DR — décision technique

**Trois opérations distinctes** :

| Fonction | Moteur envisagé | RPO attendu | Limite |
|---|---|---|---|
| Migration inter-clusters | PDM/API native | Aucun, si migration supportée et achevée | Transfert du rôle actif, pas de standby permanent |
| PRA horaire/incrémental | PBS, ZFS, snapshots baie/Pure, Ceph selon stockage | Dernière copie cohérente (1 h, 4 h...) | Il faut tester la restauration et l'exposition VM |
| PRA quasi-sync | Miroir stockage réseau avec fencing robuste | Potentiellement faible (secondes) | Contraintes lourdes de latence, réseau, cohérence et anti split-brain |

Un backup PBS synchronisé ne fournit pas une VM miroir instantanément prête à booter sans restauration. ZFS `pvesr` ne réplique nativement qu'à l'intérieur du même cluster. PDM 1.1 permet la migration inter-clusters, mais ne fournit pas actuellement un moteur DR complet maintenu à chaud sur deux sites.

Une copie arrêtée nécessite un nouveau VMID et une identité réseau isolée tant que l'originale tourne. Reprise sécurisée : vérifier panne / attestation de fencing / gel réplication / intégrité / disponibilité cible / lancement VM / reconfiguration réseau/DNS / tests applicatifs / confirmation. Retour arrière : procédure inverse avec réconciliation des écritures et rôle primaire.

## MVP intégré v0.2 — guide rapide

- Dans **Coffre-fort**, créer un secret Matrix `token=...`, un identifiant SSH Ansible ou un token Proxmox (username `user@pve!tokenid`, token `valeur`).
- Dans `.env`, configurer `MATRIX_ALLOWED_HOSTS=matrix.example.org`, `PROXMOX_ALLOWED_HOSTS=pve.example.org:8006` ; conserver HTTPS et une CA reconnue.
- Dans **Datacenters**, enregistrer une API Proxmox et choisir Découvrir ; utiliser les blocs Matrix, Ansible ou Proxmox dans l'éditeur de workflows.
- Runner Ansible : les chemins sont restreints à `/opt/origamitasks/ansible/playbooks/` et `/opt/origamitasks/ansible/inventories/`, sur un hôte SSH dédié. Configurer la politique de contrôle d'exécution sur ce runner.
- Le lancement de VM n'est accessible qu'avec `ALLOW_PROXMOX_ACTIONS=true` et l'option `approved` sur l'étape. Maintenir ce paramètre à `false` tant que les procédures et droits PVE n'ont pas été audités.
- Matrix est supporté en room non chiffrée uniquement à ce stade ; pour E2EE il faudra un client gérant les clés.
- Pas de réplication ni migration inter-DC implémentée dans v0.2 : concevoir et valider un moteur DR séparé avant exploitation.
