/* OrigamiTasks SPA — no CDN, no bundler, no unsafe eval. */
'use strict';
const state = {user:null,page:'dashboard',jobs:[],runs:[],summary:{},credentials:[],datacenters:[],agents:[],folders:[],folderFilter:'',directoryGroups:[],pickerField:null,pickerCredentialId:null,folderSelected:'Général',groupRole:'view',catalog:{available:[]},selectedRun:null,editor:null,selectedStep:0,filter:'all',query:''};
const $=(selector,base=document)=>base.querySelector(selector);
const $$=(selector,base=document)=>Array.from(base.querySelectorAll(selector));
const e=(value)=>String(value??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dt=v=>v?new Date(v).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'short'}):'—';
const statusName={success:'Réussi',failed:'Échec',running:'En cours',queued:'En attente',interrupted:'Interrompu',skipped:'Ignoré'};
const labelStatus=s=>`<span class="status ${e(s)}">${e(statusName[s]||s)}</span>`;
const toast=(msg,ok=true)=>{const n=document.createElement('div');n.className=ok?'ok':'fail';n.textContent=msg;$('#toast').appendChild(n);setTimeout(()=>n.remove(),4000)};
const cookie=n=>document.cookie.split('; ').find(x=>x.startsWith(n+'='))?.split('=').slice(1).join('=')||'';
async function api(url,method='GET',data){const options={method,credentials:'same-origin',headers:{}};if(method!=='GET'){options.headers['X-CSRF-Token']=decodeURIComponent(cookie('origami_csrf'));if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data)}}const r=await fetch('/api'+url,options);let body;try{body=await r.json()}catch{body={detail:'Réponse serveur invalide'}}if(!r.ok){if(r.status===401){state.user=null;$('#application').hidden=true;$('#auth').hidden=false}throw Error(typeof body.detail==='string'?body.detail:`Erreur HTTP ${r.status}`)}return body}
async function load(){const [jobs,runs,summary]=await Promise.all([api('/jobs'),api('/runs'),api('/summary')]);state.jobs=jobs.items;state.runs=runs.items;state.summary=summary;state.catalog=await api('/catalog');try{state.folders=(await api('/folders')).items}catch{state.folders=[]}$('#navCount').textContent=jobs.items.length;if(state.user.role==='admin')try{state.credentials=(await api('/credentials')).items;state.datacenters=(await api('/datacenters')).items;state.agents=(await api('/agents')).items}catch{}}
const roleAdmin=()=>state.user?.role==='admin';
const title=(name,subtitle,action='')=>`<div class="page-heading"><div><div class="eyebrow">ORIGAMITASKS / AUTOMATION</div><h1>${name}</h1><p>${subtitle}</p></div>${action}</div>`;
const initials=()=>state.user?.display_name?.split(' ').map(x=>x[0]).join('').slice(0,2).toUpperCase()||'OT';
const badgeTags=tags=>tags.map(t=>`<span class="chip tag">#${e(t)}</span>`).join('');
function showPage(page){state.page=page;state.selectedRun=null;$('#breadcrumb').textContent=({dashboard:"Vue d'ensemble",jobs:'Workflows',runs:'Exécutions',vault:'Coffre-fort',users:'Utilisateurs & droits',ldap:'LDAP / Active Directory',updates:'Mises à jour',datacenters:'Datacenters',agents:'Agents Linux / Windows',catalog:'Catalogue des tâches',audit:"Journal d'audit"})[page]||page;$$('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.page===page));$('.sidebar').classList.remove('visible');render()}
function render(){const pages={dashboard, jobs, runs, vault,users,updates,audit,datacenters,agents:agentsPage,catalog,ldap:ldapPage};$('#content').innerHTML=(pages[state.page]||dashboard)();bindLocal()}
function dashboard(){const s=state.summary;return title('Vue d’ensemble','Votre centre de contrôle de l’automatisation.','<button class="btn primary" data-action="create">＋ Nouveau workflow</button>')+`<div class="stats-grid">
 <div class="stat-card featured"><span class="label">Workflows disponibles</span><span class="stat-icon">◈</span><div class="value">${s.jobs||0}</div><div class="sub">${s.enabled||0} workflows actifs</div></div>
 <div class="stat-card"><span class="label">Exécutions réussies</span><span class="stat-icon">✓</span><div class="value">${s.success||0}</div><div class="sub">sur les 100 dernières</div></div>
 <div class="stat-card"><span class="label">Erreurs détectées</span><span class="stat-icon">⚑</span><div class="value">${s.failed||0}</div><div class="sub">exécutions en anomalie</div></div>
 <div class="stat-card"><span class="label">Activité en temps réel</span><span class="stat-icon">◴</span><div class="value">${s.running||0}</div><div class="sub">en cours / en attente</div></div></div>
 <div class="section-grid"><section><div class="panel"><div class="panel-title"><h2>Dernières exécutions</h2><button class="text-btn" data-action="page" data-page="runs">Voir tout ↗</button></div>${runsTable(state.runs.slice(0,7))}</div><div class="panel"><div class="panel-title"><h2>Workflows récents</h2><button class="text-btn" data-action="page" data-page="jobs">Explorer ↗</button></div>${jobsTable(state.jobs.slice(0,5))}</div></section><section><div class="panel"><div class="panel-title"><h2>Performance des automatisations</h2><span class="hint">100 dernières</span></div>${activityBars()}</div><div class="panel"><div class="panel-title"><h2>Actions rapides</h2></div><div class="quick-grid"><button class="quick-card" data-action="create"><span>⤳</span><b>Créer un workflow</b><small>Visuel et conditionnel</small></button><button class="quick-card" data-action="page" data-page="runs"><span>◷</span><b>Consulter les logs</b><small>Dernières exécutions</small></button><button class="quick-card" data-action="page" data-page="vault"><span>◇</span><b>Coffre sécurisé</b><small>Secrets chiffrés</small></button><button class="quick-card" data-action="page" data-page="updates"><span>⇣</span><b>Version & Git</b><small>Maintenance</small></button></div></div></section></div>`}
function activityBars(){const count=state.runs.slice(0,100).length,total=count||1,success=state.runs.slice(0,100).filter(r=>r.status==='success').length,fail=state.runs.slice(0,100).filter(r=>['failed','interrupted'].includes(r.status)).length;return [['Exécutions réussies',success],['Erreurs',fail],['Autres',count-success-fail]].map(([name,n])=>`<div class="activity-line"><span>${name}</span><b>${Math.round(n/total*100)}%</b></div><div class="bar-track"><span style="width:${Math.round(n/total*100)}%"></span></div>`).join('')+`<p class="hint" style="margin-top:18px">Historique conservé en base, traçabilité de chaque étape.</p>`}
function runsTable(items){if(!items.length)return empty('Aucune exécution pour le moment','Un workflow lancé apparaîtra ici.');return `<div class="table-wrap"><table><thead><tr><th>Workflow</th><th>État</th><th>Déclencheur</th><th>Date</th><th></th></tr></thead><tbody>${items.map(r=>`<tr><td><div class="name-cell">${e(r.job_name)}</div><span class="muted tinytext">#${r.id}</span></td><td>${labelStatus(r.status)}</td><td class="muted">${e(r.trigger)}</td><td class="muted">${dt(r.queued_at)}</td><td><button class="text-btn" data-action="run-detail" data-id="${r.id}">Détails ↗</button></td></tr>`).join('')}</tbody></table></div>`}
function jobsTable(items){if(!items.length)return empty('Pas encore de workflow','Créez votre première automatisation.');return `<div class="table-wrap"><table><thead><tr><th>Nom</th><th>Dossier</th><th>Étapes</th><th>Planification</th><th></th></tr></thead><tbody>${items.map(j=>`<tr><td class="name-cell">${e(j.name)}</td><td class="muted">${e(j.folder)}</td><td>${j.steps_json.length} étapes</td><td class="muted">${e(j.schedule_json.cron||'Manuel')}</td><td><button class="text-btn" data-action="edit" data-id="${j.id}">Ouvrir ↗</button></td></tr>`).join('')}</tbody></table></div>`}
const empty=(name,text)=>`<div class="empty-state"><div class="empty-icon">⌁</div><h3>${name}</h3><p>${text}</p></div>`;
function jobs(){
 let items=state.jobs.filter(j=>!state.query||[j.name,j.description,j.folder,(j.tags_json||[]).join(' ')].join(' ').toLowerCase().includes(state.query.toLowerCase()));
 if(state.filter==='active')items=items.filter(j=>j.enabled);
 if(state.filter==='scheduled')items=items.filter(j=>j.schedule_json?.cron||['once','interval'].includes(j.schedule_json?.mode));
 if(state.folderFilter)items=items.filter(j=>j.folder===state.folderFilter||j.folder.startsWith(state.folderFilter+'/'));
 const cards=items.map(j=>{
  const schedule=j.schedule_json||{};
  const scheduleLabel=schedule.mode==='once'?'Une fois : '+(schedule.at||''):(schedule.mode==='interval'?'Toutes les '+schedule.every+' '+schedule.unit:(schedule.cron||'Déclenchement manuel'));
  return '<article class="job-card"><div class="job-card-head"><div class="job-icon">⤳</div>'+(j.enabled?'<span class="status success">Actif</span>':'<span class="status">Désactivé</span>')+'</div>'
  +'<h3>'+e(j.name)+'</h3><p>'+e(j.description||'Aucune description définie.')+'</p>'
  +'<div><span class="chip folder">▱ '+e(j.folder)+'</span>'+badgeTags(j.tags_json||[])+'</div>'
  +'<div class="muted tinytext">'+j.steps_json.length+' étapes · '+e(scheduleLabel)+'</div>'
  +'<div class="actions"><button class="btn primary tiny" data-action="run-job" data-id="'+j.id+'" '+(j.permissions.run?'':'disabled')+'>▶ Exécuter</button>'
  +'<button class="btn tiny" data-action="edit" data-id="'+j.id+'" '+(j.permissions.edit?'':'disabled')+'>Modifier</button>'
  +(roleAdmin()?'<button class="btn tiny" data-action="clone" data-id="'+j.id+'" title="Cloner">⧉</button><button class="btn tiny" data-action="webhook" data-id="'+j.id+'" title="Webhook">↗</button>':'')+'</div></article>';
 }).join('');
 return title('Mes workflows','Organisez et pilotez vos automatisations dans une arborescence.',roleAdmin()?'<button class="btn primary" data-action="create">＋ Créer un workflow</button>':'')
  +'<div class="jobs-layout">'+folderPanel()+'<section class="jobs-main"><div class="filter-tabs">'
  +[['all','Tous'],['active','Actifs'],['scheduled','Planifiés']].map(([v,n])=>'<button data-action="filter" data-value="'+v+'" class="'+(state.filter===v?'active':'')+'">'+n+'</button>').join('')
  +'</div><div class="tool-row"><span class="hint">'+items.length+' workflow(s)</span></div><div class="workflow-grid">'
  +(cards||empty('Aucun workflow','Sélectionnez un autre dossier ou créez votre première automatisation.'))+'</div></section></div>';
}
function runs(){return title('Journal d’exécution','Chaque événement, chaque branche et chaque sortie de script.')+`<div class="panel"><div class="panel-title"><h2>Historique (${state.runs.length})</h2><button class="btn tiny" data-action="refresh">↻ Actualiser</button></div>${runsTable(state.runs)}</div>`}
function vault(){if(!roleAdmin())return title('Coffre-fort','Réservé aux administrateurs.')+`<div class="notice">Le coffre de secrets est accessible aux seuls administrateurs.</div>`;return title('Coffre-fort','Credentials chiffrés au repos. Les valeurs ne sont jamais renvoyées par l’API.','<button class="btn primary" data-action="new-credential">＋ Nouveau secret</button>')+`<div class="panel"><div class="panel-title"><h2>Identités et connexions</h2><span class="hint">${state.credentials.length} identifiants</span></div><div class="table-wrap"><table><thead><tr><th>Nom</th><th>Type</th><th>Créé le</th><th></th></tr></thead><tbody>${state.credentials.map(c=>`<tr><td class="name-cell">◇ ${e(c.name)}</td><td>${e(c.kind)}</td><td class="muted">${dt(c.created_at)}</td><td><button class="btn tiny" data-action="credential-test" data-id="${c.id}" title="Tester cette connexion sans action métier">Tester</button> <button class="btn tiny" data-action="credential-edit" data-id="${c.id}">Modifier</button> <button class="btn tiny" data-action="credential-clone" data-id="${c.id}">Cloner</button> <button class="btn tiny danger" data-action="delete-credential" data-id="${c.id}">Supprimer</button></td></tr>`).join('')}</tbody></table></div>${!state.credentials.length?empty('Coffre vide','Ajoutez un identifiant SSH, SFTP, SMTP ou API.'):''}</div><div class="notice">Les secrets sont chiffrés avec Fernet et la clé VAULT_KEY externe au dépôt. Pour les scripts, les valeurs de l'identifiant sélectionné sont fournies via ORIGAMI_USERNAME / ORIGAMI_PASSWORD / ORIGAMI_TOKEN. Un administrateur qui peut créer des scripts doit être considéré comme privilégié.</div>`}

function ldapPage(){
 if(!roleAdmin())return title('Configuration LDAP','Réservée aux administrateurs');
 return title('LDAP / Active Directory','Configurer l’annuaire utilisé pour la connexion et rechercher les groupes.')
 +'<div id="ldapAdminPanel" class="panel"><p class="hint">Chargement des paramètres LDAP…</p></div>';
}
function ldapInput(label,name,value='',type='text',helper=''){
 return '<div class="field"><span>'+e(label)+'</span><input name="'+e(name)+'" type="'+type+'" value="'+e(value??'')+'" autocomplete="off" '+(type==='password'?'autocomplete="new-password"':'')+'>'
 +(helper?'<small class="hint">'+e(helper)+'</small>':'')+'</div>';
}
function ldapCheck(label,name,on){
 return '<label class="field checkbox"><input type="checkbox" name="'+e(name)+'" '+(on?'checked':'')+'><span>'+e(label)+'</span></label>';
}
function ldapSettingsForm(cfg){
 const body='<div class="panel-title"><h2>Connexion de l’annuaire</h2><span class="chip">'+e(cfg.source==='administration'?'Configuration enregistrée':'Paramètres hérités du .env')+'</span></div>'
 +'<p class="hint">Les paramètres enregistrés ici prennent priorité sur le .env. Le mot de passe de liaison est chiffré avec VAULT_KEY et n’est jamais réaffiché. Le compte admin local reste disponible.</p>'
 +'<form id="ldapAdminForm"><div class="form-grid">'
 +ldapCheck('Activer la connexion LDAP', 'enabled', cfg.enabled)
 +ldapCheck('Activer STARTTLS (ldap://, généralement port 389)', 'starttls', cfg.starttls)
 +ldapInput('Adresse de l’annuaire','uri',cfg.uri,'text','Ex. ldaps://serveur:636 ou ldap://serveur:389 avec STARTTLS')
 +ldapInput('DN du compte de liaison','bind_dn',cfg.bind_dn,'text','Compte de service en lecture seule')
 +ldapInput('Mot de passe du compte de liaison','bind_password','','password',cfg.has_bind_password?'Déjà configuré — laisser vide pour conserver':'Mot de passe requis si LDAP est activé')
 +ldapInput('Base DN des utilisateurs','base_dn',cfg.base_dn,'text','Ex. dc=example,dc=org')
 +ldapInput('Base DN des groupes','group_base_dn',cfg.group_base_dn)
 +'<div class="field"><span>Attribut du login utilisateur</span><select name="user_attribute">'+option([['uid','uid — OpenLDAP/Linux'],['sAMAccountName','sAMAccountName — Active Directory'],['cn','cn'],['mail','mail'],['userPrincipalName','userPrincipalName']],cfg.user_attribute)+'</select></div>'
 +ldapInput('Groupe des administrateurs','admin_group',cfg.admin_group)
 +ldapInput('Groupe des opérateurs','operator_group',cfg.operator_group)
 +ldapInput('Groupe des lecteurs','viewer_group',cfg.viewer_group)
 +ldapInput('Chemin CA TLS (dans le conteneur)','ca_cert',cfg.ca_cert,'text','Optionnel : certificat CA de confiance, sous /etc/ssl ou /app/data')
 +ldapCheck('Utiliser la recherche des groupes AD imbriqués','nested_groups',cfg.nested_groups)
 +'</div><div class="divider"></div>'
 +'<div class="form-grid">'+ldapInput('Utilisateur à rechercher (test facultatif)','test_user','','text','Test en lecture seule du login, sans tenter de l’authentifier')+'</div>'
 +'<div class="tool-row"><button type="button" class="btn" data-action="ldap-test">Tester la connexion LDAP</button><button type="submit" class="btn primary">Enregistrer la configuration</button></div>'
 +'<div id="ldapTestResult" class="hint">Le test vérifie TLS, le bind, la base DN et une recherche des groupes. Aucune modification de l’annuaire.</div></form>';
 return body;
}
async function hydrateLDAP(){
 const host=document.getElementById('ldapAdminPanel');
 if(!host)return;
 try{const cfg=await api('/admin/ldap');if(document.getElementById('ldapAdminPanel')!==host)return;
 state.ldapSettings=cfg;host.innerHTML=ldapSettingsForm(cfg)}
 catch(ex){host.innerHTML='<div class="notice error">'+e(ex.message)+'</div>'}
}
function readLDAPForm(){
 const form=document.getElementById('ldapAdminForm');
 if(!form)throw Error('Le formulaire LDAP est fermé');
 const f=new FormData(form),data={};
 for(const key of ['enabled','starttls','nested_groups'])data[key]=f.has(key);
 for(const key of ['uri','bind_dn','bind_password','base_dn','group_base_dn','user_attribute','admin_group','operator_group','viewer_group','ca_cert']){
  if(key==='bind_password'&&!f.get(key))continue;
  data[key]=String(f.get(key)||'').trim();
 }
 return {settings:data,test_user:String(f.get('test_user')||'').trim()};
}

function users(){return title('Utilisateurs & permissions','Identités LDAP et groupes affectés aux autorisations des workflows.')+`<div class="panel"><div class="panel-title"><h2>Comptes connectés</h2><span class="hint">Les rôles sont attribués à la connexion LDAP</span></div><div id="userTable">Chargement...</div></div><div class="notice">Groupes par workflow : <b>Lecture</b>, <b>Exécution</b>, <b>Modification</b>. Un utilisateur non membre d'un groupe explicitement autorisé n'accède pas au workflow. Les administrateurs disposent de tous les droits. Les groupes et rôles LDAP sont configurés dans .env.</div>`}
const plannedCatalog=[
 ['Déclencheurs','Fichier créé/modifié, email reçu, trap SNMP, seuil Zabbix, événement Windows, dépendance entre jobs'],
 ['Logique','Boucles, parallélisme, approbation humaine, sous-workflows, rollback, verrous de concurrence'],
 ['Data','PostgreSQL, MySQL, SQL Server, Oracle, CSV, JSONPath, XML/XSLT, Excel, PDF'],
 ['Fichiers & Cloud','SCP, rsync, SMB, WebDAV, S3, SharePoint, Azure Blob, compression, PGP'],
 ['Communication','IMAP/POP3, email HTML, pièces jointes, Teams, Slack, Telegram, Matrix E2EE'],
 ['Infrastructure','Zabbix, NetBox, GLPI, Redmine, Pure Storage, PBS, PDM, Docker, Kubernetes'],
 ['Proxmox DR','Migration inter-clusters, backups, snapshots, réplication delta, tests PRA, fencing'],
 ['Enterprise','SSO Keycloak/OIDC, RBAC ressource, HA, plugin SDK, mises à jour signées des agents']
];
function catalog(){const entries=state.catalog.available||[];const groups=[...new Set(entries.map(x=>x.category))];return title('Catalogue des tâches','Toutes les actions disponibles comme blocs de workflow, et les familles planifiées.')+`<div class="tool-row"><input id="catalogSearch" placeholder="Rechercher un bloc ou une famille de tâches..." style="flex:1;min-width:200px"/><span class="chip tag">${entries.length} blocs actifs</span></div>${groups.map(group=>`<div class="panel" style="margin-top:18px"><div class="panel-title"><h2>${e(group)}</h2><span class="status success">Intégré</span></div><div class="workflow-grid">${entries.filter(x=>x.category===group).map(item=>`<div class="job-card catalog-search-item" data-search="${e((item.name+' '+item.category+' '+item.type).toLowerCase())}"><h3>${e(item.name)}</h3><div class="muted tinytext">${e(item.type)}</div><div class="actions"><button class="btn tiny" data-action="create">＋ Créer un workflow</button></div></div>`).join('')}</div></div>`).join('')}<div class="panel" style="margin-top:18px"><div class="panel-title"><h2>Connecteurs et fonctionnalités prévues</h2><span class="chip folder">Roadmap</span></div><div class="workflow-grid">${plannedCatalog.map(([name,description])=>`<div class="job-card catalog-search-item" data-search="${e((name+' '+description).toLowerCase())}"><h3>${e(name)}</h3><p>${e(description)}</p><span class="chip folder">À développer</span></div>`).join('')}</div><p class="hint">Inventaire complet dans docs/FEATURE-CATALOG.md sur GitHub. « À développer » ne signifie pas que l’exécution est déjà disponible.</p></div>`}

function agentsPage(){
 if(!roleAdmin())return title('Agents','Administration uniquement.');
 return title('Agents Linux et Windows','Gestion des agents en sortie HTTPS, déploiement SSH et révocation.',
 '<div class="tool-row"><button class="btn" data-action="agent-enroll-form">＋ Enrôlement manuel</button><button class="btn primary" data-action="agent-deploy-form">⇣ Déployer par SSH</button></div>')+
 '<div class="panel"><div class="panel-title"><h2>Parc d’agents</h2><span class="hint">'+state.agents.length+' agent(s)</span></div><div class="table-wrap"><table><thead><tr><th>Nom</th><th>Plateforme</th><th>Groupe</th><th>État</th><th>Dernier contact</th><th></th></tr></thead><tbody>'+
 state.agents.map(a=>'<tr><td class="name-cell">'+e(a.name)+'<div class="muted tinytext">'+e(a.hostname||'Non enrôlé')+'</div></td><td>'+e(a.platform)+' · '+e(a.version||'—')+'</td><td>'+e(a.group_name)+'</td><td>'+labelStatus(a.status)+'</td><td>'+dt(a.last_seen)+'</td><td>'+(a.revoked?'—':'<button class="btn tiny danger" data-action="agent-revoke" data-id="'+a.id+'">Révoquer</button>')+'</td></tr>').join('')+
 '</tbody></table></div></div><div class="notice">Déploiement SSH : compte privilégié, clé d’hôte connue, AGENT_SERVER_URL HTTPS, ALLOW_AGENT_REMOTE_DEPLOY=true et liste AGENT_DEPLOY_ALLOWED_HOSTS. Les écritures sont désactivées par défaut. Linux utilise un compte non privilégié ; Windows utilise une tâche SYSTEM.</div>'}
function agentDeployForm(){modal('Déployer un agent via SSH','<form id="agentDeployForm"><div class="form-grid">'+
 field('Nom de l’agent','name')+selectField('Système','platform',[['linux','Linux (Python 3 + systemd)'],['windows','Windows (PowerShell 5.1+)']],'linux')+
 field('Hôte / FQDN autorisé','host')+field('Port SSH','port',22,'number')+field('Groupe','group','default')+
 selectField('Compte SSH (coffre)','credential_id',[['','— Choisir —'],...state.credentials.map(c=>[c.id,c.name])],'')+
 '</div><div class="field checkbox"><input type="checkbox" name="approved" required><span>Je confirme l’installation d’un service ou d’une tâche planifiée sur cet hôte.</span></div><p class="hint">Le serveur doit joindre l’hôte par SSH et l’agent joindre OrigamiTasks par HTTPS.</p><button class="btn primary" type="submit">Déployer l’agent</button></form>', '', true)}
function agentEnrollForm(){modal('Enrôlement manuel','<form id="agentEnrollForm"><div class="form-grid">'+
 field('Nom','name')+selectField('Plateforme','platform',[['linux','Linux'],['windows','Windows']],'linux')+
 field('Groupe','group','default')+'</div><p class="hint">Jeton unique valable 15 minutes. Installer agents/linux/agent.py ou agents/windows/agent.ps1 avec config.json sur la machine.</p><button class="btn primary" type="submit">Créer un jeton</button></form>')}

function datacenters(){if(!roleAdmin())return title('Datacenters','Administration uniquement.');return title('Datacenters Proxmox','Inventaire en lecture seule des clusters indépendants.','<button class="btn primary" data-action="new-datacenter">＋ Déclarer un datacenter</button>')+`<div class="workflow-grid">${state.datacenters.map(d=>`<div class="job-card"><div class="job-card-head"><div class="job-icon">◈</div><span class="chip tag">Proxmox VE</span></div><h3>${e(d.name)}</h3><p>${e(d.url)}</p><div class="muted tinytext">Coffre #${d.credential_id}</div><div class="actions"><button class="btn primary tiny" data-action="discover-datacenter" data-id="${d.id}">⌕ Découvrir les ressources</button><button class="btn tiny danger" data-action="delete-datacenter" data-id="${d.id}">Supprimer</button></div></div>`).join('')||empty('Aucun datacenter','Déclarez votre premier cluster avec un compte API dédié.')}</div><div class="notice">Les opérations sensibles sont désactivées par défaut. Les migrations et réplications entre clusters restent au stade du plan d’architecture.</div>`}
function openDatacenterForm(){modal('Ajouter un datacenter Proxmox',`<form id="datacenterForm"><div class="form-grid">${field('Nom','dc_name')}${field('URL HTTPS, ex. https://pve.exemple:8006','dc_url')}${selectField('Token API Proxmox', 'dc_credential',[['','— Sélectionner —'],...state.credentials.map(c=>[c.id,c.name])],'')}</div><p class="hint">Configurer PROXMOX_ALLOWED_HOSTS avant l’enregistrement. API token dédié au moindre privilège ; certificat TLS vérifié.</p><button class="btn primary" type="submit">Enregistrer</button></form>`)}
function updates(){return title('Versions & mises à jour','Mises à jour Git déclenchées depuis l’interface, exécutées par un service de maintenance séparé.')+`<div class="panel"><div class="panel-title"><h2>OrigamiTasks v0.3.3-rc1</h2><span class="status success">Installé</span></div><p class="muted">Branche suivie : <span class="code-inline">main</span> · Origine Git configurée sur l’hôte</p><div class="tool-row"><button class="btn" data-action="update-request" data-value="check">⌕ Rechercher des mises à jour</button><button class="btn primary" data-action="update-request" data-value="apply">⇣ Installer la dernière version Git</button><button class="btn" data-action="refresh-updates">↻</button></div><div class="notice">Sécurité : la mise à jour est <b>approuvée manuellement</b> par un administrateur. Un service hôte indépendant effectue un <b>git fetch + fast-forward</b>, une sauvegarde et le redéploiement Docker. Aucun accès Docker n'est accordé au conteneur web. Installer le service <span class="code-inline">ops/update-worker.py</span> pour activer le bouton d’installation.</div></div><div class="panel"><div class="panel-title"><h2>Maintenance & tâches Git</h2></div><div id="updateStatus">Chargement...</div></div>`}
function audit(){return title('Journal d’audit','Traçabilité des connexions, modifications, exécutions et opérations Git.')+`<div class="panel" id="auditTable">Chargement...</div>`}
function modal(title,body,footer='',wide=false){$('#modalRoot').innerHTML=`<div class="modal-overlay"><div class="modal ${wide?'xl':''}" role="dialog" aria-modal="true"><div class="modal-head"><h2>${title}</h2><button class="close" data-action="modal-close" aria-label="Fermer">×</button></div><div class="modal-body">${body}</div>${footer?`<div class="modal-footer">${footer}</div>`:''}</div></div>`}
function closeModal(){$('#modalRoot').innerHTML='';state.editor=null}
async function webhookManager(id){const job=state.jobs.find(j=>j.id===id);try{const r=await api('/jobs/'+id+'/webhook');modal('Webhook · '+e(job?.name||id),`<p class="muted">Déclenchez ce workflow via un événement externe, avec un jeton secret distinct.</p><div class="field"><span>Endpoint (requête POST)</span><input readonly value="${e(location.origin+'/api/hooks/'+id)}"></div><p>${r.enabled?'<span class="status success">Webhook actif</span>':'<span class="status">Webhook désactivé</span>'}</p><div class="notice">Envoyez le jeton dans l’en-tête <span class="code-inline">X-Origami-Webhook-Token</span>. Un corps JSON optionnel fournit des variables accessibles comme <span class="code-inline">{{ var.ticket_id }}</span>. Le jeton n’est affiché qu’à la création/rotation.</div>`,`<button class="btn" data-action="modal-close">Fermer</button>${r.enabled?`<button class="btn danger" data-action="disable-webhook" data-id="${id}">Désactiver</button>`:''}<button class="btn primary" data-action="rotate-webhook" data-id="${id}">${r.enabled?'↻ Régénérer le jeton':'＋ Activer'}</button>`)}catch(ex){toast(ex.message,false)}}
async function rotateWebhook(id){if(!confirm('Le jeton précédent cessera de fonctionner. Continuer ?'))return;try{const r=await api('/jobs/'+id+'/webhook','POST');modal('Jeton webhook — copier maintenant',`<div class="notice">Ce jeton ne sera <b>plus jamais affiché</b>. Enregistrez-le dans votre coffre-fort.</div><div class="field"><span>URL</span><input readonly value="${e(location.origin+r.path)}"></div><div class="field"><span>Jeton</span><textarea readonly>${e(r.token)}</textarea></div><div class="hint">Exemple : curl -X POST -H 'X-Origami-Webhook-Token: &lt;token&gt;' -H 'Content-Type: application/json' -d '{"ticket_id":"123"}' …</div>`,`<button class="btn primary" data-action="modal-close">J’ai sauvegardé le jeton</button>`)}catch(ex){toast(ex.message,false)}}
async function disableWebhook(id){if(!confirm('Révoquer immédiatement ce webhook ?'))return;try{await api('/jobs/'+id+'/webhook','DELETE');toast('Webhook désactivé');closeModal()}catch(ex){toast(ex.message,false)}}
function newCredential(){modal('Créer un identifiant sécurisé',`<form id="credentialForm"><div class="form-grid"><div class="field"><span>Nom interne</span><input name="name" required placeholder="SMTP • Production"></div><div class="field"><span>Type</span><select name="kind"><option value="ssh">SSH / SFTP</option><option value="smtp">SMTP</option><option value="ftp">FTP / FTPS</option><option value="api">API / Token</option><option value="generic">Générique</option></select></div><div class="field"><span>Utilisateur</span><input name="username" autocomplete="off"></div><div class="field"><span>Mot de passe</span><input name="password" type="password" autocomplete="new-password"></div><div class="field full"><span>Token d'API</span><input name="token" type="password" autocomplete="new-password"></div><div class="field full"><span>Clé privée SSH (facultative)</span><textarea name="private_key" rows="3" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></div><div class="field full"><span>Passphrase clé SSH</span><input name="passphrase" type="password"></div></div><div class="notice">Les champs non vides sont chiffrés avec la clé locale. Ils ne seront plus affichables après sauvegarde.</div><button type="submit" class="btn primary wide" style="margin-top:15px">Chiffrer et enregistrer ↗</button></form>`)}
const kinds=['agent','directory','shell','powershell','ssh','ansible','matrix','proxmox','sftp','ftp','file','http','email','delay','set_variable'];
const kindLabel={agent:'Agent Linux / Windows',directory:'LDAP / Active Directory',shell:'Shell Linux',powershell:'PowerShell local',ssh:'SSH / PowerShell distant',ansible:'Ansible Playbook',matrix:'Message Matrix',proxmox:'Proxmox VE',sftp:'Transfert SFTP',ftp:'Transfert FTP/FTPS',file:'Fichiers / Dossiers',http:'Requête HTTP',email:'Envoyer un email',delay:'Pause / attente',set_variable:'Définir une variable'};
const FIELDS={
 agent:[['agent_id','Agent enregistré','agent'],['action','Action','select:inventory|script|processes|disk|service_status|service_start|service_stop|service_restart'],['script','Bash (Linux) ou PowerShell (Windows)','textarea'],['service','Nom du service','text'],['timeout','Timeout en secondes','number'],['approved','Confirmer action sur service','checkbox']],
 directory:[['action','Opération LDAP/AD','select:search|get_user|create_user|modify_user|group_add|group_remove|enable_user|disable_user|unlock_user|reset_password'],['uri','Serveur LDAP (facultatif si configuré en administration)','text'],['starttls','Utiliser STARTTLS','checkbox'],['base_dn','Base DN autorisée','text'],['target_dn','DN de compte ciblé','text'],['group_dn','DN de groupe ciblé','text'],['lookup_attribute','Rechercher par','select:sAMAccountName|uid|cn|mail|userPrincipalName'],['lookup_value','Valeur exacte recherchée','text'],['read_attributes','Attributs affichés (virgules)','text'],['attributes','Attributs JSON (création/modification)','textarea'],['member_attribute','Attribut de groupe','select:member|uniqueMember'],['credential_id','Compte LDAP du coffre (facultatif avec Administration / LDAP)','credential'],['password_credential_id','Secret nouveau mot de passe (reset)','credential'],['approved','Confirmer écriture annuaire','checkbox']],

 matrix:[['homeserver','Homeserver HTTPS','text'],['room_id','Room ID Matrix (!...:serveur)','text'],['message','Message (variables possibles)','textarea'],['credential_id','Token bot Matrix','credential']],
 ansible:[['host','Runner Ansible SSH','text'],['port','Port SSH','number'],['playbook','Playbook dans /opt/origamitasks/ansible/playbooks','text'],['inventory','Inventaire dans /opt/origamitasks/ansible/inventories','text'],['limit','Limite de groupes / hôtes','text'],['extra_vars','Variables JSON','textarea'],['check','Mode --check','checkbox'],['credential_id','Identifiant SSH du runner','credential']],
 proxmox:[['datacenter_id','Datacenter déclaré','datacenter'],['action','Opération','select:inventory|start|shutdown|reboot'],['node','Nœud cible (actions uniquement)','text'],['vmid','VMID (actions uniquement)','number'],['approved','Autoriser explicitement cette action','checkbox']],

 shell:[['script','Script bash','textarea'],['timeout','Timeout (s)','number'],['credential_id','Identifiant','credential']],
 powershell:[['script','Script PowerShell','textarea'],['timeout','Timeout (s)','number'],['credential_id','Identifiant','credential']],
 ssh:[['host','Hôte / FQDN','text'],['port','Port SSH','number'],['platform','Système distant','select:linux|windows'],['script','Script distant','textarea'],['timeout','Timeout (s)','number'],['credential_id','Identifiant SSH','credential']],
 sftp:[['host','Hôte / FQDN','text'],['port','Port SSH','number'],['remote_path','Fichier distant','text'],['local_path','Fichier local (transfers/)','text'],['direction','Sens','select:get|put'],['credential_id','Identifiant','credential']],
 ftp:[['host','Hôte FTP','text'],['port','Port FTP','number'],['remote_path','Fichier distant','text'],['local_path','Fichier local (transfers/)','text'],['direction','Sens','select:get|put'],['allow_plaintext','Autoriser FTP non chiffré (déconseillé)','checkbox'],['credential_id','Identifiant','credential']],
 http:[['url','URL','text'],['method','Méthode','select:GET|POST|PUT|PATCH|DELETE'],['body','Corps','textarea'],['timeout','Timeout (s)','number'],['credential_id','Token Bearer','credential']],
 file:[['action','Opération','select:list|read|write|copy|move|mkdir|delete'],['path','Chemin cible sous transfers/','text'],['source','Source copy/move sous transfers/','text'],['content','Contenu (write)','textarea'],['approved','Confirmer la suppression (delete)','checkbox']],
 email:[['host','Serveur SMTP (facultatif si renseigné dans le coffre)','text'],['port','Port SMTP (facultatif)','number'],['ssl','Forcer SSL immédiat pour cette tâche','checkbox'],['from','Expéditeur (facultatif si défini dans le coffre)','text'],['to','Destinataires','text'],['subject','Objet','text'],['body','Corps texte','textarea'],['html_body','Corps HTML (optionnel)','textarea'],['attachments','Pièces jointes (transfers/ séparées par virgules)','text'],['credential_id','Connexion SMTP configurée dans le coffre','credential']],
 delay:[['seconds','Secondes (maximum 300)','number']],
 set_variable:[['name','Nom de la variable','text'],['value','Valeur','textarea']]
};
function freshJob(){return {name:'',description:'',folder:'Général',tags_json:[],acl_json:{view:[],run:[],edit:[]},steps_json:[],schedule_json:{cron:'',timezone:'Europe/Paris'},enabled:1}}
function addStep(){return {id:'step_'+Math.random().toString(36).slice(2,11),name:'Nouvelle étape',type:'delay',config:{seconds:1},retries:0,retry_delay:2,on_success:'',on_failure:'',condition:{enabled:false,source:'last_status',operator:'equals',value:'success'}}}
function openEditor(id=null,clone=false){const found=id?state.jobs.find(j=>j.id===Number(id)):null;if(found&&!clone&&!found.permissions.edit){toast('Modification non autorisée',false);return}if(!roleAdmin()&&!found){toast('Création réservée aux administrateurs',false);return}state.editor=found?JSON.parse(JSON.stringify(found)):freshJob();if(clone){delete state.editor.id;state.editor.name+=' (copie)'}if(!state.editor.steps_json.length)state.editor.steps_json.push(addStep());state.selectedStep=0;renderEditor()}
const field=(label,name,value='',type='text',full=false)=>`<div class="field ${full?'full':''}"><span>${label}</span><input name="${name}" ${type==='checkbox'?'type="checkbox"'+(value?' checked':''):'type="'+type+'" value="'+e(value)+'"'}></div>`;
function option(values,selected){return values.map(([value,label])=>`<option value="${e(value)}" ${String(value)===String(selected)?'selected':''}>${e(label)}</option>`).join('')}
function selectField(label,name,entries,value){return `<div class="field"><span>${label}</span><select name="${name}">${option(entries,value)}</select></div>`}

/* Visual workspace helpers: all labels and paths are escaped before entering HTML. */
const sidebarIcons={
 dashboard:'<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
 jobs:'<rect x="2" y="4" width="7" height="6" rx="1.5"/><rect x="15" y="14" width="7" height="6" rx="1.5"/><path d="M9 7h5a4 4 0 0 1 4 4v3M18 14l-3-3m3 3 3-3"/>',
 catalog:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
 runs:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 vault:'<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15" r="1"/>',
 agents:'<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 2v3m8-3v3M8 19v3m8-3v3M1 9h3m-3 6h3m16-6h3m-3 6h3"/><circle cx="12" cy="12" r="3"/>',
 datacenters:'<rect x="3" y="3" width="18" height="7" rx="1.5"/><rect x="3" y="14" width="18" height="7" rx="1.5"/><circle cx="7" cy="6.5" r=".7"/><circle cx="7" cy="17.5" r=".7"/><path d="M11 6.5h7m-7 11h7"/>',
 users:'<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m1 4a5 5 0 0 1 4 5"/>',
 updates:'<path d="M12 3v12m-5-5 5 5 5-5M4 18v3h16v-3"/>',
 ldap:'<circle cx="5" cy="12" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 12h5m0 0V5h5m-5 7v7h5"/>',
 audit:'<path d="M7 3h9l4 4v14H7z"/><path d="M16 3v5h4M10 12h7m-7 4h7m-7-8h2"/>'
};
function decorateSidebar(){
 $('.nav-item[data-page]').forEach(btn=>{
  const glyph=btn.querySelector('.nav-glyph'),path=sidebarIcons[btn.dataset.page];
  if(glyph&&path)glyph.innerHTML='<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+path+'</svg>';
 });
}

function openLayer(id,heading,body){
 const existing=document.getElementById(id);
 if(existing)existing.remove();
 const host=document.createElement('div');host.className='modal-overlay manager-overlay';host.id=id;
 host.innerHTML='<div class="modal manager-dialog" role="dialog" aria-modal="true" aria-label="'+e(heading)+'"><div class="modal-head"><h2>'+e(heading)+'</h2><button type="button" class="close" data-action="layer-close" data-layer="'+id+'" aria-label="Fermer">×</button></div><div class="modal-body">'+body+'</div></div>';
 $('#modalRoot').appendChild(host);
}
function closeLayer(id){document.getElementById(id)?.remove()}
function folderPanel(){
 const total=state.jobs.length,chosen=state.folderFilter;
 return '<div class="folder-panel"><div class="folder-title"><b>Dossiers</b><button class="btn tiny" data-action="folder-manage" title="Créer, renommer ou supprimer un dossier">Gérer</button></div>'
  +'<button class="folder-node '+(!chosen?'active':'')+'" data-action="folder-filter" data-path="">Tous les dossiers <small>'+total+'</small></button>'
  +state.folders.map(f=>{
    const count=state.jobs.filter(j=>j.folder===f.path||j.folder.startsWith(f.path+'/')).length;
    return '<button class="folder-node '+(chosen===f.path?'active':'')+'" style="padding-left:'+(12+f.depth*14)+'px" data-action="folder-filter" data-path="'+e(f.path)+'"><span class="folder-icon">▱</span>'+e(f.name)+'<small>'+count+'</small></button>';
   }).join('')
  +'</div>';
}
function foldersBrowser(){
 if(state.editor)captureEditor();
 state.folderSelected=state.editor?.folder||state.folderFilter||'Général';
 drawFolderBrowser();
}
function drawFolderBrowser(){
 const chosen=state.folderSelected;
 const parentOptions=[['','À la racine'],...state.folders.map(f=>[f.path,'　'.repeat(f.depth)+f.name])];
 const rows=state.folders.map(f=>'<button type="button" class="folder-node '+(chosen===f.path?'active':'')+'" data-action="folder-select" data-path="'+e(f.path)+'" style="padding-left:'+(12+f.depth*14)+'px"><span class="folder-icon">▱</span>'+e(f.name)+'</button>').join('');
 const current=state.folders.find(f=>f.path===chosen);
 const body='<div class="manager-grid"><div><p class="hint">Arborescence des dossiers</p><div class="folder-picker-list">'+rows+'</div></div><div>'
  +'<h3>Dossier sélectionné</h3><p class="hint">'+e(chosen)+'</p>'
  +'<div class="tool-row"><button class="btn primary" data-action="folder-use">✓ '+(state.editor?'Utiliser pour ce workflow':'Filtrer ce dossier')+'</button></div>'
  +'<div class="divider"></div><h3>Créer un sous-dossier</h3>'
  +'<form id="folderCreateForm"><div class="field"><span>Dossier parent</span><select name="parent">'+option(parentOptions,chosen)+'</select></div><div class="field"><span>Nom</span><input name="name" required maxlength="70" placeholder="Exploitation, Production…"></div><button type="submit" class="btn primary">Créer le dossier</button></form>'
  +'<div class="divider"></div><h3>Renommer</h3><form id="folderRenameForm"><div class="field"><span>Nouveau nom</span><input name="name" maxlength="70" required value="'+e(current?.name||'')+'" '+(chosen==='Général'?'disabled':'')+'></div><button type="submit" class="btn" '+(chosen==='Général'?'disabled':'')+'>Renommer</button></form>'
  +'<div class="divider"></div><button class="btn danger" data-action="folder-remove" '+(chosen==='Général'?'disabled':'')+'>Supprimer le dossier</button><p class="hint">La suppression déplace les workflows vers le dossier parent, sans les effacer.</p></div></div>';
 openLayer('folderLayer','Gestion des dossiers',body);
}
async function reloadFolders(){state.folders=(await api('/folders')).items}

function folderEditorField(value){
 const choices=[['','— Sélectionner un dossier —'],...state.folders.map(f=>[f.path,'　'.repeat(f.depth)+f.name])];
 if(value&&!choices.some(x=>x[0]===value))choices.push([value,value]);
 return '<div class="field"><span>Dossier du workflow</span><div class="inline-picker"><select name="job_folder">'+option(choices,value)+'</select><button type="button" class="btn" data-action="folder-manage">Parcourir / gérer</button></div></div>';
}
function groupEditorField(label,key,groups){
 return '<div class="field"><span>'+e(label)+'</span><div class="inline-picker"><input type="hidden" name="acl_'+key+'" value="'+e((groups||[]).join(','))+'"><div class="selected-items">'+((groups||[]).map(g=>'<span class="chip tag">'+e(g)+'</span>').join('')||'<span class="hint">Aucun groupe sélectionné</span>')+'</div><button type="button" class="btn" data-action="group-browser" data-role="'+key+'" '+(!roleAdmin()?'disabled':'')+'>Rechercher</button></div></div>';
}
function scheduleFields(schedule){
 const mode=schedule.mode||(schedule.cron?'cron':'manual');
 const modeOptions=[['manual','Manuel / événement'],['once','Une seule fois'],['interval','Répéter à intervalle'],['cron','Cron avancé']];
 const units=[['minutes','minute(s)'],['hours','heure(s)'],['days','jour(s)'],['weeks','semaine(s)']];
 return '<div class="field"><span>Déclenchement</span><select name="job_schedule_mode">'+option(modeOptions,mode)+'</select></div>'
  +'<div class="field" data-schedule-mode="once"><span>Date et heure d’exécution</span><input type="datetime-local" name="job_at" value="'+e(schedule.at||'')+'"></div>'
  +'<div class="field" data-schedule-mode="interval"><span>Répéter toutes les</span><div class="inline-picker"><input type="number" name="job_every" min="1" max="100000" value="'+e(schedule.every||5)+'"><select name="job_unit">'+option(units,schedule.unit||'minutes')+'</select></div></div>'
  +'<div class="field" data-schedule-mode="cron"><span>Cron (5 champs)</span><input name="job_cron" placeholder="*/5 * * * *" value="'+e(schedule.cron||'')+'"><small class="hint">Minutes · Heures · Jour · Mois · Jour de semaine</small></div>'
  +field('Fuseau horaire','job_timezone',schedule.timezone||'Europe/Paris');
}
function showScheduleMode(){
 const mode=document.querySelector('.modal select[name="job_schedule_mode"]')?.value||'manual';
 $('[data-schedule-mode]',document.querySelector('.modal')||document).forEach(el=>el.hidden=el.dataset.scheduleMode!==mode);
}
function captureSchedule(v){
 const mode=v('job_schedule_mode')||'manual', timezone=v('job_timezone')||'Europe/Paris';
 const schedule={mode,timezone};
 if(mode==='cron')schedule.cron=v('job_cron').trim();
 if(mode==='once')schedule.at=v('job_at');
 if(mode==='interval'){schedule.every=Number(v('job_every')||1);schedule.unit=v('job_unit')||'minutes'}
 return schedule;
}


function reloadCredentials(){return api('/credentials').then(r=>{state.credentials=r.items})}
function openCredentialBrowser(field=null,action=''){
 if(state.editor)captureEditor();
 state.pickerField=field;state.credentialAction=action;state.pickerCredentialId=null;state.credentialSearch='';
 drawCredentialBrowser();
}

const connectionKinds=[
 ['smtp','SMTP · Envoi d’e-mails'],['ssh','SSH / SFTP · Linux et Windows'],
 ['ftp','FTP / FTPS · Transfert'],['ldap','LDAP / Active Directory'],
 ['api','API · Token HTTP'],['matrix','Matrix · Bot'],['proxmox','Proxmox · API'],
 ['generic','Identifiant générique']
];
const connectionSchemas={
 smtp:[
  ['host','Serveur SMTP / FQDN','text','smtp.example.org'],
  ['port','Port TCP','number','587'],
  ['tls_mode','Sécurité de transport','select','starttls',[['starttls','STARTTLS (port 587)'],['ssl','SSL/TLS direct (port 465)'],['none','Aucune (relais SMTP interne seulement)']]],
  ['auth','Le serveur requiert une authentification','checkbox',true],
  ['username','Identifiant SMTP','text','utilisateur@exemple.fr'],
  ['password','Mot de passe SMTP','password',''],
  ['from_address','Adresse expéditeur par défaut','email','noreply@exemple.fr']
 ],
 ssh:[
  ['host','Serveur SSH / FQDN','text','server.example.org'],['port','Port SSH','number','22'],
  ['username','Utilisateur SSH','text','root'],['password','Mot de passe SSH','password',''],
  ['private_key','Clé privée OpenSSH','textarea',''],['passphrase','Phrase secrète de la clé','password','']
 ],
 ftp:[
  ['host','Serveur FTP / FQDN','text','ftp.example.org'],['port','Port FTP / FTPS','number','21'],
  ['tls_mode','Transport','select','explicit',[['explicit','FTPS explicite avec TLS'],['none','FTP non chiffré — à éviter']]],
  ['username','Utilisateur FTP','text',''],['password','Mot de passe FTP','password','']
 ],
 ldap:[
  ['uri','Adresse LDAP avec port','text','ldaps://ldap.example.org:636'],
  ['starttls','Connexion LDAP avec STARTTLS','checkbox',false],
  ['base_dn','Base DN','text','dc=example,dc=org'],
  ['group_base_dn','Base DN des groupes','text','ou=Groups,dc=example,dc=org'],
  ['username','DN du compte de liaison','text','cn=service,ou=Applications,dc=example,dc=org'],
  ['password','Mot de passe de liaison','password','']
 ],
 api:[
  ['base_url','URL de base HTTPS','text','https://api.example.org'],
  ['test_path','Chemin GET de diagnostic (optionnel)','text','/health'],
  ['token','Jeton API','password',''],
  ['username','Utilisateur (si API Basic)','text',''],
  ['password','Mot de passe (si API Basic)','password','']
 ],
 matrix:[
  ['homeserver','URL du homeserver HTTPS','text','https://matrix.example.org'],
  ['token','Jeton du bot Matrix','password',''],
  ['username','Identifiant du bot (facultatif)','text','']
 ],
 proxmox:[
  ['url','URL API Proxmox','text','https://pve.example.org:8006'],
  ['username','Utilisateur / ID de token API','text','user@pve!origamitasks'],
  ['token','Secret du jeton API','password','']
 ],
 generic:[
  ['username','Utilisateur','text',''],['password','Mot de passe','password',''],
  ['token','Jeton / clé API','password',''],['private_key','Clé privée SSH','textarea',''],
  ['passphrase','Phrase secrète SSH','password','']
 ]
};
function credentialProfileFields(kind,credential){
 const profile=credential?.public||{},present=credential?.fields||[];
 const schema=connectionSchemas[kind]||connectionSchemas.generic;
 return '<div class="form-grid credential-protocol-fields">'+schema.map(([key,label,typ,fallback,choices])=>{
  const existing=profile[key]??'';
  const value=existing===''?fallback:existing;
  let control='';
  if(typ==='checkbox')control='<input type="checkbox" name="'+key+'" '+((typeof existing==='boolean'?existing:fallback)?'checked':'')+'>';
  else if(typ==='select')control='<select name="'+key+'">'+option(choices,existing||fallback)+'</select>';
  else if(typ==='textarea')control='<textarea name="'+key+'" rows="4" placeholder="'+e(present.includes(key)?'Déjà renseigné · vide = conserver':'Clé privée ou secret')+'"></textarea>';
  else control='<input name="'+key+'" type="'+typ+'" '+(typ==='number'?'min="1" max="65535" ':'')
    +' value="'+e(typ==='password'?'':existing||'')+'" placeholder="'+e(typ==='password'&&present.includes(key)?'Déjà renseigné · vide = conserver':fallback||'')+'" '
    +(kind==='smtp'&&key==='host'?'required':'')+'>';
  if(typ==='number')control='<input type="number" name="'+key+'" min="1" max="65535" value="'+e(existing||fallback||'')+'" required>';
  const cls=typ==='textarea'?'field full':typ==='checkbox'?'field checkbox':'field';
  return '<label class="'+cls+'" '+(kind==='smtp'&&['username','password'].includes(key)?'data-smtp-auth-field':'')+'><span>'+e(label)+'</span>'+control+'</label>';
 }).join('')+'</div>'
 +(kind==='smtp'?'<div class="notice">Pour le SMTP, configure le serveur, le port, la sécurité et l’authentification. La tâche utilise automatiquement ces paramètres lorsque sa connexion est sélectionnée.</div>':'');
}
function credentialKindForStep(){
 const type=state.editor?.steps_json[state.selectedStep]?.type;
 return ({email:'smtp',ssh:'ssh',sftp:'ssh',ftp:'ftp',ansible:'ssh',matrix:'matrix',directory:'ldap',http:'api',proxmox:'proxmox'})[type]||'generic';
}
function smtpAuthFieldsVisibility(){
 const form=document.getElementById('credentialManageForm');
 if(!form)return;
 const enabled=form.elements.namedItem('auth')?.checked;
 form.querySelectorAll('[data-smtp-auth-field]').forEach(el=>el.hidden=!enabled);
}
function drawCredentialBrowser(){
 const selected=state.credentials.find(c=>c.id===state.pickerCredentialId);
 const clone=state.credentialAction==='clone',edit=state.credentialAction==='edit';
 let body='';
 if(state.credentialAction){
  const kind=selected?.kind||credentialKindForStep();
  const name=clone?(selected?.name||'')+' (copie)':(edit?selected?.name||'':'');
  body='<button class="btn tiny" data-action="credential-list">← Retour aux connexions</button>'
   +'<form id="credentialManageForm"><div class="form-grid">'
   +'<div class="field"><span>Nom de la connexion</span><input name="name" value="'+e(name)+'" maxlength="120" required></div>'
   +'<div class="field"><span>Protocole / type de connexion</span><select name="kind" '+(edit||clone?'disabled':'')+'>'+option(connectionKinds,kind)+'</select></div></div>'
   +(clone?'<div class="notice">La copie conserve les paramètres et secrets chiffrés. Aucune valeur sensible n’est affichée.</div>':
    '<div id="credentialProfile">'+credentialProfileFields(kind,edit?selected:null)+'</div>'
    +'<div class="hint">Les mots de passe et jetons restent masqués ; laisser ces champs vides en modification conserve leurs valeurs.</div>')
   +'<div class="tool-row" style="margin-top:16px"><button class="btn primary" type="submit">'+(clone?'Cloner cette connexion':edit?'Enregistrer les modifications':'Créer et chiffrer la connexion')+'</button>'+(edit?'<button class="btn" type="button" data-action="credential-test" data-id="'+selected.id+'">Tester la connexion enregistrée</button>':'')+'</div>'+(edit?'<p class="hint">Le test utilise les paramètres déjà enregistrés : enregistrer d’abord les éventuelles modifications.</p>':'')+'</form>';
 }else{
  const target=state.pickerField?credentialKindForStep():null;
  body='<div class="tool-row"><input id="credentialSearch" placeholder="Rechercher une connexion (nom, type, hôte…)" value="'+e(state.credentialSearch||'')+'">'
   +'<button class="btn primary" data-action="credential-new">＋ Nouvelle connexion</button></div>'
   +(target?'<p class="hint">Connexions conseillées pour cette tâche : '+e(connectionKinds.find(x=>x[0]===target)?.[1]||target)+'. Les anciennes connexions API ou génériques restent utilisables.</p>':'')
   +'<div class="credential-list">'+(state.credentials.map(c=>{
      const compatible=!target||c.kind===target||c.kind==='generic'||(['matrix','proxmox'].includes(target)&&c.kind==='api');
      return '<div class="credential-entry" data-credential-search="'+e((c.name+' '+c.kind+' '+(c.public?.host||c.public?.uri||c.public?.url||'')).toLowerCase())+'">'
       +'<div><b>'+e(c.name)+'</b><p class="hint">'+e(c.kind.toUpperCase())+' · '+e(c.public?.host||c.public?.uri||c.public?.url||'')+' · #'+c.id+'</p></div>'
       +'<div class="credential-actions">'+(state.pickerField?'<button class="btn tiny primary" data-action="credential-select" data-id="'+c.id+'" '+(compatible?'':'disabled title="Protocole non compatible"')+'>Sélectionner</button>':'')
       +'<button class="btn tiny" data-action="credential-test" data-id="'+c.id+'">Tester</button>'
       +'<button class="btn tiny" data-action="credential-edit" data-id="'+c.id+'">Modifier</button>'
       +'<button class="btn tiny" data-action="credential-clone" data-id="'+c.id+'">Cloner</button>'
       +'<button class="btn tiny danger" data-action="credential-remove" data-id="'+c.id+'">Supprimer</button></div></div>';
    }).join('')||'<p class="hint">Aucune connexion. Crée un profil adapté au protocole.</p>')+'</div>';
 }
 openLayer('credentialLayer',state.pickerField?'Choisir une connexion sécurisée':'Connexions du coffre-fort',body);
 filterCredentialRows();
 smtpAuthFieldsVisibility();
}
function filterCredentialRows(){
 $$('#credentialLayer [data-credential-search]').forEach(row=>{
  row.hidden=!(row.dataset.credentialSearch||'').includes((state.credentialSearch||'').toLowerCase());
 });
}

function testOptionInput(name,label,placeholder='',value='',type='text',hint=''){
 return '<label class="field"><span>'+e(label)+'</span><input name="'+e(name)+'" type="'+type+'" value="'+e(value)+'" placeholder="'+e(placeholder)+'" autocomplete="off">'
 +(hint?'<small class="hint">'+e(hint)+'</small>':'')+'</label>';
}
function testOptionCheck(name,label,hint=''){
 return '<label class="test-wizard-option"><input type="checkbox" name="'+e(name)+'"><span><strong>'+e(label)+'</strong>'
 +(hint?'<small>'+e(hint)+'</small>':'')+'</span></label>';
}
function testAdvancedFields(trigger,html){
 return '<div class="test-wizard-dependent" data-test-requires="'+e(trigger)+'" hidden>'+html+'</div>';
}
function credentialTestFields(entry){
 const kind=entry.kind,publicData=entry.public||{};
 if(kind==='smtp'){
  return '<p>Vérifier la connexion SMTP, TLS et l’authentification. Pour tester également la réception, envoyer un vrai message à une adresse choisie.</p>'
   +testOptionCheck('send_email','Envoyer un e-mail de test réel','Un message sera envoyé uniquement après avoir lancé le diagnostic.')
   +testAdvancedFields('send_email',
     '<div class="form-grid">'+testOptionInput('recipient','Adresse e-mail destinataire','prenom@exemple.fr','','email')
     +testOptionInput('subject','Objet du message','','[OrigamiTasks] Test SMTP')+'</div>'
     +'<label class="field"><span>Corps du message (facultatif)</span><textarea name="message" rows="3" maxlength="1500" placeholder="Ceci est un test de réception du serveur SMTP."></textarea></label>'
     +'<div class="notice">Le serveur SMTP peut accepter le message sans qu’il soit encore arrivé dans la boîte. Vérifier la réception et les spams à l’aide de la référence renvoyée. Un seul destinataire par test, délai de 30 secondes entre deux envois.</div>'
   )
   +'<p class="hint">Expéditeur configuré : '+e(publicData.from_address||(String(publicData.username||'').includes('@')?publicData.username:'à renseigner dans le profil SMTP'))+'</p>';
 }
 if(kind==='ssh'){
  return '<p>Test de connexion et d’authentification SSH avec vérification stricte de la clé d’hôte.</p>'
   +testOptionCheck('check_sftp','Tester également le sous-système SFTP','Lit uniquement les métadonnées du répertoire courant.')
   +testOptionCheck('check_whoami','Exécuter la commande de diagnostic whoami','Commande fixe, en lecture seule. Aucune autre commande n’est autorisée par ce test.');
 }
 if(kind==='ftp'){
  return '<p>Vérification du port FTP/FTPS, de TLS et des identifiants. Aucun fichier ne sera transféré.</p>'
   +testOptionCheck('check_directory','Tester les droits de lecture du répertoire','Utilise uniquement la commande PWD, si FTPS est activé.')
   +'<div class="notice">Sur FTP non chiffré, aucun mot de passe n’est envoyé : la vérification se limite à la connectivité.</div>';
 }
 if(kind==='ldap'){
  return '<p>Vérification LDAPS/STARTTLS, liaison du compte de service et base DN.</p><div class="form-grid">'
   +testOptionInput('test_user','Rechercher un utilisateur (facultatif)','login')
   +testOptionInput('test_group','Rechercher un groupe (facultatif)','exploitation')+'</div>'
   +'<p class="hint">La recherche utilise uniquement des filtres échappés, sans modification LDAP.</p>';
 }
 if(kind==='matrix'){
  return '<p>Vérification du jeton Matrix avec whoami.</p>'
   +testOptionInput('room_id','Identifiant de room pour les tests facultatifs','!id:example.org')
   +testOptionCheck('check_room','Tester l’appartenance du bot à la room','Lecture de son état de membre.')
   +testOptionCheck('send_message','Envoyer un vrai message de test dans la room','Aucun message n’est envoyé sans choix explicite.')
   +testAdvancedFields('send_message',
     '<label class="field"><span>Contenu du message de test</span><textarea name="message" rows="3" maxlength="1000" placeholder="[OrigamiTasks] Test de notification Matrix"></textarea></label>'
     +'<div class="notice">L’envoi crée réellement un message dans la room indiquée. Pas de test E2EE.</div>');
 }
 if(kind==='proxmox'){
  return '<p>Authentification du jeton Proxmox et lecture des permissions, sans toucher aux VM.</p>'
   +testOptionCheck('check_nodes','Vérifier également la liste des nœuds','GET /api2/json/nodes, sans action')
   +testOptionInput('node','Tester la lecture du statut d’un nœud (facultatif)','pve01');
 }
 if(kind==='api'){
  return '<p>Test HTTP(S) de lecture seulement, vers un hôte explicitement autorisé.</p>'
   +'<div class="form-grid"><label class="field"><span>Méthode HTTP</span><select name="method">'
   +option([['GET','GET · Lire une ressource'],['HEAD','HEAD · Vérifier les en-têtes']],'GET')
   +'</select></label>'
   +testOptionInput('path','Chemin de diagnostic','/health',publicData.test_path||'/')
   +'</div>'+testOptionInput('expected_status','Code HTTP attendu','2xx ou 200','2xx')
   +'<div class="notice">Un statut HTTP réussi ne garantit pas que le serveur a réellement vérifié le token. Utiliser un endpoint nécessitant l’authentification. Aucun POST/PUT/DELETE autorisé ici.</div>';
 }
 return '<div class="notice">Ce type de coffre ne définit aucun protocole à tester. Créer une connexion typée avec un serveur ou une URL.</div>';
}
function updateCredentialTestVisibility(){
 const form=document.getElementById('credentialTestForm');
 if(!form)return;
 form.querySelectorAll('[data-test-requires]').forEach(el=>{
  const trigger=form.elements.namedItem(el.dataset.testRequires);
  const visible=!!trigger?.checked;
  el.hidden=!visible;
  el.querySelectorAll('input[required],textarea[required]').forEach(input=>input.required=visible);
 });
 const recipient=form.elements.namedItem('recipient');
 if(recipient)recipient.required=!!form.elements.namedItem('send_email')?.checked;
 const room=form.elements.namedItem('room_id');
 if(room)room.required=!!(form.elements.namedItem('check_room')?.checked||form.elements.namedItem('send_message')?.checked);
 const send=!!(form.elements.namedItem('send_email')?.checked||form.elements.namedItem('send_message')?.checked);
 const submit=form.querySelector('button[type="submit"]');
 if(submit)submit.textContent=send?'Envoyer le message de test':'Lancer le diagnostic sans envoi';
}
function openCredentialTestWizard(id){
 const entry=state.credentials.find(c=>c.id===id);
 if(!entry)return toast('Connexion introuvable',false);
 const body='<div class="connection-test-intro"><span class="chip tag">'+e(entry.kind.toUpperCase())+'</span><strong>'+e(entry.name)+'</strong>'
  +'<span class="hint">'+e(entry.public?.host||entry.public?.uri||entry.public?.homeserver||entry.public?.url||entry.public?.base_url||'')+'</span></div>'
  +'<form id="credentialTestForm" data-id="'+id+'"><div class="connection-test-config">'
  +credentialTestFields(entry)+'</div>'
  +'<div class="tool-row"><button class="btn primary" type="submit" '+(entry.kind==='generic'?'disabled':'')+'>Lancer le diagnostic</button>'
  +'<button class="btn" type="button" data-action="layer-close" data-layer="credentialTestLayer">Fermer</button></div>'
  +'<div id="credentialTestResult" aria-live="polite"></div></form>';
 openLayer('credentialTestLayer','Tester une connexion',body);
 updateCredentialTestVisibility();
}
function collectCredentialTestOptions(form,kind){
 const data=new FormData(form),options={};
 const boolKeys={smtp:['send_email'],ssh:['check_sftp','check_whoami'],ftp:['check_directory'],
                 matrix:['check_room','send_message'],proxmox:['check_nodes']};
 const textKeys={smtp:['recipient','subject','message'],ldap:['test_user','test_group'],
                 matrix:['room_id','message'],proxmox:['node'],
                 api:['method','path','expected_status']};
 (boolKeys[kind]||[]).forEach(k=>options[k]=data.has(k));
 (textKeys[kind]||[]).forEach(k=>{const value=String(data.get(k)||'').trim();if(value)options[k]=value});
 if(options.send_email||options.send_message)options.confirm_send=true;
 return options;
}
async function submitCredentialTest(form){
 const id=Number(form.dataset.id),entry=state.credentials.find(c=>c.id===id);
 if(!entry)return toast('Connexion introuvable',false);
 const target=document.getElementById('credentialTestResult');
 const submit=form.querySelector('button[type="submit"]');
 if(submit)submit.disabled=true;
 if(target)target.innerHTML='<div class="notice">Diagnostic en cours…</div>';
 try{
  const options=collectCredentialTestOptions(form,entry.kind);
  const result=await api('/credentials/'+id+'/test','POST',{options});
  const lines=(result.checks||[]).map(item=>{
   const good=item.status==='success',warn=item.status==='warning';
   return '<div class="connection-test-row"><span class="connection-test-mark '+(good?'passed':warn?'warning':'untested')+'">'+(good?'✓':warn?'!':'—')+'</span><div><strong>'+e(item.name)+'</strong><p class="hint">'+e(item.detail)+'</p></div></div>';
  }).join('');
  if(target)target.innerHTML='<div class="notice"><strong>Diagnostic effectué</strong> — '+e(result.message||'')+(typeof result.elapsed_ms==='number'?' <span class="hint">· '+e(result.elapsed_ms)+' ms</span>':'')+'</div>'
   +'<div class="connection-test-list">'+lines+'</div>'
   +(result.reference?'<p class="hint">Référence à rechercher dans le message : <code>'+e(result.reference)+'</code></p>':'');
 }catch(ex){
  if(target)target.innerHTML='<div class="notice error"><strong>Échec du diagnostic</strong> — '+e(ex.message)+'</div>'
   +'<p class="hint">Vérifier le serveur, les identifiants, les certificats et les listes de destinations autorisées.</p>';
 }finally{if(submit)submit.disabled=false}
}

function credentialSelected(id){
 const step=state.editor?.steps_json[state.selectedStep];
 if(!step||!state.pickerField)return;
 step.config[state.pickerField]=id;
 closeLayer('credentialLayer');renderEditor();
}
function credentialField(label,key,value){
 const current=state.credentials.find(c=>String(c.id)===String(value));
 return '<div class="field full"><span>'+e(label)+'</span><div class="inline-picker"><input name="cfg_'+e(key)+'" type="hidden" value="'+e(value)+'">'
 +'<div class="selected-items"><span class="chip '+(current?'tag':'')+'">'+e(current?current.name+' · '+current.kind:value?'Identifiant #'+value:'Aucune connexion')+'</span></div>'
 +'<button type="button" class="btn" data-action="credential-browser" data-field="'+e(key)+'">Rechercher / gérer</button>'
 +'<button type="button" class="btn tiny" data-action="credential-clear" data-field="'+e(key)+'" title="Retirer la connexion">×</button></div>'
 +'<small class="hint">Connexion centralisée et chiffrée. « Nouvelle connexion » permet aussi de renseigner les identifiants depuis cette tâche.</small></div>';
}
async function openGroupBrowser(role){
 if(!roleAdmin())return toast('Réservé aux administrateurs',false);
 captureEditor();state.groupRole=role;state.groupQuery='';state.directoryGroups=[];state.groupWarning='';
 drawGroupBrowser();
 await searchGroups();
}
async function searchGroups(){
 const q=state.groupQuery||'',token=Symbol();state.groupRequest=token;
 try{const r=await api('/directory/groups?q='+encodeURIComponent(q));if(state.groupRequest!==token)return;const focused=document.activeElement?.id==='groupSearch',caret=focused?document.activeElement.selectionStart:null;state.directoryGroups=r.items;state.groupWarning=r.warning||'';drawGroupBrowser();if(focused){const input=document.getElementById('groupSearch');input?.focus();if(caret!==null)input?.setSelectionRange(caret,caret)}}
 catch(ex){state.groupWarning=ex.message;drawGroupBrowser()}
}
function drawGroupBrowser(){
 const current=state.editor?.acl_json[state.groupRole]||[];
 const body='<div class="field"><span>Groupes sélectionnés (cliquer pour retirer)</span><div class="selected-items">'
   +(current.map(g=>'<button class="chip tag" data-action="group-toggle" data-name="'+e(g)+'">'+e(g)+' ×</button>').join('')||'<span class="hint">Aucun groupe sélectionné : accès selon les règles du workflow</span>')+'</div></div>'
   +'<div class="field"><span>Rechercher dans l’annuaire LDAP</span><input id="groupSearch" placeholder="Nom du groupe LDAP…" value="'+e(state.groupQuery||'')+'"></div>'
   +(state.groupWarning?'<div class="notice error">'+e(state.groupWarning)+'</div>':'')
   +'<div class="group-results">'+state.directoryGroups.map(g=>'<button class="group-choice" data-action="group-toggle" data-name="'+e(g.name)+'"><b>'+e(g.name)+'</b><small>'+e(g.source)+(g.dn?' · '+e(g.dn):'')+'</small><span>'+(current.includes(g.name.toLowerCase())?'✓':'＋')+'</span></button>').join('')
   +'</div><div class="tool-row"><button class="btn primary" data-action="group-done">Terminer la sélection</button></div>';
 openLayer('groupLayer','Sélection des groupes — '+({view:'Lecture',run:'Exécution',edit:'Modification'}[state.groupRole]||''),body);
}

function stepConfigFields(step){return (FIELDS[step.type]||[]).map(([key,label,type])=>{const value=step.config?.[key]??'';if(type==='textarea')return `<div class="field full"><span>${label}</span><textarea name="cfg_${key}">${e(value)}</textarea></div>`;if(type==='checkbox')return field(label,'cfg_'+key,value,'checkbox');if(type==='agent'){return selectField(label,'cfg_'+key,[['','— Sélectionner —'],...state.agents.filter(a=>a.enrolled&&!a.revoked).map(a=>[a.id,a.name+' ('+a.platform+')'])],value)}if(type==='datacenter'){return selectField(label,'cfg_'+key,[['','— Sélectionner —'],...state.datacenters.map(d=>[d.id,d.name])],value)}if(type==='credential')return credentialField(label,key,value);if(type.startsWith('select:'))return selectField(label,'cfg_'+key,type.slice(7).split('|').map(x=>[x,x]),value);return field(label,'cfg_'+key,value,type) }).join('')}
function branches(step){const s=state.editor.steps_json;const targets=[['','Automatique (étape suivante)'],['END','Fin avec succès'],['FAIL','Fin avec échec'],...s.filter(x=>x.id!==step.id).map(x=>[x.id,x.name])];return `${selectField('Si réussite','on_success',targets,step.on_success||'')}${selectField('Si échec','on_failure',[['','Arrêt en échec'],...targets.slice(1)],step.on_failure||'')}`}
function renderEditor(){const j=state.editor;const selected=j.steps_json[state.selectedStep];const body=`<div class="editor-meta">${field('Nom du workflow','job_name',j.name)}${folderEditorField(j.folder)}${field('Tags (séparés par des virgules)','job_tags',j.tags_json.join(', '))}</div><div class="editor-meta"><div class="field"><span>Description</span><input name="job_description" value="${e(j.description)}"></div>${scheduleFields(j.schedule_json||{})}</div><div class="editor-meta">${groupEditorField('Groupes lecture','view',j.acl_json.view)}${groupEditorField('Groupes exécution','run',j.acl_json.run)}${groupEditorField('Groupes modification','edit',j.acl_json.edit)}</div><div class="field checkbox"><input type="checkbox" name="job_enabled" ${j.enabled?'checked':''}><span>Workflow actif</span></div><div class="editor-columns"><section class="editor-graph"><div class="panel-title"><h2>Graphe d’exécution</h2><span class="hint">Succès / échec</span></div><div class="graph-legend"><span>Succès</span><span>Échec</span></div><div class="graph-surface" id="workflowGraph">${renderGraph(j.steps_json,state.selectedStep)}</div><div class="step-list">${j.steps_json.map((s,i)=>`<button class="step-pill ${i===state.selectedStep?'selected':''}" data-action="select-step" data-index="${i}">${i+1}. ${e(s.name)}<small>${e(kindLabel[s.type])}</small></button>`).join('')}</div></section><section class="editor-side"><div class="panel-title"><h2>Configurer l’étape ${state.selectedStep+1}</h2><span class="hint">${j.steps_json.length} étapes</span></div><div class="editor-actions"><button class="btn tiny" data-action="add-step">＋ Ajouter</button><button class="btn tiny" data-action="step-up">↑</button><button class="btn tiny" data-action="step-down">↓</button><button class="btn tiny danger" data-action="delete-step">Supprimer</button></div><div class="form-grid">${field('Nom de l’étape','step_name',selected.name)}${selectField('Type','step_type',kinds.map(k=>[k,kindLabel[k]]),selected.type)}</div><div class="subheading">Paramètres de la tâche</div><div class="step-config">${stepConfigFields(selected)}</div><div class="divider"></div><div class="subheading">Gestion des erreurs</div><div class="form-grid">${field('Nouvelles tentatives (0–5)','step_retries',selected.retries||0,'number')}${field('Attente entre tentatives (s)','step_retry_delay',selected.retry_delay??2,'number')}</div><div class="divider"></div><div class="subheading">Branches conditionnelles</div><div class="form-grid">${branches(selected)}</div><div class="divider"></div><div class="subheading">Condition avant exécution</div><div class="field checkbox"><input name="cond_enabled" type="checkbox" ${selected.condition?.enabled?'checked':''}><span>Activer cette condition</span></div><div class="form-grid">${selectField('Source','cond_source',[['last_status','État de la précédente étape'],['last_exit_code','Code retour précédent'],['last_output','Sortie précédente'],['variable','Variable']],selected.condition?.source||'last_status')}${selectField('Comparaison','cond_operator',[['equals','Égal'],['not_equals','Différent'],['contains','Contient'],['not_contains','Ne contient pas'],['exists','Existe']],selected.condition?.operator||'equals')}${field('Valeur attendue','cond_value',selected.condition?.value||'')}${field('Nom de la variable','cond_key',selected.condition?.key||'')}</div></section></div>`;
modal(j.id?'Modifier le workflow':'Nouveau workflow',body,`<button class="btn" data-action="modal-close">Annuler</button>${j.id&&roleAdmin()?`<button class="btn danger" data-action="delete-job" data-id="${j.id}">Supprimer</button>`:''}<button class="btn primary" data-action="save-job">✓ Enregistrer</button>`,true);showScheduleMode()}
function captureEditor(){const root=$('.modal');if(!root||!state.editor)return;const get=n=>root.querySelector(`[name="${n}"]`),v=n=>get(n)?.value??'',ck=n=>!!get(n)?.checked;const j=state.editor;j.name=v('job_name');j.folder=v('job_folder');j.description=v('job_description');j.tags_json=v('job_tags').split(',').map(x=>x.trim()).filter(Boolean);j.schedule_json=captureSchedule(v);j.enabled=ck('job_enabled');j.acl_json=Object.fromEntries(['view','run','edit'].map(k=>[k,v('acl_'+k).split(',').map(x=>x.trim().toLowerCase()).filter(Boolean)]));const s=j.steps_json[state.selectedStep];s.name=v('step_name');s.type=v('step_type');s.retries=Number(v('step_retries')||0);s.retry_delay=Number(v('step_retry_delay')||2);s.on_success=v('on_success');s.on_failure=v('on_failure');s.condition={enabled:ck('cond_enabled'),source:v('cond_source'),operator:v('cond_operator'),value:v('cond_value'),key:v('cond_key')};const keys=(FIELDS[s.type]||[]).map(x=>x[0]);const cfg={...s.config};for(const key of keys){const element=get('cfg_'+key);if(element){cfg[key]=element.type==='checkbox'?element.checked:element.type==='number'?Number(element.value||0):element.value}}s.config=cfg}
function renderGraph(steps,index){const coords=steps.map((_,i)=>({x:135,y:55+i*125}));const height=Math.max(375,steps.length*125+55);let svg=`<svg width="670" height="${height}" viewBox="0 0 670 ${height}" xmlns="http://www.w3.org/2000/svg"><defs><marker id="okArrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" fill="none" stroke="#51d5a5"/></marker><marker id="noArrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" fill="none" stroke="#f17883"/></marker></defs><rect x="14" y="16" width="78" height="27" rx="7" fill="#ffc34b22"/><text x="26" y="34" font-size="11" fill="#ffc34b">DÉPART</text><path d="M92 30 H120 V60 H135" class="edge-success" marker-end="url(#okArrow)"/>`;
steps.forEach((s,i)=>{const next=s.on_success||steps[i+1]?.id||'END';const fail=s.on_failure||'FAIL';const target=steps.findIndex(x=>x.id===next);const failure=steps.findIndex(x=>x.id===fail);const x=coords[i].x,y=coords[i].y; if(target>=0){const ty=coords[target].y+37;svg+=`<path d="M${x+230} ${y+27} H410 V${ty} H${coords[target].x}" class="edge-success" marker-end="url(#okArrow)"/>`}else{svg+=`<path d="M${x+230} ${y+27} H445" class="edge-success" marker-end="url(#okArrow)"/><text x="450" y="${y+31}" font-size="10" fill="#51d5a5">${next==='FAIL'?'FAIL':'END'}</text>`}if(failure>=0){const fy=coords[failure].y+14;svg+=`<path d="M${x+230} ${y+57} H535 V${fy} H${coords[failure].x}" class="edge-fail" marker-end="url(#noArrow)"/>`}else{svg+=`<path d="M${x+230} ${y+57} H565" class="edge-fail"/><text x="570" y="${y+61}" font-size="10" fill="#f17883">${fail==='END'?'END':'FAIL'}</text>`}});
steps.forEach((s,i)=>{const x=coords[i].x,y=coords[i].y;svg+=`<g class="step-node ${index===i?'selected':''}" data-action="select-step" data-index="${i}"><rect x="${x}" y="${y}" width="230" height="77" rx="11"/><circle cx="${x+22}" cy="${y+23}" r="10" fill="#ffc34b33"/><text x="${x+18}" y="${y+27}" font-size="12" fill="#ffc34b">${i+1}</text><text x="${x+41}" y="${y+28}" font-size="13" font-weight="700">${e(s.name.slice(0,25))}</text><text x="${x+17}" y="${y+54}" font-size="11" fill="#9396a2">${e(kindLabel[s.type])}</text></g>`});return svg+'</svg>'}
async function saveEditor(){captureEditor();const j=state.editor;if(!j.name.trim())return toast('Saisis un nom de workflow',false);try{if(j.id){await api('/jobs/'+j.id,'PUT',j)}else await api('/jobs','POST',j);toast('Workflow enregistré');closeModal();await refresh();showPage('jobs')}catch(ex){toast(ex.message,false)}}
async function runJob(id){try{const r=await api('/jobs/'+id+'/run','POST');toast('Exécution #'+r.run_id+' mise en file');await refresh();showPage('runs');await openRun(r.run_id)}catch(ex){toast(ex.message,false)}}
async function openRun(id){try{const r=await api('/runs/'+id);state.selectedRun=r;modal('Exécution #'+r.id+' — '+e(r.job_name),`<div class="tool-row">${labelStatus(r.status)}<span class="hint">Déclenché par ${e(r.requested_by)} · ${dt(r.queued_at)}</span></div>${r.error?`<div class="notice error">${e(r.error)}</div>`:''}<h3>Résultats par étape</h3>${r.steps.map(s=>`<div class="step-log"><div class="step-header">${labelStatus(s.status)}<b>${e(s.name)}</b><span class="muted">${e(s.type)} · essai ${s.attempt} · code ${s.exit_code??'—'}</span></div><pre>${e(s.output||'(aucune sortie)')}</pre></div>`).join('')||'<p class="muted">Exécution en attente. Réactualisez dans quelques secondes.</p>'}`,`<button class="btn" data-action="run-detail" data-id="${id}">↻ Actualiser</button><button class="btn primary" data-action="modal-close">Fermer</button>`,true)}catch(ex){toast(ex.message,false)}}
async function hydrateAdmins(){if(!roleAdmin())return;try{if(state.page==='users'){const users=(await api('/users')).items;$('#userTable').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Utilisateur</th><th>Source</th><th>Rôle</th><th>Groupes internes / LDAP</th></tr></thead><tbody>${users.map(u=>`<tr><td class="name-cell">${e(u.display_name)}<div class="tinytext muted">${e(u.username)}</div></td><td>${e(u.source)}</td><td>${e(u.role)}</td><td>${(u.groups_json||[]).map(x=>`<span class="chip">${e(x)}</span>`).join('')}</td></tr>`).join('')}</tbody></table></div>`}if(state.page==='audit'){const rows=(await api('/audit')).items;$('#auditTable').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Heure</th><th>Auteur</th><th>Action</th><th>Objet</th><th>Détails</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${dt(r.created_at)}</td><td>${e(r.actor)}</td><td>${e(r.action)}</td><td>${e(r.target)}</td><td>${e(r.detail)}</td></tr>`).join('')}</tbody></table></div>`}if(state.page==='updates'){const u=await api('/updates');$('#updateStatus').innerHTML=`<div class="hint" style="margin-bottom:14px">${u.worker?.message?e(u.worker.message):'Aucune information du service hôte. Lancez ops/update-worker.py.'}</div><div class="table-wrap"><table><thead><tr><th>Action</th><th>Statut</th><th>Auteur</th><th>Date</th><th>Message</th></tr></thead><tbody>${u.requests.map(r=>`<tr><td>${e(r.action)}</td><td>${labelStatus(r.status)}</td><td>${e(r.actor)}</td><td>${dt(r.created_at)}</td><td>${e(r.message)}</td></tr>`).join('')}</tbody></table></div>`}}catch(ex){toast(ex.message,false)}}
function bindLocal(){hydrateAdmins();if(state.page==='ldap')hydrateLDAP()}
async function refresh(){await load();render()}
async function handleClick(ev){const b=ev.target.closest('[data-action], [data-page]');if(!b)return;const a=b.dataset.action||'page',id=Number(b.dataset.id);try{if(a==='layer-close'){closeLayer(b.dataset.layer);return}
 if(a==='folder-manage'){foldersBrowser();return}
 if(a==='folder-filter'){state.folderFilter=b.dataset.path||'';render();return}
 if(a==='folder-select'){state.folderSelected=b.dataset.path;drawFolderBrowser();return}
 if(a==='folder-use'){if(state.editor){state.editor.folder=state.folderSelected;closeLayer('folderLayer');renderEditor()}else{state.folderFilter=state.folderSelected;closeLayer('folderLayer');render()}return}
 if(a==='folder-remove'){
  const path=state.folderSelected;if(!confirm('Supprimer '+path+' et ses sous-dossiers ? Les workflows seront déplacés, jamais supprimés.'))return;
  const r=await api('/folders?path='+encodeURIComponent(path),'DELETE');
  state.folderSelected=r.moved_to;state.folderFilter='';if(state.editor&&state.editor.folder.startsWith(path))state.editor.folder=r.moved_to;
  await reloadFolders();drawFolderBrowser();render();toast('Dossier supprimé, workflows conservés');return
 }
 if(a==='credential-browser'){openCredentialBrowser(b.dataset.field);return}
 if(a==='credential-clear'){captureEditor();state.editor.steps_json[state.selectedStep].config[b.dataset.field]='';renderEditor();return}
 if(a==='credential-select'){credentialSelected(id);return}
 if(a==='credential-new'){state.credentialAction='new';state.pickerCredentialId=null;drawCredentialBrowser();return}
 if(a==='credential-list'){state.credentialAction='';state.pickerCredentialId=null;drawCredentialBrowser();return}
 if(a==='credential-edit'){if(!document.getElementById('credentialLayer'))openCredentialBrowser();state.pickerCredentialId=id;state.credentialAction='edit';drawCredentialBrowser();return}
 if(a==='credential-clone'){if(!document.getElementById('credentialLayer'))openCredentialBrowser();state.pickerCredentialId=id;state.credentialAction='clone';drawCredentialBrowser();return}
 if(a==='credential-test'){openCredentialTestWizard(id);return}
 if(a==='credential-remove'){if(!confirm('Supprimer définitivement cette connexion ? Une connexion utilisée par un workflow ne pourra pas être supprimée.'))return;await api('/credentials/'+id,'DELETE');await reloadCredentials();drawCredentialBrowser();if(state.page==='vault')render();toast('Connexion supprimée');return}
 if(a==='group-browser'){await openGroupBrowser(b.dataset.role);return}
 if(a==='group-toggle'){const arr=state.editor.acl_json[state.groupRole]||[],group=b.dataset.name.toLowerCase();state.editor.acl_json[state.groupRole]=arr.includes(group)?arr.filter(x=>x!==group):[...arr,group];drawGroupBrowser();return}
 if(a==='group-done'){closeLayer('groupLayer');renderEditor();return}
 if(a==='ldap-test'){
  const data=readLDAPForm(),target=document.getElementById('ldapTestResult');
  if(target)target.textContent='Test LDAP en cours…';
  try{const result=await api('/admin/ldap/test','POST',data);
   if(target)target.innerHTML='<span class="status success">Test réussi</span> '+e(result.message)+' · '+result.group_sample_count+' groupe(s) échantillonnés'+(result.user_tested?' · Utilisateur '+(result.user_found?'trouvé':'introuvable'):'');
  }catch(ex){if(target)target.innerHTML='<div class="notice error">'+e(ex.message)+'</div>'}return
 }
 if(a==='page'){showPage(b.dataset.page);return}if(a==='agent-deploy-form'){agentDeployForm();return}if(a==='agent-enroll-form'){agentEnrollForm();return}if(a==='agent-revoke'){if(!confirm('Révoquer cet agent et ses tâches ?'))return;await api('/agents/'+id+'/revoke','POST');toast('Agent révoqué');await refresh();return}if(a==='new-datacenter'){openDatacenterForm();return}if(a==='discover-datacenter'){const x=await api('/datacenters/'+id+'/discover');let counts={};for(const r of x.resources)counts[r.type]=(counts[r.type]||0)+1;modal('Découverte '+e(x.datacenter),`<p>${Object.entries(counts).map(([k,v])=>e(k)+': '+v).join(' · ')}</p><div class="table-wrap"><table><thead><tr><th>Type</th><th>Nom/VMID</th><th>Nœud</th><th>État</th></tr></thead><tbody>${x.resources.slice(0,300).map(r=>`<tr><td>${e(r.type)}</td><td>${e(r.name||r.vmid||r.id)}</td><td>${e(r.node||'—')}</td><td>${e(r.status||'—')}${r.type==='qemu'?` <button class="btn tiny" data-action="assess-dr" data-id="${id}" data-vmid="${r.vmid}">Évaluer PRA</button>`:''}</td></tr>`).join('')}</tbody></table></div>`,`<button class="btn" data-action="modal-close">Fermer</button>`,true);return}if(a==='assess-dr'){const vmid=Number(b.dataset.vmid);const data=await api('/datacenters/'+id+'/vms/'+vmid+'/dr-assessment');modal('Évaluation PRA · VM '+vmid,`<p class="notice">Lecture seule : aucune réplication n’a été lancée. Le statut PRA n’est pas validé.</p><p><b>${e(data.name||('VM '+vmid))}</b> — ${e(data.node)} (${e(data.source_status||'inconnu')})</p><h3>Disques / stockages</h3><ul>${data.disks.map(x=>`<li>${e(x.slot)} · ${e(x.storage||'inconnu')} · ${e(x.storage_type)} ${x.shared?'(partagé)':''}</li>`).join('')}</ul><h3>Modes à étudier</h3><ul>${data.options.map(x=>`<li><b>${e(x.engine)}</b> — ${e(x.purpose)}</li>`).join('')}</ul><h3>Risques / prérequis</h3><ul>${data.risks.map(r=>`<li>${e(r)}</li>`).join('')}</ul>`,`<button class="btn" data-action="modal-close">Fermer</button>`,true);return}if(a==='delete-datacenter'){if(!confirm('Supprimer ce datacenter ?'))return;await api('/datacenters/'+id,'DELETE');toast('Datacenter supprimé');await refresh();return}if(a==='filter'){state.filter=b.dataset.value;render();return}if(a==='create'){openEditor();return}if(a==='edit'){openEditor(id);return}if(a==='clone'){openEditor(id,true);return}if(a==='webhook'){await webhookManager(id);return}if(a==='rotate-webhook'){await rotateWebhook(id);return}if(a==='disable-webhook'){await disableWebhook(id);return}if(a==='modal-close'){closeModal();return}if(a==='run-job'){await runJob(id);return}if(a==='run-detail'){await openRun(id);return}if(a==='new-credential'){openCredentialBrowser(null,'new');return}if(a==='save-job'){await saveEditor();return}if(a==='select-step'){captureEditor();state.selectedStep=Number(b.dataset.index);renderEditor();return}if(a==='add-step'){captureEditor();state.editor.steps_json.push(addStep());state.selectedStep=state.editor.steps_json.length-1;renderEditor();return}if(a==='step-up'||a==='step-down'){captureEditor();const arr=state.editor.steps_json,i=state.selectedStep,j=i+(a==='step-up'?-1:1);if(j>=0&&j<arr.length){[arr[i],arr[j]]=[arr[j],arr[i]];state.selectedStep=j}renderEditor();return}if(a==='delete-step'){captureEditor();if(state.editor.steps_json.length<=1)return toast('Un workflow doit conserver une étape dans cet éditeur',false);const removed=state.editor.steps_json.splice(state.selectedStep,1)[0];state.editor.steps_json.forEach(x=>{for(const edge of ['on_success','on_failure'])if(x[edge]===removed.id)x[edge]=''});state.selectedStep=Math.min(state.selectedStep,state.editor.steps_json.length-1);renderEditor();return}if(a==='delete-job'){if(!confirm('Supprimer définitivement ce workflow ? Son historique sera conservé.'))return;await api('/jobs/'+id,'DELETE');closeModal();toast('Workflow supprimé');await refresh();showPage('jobs');return}if(a==='delete-credential'){if(!confirm('Supprimer ce secret ? Les workflows qui l’utilisent ne pourront plus s’authentifier.'))return;await api('/credentials/'+id,'DELETE');toast('Secret supprimé');await refresh();return}if(a==='refresh'){await refresh();return}if(a==='update-request'){if(b.dataset.value==='apply'&&!confirm('Installer le dernier commit de la branche Git autorisée et redémarrer les services ?'))return;await api('/updates/request','POST',{action:b.dataset.value});toast('Demande envoyée au service de maintenance');hydrateAdmins();return}if(a==='refresh-updates'){hydrateAdmins();return}}catch(ex){toast(ex.message,false)}}
async function handleSubmit(ev){
 if(ev.target.id==='credentialTestForm'){ev.preventDefault();await submitCredentialTest(ev.target);return}

 if(ev.target.id==='ldapAdminForm'){ev.preventDefault();
  try{const data=readLDAPForm();await api('/admin/ldap','PUT',data.settings);toast('Configuration LDAP enregistrée');await hydrateLDAP()}
  catch(ex){toast(ex.message,false)}return
 }
 
 if(ev.target.id==='credentialManageForm'){
  ev.preventDefault();
  const f=new FormData(ev.target),action=state.credentialAction,id=state.pickerCredentialId,kind=String(f.get('kind')||state.credentials.find(c=>c.id===id)?.kind||'generic');
  try{
   let result;
   if(action==='clone')result=await api('/credentials/'+id+'/clone','POST',{name:f.get('name')});
   else{
    const payload={};
    for(const [key,label,typ] of (connectionSchemas[kind]||connectionSchemas.generic)){
      if(typ==='checkbox'){payload[key]=f.has(key);continue}
      const value=String(f.get(key)??'').trim();
      if(!value)continue;
      payload[key]=typ==='number'?Number(value):value;
    }
    result=await api(action==='edit'?'/credentials/'+id:'/credentials',action==='edit'?'PUT':'POST',
                     {name:f.get('name'),kind,payload});
   }
   await reloadCredentials();toast('Connexion sécurisée enregistrée');
   if(state.editor&&state.pickerField&&result?.id)credentialSelected(result.id);
   else{state.credentialAction='';state.pickerCredentialId=null;drawCredentialBrowser();if(state.page==='vault')render()}
  }catch(ex){toast(ex.message,false)}
  return
 }
 if(ev.target.id==='folderCreateForm'){
  ev.preventDefault();const f=new FormData(ev.target);
  try{const r=await api('/folders','POST',{parent:f.get('parent'),name:f.get('name')});state.folderSelected=r.path;await reloadFolders();drawFolderBrowser();toast('Dossier créé')}catch(ex){toast(ex.message,false)}return
 }
 if(ev.target.id==='folderRenameForm'){
  ev.preventDefault();const f=new FormData(ev.target),old=state.folderSelected;
  try{const r=await api('/folders','PUT',{path:old,name:f.get('name')});state.folderSelected=r.path;
   if(state.editor&&state.editor.folder.startsWith(old))state.editor.folder=r.path+state.editor.folder.slice(old.length);
   if(state.folderFilter.startsWith(old))state.folderFilter=r.path+state.folderFilter.slice(old.length);
   await reloadFolders();drawFolderBrowser();toast('Dossier renommé')}catch(ex){toast(ex.message,false)}return
 }
 if(ev.target.id==='agentEnrollForm'){ev.preventDefault();const f=new FormData(ev.target);try{const r=await api('/agents/enrollments','POST',{name:f.get('name'),platform:f.get('platform'),group:f.get('group')});modal('Jeton agent #'+r.id,'<p class="notice">Copier maintenant : jeton unique expirant dans 15 minutes.</p><div class="field"><span>Identifiant</span><input readonly value="'+e(r.id)+'"></div><div class="field"><span>Jeton</span><textarea readonly>'+e(r.enrollment_token)+'</textarea></div><p class="hint">config.json : {"server":"https://origami.exemple.fr","agent_id":'+r.id+',"enrollment_token":"…"} (droits limités au service).</p>','<button class="btn primary" data-action="modal-close">Fermer</button>');await refresh()}catch(ex){toast(ex.message,false)}return}
 if(ev.target.id==='agentDeployForm'){ev.preventDefault();const f=new FormData(ev.target);if(!f.get('approved'))return toast('Confirmation requise',false);try{const r=await api('/agents/deploy','POST',{name:f.get('name')||f.get('host'),host:f.get('host'),port:Number(f.get('port')||22),platform:f.get('platform'),group:f.get('group'),credential_id:Number(f.get('credential_id')),approved:true});closeModal();toast('Agent #'+r.id+' : installation envoyée');await refresh()}catch(ex){toast(ex.message,false)}return}
 if(ev.target.id==='datacenterForm'){ev.preventDefault();const f=new FormData(ev.target);try{await api('/datacenters','POST',{name:f.get('dc_name'),url:f.get('dc_url'),credential_id:Number(f.get('dc_credential'))});closeModal();toast('Datacenter ajouté');await refresh()}catch(ex){toast(ex.message,false)}return}if(ev.target.id==='loginForm'){ev.preventDefault();const form=new FormData(ev.target);$('#authError').textContent='';try{await api('/login','POST',{username:form.get('username'),password:form.get('password')});const r=await api('/me');state.user=r.user;$('#auth').hidden=true;$('#application').hidden=false;$('#userAvatar').textContent=initials();$('#adminNav').hidden=!roleAdmin();if(!roleAdmin())$$('.side-admin').forEach(x=>x.hidden=true);await refresh();showPage('dashboard')}catch(ex){$('#authError').textContent=ex.message}}else if(ev.target.id==='credentialForm'){ev.preventDefault();const f=new FormData(ev.target);const payload={};for(const k of ['username','password','token','private_key','passphrase'])if(f.get(k)?.trim())payload[k]=f.get(k);try{await api('/credentials','POST',{name:f.get('name'),kind:f.get('kind'),payload});closeModal();toast('Secret chiffré et enregistré');await refresh()}catch(ex){toast(ex.message,false)}}}
function initTheme(){let theme=localStorage.getItem('origami-theme')||'dark';document.body.className='theme-'+theme;$('#themeToggle').textContent={dark:'☾',light:'☀',cyber:'✧'}[theme]||'☾'}
async function bootstrap(){document.addEventListener('submit',handleSubmit);try{initTheme();decorateSidebar()}catch(ex){console.error('OrigamiTasks initialisation:',ex)}try{const r=await api('/me');state.user=r.user;$('#auth').hidden=true;$('#application').hidden=false;$('#adminNav').hidden=!roleAdmin();if(!roleAdmin())$$('.side-admin').forEach(x=>x.hidden=true);$('#userAvatar').textContent=initials();await load();showPage('dashboard')}catch{$('#auth').hidden=false;$('#application').hidden=true}document.addEventListener('click',handleClick);document.addEventListener('input',ev=>{
 if(ev.target.id==='credentialSearch'){state.credentialSearch=ev.target.value;filterCredentialRows()}
 if(ev.target.id==='groupSearch'){state.groupQuery=ev.target.value;clearTimeout(state.groupTimer);state.groupTimer=setTimeout(searchGroups,250)}
 if(ev.target.id==='catalogSearch'){const q=ev.target.value.toLowerCase().trim();$$('.catalog-search-item').forEach(card=>card.hidden=!card.dataset.search.includes(q));}});document.addEventListener('change',ev=>{
 if(ev.target.closest('#credentialTestForm'))updateCredentialTestVisibility();
 if(ev.target.name==='kind'&&ev.target.closest('#credentialManageForm')){
  document.getElementById('credentialProfile').innerHTML=credentialProfileFields(ev.target.value);
  smtpAuthFieldsVisibility();
 }
 if(ev.target.name==='auth'&&ev.target.closest('#credentialManageForm'))smtpAuthFieldsVisibility();
 if(ev.target.name==='tls_mode'&&ev.target.closest('#credentialManageForm')&&ev.target.closest('form').elements.namedItem('kind')?.value==='smtp'){
  const port=ev.target.closest('form').elements.namedItem('port');
  if(port&&['25','465','587'].includes(port.value))port.value=ev.target.value==='ssl'?465:ev.target.value==='none'?25:587;
 }
 if(ev.target.name==='job_schedule_mode')showScheduleMode();if(ev.target.name==='step_type'&&state.editor){const s=state.editor.steps_json[state.selectedStep];const newType=ev.target.value;if(s.type!==newType){captureEditor();s.config={};s.type=newType;renderEditor()}}});$('#themeToggle').addEventListener('click',()=>{const themes=['dark','light','cyber'],current=document.body.className.replace('theme-',''),next=themes[(themes.indexOf(current)+1)%themes.length];localStorage.setItem('origami-theme',next);initTheme()});$('#logoutBtn').addEventListener('click',async()=>{try{await api('/logout','POST')}catch{}state.user=null;$('#application').hidden=true;$('#auth').hidden=false;$('#loginForm').reset();showPage('dashboard')});$('#globalSearch').addEventListener('input',ev=>{state.query=ev.target.value;if(state.page!=='jobs'&&state.query)showPage('jobs');else if(state.page==='jobs')render()});$('#menuToggle').addEventListener('click',()=>$('.sidebar').classList.toggle('visible'));document.addEventListener('keydown',ev=>{if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='k'){ev.preventDefault();$('#globalSearch').focus()}if(ev.key==='Escape'&&document.getElementById('groupLayer')){closeLayer('groupLayer');renderEditor()}else if(ev.key==='Escape'&&document.getElementById('credentialLayer'))closeLayer('credentialLayer');else if(ev.key==='Escape'&&document.getElementById('folderLayer'))closeLayer('folderLayer');else if(ev.key==='Escape'&&$('.modal-overlay'))closeModal()});setInterval(async()=>{if(!state.user||$('.modal-overlay'))return;if(['dashboard','runs','jobs'].includes(state.page)){try{await refresh()}catch{}}},7000)}
bootstrap();
