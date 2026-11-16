# Historique des modifications

## 0.3.9-rc1 — 16 novembre 2026

- **SSH :** nouveau mode par défaut `SSH_HOST_KEY_POLICY=accept-new` : la première clé publique de chaque serveur est mémorisée dans `known_hosts`, sans saisie manuelle ; toute clé modifiée est bloquée. Le mode `strict` reste disponible.
- **LDAP :** correction des recherches OpenLDAP/AD avec schéma personnalisé, évitant l'erreur locale `LDAPObjectClassError`. La CA importée vérifie toujours LDAPS/STARTTLS ; prise en charge des fichiers PEM avec BOM UTF-8.
- **Interface/tests :** plus de validation obligatoire de chaque empreinte SSH ; l'import de CA décoche le contournement TLS dans le formulaire. Tests de non-régression et documentation EN/FR mis à jour.

## 0.3.8-rc1 — 16 novembre 2026

- **SSH :** approbation explicite depuis le navigateur d'une clé d'hôte publique, après comparaison indépendante et saisie exacte de l'empreinte SHA256. Les clés d'hôte déjà connues mais modifiées restent bloquées ; les identités SSH utilisateur et leurs clés privées ne sont pas modifiées.
- **LDAP :** import des bundles CRT/PEM contenant une CA et des certificats serveur ; conservation des CA uniquement, comptage et affichage des certificats non-CA exclus.
- **Interface :** affichage lisible des résultats de l'import et explication de la case de contournement TLS grisée. Tests de non-régression SSH/LDAP et documentation bilingue.



## 0.3.7-rc1 — 16 novembre 2026

- **SSH :** import d'une clé privée utilisateur OpenSSH/PEM dans le coffre, erreurs différenciées (clé illisible, empreinte d'hôte inconnue avec SHA-256, hôte différent, authentification refusée, incompatibilité de protocole) ; aucune confiance automatique d'un hôte inconnu.
- **LDAP :** dérogation explicite de certificat SSL/TLS via Administration → LDAP, conditionnée par ALLOW_INSECURE_LDAP_TLS=true sur l'hôte, désactivée par défaut et clairement signalée comme risquée. Import CA CRT/DER/PEM toujours recommandé.
- **Mises à jour :** nouveau précontrôle sans installation depuis le navigateur, indicateur de présence du service de maintenance hôte, vérification Git/Docker Compose et données.
- **GitHub :** workflow de release automatique sur tags de version vX.Y.Z ou vX.Y.Z-rcN, avec validation de version, CI, tests et documentation avant publication. Aucun tag ni release créé par simple commit.
- **Confidentialité :** exemples de serveurs remplacés par des FQDN génériques, documentation sécurité EN/FR et tests de non-régression.


## 0.3.6-rc1 — 16 novembre 2026

- **Test SSH** sur FQDN/port saisis manuellement, sans enregistrement préalable dans l’inventaire, avec vérification stricte de la clé d’hôte.
- **Certificats LDAP** : import de fichiers `.crt`, `.cer`, `.der` binaires ou PEM, conversion en CA PEM sans suppression des contrôles TLS.
- **Graphe workflow** : zoom +/− (8 à 250 %), Ctrl + molette, déplacement, adapter largeur, vue globale, conservation de la position.
- Tests API et non-régression et documentation bilingue.


## 0.3.5-rc1 — 16 novembre 2026

- Déploiement d'agents sur une **liste de serveurs**, avec sélection d'un groupe SSH, file persistante, résultats indépendants par machine et limitation à 100 cibles.
- Pour le déploiement, seuls les profils **SSH** sont affichés ; les autres sélecteurs de connexions suivent le protocole.
- **Mises à jour** : version installée, version distante, date du dernier commit Git, nombre de commits en retard et état vérifié/non vérifiable.
- **LDAP** : import de certificats CA PEM depuis le navigateur, vérification TLS conservée.
- Renommage **DC Proxmox** ; l'API Proxmox n'exige pas d'agent sur les nœuds.
- Workflows : mode **coffre ou réseau manuel**, champs adaptés, planification unique/assistant/cron sans champs inutiles et bouton **« ＋ Si échec »**.
- Tests de non-régression pour agents, LDAP, SMTP et embranchements.


## 0.3.5-rc1 — 16 novembre 2026

- Déploiement d'agents Linux/Windows sur **une liste de FQDN**, avec ajout depuis les groupes de serveurs SSH ; file persistante et résultat distinct par machine.
- Le choix du compte pour déployer les agents montre **uniquement les identités SSH** du coffre ; les sélecteurs SMTP/API/Proxmox sont filtrés par protocole.
- Page **Mises à jour** : comparaison version installée/distante, date du dernier commit distant, nombre de commits d'écart, statut à jour/nouvelle version/vérification impossible et contrôle périodique via worker.
- **LDAP** : import de certificats CA PEM depuis l'interface, conservation de la vérification TLS stricte ; pas d'option globale de contournement TLS.
- Onglet **DC Proxmox**, API Proxmox utilisable sans agent sur les hyperviseurs.
- Workflows : **coffre ou paramètres réseau manuels**, champs propres au mode ; planification unique/assistant/cron sans champs inutiles ; **« ＋ Si échec »** crée automatiquement une branche de récupération.
- Nouveaux tests pour les lots d'agents, CA LDAP, modes SMTP, contraintes du coffre et branches d'erreur.


## 0.3.4-rc1 — 16 novembre 2026

- **Identités SSH indépendantes** des serveurs dans le coffre : login, mot de passe ou clé privée (avec phrase secrète facultative).
- Nouvel écran **Administration → Serveurs & groupes SSH** : FQDN, port, libellé, groupes multiples, saisie de listes de FQDN et recherche.
- Diagnostic de clé SSH ciblé : choix d'un serveur de l'inventaire comme paramètre de test, vérification stricte de son empreinte.
- Sélecteur de serveur cible dans les blocs SSH, SFTP et runner Ansible, sans dupliquer les secrets du coffre.
- Compatibilité avec les anciennes connexions SSH contenant un serveur ; suppression protégée si un workflow référence encore l'inventaire.
- Tests de non-régression et documentation bilingue mis à jour.


## 0.3.3-rc2 — 16 novembre 2026

- Correctif de mise en page pour les assistants de diagnostic : les cases à cocher héritaient de la largeur 100 % des champs texte.
- Cases à cocher de 18 px et libellés lisibles, sans débordement horizontal dans les fenêtres modales.
- Ajustements responsive pour mobiles et petits écrans, et test automatique de non-régression.
- Versionnement des ressources CSS et JS pour forcer le navigateur à récupérer la correction après mise à jour.

## 0.3.3-rc1 — 16 novembre 2026

- **Assistant de diagnostic par protocole** dans le coffre (et depuis les tâches), avec paramètres et résultats détaillés.
- **SMTP :** adresse destinataire, objet et corps de test ; véritable envoi après confirmation, Message-ID de suivi, état « accepté par SMTP » distinct de la réception, limitation des envois.
- **SSH/SFTP :** vérification optionnelle de l'accès SFTP et commande fixe `whoami` ; **FTPS :** répertoire courant via PWD en lecture seule.
- **LDAP :** recherche utilisateur et groupe ; **Matrix :** appartenance à une room et message de test facultatif ; **Proxmox :** liste/statut des nœuds ; **API :** GET/HEAD et statut attendu.
- Aucun envoi sans choix explicite, tests administrateur audités, protections contre les injections et tests de régression.


## 0.3.2-rc2 — 16 novembre 2026

- Bouton **Tester** sur toutes les connexions du coffre avec fenêtre de diagnostic détaillée et contrôles non effectués clairement identifiés.
- SSH : clé d’hôte vérifiée et authentification sans commande ; FTPS : TLS et authentification sans transfert ; FTP clair : connectivité uniquement, aucun mot de passe transmis.
- LDAP : TLS, bind, lecture facultative ; Matrix : `whoami` ; Proxmox : lecture des permissions. API HTTPS générique : GET autorisé uniquement et avertissement si l’identité du jeton ne peut pas être prouvée.
- Liste `CONNECTION_TEST_ALLOWED_HOSTS` pour les tests d’API génériques ; tests automatisés supplémentaires et documentation EN/FR.


## 0.3.2-rc1 — 16 novembre 2026

- Menu **Administration → LDAP / Active Directory** : configuration persistante chiffrée, TLS, DN, attributs de login et groupes de rôles ; test en lecture seule.
- Connexions du coffre-fort avec formulaires spécifiques au protocole (SMTP, SSH/SFTP, FTP/FTPS, LDAP, API, Matrix, Proxmox).
- SMTP : hôte, port, STARTTLS/SSL, authentification facultative, expéditeur par défaut, test de connexion sans envoi.
- Les tâches e-mail récupèrent les paramètres SMTP du coffre ; les secrets existants restent compatibles.
- Nouveaux tests automatisés et documentation EN/FR actualisée.


## 0.3.2-rc1 — 16 novembre 2026

- Ajout du menu **Administration → LDAP / Active Directory** : configuration persistante chiffrée, URI/port, TLS, bases DN, attribut de connexion, groupes de rôles et test en lecture seule.
- Gestion des connexions propre au protocole : SMTP, SSH/SFTP, FTP/FTPS, LDAP, API, Matrix, Proxmox, générique.
- SMTP : hôte/port, SSL ou STARTTLS, relais sans authentification, identifiants facultatifs, expéditeur par défaut et bouton de test sans envoi d'e-mail.
- Les tâches SMTP réutilisent automatiquement les paramètres du coffre ; les paramètres techniques de tâche restent facultatifs.
- Maintien de la compatibilité avec les anciens secrets du coffre. Tests API et sécurité ajoutés.


## 0.3.1-rc2 — 16 novembre 2026

- Correction prioritaire du formulaire de connexion : interdiction de soumettre le mot de passe par GET.
- Chargement autonome du script `login.js`, POST JSON, cache-busting et listener enregistré tôt.
- Régression de sécurité couverte en CI ; procédure de traitement des mots de passe exposés dans les journaux.

## 0.3.1-rc1 — 16 novembre 2026

- Correction du menu latéral inopérant : les boutons `data-page` sont maintenant gérés.
- Icônes SVG cohérentes et ergonomie responsive de l'éditeur.
- Arborescence de dossiers persistante : création, renommage, suppression sans perte de workflows, filtrage et sélection.
- Planification graphique « une seule fois » et « répéter toutes les N minutes/heures/jours/semaines », avec maintien du cron avancé.
- Sélection des groupes existants LDAP/AD via recherche ; compatibilité OpenLDAP `posixGroup/memberUid` et `groupOfNames/member`.
- Coffre depuis les blocs de tâches : recherche, sélection, création, édition sans réexposition, clonage et suppression protégée.
- Tests de non-régression, documentation EN/FR. La recette navigateur et LDAP réels reste nécessaire.


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
