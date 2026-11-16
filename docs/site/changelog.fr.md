# Historique des modifications

## 0.3.0-rc1 — 15 novembre 2026

- Logo officiel d'origine rétabli dans README et la documentation bilingue.
- Agents portables Linux Python/systemd et Windows PowerShell, HTTPS sortant, enrôlement, révocation, heartbeat, file et leases.
- Déploiement par SSH/SFTP depuis l'administration avec liste d'hôtes autorisés et clés SSH vérifiées.
- Blocs de workflow `agent` et `directory` ; les écritures LDAP/AD exigent une autorisation explicite.
- Documentation EN/FR actualisée. Une recette sur Windows et LDAP/AD réels reste nécessaire.

## 0.2.0 — 15 novembre 2026

- Bloc Matrix texte utilisant un token chiffré.
- Bloc Ansible sur runner SSH dédié ; restrictions sur les chemins des playbooks et inventaires.
- Opérations fichiers confinées à `data/transfers` et SMTP HTML/pièces jointes.
- Diagnostic PRA Proxmox en lecture seule : analyse des types de stockage VM.
- Déclaration et découverte des datacenters Proxmox : nœuds, VM, LXC, stockages ; actions d'alimentation encadrées.
- Catalogue de blocs avec recherche et inventaire des fonctionnalités prévues.
- Ajout d'une **documentation multipage anglais par défaut et français**, avec contrôle CI de cohérence.

**Restant :** pas encore d'agent natif Windows/Linux, équivalence complète VisualCron, réplication inter-clusters temps réel, automatisation PRA PBS/PDM ou garantie de compatibilité production.

## 0.1.0 — 15 novembre 2026

- Premier ordonnanceur, UI sombre/claire/cyberpunk, moteur à branches, scripts locaux/SSH, SMTP/HTTP/FTP/SFTP, coffre et groupes LDAP.
- Moteur de mise à jour Git côté hôte, installeur, premiers tests et documentation sécurité.
