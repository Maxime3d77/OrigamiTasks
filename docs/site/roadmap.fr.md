# Couverture fonctionnelle et planning

**Ne pas confondre catalogue et implémentation.** VisualCron dispose de nombreux adaptateurs et déclencheurs. OrigamiTasks est encore une version préproduction partielle. L'[inventaire détaillé](https://github.com/Maxime3d77/OrigamiTasks/blob/main/docs/FEATURE-CATALOG.md) regroupe les fonctionnalités VisualCron et extensions infrastructure.

| Fonction | Statut v0.2 | Suite prévue |
|---|---|---|
| Édition, recherche, tags, dossiers, manuel, cron, webhook | Intégré | Calendriers métier et événements |
| Succès/échec, conditions, reprises, variables, audit | Intégré (graphe séquentiel) | Parallélisme DAG, approbations, versions |
| SSH/PowerShell Windows, fichiers locaux confinés, SFTP/FTPS, HTTP, SMTP HTML/pièces jointes | Intégré, tests réels à faire | Plus de familles natives |
| Messages Matrix texte | Intégré | Threads, pièces jointes, E2EE, approbation |
| Ansible sur runner SSH | Intégré | Worker Ansible Runner isolé et événements structurés |
| Découverte Proxmox, diagnostic PRA en lecture seule et alimentation VM encadrée | Intégré | Étude PRA, PBS et PDM |
| Agents Windows/Linux natifs | **Non intégré** | Enrôlement, mTLS, leases et tâches OS |
| Réplication et bascule inter-clusters | **Non intégré** | PRA par stockage et validations anti split-brain |
| Équivalence complète VisualCron | **Non intégrée** | Projet multi-versions |

## Versions proposées

- **v0.3** : catalogue enrichi, emails avec modèles, intégrations, droits par credential et contrôle documentaire.
- **v0.4** : agents multiplateformes avec enrôlement, exécution isolée, validation et versionnement.
- **v0.5** : planification de migrations PDM, suivi et opérations PBS backup/sync/restore.
- **v0.6** : plugins PRA par technologie de stockage, snapshots cohérents, restauration test et contrôle de bascule.
- **v1.0** : durcissement, conception HA, tests d'intégration/sécurité et API publiques stabilisées.

Chaque nouvelle fonctionnalité exige la mise à jour EN/FR, des tests et du changelog.
