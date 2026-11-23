# Historique des modifications

## 0.3.33-rc1 — 23 novembre 2026 (diagnostics PRA et ergonomie)

- **Correction du PRA bloqué :** les actions internes autorisées intègrent désormais explicitement `lvm_lab_prepare_standby`, `lvm_lab_publish_standby` et `lvm_lab_test_boot`. Le refus local `Action PRA non autorisée` survenait avant toute commande Proxmox.
- **Reprise contrôlée :** bouton réservé à la régression locale *précise*, uniquement si aucune copie n'a été créée, aucun run n'a démarré et aucun appel à l'agent n'a été envoyé ; tout autre état incertain reste bloquant.
- **Sélection de VM :** liste triée par nom (français, casse ignorée), recherche par nom/VMID/nœud, suggestion automatique d'un VMID libre lors du changement de source, et bouton de vérification en temps réel du VMID cible sur Proxmox.
- **Commentaires de réplique :** champs `Référence / ticket` et `Commentaire` sur la politique PRA, enregistrés en base et repris, lors de la création de la VM `REP_`, dans la description Proxmox avec nom, VMID et nœud source ainsi que les marqueurs d'appartenance.
- **Diagnostics :** nom de la source et de `REP_` cliquables dans VM protégées ; vue détaillée de la phase d'activation, des éventuels blocages, des copies, des publications et des tests. L'état de préparation ne masque plus un échec d'activation enregistré.
- **Audit :** filtres administrateur par PRA, agents/déploiements, jobs, sécurité et système, avec recherche textuelle ; catégories filtrées côté serveur avant limitation de la liste.
- **Versions :** manager `0.3.33-rc1`, agents Linux/Windows intégrés `0.3.33`. Les versions effectivement rapportées par les agents restent affichées séparément ; aucune rétrogradation automatique d'un agent plus récent.
- **Sécurité :** aucune bascule réelle et aucun failback activé ; seuls les tests de recette `LAB-ONLY` sont autorisés.


## 0.3.31-rc1 — 20 novembre 2026 (PRA QMP/LVM piloté, RECETTE uniquement)

- **Enrôlement PRA** : choix d'une VM source en marche, VMID cible libre proposé, création automatique d'une VM `REP_` arrêtée, sans réseau ni disque, avec marqueurs d'appartenance.
- **Amorçage à chaud** : une autorisation `DISPOSABLE-LAB-ONLY` dans le formulaire déclenche préparation, copie FULL QMP/NBD vers des LV QCOW2 et suivi d'activation.
- **Incrémental planifié** : cadence définie par VM après amorçage vérifié ; aucune relance des copies ambiguës, aucune action avant confirmation de la dernière publication.
- **Publication sur REP_** : attachement des volumes du dernier checkpoint vérifié, sans démarrer la VM ; le profil EFI/TPM reste refusé.
- **Test isolé** : clone indépendant `PRA_TEST_`, démarrage QEMU contrôlé sans réseau et arrêt automatique ; **aucune preuve de démarrage OS ni applicatif**.
- **Journaux** : historiques séparés par VM pour amorçage, publications, planification, copies et tests. États incertains conservés après redémarrage.
- **Stockage** : LV épais par génération, **limite de 8 générations** sans purge, car la rétention des chaînes QCOW2 n'est pas encore sûre.
- **Bascule/failback** : simulations et vérification des prérequis disponibles ; **promotion réelle, fencing/witness et réplication inverse restent désactivés**.
- **Installation** : nouvelle version du contrôleur et mise à jour des agents Linux PRA depuis OrigamiTasks ; aucune nouvelle variable `.env`.
- Détails : [Guide QMP et pilote LVM](pra-qmp.fr.md).


## 0.3.24-rc1 — 19 novembre 2026 (pilote QMP, non validé en production)

- **Moteur PRA QMP incrémental à chaud** : copie initiale complète, dirty bitmaps, transferts suivants des blocs modifiés et gestion multi-disques par transactions QMP.
- **Agents Proxmox** : auxiliaires PRA installés et mis à jour avec l'agent Linux sous `/usr/local/lib/origamitasks-pra` root-only ; transport NBD relayé par SSH avec clés conservées dans le coffre central.
- **Cible** : checkpoints QCOW2 versionnés sur stockage Proxmox DIR indépendant et inscriptible, publication sur une VM `REP_` strictement éteinte, exclusion des LUN Pure en lecture seule et du LVM brut comme cible du pilote.
- **Pilotage** : prévol, validation du lancement, suivi et historique par VM, synchronisations automatiques après premier amorçage, réconciliation explicite des états incertains, horodatage du dernier point QMP pour observer le RPO.
- **Sécurité** : source jamais arrêtée par OrigamiTasks, aucune bascule automatique, aucun témoin/fencing encore implémenté ; restaurabilité et compatibilité des hyperviseurs à valider sur VM de recette.
- Guide détaillé : [Réplication QMP](pra-qmp.fr.md).

## 0.3.23-rc1 — 18 novembre 2026

- **Mise à jour d'agents à l'unité** : bouton « Mettre à jour » pour un agent dont la version connue est plus ancienne que celle intégrée à la console.
- **Mise à jour de tous les agents obsolètes en ligne** : lot SSH administrateur avec confirmation et une identité SSH du coffre ; exécution séquentielle, statut par agent et confirmation après heartbeat à la nouvelle version.
- **Préservation de l'identité et du PRA** : conserver `config.json`, `modules.json`, jeton, association de site et ID de l'agent ; remplacement uniquement du script de l'agent et du superviseur PRA passif Linux.
- **Protection des opérations** : hôte identifié dans l'inventaire et clé SSH vérifiée, copies de sauvegarde locales `.origami-backup`, tentative de restauration des exécutables en cas d'échec de redémarrage, pas de relance automatique.
- **Limitation** : la mise à jour des agents ne rend pas opérationnels le fencing Proxmox ni la réplication de VM inter-clusters.


## 0.3.21-rc1 — 17 novembre 2026

- **VM protégées PRA** : écran dédié dans l'administration et raccourci depuis chaque groupe PRA. Sélection **manuelle** d'une VM QEMU découverte sur le cluster principal ; saisie du VMID cible libre, nœud et stockage cible Proxmox, fréquence souhaitée et stratégie envisagée (étude, copie à froid, PBS ou baie). Enregistrement d'une **fiche de protection**, avec nom candidat `REP_<nom-vm>`, sans créer de VM ni copier de disques. Une protection par VM source et par VMID cible pour éviter les collisions. Les clusters de PP et Production ne peuvent pas être croisés.
- **Tests par VM** : prévol de réplication (lecture seule), bascule simulée, retour sur site principal simulé (failback), partition réseau et panne de la console. Vérifie par API GET la présence de la VM source, le statut de la VM, le VMID destination disponible, l'état du nœud cible, le stockage déclaré et les prérequis manquants. Historique d'essais immuable avec auteur et résultat **BLOCKED** tant que moteur de copie, réplique consistante, témoin, fencing et anti-redémarrage ne sont pas réels.
- **Aucune réplication réelle à ce stade** : le bouton API de copie renvoie une erreur explicite, aucune migration distante, `vzdump`, `qmrestore`, snapshot ou manipulation de LUN n'est effectuée. La fréquence stockée est indicative, aucun ordonnanceur de copies n'est activé. Aucune prétendue synchronisation réussie.
- **Statuts UI** : vert pour succès/en ligne, orange pour en cours/en attente, rouge pour erreur/bloqué/hors ligne ; gris pour inconnu/désactivé. Mise à jour des déploiements groupés et des agents. Boutons de rafraîchissement global/par agent : relisent le **dernier heartbeat reçu**, sans forcer un contact sortant vers l'agent.
- **Renommage groupe PRA** : réponse serveur contenant le nom réellement enregistré et rechargement direct des groupes et sites indépendamment des autres API ; requêtes sans cache pour éviter les titres périmés.
- **Garde-fous** : impossible de supprimer un site ou de déplacer sa liaison principale/secours tant qu'il est utilisé par une VM protégée ; suppression d'une fiche de protection **sans suppression des VM ni volumes**.

## 0.3.20-rc1 — 17 novembre 2026

- **Déploiement agent Proxmox :** la clé SSH du coffre (authentification utilisateur, par exemple utilisée aussi par Rundeck) ne doit plus être confondue avec la **clé d'hôte du serveur SSH**. Le parcours **DC Proxmox → Agents du cluster** enregistre automatiquement au premier contact la clé publique d'hôte du nœud dans le `known_hosts` persistant d'OrigamiTasks (**TOFU / accept-new**), sans demander de valider manuellement chaque empreinte.
- **Sécurité maintenue :** si une clé d'hôte déjà mémorisée change, le déploiement est bloqué. Le worker confirme encore l'appartenance du nœud au groupe/cluster avant SSH ; la connexion authentifiée compare ensuite la clé au `known_hosts` mémorisé. Le raccourci **Voir l'empreinte SSH (facultatif)** reste disponible pour une comparaison indépendante, notamment sur les hôtes critiques.
- **Aucune modification du coffre :** la clé privée SSH et son utilisateur restent ceux sélectionnés par l'administrateur. La confiance initiale en la clé publique **du serveur** n'authentifie pas l'utilisateur et ne garantit pas l'accès à `sudo -n`, SFTP, Python 3 ou systemd.
- **Limite TOFU :** une première connexion interceptée pourrait enregistrer une mauvaise clé ; le contrôle hors bande de l'empreinte reste recommandé sur les hyperviseurs sensibles. L'option automatique ne remplace jamais une clé déjà enregistrée.
- **Historique :** les anciens lots en erreur restent historisés ; relancer explicitement un nouveau lot après mise à jour, d'abord sur un nœud non critique. Aucun agent n'est installé automatiquement par la simple mise à jour.

## 0.3.19-rc1 — 17 novembre 2026

- **Isolation PRA PP/PROD** : création de **groupes PRA indépendants** entièrement nommables et modifiables depuis la page Proxmox (ex. Préproduction, Production, Recette). Chaque site source/secours est affecté à un groupe. Les clusters conservent une appartenance exclusive à un site. Les simulations entre groupes différents sont explicitement refusées par l'API et par l'interface ; aucune action sur l'infrastructure.
- **Migration non destructive** : les sites existants apparaissent comme « Sites existants à classer » jusqu'à affectation explicite. Aucune réaffectation automatique à PP ou Production. Un groupe contenant des sites ne peut pas être supprimé. Les groupes SSH Proxmox demeurent liés à leurs clusters propres.
- **Erreurs d'agents exploitables** : les échecs d'installation par hôte distinguent maintenant une **empreinte SSH non approuvée**, une **authentification SSH refusée**, une **connexion impossible**, un problème de **droits sudo/systemd/Python** ou un refus de paramètres. Les erreurs distantes brutes, les tokens et le contenu de clés privées ne sont pas transmis au navigateur.
- **Projet générique** : pas d'URL d'entreprise en valeur par défaut ; le paramètre HTTPS de chaque instance est vierge jusqu'à configuration par son administrateur. Les exemples utilisent `example.org` et les groupes PRA sont personnalisables.
- **Recette nécessaire** : les cinq échecs de l'ancien lot ne contiennent pas le diagnostic précis ; lancer un **nouvel essai sur un seul nœud** après vérification de la clé d'hôte SSH. Aucune réplication / bascule PRA réelle dans cette version.

## 0.3.18-rc1 — 17 novembre 2026

- **Correction du déploiement groupé :** l'adresse de contrôle des agents n'est plus obligatoirement `AGENT_SERVER_URL` dans le `.env`. Nouveau formulaire **Agents → Paramètres de connexion** pour enregistrer l'origine HTTPS vue par les agents derrière Nginx ; repli vers l'ancienne variable si disponible.
- **Proxmox :** affichage de l'URL dans l'assistant de déploiement multi-nœuds, et blocage explicite avec raccourci de configuration lorsque celle-ci est manquante. Le module PRA préparatoire n'est plus présélectionné.
- **Désinstallation :** le bouton **Désinstaller via SSH** reste présent sur Linux et Windows ; la console retient désormais l'URL publique exacte de chaque nouvelle installation pour supprimer un agent même après changement du FQDN de la console. Pour les agents plus anciens, l'administrateur peut fournir l'URL utilisée historiquement.
- **Windows :** les agents Windows sont réservés au module Standard, pour étendre les workflows Windows ; interdiction des modules Proxmox et PRA dans l'API et l'interface.
- **Aucune réplication ou bascule effective** n'est incluse dans le module PRA passif. La validation TLS reste obligatoire côté agent.

## 0.3.17-rc1 — 17 novembre 2026

- **Proxmox → Agents du cluster :** découverte directe des nœuds Linux depuis l'API du cluster ; prévisualisation des FQDN SSH via le domaine de l'endpoint Proxmox (suffixe modifiable), sélection des nœuds, création / synchronisation d'un **groupe SSH dédié lié au cluster**. Le groupe est consultable dans l'inventaire SSH ; les éditions génériques d'un groupe géré sont bloquées pour préserver son lien Proxmox.
- **Sites PRA → Déployer sur les nœuds :** choisir parmi les clusters du site, créer le groupe et sélectionner les agents Linux manquants à installer via une **identité SSH du coffre**, puis confirmer le lot. Les installations sont traitées individuellement, journalisées, et les nouveaux agents rattachés au site PRA lorsque celui-ci est déclaré.
- **Autorisation :** ce parcours n'exige **aucun ajout de FQDN dans `AGENT_DEPLOY_ALLOWED_HOSTS` ni activation globale par `.env`**. La liste des cibles provient de l'API Proxmox, des serveurs du groupe liés en base et de la sélection administrateur. Le worker recoupe à nouveau les identifiants, FQDN/ports et **l'empreinte d'hôte SSH déjà approuvée** avant chaque installation. Les déploiements SSH génériques restent protégés par leur ancien réglage global.
- **Mécanisme d'approbation SSH :** inspection de la clé publique depuis le wizard ; une nouvelle empreinte doit être **comparée sur la console du serveur, puis confirmée textuellement**. Aucune acceptation automatique des clés inconnues. Les hôtes non approuvés échouent individuellement et ne reçoivent pas d'agent.
- **Limites :** uniquement Linux via SSH/SFTP et sudo/root, 100 nœuds par lot ; associer les agents existants plutôt que les réinstaller. Les modules Proxmox et PRA passif peuvent être préactivés dans l'assistant : la console transmet la configuration après l'enrôlement, sans déclencher de réplication. Ils restent modifiables dans Agents. **Aucune réplication ni bascule de VM n'est déclenchée par ce parcours.**

## 0.3.16-rc1 — 17 novembre 2026

- **Agent unique modulaire :** chaque agent conserve son identité, son token et son installation, avec modules administrables **Standard** (toujours actif), **Proxmox** (association aux clusters déclarés, requêtes API aujourd'hui côté console) et **PRA passif** (Linux uniquement). Les modules sont activables dans **Agents → Configurer modules**, avec contrôle du site et des clusters associés, version de politique et état réellement accusé réception.
- **Linux :** l'installation SSH existante déploie un seul paquet contenant le moteur standard `agent.py` et le superviseur PRA `pra_worker.py` en deux services systemd isolés ; le superviseur PRA conserve une politique locale et un heartbeat **même si la console tombe**. **Il ne réplique aucune VM, ne commande pas Proxmox, et ne déclenche pas de bascule** : ni témoin indépendant ni fencing opérationnels. Limites supplémentaires du superviseur : 64 Mio et 10 % d'un CPU.
- **Windows :** l'agent unique accepte les modules Standard et Proxmox en métadonnées ; module PRA autonome non disponible sur Windows. Les versions 0.3.2 Linux/Windows accusent réception des changements de configuration ; les agents anciens continuent leur fonctionnement standard sans activation cachée.
- **Sécurité / contrôle :** stockage en base du module désiré et du dernier module réellement appliqué, changements validés côté serveur et auditables, aucune clé API Proxmox transmise à l'agent ; politique PRA strictement non autorisante (fencing, témoin, réplication et promotion à false). L'activation PRA est refusée sans site principal/secours Linux associé à un cluster.
- **Cycle de vie :** désinstallation SSH supprime les deux services Linux et leurs fichiers ; les anciens agents devront être explicitement réinstallés / mis à jour pour posséder le superviseur PRA. La mise à jour signée à chaud et l'agent PRA réellement opérationnel restent à développer.

## 0.3.15-rc1 — 17 novembre 2026

- **Vue d'ensemble :** trois onglets *Tâches & exécutions*, *Agents actifs* et *DC Proxmox / PRA*. Totaux et historiques existants ; états des agents (en ligne, hors ligne, révoqué, désinstallé), CPU/RAM du processus quand l'agent les remonte ; clusters et sites PRA associés. Les retards de synchronisation PRA restent « non disponibles » tant qu'aucun moteur réel n'est implémenté.
- **Désinstallation via SSH :** disponible pour les agents Linux et Windows déjà enrôlés. Choix d'une identité SSH du coffre, saisie du FQDN, confirmation du nom exact ; vérification de la clé d'hôte SSH déjà approuvée, du hostname de l'agent et de son identité locale (agent_id + URL de la console) avant suppression. Le service/tâche et le répertoire agent sont retirés, puis le token est révoqué. Si la suppression distante échoue, aucune révocation automatique ; possibilité de révoquer séparément.
- **Consommation agents :** les nouvelles installations Linux imposent `MemoryMax=192M`, `CPUQuota=20%`, `TasksMax=48` via systemd. Les nouvelles versions des scripts agents pollent toutes les 10 secondes par défaut (intervalle paramétrable de 5 à 60 secondes) et remontent CPU/RAM du processus agent uniquement ; scripts fils/VM exclus des mesures, quota systemd applicable au service Linux. Les installations antérieures ne sont pas mises à jour automatiquement.
- **Sécurité :** le modèle actuel reste une **préversion** : HTTPS et tokens individuels, restrictions Linux mais pas encore de mTLS, signature de packages ni véritable service PRA autonome. Sous Windows, la tâche agent actuelle s'exécute sous SYSTEM : à durcir avant déploiement étendu.

## 0.3.14-rc1 — 17 novembre 2026

- **Sites PRA :** nouveau gestionnaire directement dans « Serveurs & clusters Proxmox », création de sites aux noms libres avec rôle **principal / secours / autre**, rattachement de plusieurs clusters Proxmox, des agents Linux existants et annotation du profil de stockage (auto, iSCSI+LVM, ZFS, Ceph, PBS ou autre).
- **Simulation de panne :** scénarios **cluster indisponible**, **partition réseau** et **console OrigamiTasks indisponible**. Exécution entièrement virtuelle, sans action sur VM, agent, hyperviseur ou stockage ; résultats et contrôles bloquants tracés dans le journal d'audit.
- **Agents :** lien vers l'installation SSH de l'agent standard et affichage du dernier contact/version. Cet agent dépend toujours du contrôle central et n'est pas un agent PRA autonome. Les mises à jour sans interruption, le témoin, le fencing et la réplication restent à implémenter et valider.
- **Sécurité :** aucune décision de démarrage d'une copie REP_ n'est possible depuis le simulateur. Les VM ne sont jamais modifiées par la déclaration d'un site PRA.

## 0.3.13-rc1 — 17 novembre 2026

- **Découverte Proxmox :** recherche instantanée sur nom, VMID, nœud, ID, statut et stockage ; filtres VM (QEMU), LXC, nœuds et stockages ; tri nom/type/VMID/nœud/état croissant ou décroissant ; pagination de 100 résultats et actualisation via API. L'évaluation PRA existante reste accessible pour les VM.
- **PRA SD → AU :** dossier de conception bilingue des agents intersites, copies REP_ éteintes, synchronisation adaptée au stockage, témoin indépendant, fencing et anti-double-démarrage, bascule/retour arrière. **Aucune réplication ni bascule automatique n'est implémentée par cette version** ; tout démarrage automatique nécessite une validation du témoin/fencing et des exercices de panne.

## 0.3.12-rc1 — 17 novembre 2026

- **Proxmox :** option administrateur **« Ignorer la vérification du certificat TLS (certificat autosigné) »** sur chaque fiche serveur/cluster et dans les connexions Proxmox du coffre. Désactivée par défaut, stockée en base, réversible à tout moment ; les autres serveurs restent vérifiés. HTTPS conserve son chiffrement, mais l’identité du serveur n'est pas vérifiée si l'option est activée.
- **Diagnostic :** distingue validation TLS, négociation TLS, erreurs DNS/port/pare-feu, token et permissions. Avertissement explicite sur les diagnostics effectués sans vérification du certificat.
- **Interface :** cartes Proxmox redimensionnables, boutons disposés sur deux colonnes sans débordement, bouton d'ajout sous le titre pour ne plus être masqué, correction de la recherche par FQDN.
- **Compatibilité :** migration SQLite non destructive ; workflows et inventaires existants conservés, sans modification du `.env`.

## 0.3.11-rc1 — 17 novembre 2026

- **Proxmox 100 % web pour l'ajout des hôtes :** écran **Serveurs & clusters Proxmox** permettant d'enregistrer un cluster ou un nœud autonome, de saisir directement l'identifiant et le secret du token (chiffrés dans le coffre), d'affecter une CA TLS importée depuis le navigateur et de modifier la configuration existante sans `.env` ni redémarrage.
- **Certificats :** import PEM/CRT/CER/DER, stockage persistant isolé, choix d'une CA différente par serveur/cluster ou dans le profil Proxmox du coffre. Aucun chemin arbitraire ni clé privée accepté ; HTTPS reste vérifié.
- **Exploration :** recherche dans la flotte Proxmox, diagnostic API depuis chaque fiche et découverte des nœuds, VM, LXC et stockages. Les contrôles d'autorisation et les permissions du token restent applicables.
- **Sécurité et compatibilité :** les DC existants sont conservés par migration SQLite, les tokens restent chiffrés, l'URL du token et celle du DC doivent correspondre, et les actions VM restent soumises à leur autorisation explicite.

## 0.3.10-rc1 — 16 novembre 2026

- **Proxmox :** déclaration des DC et jetons API depuis l'interface web, sans `PROXMOX_ALLOWED_HOSTS` dans `.env` ; recherche parmi de nombreux DC, contrôles de l'URL liée au jeton et diagnostics HTTP 401/403, TLS et réseau distincts.
- **LDAP :** import des exports PEM OpenSSL contenant des métadonnées de certificats (Bag Attributes, friendlyName, subject, issuer), sans accepter d'autres clés privées ou contenu arbitraire.
- **E-mails :** rapports HTML + texte automatique à la fin des workflows, objet SUCCÈS / ÉCHEC, SMTP du coffre, choix des événements, journal par étape et extraits d'erreur avec masquage des secrets ; statut d'envoi archivé en base.
- **Fiabilité :** migrations SQLite conservant les workflows, tests de non-régression et documentation FR/EN.

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
