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

Configurer hôte, port, expéditeur, destinataire, sujet, corps et credentials. Utiliser 587 STARTTLS ou 465 TLS implicite. En v0.2, les emails acceptent un corps texte, un corps HTML facultatif (`html_body`) et des pièces jointes (`attachments`) limitées à 10 fichiers / 10 Mo dans `data/transfers`. Les modèles avancés, les emails entrants IMAP et le chiffrement S/MIME sont prévus.

## HTTP et transferts

Les appels HTTP REST acceptent en-têtes, bearer token et corps. FTP est chiffré TLS par défaut. SFTP utilise SSH à clé hôte vérifiée et n'accède qu'à `data/transfers` côté application. Prévoir un runner isolé pour les transferts volumineux ou non fiables.

## Proxmox

Voir [Proxmox et reprise d'activité](proxmox.md) pour la découverte, les autorisations et la réplication.
