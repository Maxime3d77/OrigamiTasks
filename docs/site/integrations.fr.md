# Intégrations

## Matrix / Element

1. Créer un bot Matrix dédié et le joindre à une room **non chiffrée**.
2. Stocker son token d'accès dans le coffre sous la clé `token`.
3. Configurer `MATRIX_ALLOWED_HOSTS=matrix.example.org` dans `.env`, puis redémarrer.
4. Ajouter un bloc `matrix` avec `homeserver`, `room_id` complet (`!room:server`), `message`, `credential_id`.

L'envoi utilise `PUT /_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}`, avec identifiant de transaction déterministe. **Pas encore de chiffrement E2EE, pièces jointes chiffrées ou approbation interactive.** Les permissions de la room doivent permettre au bot d'envoyer.

## Ansible

Installer Ansible sur un **runner SSH dédié**, jamais dans le conteneur API. Déployer les playbooks autorisés dans `/opt/origamitasks/ansible/playbooks/` et les inventaires dans `/opt/origamitasks/ansible/inventories/`. Déclarer le fingerprint SSH et le credential du compte de service. Le bloc `ansible` accepte `playbook`, `inventory` relatifs, `limit`, `extra_vars` JSON, et `check` pour la simulation. Restreindre sudo et les accès du compte runner.

Exemple :

```json
{"host":"runner.example.org","playbook":"linux/patch.yml","inventory":"prod/hosts.yml",
 "limit":"webservers","check":true,"extra_vars":{"reboot":false},"credential_id":4}
```

## Email / SMTP

Dans **Coffre-fort**, créer une connexion **SMTP** : serveur/FQDN, port (587 STARTTLS, 465 SSL/TLS direct ou 25 pour un relais interne), protocole de sécurité, activation ou non de l'authentification, identifiant/mot de passe si besoin et expéditeur par défaut. Les secrets sont chiffrés. Le bouton **Tester** ouvre un assistant : contrôle de connexion sans envoi par défaut, ou option **Envoyer un e-mail de test** avec destinataire, objet et contenu. Le test retourne un **Message-ID** pour retrouver le mail reçu ; l'acceptation SMTP n'atteste pas de la remise dans la boîte. L'authentification est interdite sans TLS.

Dans le bloc workflow **Envoyer un email**, sélectionner cette connexion puis renseigner les destinataires, le sujet et le corps. Hôte, port et expéditeur peuvent être laissés vides pour réutiliser le coffre. Les champs techniques de la tâche restent disponibles pour des surcharges explicites. En v0.2, les emails acceptent un corps texte, un corps HTML facultatif (`html_body`) et des pièces jointes (`attachments`) limitées à 10 fichiers / 10 Mo dans `data/transfers`. Les modèles avancés, les emails entrants IMAP et le chiffrement S/MIME sont prévus.

## HTTP et transferts

Les appels HTTP REST acceptent en-têtes, bearer token et corps. FTP est chiffré TLS par défaut. SFTP utilise SSH à clé hôte vérifiée et n'accède qu'à `data/transfers` côté application. Prévoir un runner isolé pour les transferts volumineux ou non fiables.

## Proxmox

Voir [Proxmox et reprise d'activité](proxmox.md) pour la découverte, les autorisations et la réplication.
