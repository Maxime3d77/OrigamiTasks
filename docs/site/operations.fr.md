# Exploitation, mises à jour et restauration

## Santé et diagnostic

```bash
curl -fsS http://127.0.0.1:8080/api/health
docker compose ps
docker compose logs --tail=200 -f app
```

Consulter l'historique et l'audit dans l'interface. Conserver **une seule instance API** : le scheduler mémoire et la file SQLite ne supportent pas une architecture HA multi-instances.

## Mise à jour Git depuis le web

L'écran **Mises à jour** envoie une demande `check` ou `apply` enregistrée en base ; aucun `git pull` n'est lancé dans le conteneur API. Le service hôte indépendant `ops/update-worker.py` réalise les opérations Git limitées. Examiner `ops/origamitasks-updater.service`, définir `GIT_ALLOWED_ORIGIN` et la branche autorisée, et tester en préproduction.

**Recette :** branche Git protégée, commit relu, CI verte, sauvegarde de la base et du `.env`/clé de coffre, vérification, application en PP, tests et déploiement en PROD. Pas de mise à jour automatique d'une référence inconnue ou de workflows sans approbation.

## Sauvegarde

Arrêter proprement ou utiliser l'API **backup cohérent** de SQLite ; copier seulement `.db` pendant que WAL est actif peut être incomplet. Sauvegarder `data/`, transferts/known_hosts si besoin, `.env`, TLS et une copie **séparée protégée** de `VAULT_KEY`. Ne jamais exposer ces archives publiquement.

Exemple avec services arrêtés :

```bash
docker compose down
sudo tar -czf /root/origamitasks-backup-$(date +%F).tar.gz data .env
sudo chmod 600 /root/origamitasks-backup-*.tar.gz
docker compose up -d
```

## Restauration et rollback

1. Arrêter les services, conserver le diagnostic et l'état défaillant.
2. Vérifier l'intégrité des sauvegardes et récupérer la **bonne** clé Fernet.
3. Restaurer les données et paramètres, respecter UID/GID ; revenir au dernier tag Git testé.
4. Reconstruire/redémarrer, vérifier l'API, se connecter et lancer un workflow non destructif.
5. Examiner les exécutions interrompues avant reprise : des effets peuvent déjà avoir eu lieu.

## Supervision

Contrôler API health, redémarrages, espace disque, échecs, ancienneté des runs en file, LDAP, expiration tokens et disponibilité des intégrations. L'export Zabbix/Prometheus est prévu ; temporairement, interroger health et centraliser les journaux hôte.
