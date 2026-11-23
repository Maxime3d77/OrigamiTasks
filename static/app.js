/* OrigamiTasks SPA — no CDN, no bundler, no unsafe eval. */
'use strict';
const state = {user:null,page:'dashboard',jobs:[],runs:[],summary:{},credentials:[],datacenters:[],proxmoxCAs:[],proxmoxDiscovery:null,proxmoxFleet:null,drSites:[],drPlans:[],drProtectedVMs:[],drVMOptions:null,drVMPlanFilter:'all',auditCategory:'all',auditSearch:'',drSimulations:[],agents:[],agentSettings:null,sshInventory:{servers:[],groups:[]},agentDeployBatches:[],agentUpdates:{items:[],available_versions:{}},folders:[],folderFilter:'',directoryGroups:[],pickerField:null,pickerCredentialId:null,folderSelected:'Général',groupRole:'view',catalog:{available:[]},selectedRun:null,dashboardTab:'tasks',editor:null,selectedStep:0,filter:'all',query:''};
const $=(selector,base=document)=>base.querySelector(selector);
const $$=(selector,base=document)=>Array.from(base.querySelectorAll(selector));
const e=(value)=>String(value??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dt=v=>v?new Date(v).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'short'}):'—';
const statusName={success:'Réussi',failed:'Échec',running:'En cours',queued:'En attente',interrupted:'Interrompu',skipped:'Ignoré',online:'En ligne',offline:'Hors ligne',pending:'En attente',revoked:'Révoqué',uninstalled:'Désinstallé',completed_with_errors:'Terminé avec erreurs',awaiting_heartbeat:'Vérification version',BLOCKED:'Bloqué',planned:'Préparation',paused:'En pause',not_configured:'Non configurée',unknown:'Inconnu'};
const labelStatus=s=>`<span class="status ${e(s)}">${e(statusName[s]||s)}</span>`;
const toast=(msg,ok=true)=>{const n=document.createElement('div');n.className=ok?'ok':'fail';n.textContent=msg;$('#toast').appendChild(n);setTimeout(()=>n.remove(),4000)};
const cookie=n=>document.cookie.split('; ').find(x=>x.startsWith(n+'='))?.split('=').slice(1).join('=')||'';
async function api(url,method='GET',data){const options={method,credentials:'same-origin',headers:{},cache:'no-store'};if(method!=='GET'){options.headers['X-CSRF-Token']=decodeURIComponent(cookie('origami_csrf'));if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data)}}const r=await fetch('/api'+url,options);let body;try{body=await r.json()}catch{body={detail:'Réponse serveur invalide'}}if(!r.ok){if(r.status===401){state.user=null;$('#application').hidden=true;$('#auth').hidden=false}throw Error(typeof body.detail==='string'?body.detail:`Erreur HTTP ${r.status}`)}return body}
async function load(){try{state.managerVersion=(await api('/health')).version}catch{}const [jobs,runs,summary]=await Promise.all([api('/jobs'),api('/runs'),api('/summary')]);state.jobs=jobs.items;state.runs=runs.items;state.summary=summary;state.catalog=await api('/catalog');try{state.folders=(await api('/folders')).items}catch{state.folders=[]}$('#navCount').textContent=jobs.items.length;if(state.user.role==='admin')try{state.credentials=(await api('/credentials')).items;state.datacenters=(await api('/datacenters')).items;state.proxmoxCAs=(await api('/proxmox/cas')).items;state.drSites=(await api('/dr/sites')).items;state.drPlans=(await api('/dr/plans')).items;state.drProtectedVMs=(await api('/dr/vms')).items;state.drSimulations=(await api('/dr/simulations')).items;state.agents=(await api('/agents')).items;state.agentSettings=await api('/admin/agent-settings');state.sshInventory=await api('/ssh/inventory');state.agentDeployBatches=(await api('/agents/deploy/batches')).items;state.agentUpdates=await api('/agents/updates')}catch{}}
const roleAdmin=()=>state.user?.role==='admin';
const title=(name,subtitle,action='')=>`<div class="page-heading"><div><div class="eyebrow">ORIGAMITASKS / AUTOMATION</div><h1>${name}</h1><p>${subtitle}</p></div>${action}</div>`;
const initials=()=>state.user?.display_name?.split(' ').map(x=>x[0]).join('').slice(0,2).toUpperCase()||'OT';
const badgeTags=tags=>tags.map(t=>`<span class="chip tag">#${e(t)}</span>`).join('');
function showPage(page){state.page=page;state.selectedRun=null;$('#breadcrumb').textContent=({dashboard:"Vue d'ensemble",jobs:'Workflows',runs:'Exécutions',vault:'Coffre-fort',users:'Utilisateurs & droits',ldap:'LDAP / Active Directory',updates:'Mises à jour',datacenters:'DC Proxmox','protected-vms':'VM protégées PRA',agents:'Agents Linux / Windows','ssh-inventory':'Serveurs & groupes SSH',catalog:'Catalogue des tâches',audit:"Journal d'audit"})[page]||page;$$('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.page===page));$('.sidebar').classList.remove('visible');render()}
function render(){const pages={dashboard, jobs, runs, vault,users,updates,audit,datacenters,agents:agentsPage,catalog,ldap:ldapPage,'ssh-inventory':sshInventoryPage,'protected-vms':protectedVMsPage};$('#content').innerHTML=(pages[state.page]||dashboard)();bindLocal()}
function dashboard(){
 const tab=roleAdmin()?(state.dashboardTab||'tasks'):'tasks';
 const all=[['tasks','Tâches & exécutions'],['agents','Agents actifs'],['proxmox','DC Proxmox / PRA']];
 const tabs='<div class="overview-tabs" role="tablist" aria-label="Vue d’ensemble">'
    +(roleAdmin()?all:[all[0]]).map(([key,label])=>
      '<button type="button" role="tab" aria-selected="'+(tab===key?'true':'false')+'" class="'+(tab===key?'active':'')+'" data-action="overview-tab" data-value="'+key+'">'+label+'</button>').join('')
    +'</div>';
 let page=tab==='agents'?dashboardAgents():tab==='proxmox'?dashboardProxmox():dashboardTasks();
 return title('Vue d’ensemble','Workflows, agents et état des clusters dans un centre de supervision unique.','<button class="btn tiny" data-action="refresh">↻ Actualiser</button>')
  +tabs+'<div role="tabpanel" class="overview-panel">'+page+'</div>';
}
function dashboardTasks(){
 const s=state.summary;
 return '<div class="stats-grid">'
  +'<div class="stat-card featured"><span class="label">Workflows disponibles</span><span class="stat-icon">◈</span><div class="value">'+(s.jobs||0)+'</div><div class="sub">'+(s.enabled||0)+' actifs</div></div>'
  +'<div class="stat-card"><span class="label">Exécutions réussies</span><span class="stat-icon">✓</span><div class="value">'+(s.success||0)+'</div><div class="sub">sur les 100 dernières</div></div>'
  +'<div class="stat-card"><span class="label">Exécutions échouées</span><span class="stat-icon">⚑</span><div class="value">'+(s.failed||0)+'</div><div class="sub">à examiner</div></div>'
  +'<div class="stat-card"><span class="label">En cours / en attente</span><span class="stat-icon">◴</span><div class="value">'+(s.running||0)+'</div><div class="sub">exécutions actives</div></div></div>'
  +'<div class="section-grid"><section><div class="panel"><div class="panel-title"><h2>Dernières exécutions</h2>'
  +'<button class="text-btn" data-action="page" data-page="runs">Voir tout ↗</button></div>'
  +runsTable(state.runs.slice(0,7))+'</div>'
  +'<div class="panel"><div class="panel-title"><h2>Workflows récents</h2>'
  +'<button class="text-btn" data-action="page" data-page="jobs">Explorer ↗</button></div>'
  +jobsTable(state.jobs.slice(0,5))+'</div></section><section>'
  +'<div class="panel"><div class="panel-title"><h2>Répartition des exécutions</h2>'
  +'<span class="hint">100 dernières</span></div>'+activityBars()+'</div>'
  +'<div class="panel"><div class="panel-title"><h2>Actions rapides</h2></div><div class="quick-grid">'
  +'<button class="quick-card" data-action="create"><span>⤳</span><b>Créer un workflow</b><small>Visuel et conditionnel</small></button>'
  +'<button class="quick-card" data-action="page" data-page="runs"><span>◷</span><b>Consulter les logs</b><small>Dernières exécutions</small></button>'
  +'<button class="quick-card" data-action="page" data-page="vault"><span>◇</span><b>Coffre sécurisé</b><small>Secrets chiffrés</small></button>'
  +'<button class="quick-card" data-action="page" data-page="updates"><span>⇣</span><b>Version & Git</b><small>Maintenance</small></button>'
  +'</div></div></section></div>';
}
function dashboardAgents(){
 const items=state.agents||[],online=items.filter(x=>x.status==='online'),offline=items.filter(x=>x.status==='offline'),
       pending=items.filter(x=>x.status==='pending'),removed=items.filter(x=>['revoked','uninstalled'].includes(x.status));
 const mini=(label,value,sub)=>'<div class="stat-card"><span class="label">'+e(label)+'</span><div class="value">'+value+'</div><div class="sub">'+e(sub)+'</div></div>';
 const summary='<div class="stats-grid">'+mini('Agents en ligne',online.length,'Heartbeat reçu sous 90 secondes')
  +mini('Agents hors ligne',offline.length,'Aucun contact récent')
  +mini('Agents en attente',pending.length,'Enrôlement non terminé')
  +mini('Révoqués / désinstallés',removed.length,'Jetons invalidés')+'</div>';
 const rows=items.slice(0,25).map(agent=>
  '<tr><td><b>'+e(agent.name)+'</b><div class="muted tinytext">'+e(agent.hostname||'Non enrôlé')+'</div></td>'
  +'<td>'+labelStatus(agent.status)+'</td><td>'+e(agent.platform)+' · '+e(agent.version||'—')+'</td>'
  +'<td>'+(Number.isFinite(agent.cpu_percent)?e(agent.cpu_percent.toFixed(1))+' %':'Non remonté')+'</td>'
  +'<td>'+(Number.isFinite(agent.memory_mb)?e(agent.memory_mb.toFixed(1))+' Mio':'Non remontée')+'</td>'
  +'<td>'+dt(agent.last_seen)+'</td></tr>').join('');
 return summary+'<div class="panel"><div class="panel-title"><div><h2>État des agents</h2>'
  +'<p class="hint">CPU et RAM du processus agent uniquement, sans les scripts lancés ni les VM hébergées.</p></div>'
  +'<button class="btn primary" data-action="page" data-page="agents">Gérer les agents ↗</button></div>'
  +'<div class="table-wrap"><table><thead><tr><th>Agent</th><th>État</th><th>Plateforme / version</th><th>CPU</th><th>RAM</th><th>Dernier contact</th></tr></thead>'
  +'<tbody>'+(rows||'<tr><td colspan="6">Aucun agent enregistré.</td></tr>')+'</tbody></table></div>'
  +'<p class="hint">Les agents de tâches classiques dépendent toujours de la console. Leur état « en ligne » ne garantit pas la disponibilité du PRA autonome.</p></div>';
}
function dashboardProxmox(){
 const dcs=state.datacenters||[],sites=state.drSites||[];
 const cards=dcs.slice(0,80).map(dc=>{
  const site=sites.find(s=>(s.datacenters||[]).some(d=>d.id===dc.id));
  const assoc=site?.agents||[],active=assoc.filter(x=>x.status==='online').length;
  return '<article class="job-card overview-dc-card"><div class="job-card-head"><div class="job-icon">◈</div>'
   +'<span class="chip tag">'+e(dc.kind==='node'?'Nœud seul':'Cluster')+'</span></div>'
   +'<h3>'+e(dc.name)+'</h3><p>'+e(dc.url)+'</p>'
   +'<div class="overview-dc-details"><span>Site : <b>'+e(site?.name||'Non rattaché')+'</b></span>'
   +'<span>Agents standards : <b>'+active+' / '+assoc.length+' en ligne</b></span>'
   +'<span>Réplication PRA : <b>Non disponible (moteur non implémenté)</b></span>'
   +'<span>État API : <b>Non testé en temps réel sur cette vue</b></span>'
   +'</div><button class="btn tiny" data-action="page" data-page="datacenters">Ouvrir dans Proxmox ↗</button></article>';
 }).join('');
 const agents=sites.reduce((acc,site)=>acc+(site.agents||[]).filter(x=>x.status==='online').length,0);
 return '<div class="stats-grid">'
  +'<div class="stat-card featured"><span class="label">Clusters / serveurs déclarés</span><div class="value">'+dcs.length+'</div><div class="sub">Inventaire OrigamiTasks</div></div>'
  +'<div class="stat-card"><span class="label">Sites PRA</span><div class="value">'+sites.length+'</div><div class="sub">Sites nommés</div></div>'
  +'<div class="stat-card"><span class="label">Agents liés en ligne</span><div class="value">'+agents+'</div><div class="sub">Agents classiques, non autonomes PRA</div></div>'
  +'<div class="stat-card"><span class="label">Synchronisations actives</span><div class="value">—</div><div class="sub">Non mesurées : moteur PRA absent</div></div></div>'
  +'<div class="notice">Les statuts de réplication (dernier point de restauration, retard, réussite / échec) ne seront affichés qu’après intégration d’un moteur réel. Aucun statut « synchronisé » n’est inventé.</div>'
  +'<div class="panel"><div class="panel-title"><h2>Datacenters Proxmox</h2>'
  +'<div class="tool-row"><button class="btn primary" data-action="page" data-page="datacenters">Administrer les DC / sites PRA ↗</button>'
  +'<button class="btn" data-action="page" data-page="protected-vms">VM protégées ↗</button></div></div>'
  +'<div class="workflow-grid overview-dc-grid">'+(cards||'<p class="hint">Aucun DC Proxmox enregistré.</p>')+'</div>'
  +(dcs.length>80?'<p class="hint">80 premiers DC affichés. Ouvrir Proxmox pour la liste complète.</p>':'')
  +'</div>';
}
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
 +'<div class="field full"><label class="field checkbox ldap-insecure-toggle"><input type="checkbox" name="skip_cert_verification" '+(cfg.skip_cert_verification?'checked':'')+' '+(!cfg.insecure_tls_allowed?'disabled':'')+'><span>Exception avancée : ignorer la vérification du certificat LDAP</span></label><small class="hint">DANGEREUX : le chiffrement demeure actif mais l’identité du serveur n’est plus vérifiée. Cela expose les identifiants à une interception. La case est grisée tant que ALLOW_INSECURE_LDAP_TLS=true n’est pas défini dans le .env de l’hôte et le conteneur recréé. Préférer l’import de la CA pour conserver la validation TLS.</small></div>'
 +(cfg.skip_cert_verification?'<div class="notice error">Attention : la vérification des certificats LDAPS/STARTTLS est désactivée pour cet annuaire. Remettre une CA valide dès que possible.</div>':'')
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
 +'<div class="field full"><span>Importer un certificat CA public (.pem, .crt, .cer ou .der)</span><div class="inline-picker"><input id="ldapCAImportFile" type="file" accept=".pem,.crt,.cer,.der,application/x-pem-file,application/pkix-cert,text/plain,application/octet-stream"><button class="btn" type="button" data-action="ldap-ca-import">Importer la CA</button></div><small class="hint">Certificats publics CA au format PEM ou DER, même si le fichier porte l’extension .crt. Une clé privée ou un certificat serveur non-CA sera refusé. Le certificat est converti en PEM, puis utilisé pour vérifier TLS. Enregistrer la configuration et tester LDAP.</small></div>'
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
 for(const key of ['enabled','starttls','nested_groups','skip_cert_verification'])data[key]=f.has(key);
 for(const key of ['uri','bind_dn','bind_password','base_dn','group_base_dn','user_attribute','admin_group','operator_group','viewer_group','ca_cert']){
  if(key==='bind_password'&&!f.get(key))continue;
  data[key]=String(f.get(key)||'').trim();
 }
 return {settings:data,test_user:String(f.get('test_user')||'').trim()};
}


function sshServerOptions(selected='',query='',group=''){
 const list=state.sshInventory.servers||[];
 return [['','— Choisir un serveur SSH —'],...list
  .filter(s=>(!group||s.group_ids.includes(Number(group)))&&
      (!query||(s.fqdn+' '+s.label).toLowerCase().includes(query.toLowerCase())))
  .map(s=>[s.id,s.fqdn+' : '+s.port+(s.label?' · '+s.label:'')])];
}
function sshTargetPicker(prefix,selected='',required=false){
 const groups=state.sshInventory.groups||[];
 const count=(state.sshInventory.servers||[]).length;
 return '<div class="ssh-target-picker" data-ssh-picker="'+e(prefix)+'">'
  +'<div class="form-grid"><div class="field"><span>Filtrer par groupe</span><select data-ssh-filter-group>'
  +option([['','Tous les groupes'],...groups.map(g=>[g.id,g.name+' ('+g.server_count+')'])],'')+'</select></div>'
  +'<div class="field"><span>Rechercher un FQDN</span><input data-ssh-filter-search placeholder="server.infra.example.org" autocomplete="off"></div></div>'
  +'<div class="field"><span>Serveur cible'+(required?' *':'')+'</span><select name="'+e(prefix)+'" data-ssh-target-select '+(required?'required':'')+'>'
  +option(sshServerOptions(selected),selected)+'</select></div>'
  +'<p class="hint">'+count+' serveur(s) enregistré(s) · Gérer les FQDN dans <b>Administration → Serveurs & groupes SSH</b>.</p></div>';
}
function sshRefreshPicker(container){
 if(!container)return;
 const selected=container.querySelector('[data-ssh-target-select]');
 if(!selected)return;
 const value=selected.value,query=container.querySelector('[data-ssh-filter-search]')?.value||'';
 const group=container.querySelector('[data-ssh-filter-group]')?.value||'';
 selected.innerHTML=option(sshServerOptions(value,query,group),value);
 if(value&&!Array.from(selected.options).some(x=>x.value===value))selected.value='';
}
function sshInventoryPage(){
 if(!roleAdmin())return title('Serveurs SSH','Administration uniquement');
 const groups=state.sshInventory.groups||[],servers=state.sshInventory.servers||[];
 const selected=state.sshInventoryGroupFilter||'',term=(state.sshInventorySearch||'').toLowerCase();
 const filtered=servers.filter(x=>(!selected||x.group_ids.includes(Number(selected)))&&
      (!term||(x.fqdn+' '+x.label).toLowerCase().includes(term)));
 const groupCards=groups.map(g=>'<div class="ssh-group-card"><div><b>'+e(g.name)+'</b><p class="hint">'+e(g.description||'')+' · '+g.server_count+' serveur(s)</p></div>'
  +'<div class="credential-actions"><button class="btn tiny" data-action="ssh-group-edit" data-id="'+g.id+'">Modifier</button>'
  +'<button class="btn tiny danger" data-action="ssh-group-remove" data-id="'+g.id+'">Supprimer</button></div></div>').join('');
 const rows=filtered.map(x=>'<div class="ssh-server-row" data-ssh-search="'+e((x.fqdn+' '+x.label).toLowerCase())+'"><div><strong>'+e(x.fqdn)+'</strong><small>Port '+e(x.port)+(x.label?' · '+e(x.label):'')+'</small>'
  +'<div class="ssh-server-tags">'+x.group_ids.map(gid=>{const group=groups.find(g=>g.id===gid);return group?'<span class="chip">'+e(group.name)+'</span>':''}).join('')+'</div></div>'
  +'<div class="credential-actions"><button class="btn tiny" data-action="ssh-server-edit" data-id="'+x.id+'">Modifier</button>'
  +'<button class="btn tiny danger" data-action="ssh-server-remove" data-id="'+x.id+'">Supprimer</button></div></div>').join('');
 return title('Serveurs & groupes SSH','Inventaire des cibles indépendant des identités chiffrées du coffre-fort.',
   '<button class="btn primary" data-action="ssh-group-new">＋ Groupe</button><button class="btn" data-action="ssh-server-new">＋ Serveur</button>')
  +'<div class="notice">Créer une identité <b>SSH</b> dans le Coffre-fort, puis sélectionner un serveur de cet inventaire lors du test ou dans une tâche. Un même serveur peut appartenir à plusieurs groupes. Supprimer un groupe ne supprime pas les serveurs.</div>'
  +'<div class="section-grid ssh-inventory-layout"><section class="panel"><div class="panel-title"><h2>Groupes de serveurs</h2><span class="hint">'+groups.length+' groupe(s)</span></div>'
  +(groupCards||'<p class="hint">Aucun groupe. Crée un groupe et ajoute tes FQDN, un par ligne.</p>')+'</section>'
  +'<section class="panel"><div class="panel-title"><h2>Serveurs enregistrés</h2><span class="hint">'+filtered.length+' / '+servers.length+'</span></div>'
  +'<div class="form-grid"><div class="field"><span>Groupe</span><select id="sshInventoryGroupFilter">'+option([['','Tous les groupes'],...groups.map(g=>[g.id,g.name])],selected)+'</select></div>'
  +'<div class="field"><span>Rechercher</span><input id="sshInventorySearch" placeholder="FQDN ou libellé…" value="'+e(state.sshInventorySearch||'')+'"></div></div>'
  +'<div class="ssh-server-list">'+(rows||'<p class="hint">Aucun serveur correspondant.</p>')+'</div></section></div>';
}
function sshGroupForm(groupId=null){
 const group=(state.sshInventory.groups||[]).find(g=>g.id===groupId);
 const servers=group?(state.sshInventory.servers||[]).filter(s=>group.server_ids.includes(s.id)):[];
 const lines=servers.map(x=>x.fqdn+(x.port!==22?':'+x.port:'')).join('\n');
 const body='<form id="sshGroupForm" data-id="'+(group?.id||'')+'" data-managed="'+(group?.managed_proxmox?'1':'0')+'">'
  +'<div class="form-grid">'+field('Nom du groupe','name',group?.name||'')
  +field('Description','description',group?.description||'')+'</div>'
  +(group?.managed_proxmox
   ?'<div class="notice">Groupe géré par découverte Proxmox : nom et description modifiables. Les membres sont synchronisés depuis le cluster, sans être modifiés ici.</div>'
   :'<div class="field"><span>Liste des FQDN (un par ligne)</span><textarea name="servers" rows="9" placeholder="serveur-a.example.org&#10;serveur-b.example.org&#10;server-custom.infra.example.org:2222">'+e(lines)+'</textarea>')
  +(group?.managed_proxmox?'':'<small class="hint">Ajouter ou retirer des lignes modifie l’appartenance au groupe. Les serveurs retirés restent dans l’inventaire. Port 22 par défaut, préciser :2222 si nécessaire. Max. 250 serveurs.</small></div>')
  +'<div class="tool-row"><button class="btn primary" type="submit">'+(group?'Enregistrer le groupe':'Créer le groupe')+'</button>'
  +'<button class="btn" data-action="layer-close" data-layer="sshInventoryLayer" type="button">Annuler</button></div></form>';
 openLayer('sshInventoryLayer',group?'Modifier le groupe SSH':'Créer un groupe SSH',body);
}
function sshServerForm(serverId=null){
 const server=(state.sshInventory.servers||[]).find(x=>x.id===serverId);
 const groups=state.sshInventory.groups||[];
 const boxes=groups.map(g=>'<label class="ssh-group-option"><input type="checkbox" name="group_id" value="'+g.id+'" '+(server?.group_ids.includes(g.id)?'checked':'')+'><span>'+e(g.name)+'</span></label>').join('');
 const body='<form id="sshServerForm" data-id="'+(server?.id||'')+'"><div class="form-grid">'
  +'<div class="field"><span>FQDN *</span><input name="fqdn" value="'+e(server?.fqdn||'')+'" '+(server?'readonly':'required')+' placeholder="server01.infra.example.org"></div>'
  +'<div class="field"><span>Port SSH</span><input type="number" min="1" max="65535" name="port" value="'+(server?.port||22)+'" required></div></div>'
  +field('Libellé / environnement','label',server?.label||'')
  +'<div class="field"><span>Groupes du serveur</span><div class="ssh-group-checkboxes">'+(boxes||'<span class="hint">Aucun groupe créé</span>')+'</div></div>'
  +'<p class="hint">Le FQDN est immuable sur une fiche existante : cela évite de rediriger silencieusement des workflows vers une autre machine.</p>'
  +'<div class="tool-row"><button class="btn primary" type="submit">'+(server?'Enregistrer le serveur':'Ajouter le serveur')+'</button>'
  +'<button class="btn" type="button" data-action="layer-close" data-layer="sshInventoryLayer">Annuler</button></div></form>';
 openLayer('sshInventoryLayer',server?'Modifier le serveur SSH':'Ajouter un serveur SSH',body);
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

function agentConnectionSettingsPanel(){
 const settings=state.agentSettings||{};
 const url=settings.controller_url||'';
 const configured=!!settings.configured;
 return '<section class="panel agent-settings-panel"><div class="panel-title"><div><h2>Paramètres de connexion des agents</h2>'
  +'<p class="hint">URL HTTPS de la console accessible depuis les machines Linux et Windows, via le proxy inverse Nginx.</p></div>'
  +'<span class="chip '+(configured?'tag':'')+'">'+(configured?'Configurée · '+e(settings.source):'URL manquante')+'</span></div>'
  +'<form id="agentConnectionSettingsForm">'
  +'<label class="field"><span>Adresse HTTPS utilisée par les agents</span>'
  +'<input name="controller_url" type="url" autocomplete="off" spellcheck="false" placeholder="https://controller.example.org" value="'+e(url)+'" required></label>'
  +'<p class="hint">Chaque installation OrigamiTasks configure son propre FQDN HTTPS accessible par ses agents. Saisir uniquement l’origine HTTPS, sans /api, identifiants, ni adresse localhost / 127.0.0.1. '
  +'L’URL interne d’écoute du conteneur ne doit jamais être écrite dans les agents distants. '
  +'Les certificats TLS doivent être reconnus par chaque hôte agent (installer la CA interne si nécessaire).</p>'
  +'<div class="tool-row"><button class="btn primary" type="submit">Enregistrer l’URL des agents</button>'
  +'<span class="hint">Appliquée immédiatement aux nouveaux déploiements ; les agents déjà installés conservent leur URL précédente.</span></div>'
  +'</form></section>';
}

function agentVersionOutdated(current,latest){
 const parse=x=>{const m=/^(\d+)\.(\d+)\.(\d+)$/.exec(String(x||''));return m?m.slice(1).map(Number):null;};
 const a=parse(current),b=parse(latest);
 if(!a||!b)return false;
 for(let i=0;i<3;i++){if(a[i]<b[i])return true;if(a[i]>b[i])return false;}
 return false;
}
function openAgentUpdateForm(ids){
 const targets=(state.agents||[]).filter(a=>ids.includes(a.id));
 const options=state.credentials.filter(c=>c.kind==='ssh').map(c=>[c.id,c.name]);
 if(!options.length)return toast('Créer une identité SSH dans le coffre-fort.',false);
 if(!targets.length)return toast('Aucun agent éligible.',false);
 const body='<form id="agentUpdateForm"><input type="hidden" name="agent_ids" value="'+e(targets.map(a=>a.id).join(','))+'">'
 +'<div class="notice">Mise à jour sur place par SSH : identifiant, jeton, config.json et modules conservés. Les services seront redémarrés un par un. Les agents doivent être dans l’inventaire SSH et présenter une clé d’hôte approuvée.</div>'
 +'<p>'+e(targets.length)+' agent(s) : '+e(targets.map(a=>a.name).join(', '))+'</p>'
 +selectField('Identité SSH (coffre)','credential_id',[['','— Choisir une identité SSH —'],...options],'')
 +'<label class="field checkbox"><input type="checkbox" name="approved" required><span>Je confirme la mise à jour et le redémarrage de ces agents.</span></label>'
 +'<div class="tool-row"><button class="btn primary" type="submit">Lancer les mises à jour</button><button class="btn" data-action="modal-close" type="button">Annuler</button></div></form>';
 modal('Mettre à jour les agents',body,'',true);
}
function agentsPage(){
 if(!roleAdmin())return title('Agents','Administration uniquement.');
 const targetVersions=state.agentUpdates?.available_versions||{};
 const oldAgents=(state.agents||[]).filter(a=>a.status==='online'&&agentVersionOutdated(a.version,targetVersions[a.platform]));
 const cards=(state.agents||[]).map(agent=>{
  const action=agent.uninstalled_at?''
   :'<button class="btn tiny primary" data-action="agent-modules" data-id="'+agent.id+'">Configurer modules</button>'
    +'<button class="btn tiny danger" data-action="agent-uninstall" data-id="'+agent.id+'">Désinstaller via SSH</button>'
     +(agent.revoked?'':'<button class="btn tiny" data-action="agent-revoke" data-id="'+agent.id+'">Révoquer le jeton</button>');
  const currentVersion=targetVersions[agent.platform];
  const isOld=agentVersionOutdated(agent.version,currentVersion);
  const inProgress=(state.agentUpdates?.items||[]).some(j=>j.agent_id===agent.id&&['queued','running','awaiting_heartbeat'].includes(j.status));
  const updateButton=isOld&&!agent.revoked&&!agent.uninstalled_at
   ?'<button class="btn tiny primary" data-action="agent-update" data-id="'+agent.id+'" '+(inProgress?'disabled':'')+'>'+(inProgress?'Mise à jour en cours':'↑ Mettre à jour')+'</button>':'';
  const refreshButton='<button class="btn tiny" data-action="agent-status-refresh" data-id="'+agent.id+'">↻ État reçu</button>';
  return '<tr><td class="name-cell">'+e(agent.name)+'<div class="muted tinytext">'+e(agent.hostname||'Non enrôlé')+'</div></td>'
    +'<td>'+e(agent.platform)+' · '+e(agent.version||'Version inconnue')
    +(isOld?'<div class="muted tinytext">Dernière : '+e(currentVersion)+' · à mettre à jour</div>':'')+'</td>'
    +'<td>'+e(agent.group_name)+'</td><td>'+labelStatus(agent.status)+'</td>'
    +'<td><span class="hint">Base intégrée</span>'
    +(agent.platform==='linux'&&agent.modules?.proxmox?'<span class="chip tag">Proxmox</span>':'')
    +(agent.platform==='linux'&&agent.modules?.pra?'<span class="chip tag">PRA préparatoire</span>':'')
    +'<div class="muted tinytext">'+(agent.modules_in_sync?'Appliqué · révision '+e(agent.modules_revision):'En attente / agent ancien ou hors ligne')+'</div>'
    +(agent.modules_status?.pra?'<small class="hint">PRA local : '+e(agent.modules_status.pra)+'</small>':'')+'</td>'
    +'<td>'+(Number.isFinite(agent.cpu_percent)?e(agent.cpu_percent.toFixed(1))+' %':'—')+'</td>'
    +'<td>'+(Number.isFinite(agent.memory_mb)?e(agent.memory_mb.toFixed(1))+' Mio':'—')+'</td>'
    +'<td>'+dt(agent.last_seen)+'</td>'
    +'<td class="agent-action-buttons">'+refreshButton+updateButton+(action||'<span class="hint">Désinstallé</span>')+'</td></tr>';
 }).join('');
 return title('Agents Linux et Windows','Agent Linux modulaire ; agent Windows dédié aux actions des workflows.',
  '<div class="tool-row"><button class="btn" data-action="agent-enroll-form">＋ Enrôlement manuel</button>'
  +'<button class="btn primary" data-action="agent-deploy-form">⇣ Déployer sur un serveur</button>'
  +'<button class="btn" data-action="agent-deploy-bulk-form">⇣ Plusieurs serveurs</button>'
  +'<button class="btn primary" data-action="agent-update-all" '+(!oldAgents.length?'disabled':'')+'>↑ Mettre à jour les '+oldAgents.length+' agent(s) obsolètes en ligne</button></div>')
  +agentConnectionSettingsPanel()
  +'<div class="panel"><div class="panel-title"><h2>Parc d’agents</h2>'
  +'<button class="btn tiny" data-action="agents-status-refresh">↻ Actualiser les états reçus</button>'
  +'<span class="hint">'+state.agents.length+' agent(s)</span></div>'
  +'<div class="table-wrap"><table><thead><tr><th>Nom / hôte</th><th>OS / version</th><th>Groupe</th>'
  +'<th>État</th><th>Modules & synchronisation</th><th>CPU agent</th><th>RAM agent</th><th>Dernier contact</th><th>Actions</th></tr></thead>'
  +'<tbody>'+(cards||'<tr><td colspan="9">Aucun agent installé.</td></tr>')+'</tbody></table></div></div>'
  +'<div class="panel"><div class="panel-title"><h2>Mises à jour des agents</h2><span class="hint">Manager '+e(state.managerVersion||'—')+' · Agents intégrés Linux '+e(targetVersions.linux||'—')+' / Windows '+e(targetVersions.windows||'—')+' (versions réellement installées détaillées ci-dessus)</span></div>'
  +'<div class="table-wrap"><table><thead><tr><th>Agent</th><th>Version</th><th>État</th><th>Demande</th></tr></thead><tbody>'
  +((state.agentUpdates?.items||[]).slice(0,15).map(j=>'<tr><td>'+e(j.host)+'</td><td>'+e(j.from_version)+' → '+e(j.target_version)+'</td><td>'+labelStatus(j.status)+'</td><td>'+dt(j.created_at)+(j.error?'<div class="hint">'+e(j.error)+'</div>':'')+'</td></tr>').join('')||'<tr><td colspan="4">Aucune mise à jour demandée.</td></tr>')
  +'</tbody></table></div></div>'
  +'<div class="panel"><div class="panel-title"><h2>Déploiements groupés</h2>'
  +'<button class="btn tiny" data-action="agent-batch-refresh">↻ Actualiser</button><button class="btn tiny danger" data-action="agent-batch-clean">Nettoyer les lots terminés</button></div>'
  +'<div id="agentBatchList">'+agentBatchPanel()+'</div></div>'
  +'<div class="notice"><b>Révoquer ≠ Désinstaller.</b> La révocation invalide le token sans retirer le programme sur le serveur.'
  +' La désinstallation vérifie la cible et le fichier d’identité de l’agent, supprime uniquement les éléments OrigamiTasks puis révoque son token.'
  +' L’installation Linux applique des limites systemd (192 Mio / 20 % d’un CPU) aux nouveaux déploiements ;'
  +' les métriques affichées concernent uniquement le processus agent. Les agents existants ne sont pas modifiés automatiquement.'
  +' Les agents Windows actuels fonctionnent sous SYSTEM : leur durcissement doit être renforcé avant une généralisation sur les serveurs critiques.</div>';
}
function openAgentModulesForm(agentId){
 const a=(state.agents||[]).find(x=>x.id===Number(agentId));
 if(!a)return toast('Agent introuvable',false);
 if(a.revoked||a.uninstalled_at)return toast('Agent révoqué ou désinstallé',false);
 const site=(state.drSites||[]).find(x=>(x.agents||[]).some(y=>y.id===a.id));
 const siteLine=site?e(site.name)+' · '+e(site.role)+' · '+(site.datacenters||[]).length+' cluster(s)'
                    :'Aucun site rattaché — associer cet agent dans DC Proxmox → Sites PRA';
 const canInfra=a.platform==='linux';
 const module=(key,label,desc,on,disabled=false)=>
   '<label class="agent-module-choice"><input type="checkbox" name="'+key+'" '+(on?'checked ':'')+(disabled?'disabled ':'')+'>'
   +'<span><strong>'+label+'</strong><small>'+desc+'</small></span></label>';
 const m=a.modules||{standard:true,proxmox:false,pra:false};
 const form='<form id="agentModulesForm" data-id="'+a.id+'">'
  +'<div class="notice">Un seul agent installé par machine. Les modules sont attribués par la console puis appliqués au prochain contact HTTPS de l’agent.'
  +' Un agent ancien ou hors ligne reste en attente ; ce formulaire ne lance aucune VM.</div>'
  +'<p><b>Agent :</b> '+e(a.name)+' · '+e(a.platform)+' · '+e(a.hostname||'—')+'</p>'
  +'<p><b>Site :</b> '+siteLine+'</p>'
  +'<div class="agent-module-choices">'
  +'<div class="notice">Base Standard intégrée à tous les agents ; aucune activation séparée nécessaire.</div>'
  +(canInfra?module('proxmox','Proxmox','Association au site et aux clusters ; API Proxmox toujours côté console',m.proxmox)
    +module('pra','PRA — préparation passive','Surveillance locale uniquement : aucune réplication ni bascule VM',m.pra)
    :'<div class="notice">Agent Windows : uniquement le module Standard pour étendre les actions des workflows (scripts PowerShell, processus et services). Aucun rôle Proxmox ou PRA.</div>')
  +'</div>'
  +'<div class="notice"><b>État réel :</b> '
  +e(a.modules_in_sync?'Configuration appliquée':'Configuration pas encore confirmée')
  +' · révision souhaitée '+e(a.modules_revision)
  +' · version appliquée '+e(a.modules_applied_revision)
  +' · service PRA : '+e(a.modules_status?.pra||'Non remonté')+'</div>'
  +'<div class="tool-row"><button class="btn primary" type="submit">Enregistrer les modules</button>'
  +'<button class="btn" type="button" data-action="modal-close">Annuler</button></div></form>';
 modal('Modules de '+e(a.name),form,'',true);
}

function openAgentUninstallForm(id){
 const a=(state.agents||[]).find(item=>item.id===Number(id));
 if(!a)return toast('Agent introuvable',false);
 const known=(state.sshInventory.servers||[]).filter(h=>
  (h.fqdn||'').toLowerCase().split('.')[0]===(a.hostname||'').toLowerCase()||
  (h.fqdn||'').toLowerCase()===(a.hostname||'').toLowerCase());
 const ssh=state.credentials.filter(c=>c.kind==='ssh');
 const contents='<form id="agentUninstallForm" data-id="'+a.id+'">'
  +'<div class="notice error"><b>Retrait sur le serveur réel.</b> Le service Linux ou la tâche planifiée Windows sera supprimé sur la machine SSH vérifiée.'
  +' Aucun autre service ne sera supprimé. En cas d’échec distant, le jeton reste utilisable : tu pourras réessayer ou le révoquer séparément.</div>'
  +'<p><b>Agent :</b> '+e(a.name)+' · '+e(a.platform)+' · '+e(a.hostname||'hôte inconnu')+'</p>'
  +'<div class="form-grid">'
  +field('FQDN complet du serveur','uninstall_host',known[0]?.fqdn||(a.hostname?.includes('.')?a.hostname:''))
  +field('Port SSH','uninstall_port',known[0]?.port||22,'number')
  +selectField('Identité SSH du coffre','uninstall_credential',
    [['','— Sélectionner une identité SSH —'],...ssh.map(v=>[v.id,v.name])],'')
  +field('Confirmer en saisissant le nom exact de l’agent','uninstall_confirmation','','text')
  +field('URL HTTPS configurée lors de son installation','installed_controller_url',
    a.installed_controller_url||state.agentSettings?.controller_url||'','url')
  +'</div><label class="field checkbox"><input type="checkbox" name="uninstall_approved" required>'
  +'<span>Je confirme que cet agent doit être désinstallé de cette machine.</span></label>'
  +'<p class="hint">La clé d’hôte SSH doit déjà être connue et approuvée. '
  +'Le serveur distant doit présenter le même agent_id et la même URL de contrôle. '
  +'Pour un ancien agent sans URL enregistrée dans la base, indiquer son ancienne URL HTTPS exacte. '
  +'Une clé inconnue ou modifiée bloque la suppression.</p>'
  +'<div class="tool-row"><button class="btn danger" type="submit">Désinstaller cet agent</button>'
  +'<button class="btn" type="button" data-action="modal-close">Annuler</button></div></form>';
 modal('Désinstaller l’agent '+e(a.name),contents,'',true);
}
function agentDeployForm(){modal('Déployer un agent via SSH','<form id="agentDeployForm"><div class="form-grid">'+
 field('Nom de l’agent','name')+selectField('Système','platform',[['linux','Linux (Python 3 + systemd)'],['windows','Windows (PowerShell 5.1+)']],'linux')+
 field('Hôte / FQDN autorisé','host')+field('Port SSH','port',22,'number')+field('Groupe','group','default')+
 selectField('Compte SSH (coffre)','credential_id',[['','— Sélectionner une identité SSH —'],...state.credentials.filter(c=>c.kind==='ssh').map(c=>[c.id,c.name])],'')+
 '</div><div class="field checkbox"><input type="checkbox" name="approved" required><span>Je confirme l’installation d’un service ou d’une tâche planifiée sur cet hôte.</span></div><p class="hint">Le serveur doit joindre l’hôte par SSH et l’agent joindre OrigamiTasks par HTTPS.</p><button class="btn primary" type="submit">Déployer l’agent</button></form>', '', true)}

let agentBatchPollTimer=null;
function scheduleAgentBatchPoll(){
 if(agentBatchPollTimer||state.page!=='agents'||!roleAdmin())return;
 if(!(state.agentDeployBatches||[]).some(x=>x.status==='queued'||x.status==='running') &&
     !(state.agentUpdates?.items||[]).some(x=>['queued','running','awaiting_heartbeat'].includes(x.status)))return;
 agentBatchPollTimer=setTimeout(async()=>{
  agentBatchPollTimer=null;
  if(state.page!=='agents'||!roleAdmin())return;
  try{
   state.agentDeployBatches=(await api('/agents/deploy/batches')).items;
   state.agentUpdates=await api('/agents/updates');
   state.agents=(await api('/agents')).items;
   if(state.page==='agents'){render();return;}
   const panel=document.getElementById('agentBatchList');
   if(panel)panel.innerHTML=agentBatchPanel();
  }catch(ex){console.warn('Statut des déploiements agents non disponible',ex?.message)}
  scheduleAgentBatchPoll();
 },3500);
}
function agentBatchPanel(){
 const batches=state.agentDeployBatches||[];
 if(!batches.length)return '<p class="hint">Aucun déploiement groupé pour le moment.</p>';
 return batches.slice(0,8).map(batch=>{
  const stateLabel=batch.status==='success'?'Terminé':batch.status==='completed_with_errors'?'Terminé avec erreurs':batch.status==='interrupted'?'Interrompu':'En cours';
  const rows=batch.targets.map(t=>'<div class="agent-batch-host"><strong>'+e(t.host)+':'+e(t.port)+'</strong><span class="status '+e(t.status)+'">'+e(statusName[t.status]||t.status)+'</span>'
   +(t.error?'<small class="hint">'+e(t.error)+'</small>':'')+'</div>').join('');
  const planName=batch.dr_plan_name||'';
  const scope=batch.datacenter_name
   ?'<small class="hint">'+(planName?'PRA : '+e(planName)+' · ':'PRA non classé · ')+e(batch.datacenter_name)+'</small>'
   :'<small class="hint">Déploiement générique (hors groupe PRA)</small>';
  return '<details class="agent-batch"><summary><strong>Lot #'+e(batch.id)+'</strong>'+scope+'<span class="dr-batch-summary">'+labelStatus(batch.status)+' · '+e(batch.done)+'/'+e(batch.total)+' · '+e(batch.success)+' réussis</span></summary>'
   +'<div class="agent-batch-hosts">'+rows+'</div></details>';
 }).join('');
}
function agentBulkDeployForm(){
 const groups=(state.sshInventory.groups||[]),serverCount=(state.sshInventory.servers||[]).length;
 const sshAccounts=state.credentials.filter(c=>c.kind==='ssh');
 const body='<form id="agentBulkDeployForm"><div class="notice">Installer des agents sur une liste de serveurs est une action privilégiée. Les installations sont mises en file et suivies individuellement ; aucune relance automatique après interruption.</div>'
  +'<div class="form-grid">'+selectField('Système cible','platform',[['linux','Linux (Python 3 / systemd)'],['windows','Windows (PowerShell / tâche SYSTEM)']],'linux')
  +selectField('Identité SSH (coffre)','credential_id',[['','— Choisir une identité SSH —'],...sshAccounts.map(c=>[c.id,c.name])],'')
  +field('Groupe attribué aux agents','agent_group','default')
  +'<div class="field"><span>Choisir un groupe de serveurs</span><div class="inline-picker"><select name="inventory_group" id="agentBulkInventoryGroup">'
  +option([['','— Ajouter par FQDN —'],...groups.map(g=>[g.id,g.name+' ('+g.server_count+')'])],'')
  +'</select><button type="button" class="btn tiny" data-action="agent-bulk-add-group">Ajouter</button></div></div></div>'
  +'<div class="field"><span>FQDN des cibles (un par ligne, port optionnel)</span><textarea name="targets" rows="10" placeholder="au-host01.infra.example.org&#10;sd-host02.infra.example.org&#10;au-host03.infra.example.org:2222" required></textarea>'
  +'<small class="hint">'+serverCount+' serveur(s) dans l’inventaire, 100 cibles maximum par lot. Le FQDN doit être explicitement autorisé dans AGENT_DEPLOY_ALLOWED_HOSTS. Les cibles Windows nécessitent SSH/SFTP actif.</small></div>'
  +'<label class="test-wizard-option"><input type="checkbox" name="approved" required><span><strong>Je confirme l’installation de l’agent sur chacun des serveurs indiqués.</strong><small>Service systemd non privilégié sur Linux ou tâche planifiée SYSTEM sur Windows. L’agent contacte OrigamiTasks par HTTPS.</small></span></label>'
  +'<div class="tool-row"><button class="btn primary" type="submit">Mettre les déploiements en file</button><button class="btn" type="button" data-action="modal-close">Annuler</button></div></form>';
 modal('Déployer des agents sur plusieurs serveurs',body,'',true);
}

function agentEnrollForm(){modal('Enrôlement manuel','<form id="agentEnrollForm"><div class="form-grid">'+
 field('Nom','name')+selectField('Plateforme','platform',[['linux','Linux'],['windows','Windows']],'linux')+
 field('Groupe','group','default')+'</div><p class="hint">Jeton unique valable 15 minutes. Installer agents/linux/agent.py ou agents/windows/agent.ps1 avec config.json sur la machine.</p><button class="btn primary" type="submit">Créer un jeton</button></form>')}


const PVE_TYPES={node:'Nœuds',qemu:'VM',lxc:'LXC',storage:'Stockages'};
function openProxmoxDiscovery(dcId,response){
 const items=Array.isArray(response.resources)?response.resources.filter(x=>x&&PVE_TYPES[x.type]):[];
 state.proxmoxDiscovery={dcId,name:String(response.datacenter||''),items,type:'all',query:'',sort:'name',descending:false,page:0,pageSize:100};
 const options=[['all','Tous les types'],['node','Nœuds'],['qemu','VM'],['lxc','LXC'],['storage','Stockages']];
 const ordering=[['name','Nom / VMID'],['type','Type'],['vmid','VMID'],['node','Nœud'],['status','État']];
 const content='<p class="hint">Données obtenues en lecture seule via l’API Proxmox. Le filtre et le tri s’appliquent à toutes les ressources récupérées.</p>'
   +'<div class="proxmox-discovery-tools">'
   +'<label class="field"><span>Rechercher nom, VMID, nœud ou état</span><input id="proxmoxResourceSearch" type="search" placeholder="Ex. 106, postgres, pve-node01…"></label>'
   +'<label class="field"><span>Type de ressource</span><select id="proxmoxResourceType">'+option(options,'all')+'</select></label>'
   +'<label class="field"><span>Trier par</span><select id="proxmoxResourceSort">'+option(ordering,'name')+'</select></label>'
   +'<label class="field"><span>Ordre</span><select id="proxmoxResourceDirection">'+option([['asc','Croissant'],['desc','Décroissant']],'asc')+'</select></label></div>'
   +'<div id="proxmoxDiscoveryCounts" class="proxmox-discovery-counts" aria-live="polite"></div>'
   +'<div id="proxmoxDiscoveryTable" class="table-wrap"></div>'
   +'<div id="proxmoxDiscoveryPager" class="proxmox-discovery-pager"></div>';
 modal('Ressources Proxmox · '+e(state.proxmoxDiscovery.name),content,
  '<button class="btn" data-action="proxmox-discovery-refresh">Actualiser depuis Proxmox</button><button class="btn primary" data-action="modal-close">Fermer</button>',true);
 renderProxmoxDiscoveryResults();
}
function renderProxmoxDiscoveryResults(){
 const d=state.proxmoxDiscovery;
 if(!d||!document.getElementById('proxmoxDiscoveryTable'))return;
 const counts={node:0,qemu:0,lxc:0,storage:0};
 d.items.forEach(item=>{if(Object.prototype.hasOwnProperty.call(counts,item.type))counts[item.type]++});
 const query=d.query.trim().toLocaleLowerCase('fr');
 const filtered=d.items.filter(item=>(d.type==='all'||item.type===d.type)&&
  (!query||[item.name,item.vmid,item.id,item.node,item.status,item.storage]
    .some(value=>String(value??'').toLocaleLowerCase('fr').includes(query))));
 const str=value=>String(value??'');
 const valueFor=item=>d.sort==='name'?(item.name||item.vmid||item.id||''):item[d.sort];
 filtered.sort((a,b)=>{
  const x=valueFor(a),y=valueFor(b);
  let result=(d.sort==='vmid')?(Number(x||0)-Number(y||0))
      :str(x).localeCompare(str(y),'fr',{numeric:true,sensitivity:'base'});
  if(result===0)result=str(a.id||a.vmid||a.name).localeCompare(str(b.id||b.vmid||b.name),'fr',{numeric:true});
  return d.descending?-result:result;
 });
 const pages=Math.max(1,Math.ceil(filtered.length/d.pageSize));
 d.page=Math.max(0,Math.min(d.page,pages-1));
 const slice=filtered.slice(d.page*d.pageSize,(d.page+1)*d.pageSize);
 $('#proxmoxDiscoveryCounts').innerHTML='<span class="chip">Total : '+d.items.length+'</span>'
  +Object.entries(counts).map(([key,count])=>'<span class="chip">'+PVE_TYPES[key]+' : '+count+'</span>').join('')
  +'<span class="chip tag">Résultats : '+filtered.length+'</span>';
 const rows=slice.map(item=>{
  const label=PVE_TYPES[item.type]||item.type;
  const vmid=item.vmid??'—';
  return '<tr><td>'+e(label)+'</td><td><strong>'+e(item.name||item.vmid||item.id||'—')+'</strong></td>'
    +'<td>'+e(vmid)+'</td><td>'+e(item.node||'—')+'</td><td>'+e(item.status||'—')+'</td>'
    +'<td>'+(item.type==='qemu'&&Number.isSafeInteger(Number(item.vmid))
      ?'<div class="tool-row"><button class="btn tiny" data-action="assess-dr" data-id="'+d.dcId+'" data-vmid="'+e(item.vmid)+'">Évaluer PRA</button>'
        +'<button class="btn tiny primary" data-action="protect-vm-resource" data-id="'+d.dcId+'" data-vmid="'+e(item.vmid)+'">Protéger</button></div>':'')
    +'</td></tr>';
 }).join('');
 $('#proxmoxDiscoveryTable').innerHTML='<table class="proxmox-discovery-table"><thead><tr>'
  +'<th>Type</th><th>Nom</th><th>VMID</th><th>Nœud</th><th>État</th><th>Actions</th></tr></thead><tbody>'
  +(rows||'<tr><td colspan="6">Aucune ressource ne correspond aux filtres.</td></tr>')+'</tbody></table>';
 $('#proxmoxDiscoveryPager').innerHTML='<span class="hint">'+(filtered.length?(d.page*d.pageSize+1)+'–'+Math.min((d.page+1)*d.pageSize,filtered.length):'0')
  +' sur '+filtered.length+' · page '+(d.page+1)+'/'+pages+'</span>'
  +'<div class="tool-row"><button class="btn tiny" data-action="proxmox-discovery-prev" '+(d.page===0?'disabled':'')+'>Précédent</button>'
  +'<button class="btn tiny" data-action="proxmox-discovery-next" '+(d.page>=pages-1?'disabled':'')+'>Suivant</button></div>';
}

function datacenters(){
 if(!roleAdmin())return title('Proxmox','Administration uniquement.');
 const list=state.datacenters||[];
 const cards=list.map(d=>'<div class="job-card proxmox-card" data-dc-search="'+e((d.name+' '+d.url).toLowerCase())+'">'
   +'<div class="job-card-head"><div class="job-icon">◈</div><span class="chip tag">'+(d.kind==='node'?'Serveur individuel':'Cluster Proxmox')+'</span></div>'
   +'<h3>'+e(d.name)+'</h3><p>'+e(d.url)+'</p>'
   +'<div class="muted tinytext">Connexion coffre #'+e(d.credential_id)+(d.skip_tls_verification?' · ⚠ Certificat TLS non vérifié':d.ca_cert?' · CA TLS importée':' · CAs système')+'</div>'
   +'<div class="actions"><button class="btn tiny" data-action="test-datacenter" data-id="'+d.id+'">Tester l’API</button>'
   +'<button class="btn primary tiny" data-action="discover-datacenter" data-id="'+d.id+'">Découvrir les nœuds/VM</button>'
   +'<button class="btn tiny" data-action="fleet-open" data-id="'+d.id+'">Agents du cluster</button>'
   +'<button class="btn tiny" data-action="edit-datacenter" data-id="'+d.id+'">Modifier</button>'
   +'<button class="btn tiny danger" data-action="delete-datacenter" data-id="'+d.id+'">Supprimer</button></div></div>').join('');
 return title('Serveurs & clusters Proxmox','Déclare chaque cluster ou serveur autonome dans OrigamiTasks, avec son token et sa CA, sans modifier le .env.')
   +'<div class="proxmox-toolbar"><button class="btn primary" data-action="new-datacenter">＋ Ajouter un serveur / cluster</button></div>'
   +'<div class="form-grid"><label class="field"><span>Rechercher parmi '+list.length+' serveurs et clusters</span>'
   +'<input id="proxmoxDcSearch" placeholder="Nom, FQDN, URL…"></label></div>'
   +'<div class="workflow-grid proxmox-grid">'+(cards||empty('Aucun Proxmox enregistré','Ajoute un cluster ou un serveur depuis cette page.'))+'</div>'
    +renderDRSitesPanel()
   +'<div class="notice">Chaque cluster dévoile ses nœuds et VM via la découverte. Les tokens restent chiffrés dans le coffre. '
   +'La vérification TLS est activée par défaut ; une exception est possible sur chaque fiche Proxmox pour les certificats autosignés. Les actions sensibles restent soumises à des permissions et validations distinctes.</div>';
}


/* A protected VM can run a disposable LAB copy; actual promotion remains
   blocked until independent fencing and witness controls are proven. */
function protectedVMsPage(){
 if(!roleAdmin())return title('VM protégées PRA','Administration uniquement.');
 const plans=state.drPlans||[],all=state.drProtectedVMs||[];
 const selected=state.drVMPlanFilter||'all';
 const current=selected==='all'?all:all.filter(x=>String(x.plan_id)===String(selected));
 const tableRows=current.map(vm=>{
  const last=vm.last_check||{};
  return '<tr><td><button class="text-btn" data-action="dr-vm-lvm-lab-open" data-id="'+vm.id+'" title="Ouvrir le diagnostic PRA">'+e(vm.source_name||'VM #'+vm.source_vmid)+'</button>'
   +'<div class="muted tinytext">Protection OrigamiTasks #'+e(vm.id)+'</div>'
   +'<div class="muted tinytext">VMID '+e(vm.source_vmid)+' · '+e(vm.source_node)+'</div></td>'
   +'<td><b>'+e(vm.plan_name)+'</b><div class="muted tinytext">'+e(vm.source_dc_name)+' / '+e(vm.source_site_name)
   +' → '+e(vm.target_dc_name)+' / '+e(vm.target_site_name)+'</div></td>'
   +'<td><button class="text-btn" data-action="dr-vm-lvm-lab-open" data-id="'+vm.id+'" title="Journal de réplication et diagnostic">'+e(vm.replica_name)+'</button>'
   +'<div class="muted tinytext">'+(vm.lvm_standby_configured?'Disques publiés sur REP_ · boot non validé':'Réplique non encore publiée')+'</div>'
   +'<div class="muted tinytext">Nom Proxmox : '+e(vm.replica_name.replaceAll('_','-'))+'</div>'
   +'<div class="muted tinytext">VMID '+e(vm.target_vmid)
   +' · '+e(vm.target_node)+' · '+e(vm.target_storage)+'</div></td>'
   +'<td>'+labelStatus(vm.lvm_effective_status==='uncertain'?'failed':vm.state)
     +(vm.lvm_activation?.status==='uncertain'?'<div class="muted tinytext">Activation bloquée : '+e(vm.lvm_activation.details||'')+'</div>':'')
     +'<div class="muted tinytext">'+e(vm.interval_minutes)
     +' min '+(vm.lvm_lab_activated?'(incrémental programmé après amorçage)':'(objectif indicatif)')+'</div>'
     +(vm.lvm_activation?'<div class="muted tinytext">'+e(vm.lvm_activation.phase)+' · '+e(vm.lvm_activation.details)+'</div>':'')+'</td>'
   +'<td>'+labelStatus(vm.lvm_lab_latest_run?.status||vm.replication_status)
     +'<div class="muted tinytext">Dernière copie LVM : '
     +(vm.lvm_lab_latest_run?.point_at?dt(vm.lvm_lab_latest_run.point_at):(vm.last_successful_sync?dt(vm.last_successful_sync):'aucune'))+'</div>'
     +'<div class="muted tinytext">Moteur : '+(vm.lvm_lab_latest_run?'QMP → LVM de recette':'QMP → DIR / non configuré')+'</div>'
   +'<div class="muted tinytext">Validation : '+(vm.lvm_standby_configured?'checkpoint LVM vérifié et attaché, boot non certifié':vm.offline_image_verified?'structurelle DIR, hors ligne':'pas encore certifiée')+'</div>'
   +'<div class="muted tinytext">'+e(vm.last_replication_run?.details||'')+'</div>'
   +(vm.lvm_lab_latest_run?'<div class="muted tinytext"><b>LVM RECETTE :</b> '
     +labelStatus(vm.lvm_lab_latest_run.status)+' · '+e(vm.lvm_lab_latest_run.mode)
     +' · '+e(vm.lvm_lab_latest_run.details||'')+'</div>':'')
   +(vm.lvm_last_publication?'<div class="muted tinytext"><b>REP_ LVM :</b> '
     +labelStatus(vm.lvm_last_publication.status)+' · '+e(vm.lvm_last_publication.details||'')
     +'</div>':'')+'</td>'
   +'<td>'+(last.scenario?labelStatus(last.decision||'BLOCKED')+'<div class="muted tinytext">'
     +dt(vm.last_check_at)+'</div>':'<span class="muted">Jamais testé</span>')+'</td>'
   +'<td class="dr-vm-actions">'
   +'<button class="btn tiny" data-action="dr-vm-preflight" data-id="'+vm.id+'">Tester la réplication</button>'
   +'<button class="btn tiny" data-action="dr-vm-storage-readiness" data-id="'+vm.id+'">Compatibilité stockage</button>'
   +'<button class="btn tiny" data-action="dr-vm-recovery-readiness" data-id="'+vm.id+'">Préparation bascule</button>'
   +(vm.engine==='qmp_live_incremental'?'<button class="btn tiny" data-action="dr-vm-lvm-lab-open" data-id="'+vm.id+'">Réplication LVM</button>':'')
   +(vm.engine==='pbs_backup_restore'?'<button class="btn tiny primary" data-action="dr-vm-pbs-backup" data-id="'+vm.id+'">Sauvegarder sur PBS</button>':'')
   +(vm.engine==='qmp_live_incremental'&&!vm.lvm_lab_activated?'<button class="btn tiny" data-action="dr-vm-qmp-preflight" data-id="'+vm.id+'">Prévol QMP</button><button class="btn tiny primary" data-action="dr-vm-qmp-sync" data-id="'+vm.id+'" '+(['queued','running','uncertain'].includes(vm.replication_status)?'disabled':'')+'>Synchroniser à chaud</button>'+(vm.replication_status==='uncertain'?'<button class="btn tiny" data-action="dr-vm-qmp-reconcile" data-id="'+vm.id+'">Réconcilier</button>':''):'')
   +'<button class="btn tiny" data-action="dr-vm-failover" data-id="'+vm.id+'">Simuler la bascule</button>'
   +'<button class="btn tiny" data-action="dr-vm-failback" data-id="'+vm.id+'">Simuler le retour</button>'
   +'<button class="btn tiny" data-action="dr-vm-history" data-id="'+vm.id+'">Historique</button>'
   +'<button class="btn tiny" data-action="dr-vm-edit" data-id="'+vm.id+'">Modifier</button>'
   +'<button class="btn tiny" data-action="dr-vm-pause" data-id="'+vm.id+'">'+(vm.state==='paused'?'Reprendre la préparation':'Mettre en pause')+'</button>'
   +'<button class="btn tiny danger" data-action="dr-vm-delete" data-id="'+vm.id+'">Retirer</button>'
   +'</td></tr>';
 }).join('');
 return title('VM protégées PRA','Copie QEMU à chaud : DIR ou LVM de recette exclusivement ; bascule réelle désactivée.',
   '<div class="tool-row"><button class="btn primary" data-action="dr-vm-new">＋ Ajouter une VM</button>'
   +'<button class="btn" data-action="dr-vm-refresh">↻ Actualiser</button></div>')
  +'<div class="notice dr-safety-notice"><b>QMP expérimental :</b> une synchronisation approuvée peut créer une VM REP_ arrêtée sur stockage DIR inscriptible ; source toujours allumée. '
  +'Copie LVM recette : amorçage REP_, copies FULL/incrémentales et publication, avec limite de 8 générations sans rétention automatique. '
  +'Une vérification QCOW2 hors ligne ne garantit pas le démarrage ni la cohérence applicative. '
   +'Les tests de bascule et de retour restent des simulations : aucune promotion automatique.</div>'
  +'<div class="stats-grid dr-vm-stat-grid">'
  +'<div class="stat-card featured"><span class="label">VM sélectionnées</span><div class="value">'+current.length+'</div><div class="sub">Politiques PRA</div></div>'
  +'<div class="stat-card"><span class="label">Politiques actives</span><div class="value">'+current.filter(x=>x.state==='planned').length+'</div><div class="sub">Tâches programmées après amorçage</div></div>'
  +'<div class="stat-card"><span class="label">En pause</span><div class="value">'+current.filter(x=>x.state==='paused').length+'</div><div class="sub">Enregistrements conservés</div></div>'
  +'<div class="stat-card"><span class="label">Dernières synchros réussies</span><div class="value">'+current.filter(x=>x.replication_status==='completed').length+'</div><div class="sub">Sans validation de bascule PRA</div></div></div>'
  +'<div class="panel"><div class="panel-title"><h2>Inventaire des VM sélectionnées</h2>'
  +'<div class="tool-row"><label class="field">Groupe PRA'
  +'<select id="drVMPlanFilter">'+option([['all','Tous les groupes'],...plans.map(p=>[p.id,p.name])],selected)+'</select>'
  +'</label></div></div>'
  +'<div class="table-wrap"><table><thead><tr><th>VM source</th><th>Périmètre</th>'
  +'<th>Réplique prévue</th><th>État</th><th>Réplication</th><th>Dernier test</th><th>Actions</th></tr></thead>'
  +'<tbody>'+(tableRows||'<tr><td colspan="7">Aucune VM sélectionnée dans ce groupe. '
    +'Ajoute manuellement une VM après découverte des clusters.</td></tr>')+'</tbody></table></div></div>';
}
function drVMClusters(planId,role){
 const sites=(state.drSites||[]).filter(s=>s.plan_id===Number(planId)&&s.role===role);
 return sites.flatMap(s=>(s.datacenters||[]).map(d=>({
  id:d.id,name:d.name+' · '+s.name,site:s.name
 })));
}
function openProtectedVMWizard(existingId=null,preselectDC=null,preselectVMID=null,preselectPlan=null){
 const current=(state.drProtectedVMs||[]).find(x=>x.id===Number(existingId));
 const groups=(state.drPlans||[]).filter(p=>
  drVMClusters(p.id,'primary').length&&drVMClusters(p.id,'standby').length);
 if(!groups.length)return toast('Créer d’abord un groupe PRA avec un cluster principal et un cluster secours.',false);
 const site=(state.drSites||[]).find(s=>(s.datacenters||[]).some(d=>d.id===Number(preselectDC)));
 const planId=current?.plan_id||preselectPlan||site?.plan_id||Number(groups[0].id);
 const src=drVMClusters(planId,'primary');
 const dst=drVMClusters(planId,'standby');
 if(!src.length||!dst.length)return toast('Les deux sites du groupe PRA doivent être configurés.',false);
 const sourceId=current?.source_dc_id||(src.find(x=>x.id===Number(preselectDC))||src[0]).id;
 const targetId=current?.target_dc_id||dst[0].id;
 state.drVMWizard={existingId:current?.id||null,
                   preselectVMID:current?.source_vmid||preselectVMID||null};
 const body='<form id="drVMSourceForm"><div class="notice">Étape 1 — choisir le groupe PRA et deux clusters de sites différents. '
  +'Les VM et stockages seront ensuite lus directement dans les API Proxmox.</div>'
  +'<div class="form-grid">'
  +selectField('Groupe PRA','dr_vm_plan',groups.map(p=>[p.id,p.name]),planId)
  +selectField('Cluster principal','dr_vm_source_dc',src.map(x=>[x.id,x.name]),sourceId)
  +selectField('Cluster secours','dr_vm_target_dc',dst.map(x=>[x.id,x.name]),targetId)
  +'</div><div class="tool-row"><button class="btn primary" type="submit">Découvrir les VM et stockages</button>'
  +'<button class="btn" data-action="modal-close" type="button">Annuler</button></div></form>';
 modal(current?'Modifier la protection de VM #'+current.source_vmid:'Ajouter une VM protégée',body,'',true);
}
async function openProtectedVMConfig(planId,sourceDC,targetDC){
 const r=await api('/dr/vms/options?plan_id='+Number(planId)
    +'&source_dc_id='+Number(sourceDC)+'&target_dc_id='+Number(targetDC));
 state.drVMOptions=r;
 const current=(state.drProtectedVMs||[]).find(x=>x.id===state.drVMWizard?.existingId);
 const vmid=current?.source_vmid||Number(state.drVMWizard?.preselectVMID)||r.vms[0]?.vmid;
 if(!r.vms.length)throw Error('Aucune VM QEMU n’a été découverte sur le cluster principal.');
 const src= r.vms.find(x=>x.vmid===vmid)||r.vms[0];
 const candidate=current?.target_vmid||src.vmid;
 const occupied=new Set(r.target_vmids_in_use);
 let targetDefault=candidate;
 if(!current&&occupied.has(candidate)){
  targetDefault=Math.max(100,candidate);
  while(occupied.has(targetDefault)&&targetDefault<999999999)targetDefault++;
  if(occupied.has(targetDefault))throw Error('Aucun VMID libre disponible sur la destination.');
 }
 const nodes=r.target_nodes.filter(x=>x.status==='online');
 const pools=r.target_storages.filter(x=>x.images);
 if(!nodes.length||!pools.length)throw Error('Aucun nœud en ligne ou stockage cible compatible VM détecté.');
 const labCompatible=pools.some(x=>x.type==='lvm'&&x.snapshot_as_volume_chain);
 const defaultEngine=labCompatible&&src.status==='running'?'qmp_live_incremental':'planning_only';
 const engines=[['planning_only','Préparation / simulation uniquement'],
   ['qmp_live_incremental','QEMU à chaud : DIR ou LVM de recette compatible'],
   ['offline_copy','Copie à froid (non disponible)'],
   ['pbs_backup_restore','PBS : sauvegarde réelle manuelle (restauration à intégrer)']];
 const body='<form id="drVMConfigForm" data-id="'+(current?.id||'')+'">'
  +'<div class="notice"><b>Étape 2 — Choisir la VM à protéger.</b> Pour un stockage LVM de recette compatible, '
  +'l’activation crée REP_ puis lance la première copie FULL à chaud sans autre commande. '
  +'La réplique reste arrêtée et sans réseau ; aucune bascule de production n’est autorisée.</div>'
  +'<p class="hint"><b>Source :</b> '+e(r.source_name)+' → <b>Destination :</b> '+e(r.target_name)
  +' · '+e(r.source_vm_total??r.vms.length)+' VM QEMU visibles'
  +' · '+e(r.source_lxc_excluded??0)+' conteneur(s) LXC exclus. Si des VM manquent, contrôler les ACL du token Proxmox sur le cluster.</p>'
  +'<div class="form-grid">'
  +'<label class="field"><span>Rechercher une VM (nom ou VMID)</span><input id="praVMSearch" placeholder="Rechercher parmi '+e(r.vms.length)+' VM..." autocomplete="off"></label>'
  +selectField('VM source (ordre alphabétique)','source_vmid',
    [...r.vms].sort((a,b)=>a.name.localeCompare(b.name,'fr',{sensitivity:'base',numeric:true})).map(v=>[v.vmid,v.name+' — #'+v.vmid+' · '+v.node+' ['+v.status+']']),src.vmid)
  +field('VMID cible réservé pour REP_','target_vmid',targetDefault,'number')
  +'<div class="tool-row"><button class="btn tiny" type="button" data-action="dr-vm-check-vmid">Vérifier VMID sur Proxmox</button><span id="praVMIDStatus" class="hint">Vérification en temps réel disponible</span></div>'
  +selectField('Nœud Proxmox cible','target_node',
    nodes.map(x=>[x.name,x.name]),current?.target_node||nodes[0].name)
  +selectField('Stockage cible','target_storage',
    pools.map(x=>[x.storage,x.storage+' · '+x.type+(x.shared?' (partagé)':'')+
      (x.snapshot_as_volume_chain?' · chaînes QCOW2 (preview; adaptateur PRA indisponible)':'')+
      (x.qmp_receiver_available?' · cible pilote QMP DIR':'')]),
    current?.target_storage||(labCompatible?pools.find(x=>x.type==='lvm'&&x.snapshot_as_volume_chain)?.storage:pools[0].storage))
  +selectField('Moteur de réplication','engine',engines,current?.engine||defaultEngine)
  +(current?'':'<label class="field"><span>Amorçage automatique (recette LVM uniquement)</span><select name="activate_lab">'+(labCompatible&&src.status==='running'
       ?'<option value="yes">Créer REP_ puis lancer une copie FULL à chaud</option><option value="no">Ne pas lancer (planification uniquement)</option>'
       :'<option value="no">Ne pas lancer (planification uniquement)</option><option value="yes">Créer REP_ puis lancer une copie FULL à chaud (stockage LVM requis)</option>')+'</select></label>'
    +'<label class="field"><span>Confirmation pour écriture sur stockage de RECETTE</span><input name="lab_confirmation" autocomplete="off" placeholder="DISPOSABLE-LAB-ONLY"></label>')
  +'<label class="field"><span>Référence / ticket PRA (facultatif)</span><input name="reference" maxlength="80" value="'+e(current?.reference||'')+'" placeholder="Ex. REDMINE-40230"></label>'
  +'<label class="field"><span>Commentaire de protection (facultatif)</span><input name="protection_note" maxlength="240" value="'+e(current?.protection_note||'')+'" placeholder="Description de cette protection"></label>'
  +selectField('Fréquence automatique après amorçage','interval_minutes',
    [[15,'15 minutes'],[30,'30 minutes'],[60,'1 heure'],[240,'4 heures'],
     [720,'12 heures'],[1440,'24 heures']],
    current?.interval_minutes||60)+'</div>'
  +'<p class="hint">Noms cibles : REP_&lt;nom de VM&gt;. Un VMID libre est proposé automatiquement. '
  +'Confirmer la recette LAB-ONLY pour démarrer les opérations réelles sur la destination. '
  +'Les incrémentaux LVM allouent des volumes épais : la planification s’arrête à la limite '
  +'de sécurité sans rétention validée.</p>'
  +'<div class="tool-row"><button class="btn primary" type="submit">Enregistrer la protection</button>'
  +'<button type="button" class="btn" data-action="modal-close">Annuler</button></div></form>';
 modal('VM protégée · '+e(r.source_name)+' → '+e(r.target_name),body,'',true);
}
function showProtectedVMTest(report){
 const rows=(report.checks||[]).map(x=>
  '<tr><td>'+e(x.name)+'</td><td>'+labelStatus(x.pass?'success':'failed')+'</td><td>'+e(x.detail)+'</td></tr>').join('');
 modal('Test PRA · VM #'+e(report.vmid)+' · '+e(report.scenario),
  '<div class="notice error"><b>Décision : '+e(report.decision)+'.</b> '+e(report.conclusion)
   +' Aucune copie de disque, aucun arrêt ni démarrage de VM.</div>'
  +'<div class="tool-row"><span class="chip">Préfixe cible : '+e(report.replica_name)+'</span>'
  +'<span class="chip">Source : '+e(report.source_status)+'</span></div>'
  +'<div class="table-wrap"><table><thead><tr><th>Contrôle</th><th>État</th><th>Constat</th></tr></thead>'
  +'<tbody>'+rows+'</tbody></table></div>'
  +'<p class="hint">'+e(report.next_step)+'</p>',
  '<button class="btn primary" data-action="modal-close">Fermer</button>',true);
}
async function openPRALvmLabPanel(vmId){
 const vm=(state.drProtectedVMs||[]).find(x=>x.id===Number(vmId));
 if(!vm)return toast('Protection VM introuvable',false);
 const history=(await api('/dr/vms/'+vmId+'/lvm-lab/runs')).items||[];
 let activations=[],scheduleEvents=[],bootTests=[],publications=[],stepHistory=[];
 try{publications=(await api('/dr/vms/'+vmId+'/lvm-lab/publications')).items||[]}
 catch(ex){ /* The history remains accessible for older controllers. */ }
 try{activations=(await api('/dr/vms/'+vmId+'/lvm-lab/activations')).items||[]}
 catch(ex){ /* The history remains accessible for older controllers. */ }
 try{stepHistory=(await api('/dr/vms/'+vmId+'/lvm-lab/steps')).items||[]}
 catch(ex){ /* The history remains accessible for older controllers. */ }
 try{scheduleEvents=(await api('/dr/vms/'+vmId+'/lvm-lab/schedule-events')).items||[]}
 catch(ex){ /* The history remains accessible for older controllers. */ }
 try{bootTests=(await api('/dr/vms/'+vmId+'/lvm-lab/boot-tests')).items||[]}
 catch(ex){ /* The history remains accessible for older controllers. */ }
 let pre=null,preError='';
 try{pre=await api('/dr/vms/'+vmId+'/lvm-lab/preflight')}
 catch(ex){preError=ex.message}
 const rows=history.map(x=>'<tr><td>'+e('#'+x.id)+'</td><td>'+labelStatus(x.status)
   +'</td><td>'+e(x.mode)+'</td><td>'+e(x.point_at?dt(x.point_at):'—')
   +'</td><td>'+e(x.details||'')+(x.test_vmid?' · clone VMID '+e(x.test_vmid):'')+'</td></tr>').join('');
 const running=history.some(x=>['queued','running','uncertain'].includes(x.status));
 const writable=Boolean(pre && pre.source_running && pre.target_stopped) && !running;
 const latestCopy=history.find(x=>['full','incremental'].includes(x.mode));
 const restoreReady=writable&&Boolean(pre?.checkpoint)
   &&latestCopy?.status==='completed'&&latestCopy?.checkpoint_token===pre.checkpoint;
 const restoredClone=history.find(x=>x.mode==='restore'&&x.status==='completed'
   &&x.checkpoint_token===pre?.checkpoint);
 const bootTestActive=bootTests.some(x=>['queued','running','uncertain'].includes(x.status));
 const canTestBoot=restoreReady&&Boolean(restoredClone)&&!bootTestActive;
 const targetLabel=pre?'Source VM #'+e(pre.source_vmid)+' ON → cible VM #'+e(pre.target_vmid)
   +' OFF · '+e(pre.destination_storage):'Connexion des nœuds non validée';
 const canPrepare=vm.engine==='qmp_live_incremental' && !running
   && !activations.some(x=>['queued','running','uncertain'].includes(x.status));
 const canActivate=vm.engine==='qmp_live_incremental'&&vm.state==='planned'
   &&activations.length===0&&!running;
 const canResumeSource=Boolean(activations[0]?.status==='uncertain'
   &&activations[0]?.phase==='blocked'&&!activations[0]?.replication_run_id
   &&String(activations[0]?.details||'').includes('source_probe')
   &&history.length===0);
 const canResumeOwned=Boolean(activations[0]?.status==='uncertain'
   &&activations[0]?.phase==='blocked'&&!activations[0]?.replication_run_id
   &&String(activations[0]?.details||'').includes('LAB target VM must exist, be stopped and have exclusive VMID')
   &&history.length===0);
 const canRetryProvision=Boolean(activations[0]?.status==='uncertain'
   &&activations[0]?.phase==='blocked'&&!activations[0]?.replication_run_id
   &&/LiveReplicationError\s*:\s*Échec commande agent PRA\s*:\s*lvm_lab_prepare_standby/.test(
      String(activations[0]?.details||''))&&history.length===0);
 const canRetryAuthorizationBug=Boolean(activations[0]?.status==='uncertain'
   &&activations[0]?.phase==='blocked'&&!activations[0]?.replication_run_id
   &&/LiveReplicationError\s*:\s*Action PRA non autorisée/.test(String(activations[0]?.details||''))
   &&history.length===0);
 const activation=activations[0]||null;
 const content='<div class="panel"><h3>Diagnostic de la protection #'+e(vm.id)+'</h3>'
   +'<p><b>Source : </b>'+e(vm.source_name)+' (#'+e(vm.source_vmid)+') sur '+e(vm.source_node)
   +' → <b>Secours :</b> '+e(vm.replica_name)+' (#'+e(vm.target_vmid)+') sur '+e(vm.target_node)
   +' · nom Proxmox : '+e(vm.replica_name.replaceAll('_','-'))+'</p>'
   +'<p><b>Phase :</b> '+e(activation?.phase||'non démarrée')+' · <b>État :</b> '
   +labelStatus(activation?.status||'not_configured')+'</p>'
   +(activation?.details?'<div class="notice '+(activation.status==='uncertain'?'error':'')+'">'+e(activation.details)+'</div>':'')
   +'<p class="hint">Référence : '+e(vm.reference||'—')+' · Commentaire : '+e(vm.protection_note||'—')+'</p></div>'
   +'<div class="notice error"><b>RECETTE UNIQUEMENT : vraies écritures sur LVM.</b> '
   +'La cible doit porter ORIGAMITASKS_LAB_ONLY, être arrêtée, sans carte réseau et avec onboot=0. '
   +'Chaque transfert alloue de nouveaux volumes logiques épais, sans suppression automatique. '
   +'Aucune bascule ni aucun démarrage automatique.</div>'
   +'<p><b>'+targetLabel+'</b></p>'
   +'<div class="tool-row"><button class="btn primary" data-action="dr-vm-lvm-lab-activate" data-id="'+vmId+'" '
   +(canActivate?'':'disabled')+'>Activer la protection (REP_ + première copie FULL)</button>'
   +(canRetryAuthorizationBug?'<button class="btn primary" data-action="dr-vm-lvm-lab-retry-allowlist" data-id="'+vmId+'">Relancer après correction du refus local</button>':'')
   +(canRetryProvision?'<button class="btn primary" data-action="dr-vm-lvm-lab-reconcile-provision" data-id="'+vmId+'">Vérifier le VMID cible et reprendre la préparation</button>':'')
   +(canResumeOwned?'<button class="btn primary" data-action="dr-vm-lvm-lab-resume-owned" data-id="'+vmId+'">Vérifier REP_ existante et reprendre la copie FULL</button>':'')
   +'<button class="btn" data-action="dr-vm-lvm-lab-diagnose-source" data-id="'+vmId+'">Diagnostiquer QMP source (lecture seule)</button>'
   +(canResumeSource?'<button class="btn primary" data-action="dr-vm-lvm-lab-resume-source" data-id="'+vmId+'">Vérifier la source et reprendre la copie FULL</button>':'')
   +'<button class="btn" data-action="dr-vm-lvm-lab-diagnose" data-id="'+vmId+'">Diagnostiquer Proxmox (lecture seule)</button>'
   +'<button class="btn" data-action="dr-vm-lvm-lab-prepare" data-id="'+vmId+'" '
   +(canPrepare?'':'disabled')+'>Préparer seulement la VM de secours</button></div>'
   +'<p class="hint">OrigamiTasks vérifie le VMID sur le cluster cible, prépare une VM REP_ arrêtée, sans réseau ni disque, et applique les marqueurs de protection. Aucune commande manuelle.</p>'
   +(!pre&&(canRetryAuthorizationBug||canRetryProvision)?'<p class="hint">Le prévol de copie requiert REP_ créée. '
     +'Ne pas lancer une copie FULL manuellement : vérifier d’abord l’état de la création de secours via la reprise contrôlée.</p>':'')
   +(pre?'<p class="hint">Disques : '+e((pre.disk_slots||[]).join(', '))
      +' · Espace libre : '+e(Math.round(pre.available_bytes/1073741824))+' Gio'
      +' · Dernier checkpoint : '+e(pre.checkpoint||'aucun')+'</p>'
      :'<div class="notice error"><b>Prévol bloqué :</b> '+e(preError)+'</div>')
   +'<label class="field"><span>Confirmer exactement DISPOSABLE-LAB-ONLY</span>'
   +'<input id="praLvmLabConfirmation" autocomplete="off" placeholder="DISPOSABLE-LAB-ONLY"></label>'
   +'<div class="tool-row">'
   +'<button class="btn primary" data-action="dr-vm-lvm-lab-run" data-id="'+vmId+'" data-mode="full" '
   +(writable?'':'disabled')+'>Copie complète à chaud</button>'
   +'<button class="btn" data-action="dr-vm-lvm-lab-run" data-id="'+vmId+'" data-mode="incremental" '
   +((writable&&pre?.checkpoint)?'':'disabled')+'>Synchronisation incrémentale</button>'
   +'<button class="btn" data-action="dr-vm-lvm-lab-open" data-id="'+vmId+'">Actualiser</button>'
   +'</div>'
   +'<h3>Préparer un clone indépendant pour un test de restauration</h3>'
   +'<p class="hint">Crée une nouvelle VM PRA_TEST_ arrêtée, avec ses propres LV aplatis, '
   +'sans interface réseau. Un test QEMU séparé peut le démarrer puis l’arrêter automatiquement ; cela ne vérifie pas le système invité.</p>'
   +'<label class="field"><span>VMID Proxmox libre pour le clone</span>'
   +'<input id="praLvmLabCloneVMID" type="number" min="100" step="1" placeholder="Ex. 4301"></label>'
   +'<div class="tool-row"><button class="btn" data-action="dr-vm-lvm-lab-restore" data-id="'+vmId+'" '
   +(restoreReady?'':'disabled')+'>Préparer un clone isolé (arrêté)</button>'
   +'<button class="btn" data-action="dr-vm-lvm-lab-test-boot" data-id="'+vmId+'" data-restore-run="'+e(restoredClone?.id||'')+'" '
   +(canTestBoot?'':'disabled')+'>Tester le démarrage du clone (isolé)</button></div>'
   +'<div class="table-wrap"><table><thead><tr><th>Test</th><th>Clone</th><th>État</th><th>Journal</th></tr></thead><tbody>'
   +(bootTests.map(x=>'<tr><td>#'+e(x.id)+'</td><td>VMID '+e(x.test_vmid)+'</td><td>'+labelStatus(x.status)+'</td><td>'+e(x.details)+'</td></tr>').join('')
     ||'<tr><td colspan="4">Aucun test de démarrage QEMU</td></tr>')+'</tbody></table></div>'
   +'<h3>Publication de la réplique REP_</h3>'
   +'<p class="hint">Les volumes vérifiés sont attachés à la configuration Proxmox de REP_ arrêtée. '
   +'Ce contrôle ne certifie pas le démarrage de la VM ni une bascule réelle.</p>'
   +'<div class="table-wrap"><table><thead><tr><th>Date</th><th>État</th><th>Journal</th></tr></thead><tbody>'
   +(publications.map(x=>'<tr><td>'+e(dt(x.updated_at))+'</td><td>'+labelStatus(x.status)
      +'</td><td>'+e(x.details)+'</td></tr>').join('')
      ||'<tr><td colspan="3">Aucune publication de disque LVM</td></tr>')+'</tbody></table></div>'
   +'<h3>Débogage : étapes de la protection</h3>'
   +'<div class="table-wrap"><table><thead><tr><th>Heure</th><th>Étape</th><th>Diagnostic</th></tr></thead><tbody>'
   +(stepHistory.map(x=>'<tr><td>'+e(dt(x.created_at))+'</td><td>'+e(x.action.replace('pra.lvm.lab.activation.',''))+'</td><td>'+e(x.detail)+'</td></tr>').join('')
       ||'<tr><td colspan="3">Aucune étape reçue</td></tr>')+'</tbody></table></div>'
   +'<h3>Journal d’activation de la protection</h3>'
   +'<div class="table-wrap"><table><thead><tr><th>Activation</th><th>État</th><th>Étape</th><th>Journal</th></tr></thead><tbody>'
   +(activations.map(x=>'<tr><td>#'+e(x.id)+'</td><td>'+labelStatus(x.status)+'</td><td>'+e(x.phase)+'</td><td>'+e(x.details)
     +(x.replication_run_id?' · Copie #'+e(x.replication_run_id)+': '+e(x.replication_details||x.replication_status||'en cours'):'')+'</td></tr>').join('')
     ||'<tr><td colspan="4">Aucune activation automatique</td></tr>')+'</tbody></table></div>'
   +'<h3>Journal de planification incrémentale</h3>'
   +'<p class="hint">Intervalle configuré : '+e(vm.interval_minutes)+' min après la dernière copie terminée. '
   +'Aucune reprise automatique si le bitmap, les volumes ou les checkpoints sont incertains. '
   +'Une vérification structurelle ne garantit pas un démarrage.</p>'
   +'<div class="table-wrap"><table><thead><tr><th>Date</th><th>État</th><th>Journal</th></tr></thead><tbody>'
   +(scheduleEvents.map(x=>'<tr><td>'+e(dt(x.created_at))+'</td><td>'+labelStatus(x.status)
      +'</td><td>'+e(x.detail)+'</td></tr>').join('')
     ||'<tr><td colspan="3">Aucune synchronisation planifiée exécutée</td></tr>')+'</tbody></table></div>'
   +'<h3>Historique des copies LVM de recette</h3>'
   +'<div class="table-wrap"><table><thead><tr><th>Run</th><th>État</th><th>Mode</th><th>Point</th><th>Diagnostic</th>'
   +'</tr></thead><tbody>'+(rows||'<tr><td colspan="5">Aucune copie lancée</td></tr>')+'</tbody></table></div>'
   +(running?'<div class="notice error"><b>Une copie est en cours ou incertaine.</b> '
     +'Aucune reprise automatique ni suppression des LV. Vérifier les QMP/NBD et le journal du nœud cible.</div>':'');
 modal('Réplication QMP → LVM de recette · VM #'+e(vm.source_vmid),
       content,'<button class="btn" data-action="modal-close">Fermer</button>',true);
}

async function openProtectedVMHistory(vmId){
 const item=(state.drProtectedVMs||[]).find(x=>x.id===vmId);
 const rows=(await api('/dr/vms/'+vmId+'/tests')).items;
 const syncs=(item?.engine==='qmp_live_incremental'?(await api('/dr/vms/'+vmId+'/replications')).items:[]);
 const syncHtml=syncs.map(t=>'<div class="dr-vm-test-history"><div class="tool-row">'
   +labelStatus(t.status)+' <b>Copie '+e(t.mode||'à préparer')+'</b><small class="hint">'+dt(t.created_at)
   +' · '+e(t.actor)+'</small></div><p>'+e(t.details||'')+'</p></div>').join('');
 const html=rows.map(t=>'<div class="dr-vm-test-history"><div class="tool-row">'
   +labelStatus(t.outcome)+' <b>'+e(t.scenario)+'</b><small class="hint">'+dt(t.created_at)
   +' · '+e(t.actor)+'</small></div><p>'+e(t.report.conclusion)+'</p>'
   +'<small class="hint">Lecture seule : '+e(t.report.mode)+'</small></div>').join('');
 modal('Historique des réplications et tests · '+e(item?.source_name||vmId),syncHtml+html||
   '<div class="notice">Aucun test réalisé. Tu peux lancer le prévol de réplication ou une bascule simulée.</div>',
   '<button class="btn primary" data-action="modal-close">Fermer</button>',true);
}

function renderDRSitesPanel(){
 const plans=state.drPlans||[],sites=state.drSites||[];
 const card=site=>{
  const clusters=(site.datacenters||[]).map(dc=>e(dc.name)).join(' · ')||'—';
  const ags=(site.agents||[]).map(agent=>e(agent.name)+' '+labelStatus(agent.status)).join(' · ')||'Aucun';
  const role=site.role==='primary'?'Principal':site.role==='standby'?'Secours':'Autre';
  const storage={auto:'À détecter',iscsi_lvm:'iSCSI + LVM',zfs:'ZFS',ceph:'Ceph',pbs:'PBS',other:'Autre'}[site.storage_profile]||site.storage_profile;
  return '<div class="job-card dr-site-card"><div class="job-card-head"><span class="chip tag">'+e(role)+'</span><span class="chip">'+e(storage)+'</span></div>'
    +'<h3>'+e(site.name)+'</h3><p>Clusters : '+clusters+'</p><p class="hint">Agents associés : '+ags+'</p>'
    +'<p class="hint">PRA : préparation uniquement, aucune réplication / bascule opérationnelle</p>'
    +'<div class="actions"><button class="btn tiny" data-action="dr-site-edit" data-id="'+site.id+'">Modifier</button>'
    +'<button class="btn tiny" data-action="dr-site-fleet" data-id="'+site.id+'">Déployer sur les nœuds</button>'
    +'<button class="btn tiny danger" data-action="dr-site-delete" data-id="'+site.id+'">Supprimer</button></div></div>';
 };
 const group=(plan,items)=>
  '<div class="dr-plan-section"><div class="panel-title"><div><h3>'+e(plan.name)+'</h3>'
  +'<p class="hint">'+e(plan.description||'Périmètre de protection indépendant')+' · '+items.length+' site(s)</p></div>'
  +'<div class="tool-row"><button class="btn tiny" data-action="dr-plan-protected" data-id="'+plan.id+'">VM protégées</button>'
  +'<button class="btn tiny" data-action="dr-site-new" data-plan-id="'+plan.id+'">＋ Site</button>'
  +'<button class="btn tiny" data-action="dr-plan-edit" data-id="'+plan.id+'">Modifier le groupe</button>'
  +'<button class="btn tiny danger" data-action="dr-plan-delete" data-id="'+plan.id+'">Supprimer</button></div></div>'
  +'<div class="workflow-grid dr-sites-grid">'+(items.map(card).join('')||'<p class="hint">Aucun site associé. Ajouter les clusters principal et secours de ce périmètre.</p>')+'</div></div>';
 const legacy=sites.filter(x=>x.plan_id==null);
 const history=(state.drSimulations||[]).slice(0,5).map(item=>
  '<div class="dr-sim-history"><span class="chip">#'+e(item.id)+' · '+e(item.scenario)+'</span>'
  +'<span>'+e(item.result.source.name)+' → '+e(item.result.target.name)+'</span>'
  +'<span class="status failed">'+e(item.outcome)+'</span>'
  +'<small class="muted">'+e(dt(item.created_at))+'</small></div>').join('');
 return '<section class="panel dr-sites-panel"><div class="panel-title"><div><h2>Groupes PRA indépendants</h2>'
  +'<p class="hint">Créer autant de périmètres PP, Production, Recette ou autres que nécessaire. '
  +'Chaque groupe contient ses propres sites et clusters. Aucune association ou simulation inter-groupes.</p></div>'
  +'<div class="tool-row"><button class="btn primary" data-action="dr-plan-new">＋ Nouveau groupe PRA</button>'
  +'<button class="btn" data-action="dr-plans-refresh">↻ Actualiser</button>'
  +'<button class="btn" data-action="page" data-page="protected-vms">VM protégées</button>'
  +'<button class="btn" data-action="dr-simulation-form">Simuler une panne (sans action)</button></div></div>'
  +(plans.map(p=>group(p,sites.filter(s=>s.plan_id===p.id))).join('')||'<div class="notice">Créer un premier groupe PRA pour séparer les environnements.</div>')
  +(legacy.length?'<div class="dr-plan-section"><h3>Sites existants à classer</h3><div class="notice">'
   +'Ces sites ont été créés avant les groupes PRA. Ouvrir « Modifier » pour leur attribuer un groupe '
   +'sans perdre leur configuration. Ne pas les mélanger avec de nouveaux groupes.</div>'
   +'<div class="workflow-grid dr-sites-grid">'+legacy.map(card).join('')+'</div></div>':'')
  +'<h3>Dernières simulations</h3>'+(history||'<p class="hint">Aucune simulation effectuée.</p>')+'</section>';
}
function openDRPlanForm(id=null){
 const plan=(state.drPlans||[]).find(x=>x.id===Number(id));
 const form='<form id="drPlanForm" data-id="'+(plan?.id||'')+'">'
 +field('Nom du groupe PRA','dr_plan_name',plan?.name||'','text')
 +field('Description (facultatif)','dr_plan_description',plan?.description||'','text')
 +'<p class="hint">Exemples génériques : Préproduction, Production, Recette. Un groupe est un périmètre isolé, pas un cluster physique.</p>'
 +'<div class="tool-row"><button class="btn primary" type="submit">Enregistrer le groupe</button>'
 +'<button class="btn" type="button" data-action="modal-close">Annuler</button></div></form>';
 modal(plan?'Modifier le groupe PRA':'Créer un groupe PRA indépendant',form,'',true);
}
function openDRSiteForm(siteId=null,presetPlanId=null){
 const site=(state.drSites||[]).find(x=>x.id===Number(siteId)),selectedDCs=new Set((site?.datacenters||[]).map(x=>x.id)),
 selectedAgents=new Set((site?.agents||[]).map(x=>x.id));
 const plans=state.drPlans||[];
 if(!plans.length){toast('Créer un groupe PRA avant de déclarer les sites',false);return}
 const checkboxes=(list,field,selected,valueLabel)=>
  list.map(item=>'<label class="dr-site-choice"><input type="checkbox" name="'+field+'" value="'+item.id+'" '
   +(selected.has(item.id)?'checked':'')+'><span>'+e(valueLabel(item))+'</span></label>').join('');
 const dcs=checkboxes(state.datacenters,'dr_dc',selectedDCs,x=>x.name+' · '+x.url);
 const allAgents=(state.agents||[]).filter(x=>x.platform==='linux'&&!x.revoked);
 const agentsHTML=checkboxes(allAgents,'dr_agent',selectedAgents,x=>x.name+' · '+(x.hostname||'non enrôlé'));
 const chosen=site?.plan_id||presetPlanId||plans[0].id;
 const form='<form id="drSiteForm" data-id="'+(site?.id||'')+'"><div class="form-grid">'
  +selectField('Groupe PRA (isolation PP / Production)','dr_plan',plans.map(p=>[p.id,p.name]),chosen)
  +field('Nom du site','dr_site_name',site?.name||'')
  +selectField('Rôle du site','dr_role',[['primary','Principal / actif'],['standby','Secours / standby'],['other','Autre']],site?.role||'other')
  +selectField('Profil de stockage (déclaratif)','dr_storage',[
   ['auto','À détecter'],['iscsi_lvm','iSCSI + LVM'],['zfs','ZFS'],['ceph','Ceph'],['pbs','PBS'],['other','Autre']
   ],site?.storage_profile||'auto')+'</div>'
  +'<label class="field"><span>Clusters Proxmox de ce site</span><div class="dr-site-choices">'
  +(dcs||'<span class="hint">Déclarer d’abord un cluster Proxmox.</span>')+'</div></label>'
  +'<label class="field"><span>Agents Linux du site (facultatif)</span><div class="dr-site-choices">'
  +(agentsHTML||'<span class="hint">Les agents pourront être installés ensuite depuis le cluster.</span>')+'</div></label>'
  +'<div class="notice">Chaque cluster ne peut appartenir qu’à un seul site, donc un seul groupe PRA. '
  +'Les simulations entre groupes différents sont interdites. Aucune copie de VM n’est créée.</div>'
  +'<div class="tool-row"><button class="btn primary" type="submit">Enregistrer le site</button>'
  +'<button class="btn" type="button" data-action="modal-close">Annuler</button></div></form>';
 modal(site?'Modifier le site · '+e(site.name):'Déclarer un site PRA',form,'',true);
}
function openDRSimulationForm(){
 const plans=(state.drPlans||[]).filter(p=>(state.drSites||[]).filter(s=>s.plan_id===p.id).length>=2);
 if(!plans.length){toast('Créer un groupe PRA avec au moins deux sites avant de simuler',false);return}
 const plan=plans[0];
 const sites=(state.drSites||[]).filter(s=>s.plan_id===plan.id);
 const source=sites.find(x=>x.role==='primary')?.id||sites[0].id;
 const target=sites.find(x=>x.role==='standby')?.id||sites[1].id;
 const body='<form id="drSimulationForm"><div class="notice">Simulation virtuelle uniquement : aucune action sur les VM ou hyperviseurs.</div>'
  +'<div class="form-grid">'
  +selectField('Groupe PRA à simuler','dr_sim_plan',plans.map(p=>[p.id,p.name]),plan.id)
  +selectField('Site source supposé en panne','dr_source',sites.map(x=>[x.id,x.name+' ('+x.role+')']),source)
  +selectField('Site de secours','dr_target',sites.map(x=>[x.id,x.name+' ('+x.role+')']),target)
  +selectField('Scénario','dr_scenario',[
   ['cluster_down','Cluster indisponible'],['network_partition','Partition réseau'],
   ['console_down','Console OrigamiTasks indisponible']
  ],'cluster_down')+'</div>'
  +'<p class="hint">Changer de groupe actualise les deux listes de sites. Aucune simulation inter-groupes n’est autorisée.</p>'
  +'<button class="btn primary" type="submit">Simuler sans action</button></form>';
 modal('Simulation PRA isolée',body);
}
function showDRSimulationResult(result){
 const lines=(result.checks||[]).map(c=>
  '<tr><td>'+e(c.name)+'</td><td>'+labelStatus(c.pass?'success':'failed')+'</td><td>'+e(c.detail)+'</td></tr>').join('');
 const vms=result.protected_vms||[];
 const rows=vms.map(vm=>'<tr><td>'+e(vm.source_vmid)+' · '+e(vm.source_name)+'</td>'
  +'<td>'+e(vm.replica_name)+' (#'+e(vm.target_vmid)+')</td>'
  +'<td>'+e(vm.target_node)+' · '+e(vm.target_storage)+'</td>'
  +'<td>'+labelStatus('BLOCKED')+'</td></tr>').join('');
 modal('Simulation #'+e(result.simulation_id)+' · '+e(result.decision),
  '<div class="notice error"><b>Aucune bascule effectuée.</b> '+e(result.conclusion)+'</div>'
  +'<p>'+e(result.source.name)+' → '+e(result.target.name)+'</p>'
  +'<div class="table-wrap"><table><thead><tr><th>Contrôle</th><th>Résultat</th><th>Explication</th></tr></thead><tbody>'+lines+'</tbody></table></div>'
  +'<h3>VM déclarées dans ce parcours PRA ('+vms.length+')</h3>'
  +'<div class="table-wrap"><table><thead><tr><th>VM source</th><th>Réplique prévue</th><th>Nœud / stockage</th><th>Reprise</th></tr></thead>'
  +'<tbody>'+(rows||'<tr><td colspan="4">Aucune VM sélectionnée : créer une fiche dans VM protégées PRA.</td></tr>')+'</tbody></table></div>'
  +'<p class="hint">'+e(result.next_step)+'</p>',
  '<button class="btn primary" data-action="modal-close">Fermer</button>',true);
}


async function openClusterAgentWizard(dcId, overrideSuffix=''){
 const dc=(state.datacenters||[]).find(x=>x.id===Number(dcId));
 if(!dc)return toast('Cluster Proxmox introuvable',false);
 let data;
 try{
  data=await api('/datacenters/'+dc.id+'/agent-nodes?domain_suffix='+encodeURIComponent(overrideSuffix));
 }catch(ex){
  if(!overrideSuffix){
   const userSuffix=prompt('Domaine SSH des nœuds du cluster '+dc.name+' (ex. infra.example.org). Le certificat Proxmox ou l’URL peut utiliser une IP.','');
   if(userSuffix){return openClusterAgentWizard(dcId,userSuffix.trim())}
  }
  return toast(ex.message,false);
 }
 state.proxmoxFleet=data;
 const site=(state.drSites||[]).find(x=>(x.datacenters||[]).some(y=>y.id===dc.id));
 const available=(data.nodes||[]).filter(n=>n.server_id&&!n.already_registered);
 const credentials=(state.credentials||[]).filter(c=>c.kind==='ssh');
 const nodeRows=(data.nodes||[]).map(node=>
   '<label class="fleet-node-row"><input type="checkbox" name="fleet_node" value="'+e(node.node)+'" checked>'
   +'<span class="fleet-node-main"><strong>'+e(node.node)+'</strong><small>'+e(node.fqdn)+':22</small></span>'
   +'<span class="chip '+(node.already_registered?'tag':'')+'">'+(node.already_registered?'Agent #'+e(node.agent_id):'Sans agent')+'</span>'
   +'<button class="btn tiny" data-action="fleet-ssh-key" data-host="'+e(node.fqdn)+'" data-port="22" type="button">Voir l’empreinte SSH (facultatif)</button></label>'
 ).join('');
 const deployRows=available.map(node=>
  '<label class="fleet-deploy-row"><input type="checkbox" name="fleet_server" value="'+node.server_id+'" checked>'
  +'<span>'+e(node.node)+' <small>'+e(node.fqdn)+'</small></span></label>'
 ).join('');
 const group='<form id="proxmoxFleetGroupForm" data-id="'+dc.id+'">'
  +'<div class="form-grid"><label class="field"><span>Domaine DNS SSH des nœuds</span><input name="domain_suffix" value="'+e(data.domain_suffix)+'" required></label>'
  +'<div class="field"><span>Rôle et association PRA</span><div class="notice">'+e(site?site.name+' · '+site.role:'Cluster non rattaché à un site PRA')+'</div></div></div>'
  +'<p class="hint">Nœuds identifiés depuis l’API du cluster. Vérifier les FQDN et, si besoin, changer le suffixe puis cliquer « Actualiser » avant de créer le groupe.</p>'
  +'<div class="tool-row"><button class="btn tiny" type="button" data-action="fleet-preview-refresh" data-id="'+dc.id+'">↻ Actualiser le domaine et les nœuds</button>'
  +'<span class="chip">'+data.nodes.length+' nœud(s) détecté(s)</span></div>'
  +'<div class="fleet-node-list">'+nodeRows+'</div>'
  +'<div class="tool-row"><button class="btn primary" type="submit">'+(data.group_id?'Synchroniser le groupe SSH':'Créer le groupe SSH lié au cluster')+'</button>'
  +'<small class="hint">L’inventaire SSH sera mis à jour, aucune installation à cette étape.</small></div></form>';
 const agentURL=state.agentSettings?.controller_url||'';
 const urlConfigured=!!state.agentSettings?.configured;
 const deploy='<form id="proxmoxFleetDeployForm" data-id="'+dc.id+'">'
  +'<h3>Déployer l’agent unique Linux sur le groupe</h3>'
  +'<p class="hint">Connexion SSH simplifiée : le premier contact enregistre automatiquement la clé publique du nœud (TOFU). '
  +'Aucune empreinte à confirmer pour le déploiement. Les changements ultérieurs sont refusés. '
  +'La première connexion TOFU ne protège pas contre une interception initiale : contrôle facultatif de l’empreinte recommandé en environnement sensible.</p>'
  +(urlConfigured
     ?'<div class="notice">URL HTTPS de la console pour les agents : <strong>'+e(agentURL)+'</strong></div>'
     :'<div class="notice error"><b>URL HTTPS des agents manquante.</b> '
       +'Configurer le FQDN public de la console (derrière Nginx) avant le déploiement. '
       +'<button type="button" class="btn tiny" data-action="agent-settings-open">Configurer l’URL des agents</button></div>')
  +(data.group_id?'<p class="hint">Groupe géré #'+e(data.group_id)+' · '+available.length+' nœud(s) sans agent déjà enregistré.</p>':
    '<p class="notice">Créer ou synchroniser le groupe SSH avant de lancer le déploiement.</p>')
  +'<div class="fleet-node-list">'+(deployRows||'<p class="hint">Aucun nœud éligible : les agents sont déjà enregistrés ou le groupe est vide.</p>')+'</div>'
  +'<div class="form-grid">'
  +selectField('Identité SSH avec clé privée (coffre)','credential_id',
     [['','— Choisir une identité SSH —'],...credentials.map(c=>[c.id,c.name])],'')
  +'<label class="field"><span>Plateforme installée</span><input value="Linux · agent unique Standard / Proxmox / PRA passif" readonly></label></div>'
  +'<div class="fleet-modules"><strong>Modules à activer automatiquement après enrôlement</strong>'
  +'<label class="test-wizard-option"><input type="checkbox" name="enable_proxmox" '+(site?'checked':'disabled')+'>'
  +'<span><strong>Proxmox</strong><small>Associe les agents aux clusters du site. Les tokens PVE restent dans la console.</small></span></label>'
  +'<label class="test-wizard-option"><input type="checkbox" name="enable_pra" '+(site&&['primary','standby'].includes(site.role)?'':'disabled')+'>'
  +'<span><strong>PRA préparatoire (facultatif)</strong><small>Heartbeat local uniquement : ne réplique aucune VM, ne réalise aucun PRA opérationnel.</small></span></label>'
  +(!site?'<p class="hint">Rattacher d’abord le cluster à un site PRA pour activer ses modules automatiquement.</p>':'')
  +'</div>'
  +'<label class="test-wizard-option"><input type="checkbox" name="approved" required><span>'
  +'<strong>Confirmer l’installation de l’agent sur tous les hôtes cochés</strong>'
  +'<small>Première clé d’hôte SSH mémorisée automatiquement (TOFU) ; une clé d’hôte déjà connue mais modifiée bloque le nœud. '
  +'La clé privée du coffre sert à authentifier le déploiement. Le module PRA est passif : aucune VM démarrée ou répliquée.</small></span></label>'
  +'<div class="tool-row"><button class="btn primary" type="submit" '+(!data.group_id||!available.length||!urlConfigured?'disabled':'')+'>Déployer les agents sélectionnés</button>'
  +'<button class="btn" data-action="modal-close" type="button">Fermer</button></div></form>';
 modal('Nœuds & agents · '+e(dc.name),
    '<div class="notice">Assistant cluster Proxmox → groupe SSH → déploiement via le coffre. '
    +'Les agents installés sont associés automatiquement au site PRA si le cluster en possède un.</div>'
    +group+'<hr class="fleet-divider">'+deploy,'',true);
}
function openPRAClusterSelector(siteId){
 const site=(state.drSites||[]).find(x=>x.id===Number(siteId));
 if(!site)return toast('Site PRA introuvable',false);
 if((site.datacenters||[]).length===1)return openClusterAgentWizard(site.datacenters[0].id);
 if(!(site.datacenters||[]).length)return toast('Associer d’abord un cluster au site',false);
 modal('Déploiement des agents · '+e(site.name),
  '<p>Sélectionner le cluster Proxmox concerné :</p><div class="fleet-site-pick">'
   +(site.datacenters||[]).map(dc=>
     '<button class="btn" data-action="fleet-open" data-id="'+dc.id+'">'+e(dc.name)+'</button>').join('')
   +'</div>','',true);
}
async function inspectClusterSSHKey(button){
 const hostname=button.dataset.host,port=Number(button.dataset.port||22);
 try{
  const info=await api('/ssh/host-key/inspect','POST',{host:hostname,port});
  if(info.trusted){toast('Empreinte SSH déjà approuvée : '+hostname);return}
  if(info.status==='changed')throw Error('Empreinte SSH modifiée : vérifier hors bande avant toute installation.');
  const typed=prompt('Empreinte publique présentée par '+hostname+':\n'+info.fingerprint+
    '\n\nComparer cette empreinte à celle obtenue directement sur la console physique/SSH déjà fiable du serveur. '
    +'Saisir ci-dessous l’empreinte vérifiée (SHA256:...) pour l’approuver. Annuler pour ne pas faire confiance.','');
  if(!typed)return;
  if(typed.trim()!==info.fingerprint)throw Error('Empreinte différente : confiance refusée.');
  await api('/ssh/host-key/approve','POST',
    {host:hostname,port,fingerprint:typed.trim(),verified_out_of_band:true});
  toast('Empreinte SSH approuvée pour '+hostname);
 }catch(ex){toast(ex.message,false)}
}

function proxmoxCAChoices(selected=''){
 const choices=[['','Autorités du système (CA publique)'],
    ...(state.proxmoxCAs||[]).map(item=>[item.id,item.subject+' · '+item.id.slice(0,12)])];
 if(selected&&!choices.some(x=>x[0]===selected))choices.push([selected,'CA précédemment importée : '+selected.slice(0,12)]);
 return choices;
}
function openDatacenterForm(id=null){
 const d=(state.datacenters||[]).find(x=>x.id===Number(id)),existing=!!d;
 const credentialChoices=[['','— Choisir une connexion Proxmox existante —'],
     ...state.credentials.filter(x=>x.kind==='proxmox'||x.kind==='api').map(x=>[x.id,x.name+' · '+(x.public?.url||x.public?.base_url||'')])];
 const content='<form id="datacenterForm" data-id="'+(d?.id||'')+'"><div class="form-grid">'
   +field('Nom du serveur / cluster','dc_name',d?.name||'')
   +selectField('Type de cible','dc_kind',[['cluster','Cluster Proxmox VE'],['node','Serveur Proxmox autonome']],d?.kind||'cluster')
   +field('URL HTTPS Proxmox (ex. https://pve.infra.example.org:8006)','dc_url',d?.url||'')
   +selectField('Authentification','dc_auth_mode',[['new','Créer un nouveau token dans le coffre'],['existing','Choisir un token déjà enregistré']],existing?'existing':'new')
   +'</div><div class="form-grid" data-proxmox-auth="new">'
   +field('ID du token (user@pve!nom)','dc_token_username','','text')
   +'<label class="field"><span>Secret du token API</span><input name="dc_token_secret" type="password" autocomplete="new-password" placeholder="Secret créé dans Proxmox"></label></div>'
   +'<div data-proxmox-auth="existing">'+selectField('Connexion du coffre','dc_credential',credentialChoices,d?.credential_id||'')+'</div>'
   +'<div class="field"><span>Autorité de certification TLS du serveur</span>'
   +'<select name="dc_ca_cert">'+option(proxmoxCAChoices(d?.ca_cert||''),d?.ca_cert||'')+'</select>'
   +'<small class="hint">Choisir une CA déjà importée ou fournir un certificat CA PEM/CRT/DER ci-dessous. Les certificats sont conservés dans les données persistantes.</small></div>'
   +'<label class="field"><span>Importer une nouvelle CA publique (facultatif)</span><input type="file" name="dc_ca_file" accept=".crt,.cer,.pem,.der"></label>'
   +'<div class="proxmox-tls-option"><label class="field checkbox"><input type="checkbox" name="dc_skip_tls" '+(d?.skip_tls_verification?'checked':'')+'> Ignorer la vérification du certificat TLS (certificat autosigné)</label>'
   +'<p class="hint">Exception pour ce serveur / cluster uniquement. La connexion reste chiffrée en HTTPS, mais l’identité du serveur n’est plus vérifiée : risque d’interception. Privilégier l’import de la CA.</p></div>'
   +'<p class="hint">Une même entrée peut représenter un cluster (découverte de tous ses nœuds) ou un serveur isolé. '
   +'La CA sélectionnée est utilisée immédiatement, sans redémarrage.</p>'
   +'<div class="tool-row"><button class="btn primary" type="submit">'+(existing?'Enregistrer les modifications':'Ajouter et enregistrer le token')+'</button>'
   +'<button class="btn" type="button" data-action="modal-close">Annuler</button></div></form>';
 modal(existing?'Modifier Proxmox · '+d.name:'Ajouter un serveur ou cluster Proxmox',content);
 updateProxmoxAuthFields();
}
function updateProxmoxAuthFields(){
 const form=document.getElementById('datacenterForm');
 if(!form)return;
 const mode=form.elements.namedItem('dc_auth_mode')?.value||'new';
 form.querySelectorAll('[data-proxmox-auth]').forEach(el=>el.hidden=el.dataset.proxmoxAuth!==mode);
}
async function registerProxmoxCA(file){
 if(!file||file.size===0)return null;
 if(file.size>131072)throw Error('CA trop volumineuse : 128 Kio maximum');
 const text=await file.text();
 let payload;
 if(text.includes('-----BEGIN CERTIFICATE-----')){
  payload={pem_text:text};
 }else{
  const bytes=new Uint8Array(await file.arrayBuffer());
  let binary='';
  for(let i=0;i<bytes.length;i+=8192)
   binary+=String.fromCharCode.apply(null,bytes.subarray(i,i+8192));
  payload={der_base64:btoa(binary)};
 }
 const result=await api('/proxmox/cas','POST',payload);
 state.proxmoxCAs=(await api('/proxmox/cas')).items;
 return result;
}
function updates(){
 return title('Versions & mises à jour',
   'Comparaison de la version installée avec la branche Git autorisée. Aucun déploiement automatique.')
  +'<div class="panel"><div class="panel-title"><h2>État des versions</h2><span class="hint">Source : service de maintenance hôte</span></div>'
  +'<div id="updateVersionSummary"><div class="hint">Vérification des versions…</div></div>'
  +'<div class="tool-row"><button class="btn" data-action="update-request" data-value="check">⌕ Vérifier maintenant</button>'
  +'<button class="btn" data-action="update-request" data-value="preflight">◫ Tester la mise à jour (simulation)</button>'
  +'<button class="btn primary" data-action="update-request" data-value="apply">⇣ Installer la version Git disponible</button>'
  +'<button class="btn" data-action="refresh-updates">↻ Rafraîchir</button></div>'
  +'<div class="notice">Les vérifications sont effectuées par <code>ops/update-worker.py</code> sur l’hôte, avec <code>GIT_ALLOWED_ORIGIN</code> et <code>GIT_BRANCH</code>. La date affichée est la date du dernier commit de la branche suivie, pas nécessairement une date de release. Le bouton « Tester la mise à jour » vérifie Git, Docker Compose et la sauvegarde sans modifier la version. Les releases GitHub sont créées automatiquement par la CI lors de l’envoi d’un tag `vX.Y.Z` ou `vX.Y.Z-rcN`. Un correctif en recette sur une autre branche ne doit pas être annoncé comme « à jour sur main ». Installation toujours sur validation manuelle.</div></div>'
  +'<div class="panel"><div class="panel-title"><h2>Historique des opérations Git</h2></div><div id="updateStatus">Chargement…</div></div>';
}
function renderUpdateVersion(data){
 const w=data.worker||{},installed=data.version||'inconnue';
 const serviceOnline=data.worker_online===true;
 const checkedTime=Date.parse(w.checked_at||'');
 const fresh=Number.isFinite(checkedTime)&&Date.now()-checkedTime<26*3600*1000
             &&Date.now()-checkedTime>=-5*60*1000;
 const matchingBranch=w.branch===data.git_branch;
 const matchingInstall=!w.current_version||w.current_version===installed;
 const known=typeof w.available==='boolean'&&!!w.latest&&!!w.current&&fresh
             &&matchingBranch&&matchingInstall;
 const branch=w.branch||data.git_branch||'non définie';
 let status,css;
 if(!known){status='Vérification indisponible';css='warning'}
 else if(w.available){status='Nouvelle version disponible';css='warning'}
 else{status='À jour sur la branche suivie';css='success'}
 const checked=w.checked_at?dt(w.checked_at):'jamais vérifié';
 return '<div class="version-comparison"><div class="version-entry"><span>Version installée</span>'
  +'<strong>'+e(installed)+'</strong><small>Depuis le conteneur en cours</small></div>'
  +'<div class="version-entry"><span>Version distante</span>'
  +'<strong>'+e(w.latest_version||'non vérifiée')+'</strong>'
  +'<small>'+e(w.latest_date&&Number.isFinite(Date.parse(w.latest_date))
     ?new Date(w.latest_date).toLocaleString('fr-FR',{dateStyle:'medium',timeStyle:'short'}):'Date inconnue')+'</small></div></div>'
  +'<div class="tool-row"><span class="status '+e(css)+'">'+e(status)+'</span>'
  +(known&&w.available?'<span class="hint">'+e(w.commits_behind||'?')+' commit(s) disponibles</span>':'')+'</div>'
  +'<p class="hint">Branche suivie : <code>'+e(branch)+'</code> · Dernière vérification : '+e(checked)+'</p>'
  +'<p class="hint">'+e(w.message||'Le service de maintenance hôte n’a pas encore effectué de vérification')+'</p>'
 +'<p class="hint">Service de maintenance : <strong>'+e(serviceOnline?'En ligne (sur l’hôte)':'Hors ligne ou non configuré')+'</strong></p>'
 +(w.preflight_ok===true?'<div class="notice">Simulation réussie'+(w.preflight_at?' le '+e(dt(w.preflight_at)):'')+' ; aucune installation effectuée. Cette vérification est indépendante des futurs tests fonctionnels.</div>':'')
  +(!matchingBranch&&w.branch?'<div class="notice error">Le service surveille une branche différente de GIT_BRANCH. Vérification non fiable.</div>':'')
  +(!matchingInstall?'<div class="notice error">Le commit local ne correspond pas à la version exécutée dans le conteneur. Reconstruire et vérifier le déploiement.</div>':'')
  +(!fresh&&w.checked_at?'<div class="notice error">Dernière vérification trop ancienne : relancer un contrôle Git.</div>':'')
  +(w.error?'<div class="notice error">'+e(w.error)+'</div>':'');
}
function audit(){return title('Journal d’audit','Traçabilité des connexions, modifications, exécutions et opérations Git.')
  +'<div class="panel"><div class="tool-row"><label class="field"><span>Catégorie</span><select id="auditCategory">'
  +option([['all','Tous'],['pra','PRA / Proxmox'],['agents','Agents / installations'],['jobs','Jobs / exécutions'],['security','Connexions / sécurité'],['system','Mises à jour / système']],state.auditCategory||'all')
  +'</select></label><label class="field"><span>Rechercher</span><input id="auditSearch" value="'+e(state.auditSearch||'')+'" placeholder="Action, auteur, VMID, détails..."></label>'
  +'<button class="btn" data-action="audit-refresh">Rechercher / Actualiser</button></div><div id="auditTable">Chargement...</div></div>'}
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
 ansible:[['ssh_server_id','Serveur SSH du runner (inventaire)','ssh-target'],['host','Runner Ansible SSH (ancienne configuration)','text'],['port','Port SSH','number'],['playbook','Playbook dans /opt/origamitasks/ansible/playbooks','text'],['inventory','Inventaire dans /opt/origamitasks/ansible/inventories','text'],['limit','Limite de groupes / hôtes','text'],['extra_vars','Variables JSON','textarea'],['check','Mode --check','checkbox'],['credential_id','Identifiant SSH du runner','credential']],
 proxmox:[['datacenter_id','Datacenter déclaré','datacenter'],['action','Opération','select:inventory|start|shutdown|reboot'],['node','Nœud cible (actions uniquement)','text'],['vmid','VMID (actions uniquement)','number'],['approved','Autoriser explicitement cette action','checkbox']],

 shell:[['script','Script bash','textarea'],['timeout','Timeout (s)','number'],['credential_id','Identifiant','credential']],
 powershell:[['script','Script PowerShell','textarea'],['timeout','Timeout (s)','number'],['credential_id','Identifiant','credential']],
 ssh:[['ssh_server_id','Serveur cible (inventaire SSH)','ssh-target'],['host','Hôte manuel (ancienne configuration)','text'],['port','Port SSH','number'],['platform','Système distant','select:linux|windows'],['script','Script distant','textarea'],['timeout','Timeout (s)','number'],['credential_id','Identifiant SSH','credential']],
 sftp:[['ssh_server_id','Serveur cible (inventaire SSH)','ssh-target'],['host','Hôte manuel (ancienne configuration)','text'],['port','Port SSH','number'],['remote_path','Fichier distant','text'],['local_path','Fichier local (transfers/)','text'],['direction','Sens','select:get|put'],['credential_id','Identifiant','credential']],
 ftp:[['host','Hôte FTP','text'],['port','Port FTP','number'],['remote_path','Fichier distant','text'],['local_path','Fichier local (transfers/)','text'],['direction','Sens','select:get|put'],['allow_plaintext','Autoriser FTP non chiffré (déconseillé)','checkbox'],['credential_id','Identifiant','credential']],
 http:[['url','URL complète (manuel)','text'],['path','Chemin relatif (coffre)','text'],['method','Méthode','select:GET|POST|PUT|PATCH|DELETE'],['body','Corps','textarea'],['timeout','Timeout (s)','number'],['credential_id','Token Bearer','credential']],
 file:[['action','Opération','select:list|read|write|copy|move|mkdir|delete'],['path','Chemin cible sous transfers/','text'],['source','Source copy/move sous transfers/','text'],['content','Contenu (write)','textarea'],['approved','Confirmer la suppression (delete)','checkbox']],
 email:[['host','Serveur SMTP (manuel)','text'],['port','Port SMTP (manuel)','number'],['ssl','SSL direct (compatibilité ancienne tâche)','checkbox'],['tls_mode','Sécurité SMTP','select:starttls|ssl|none'],['from','Expéditeur (facultatif si défini dans le coffre)','text'],['to','Destinataires','text'],['subject','Objet','text'],['body','Corps texte','textarea'],['html_body','Corps HTML (optionnel)','textarea'],['attachments','Pièces jointes (transfers/ séparées par virgules)','text'],['credential_id','Connexion SMTP configurée dans le coffre','credential']],
 delay:[['seconds','Secondes (maximum 300)','number']],
 set_variable:[['name','Nom de la variable','text'],['value','Valeur','textarea']]
};
function freshJob(){return {name:'',description:'',folder:'Général',tags_json:[],acl_json:{view:[],run:[],edit:[]},steps_json:[],schedule_json:{cron:'',timezone:'Europe/Paris'},notification_json:{},enabled:1}}
function addStep(){return {id:'step_'+Math.random().toString(36).slice(2,11),name:'Nouvelle étape',type:'delay',config:{seconds:1},retries:0,retry_delay:2,on_success:'',on_failure:'',condition:{enabled:false,source:'last_status',operator:'equals',value:'success'}}}
function openEditor(id=null,clone=false){const found=id?state.jobs.find(j=>j.id===Number(id)):null;if(found&&!clone&&!found.permissions.edit){toast('Modification non autorisée',false);return}if(!roleAdmin()&&!found){toast('Création réservée aux administrateurs',false);return}state.editor=found?JSON.parse(JSON.stringify(found)):freshJob();if(clone){delete state.editor.id;state.editor.name+=' (copie)'}if(!state.editor.steps_json.length)state.editor.steps_json.push(addStep());state.selectedStep=0;state.graphZoom=1;state.graphViewport=null;renderEditor()}
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
 'ssh-inventory':'<rect x="2" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="8" height="7" rx="1"/><path d="M5 15h14v6H5zM9 6h5m-4 0v9M9 18h6"/>',
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
 const modeOptions=[['manual','Manuel / évènement (aucune répétition)'],
    ['once','Une seule exécution programmée'],
    ['interval','Assistant de répétition'],
    ['cron','Expression Cron avancée']];
 const units=[['minutes','minute(s)'],['hours','heure(s)'],['days','jour(s)'],['weeks','semaine(s)']];
 const presets=[['','Personnalisé'],['1:minutes','Toutes les minutes'],
    ['5:minutes','Toutes les 5 minutes'],['10:minutes','Toutes les 10 minutes'],
    ['15:minutes','Toutes les 15 minutes'],['30:minutes','Toutes les 30 minutes'],
    ['1:hours','Toutes les heures'],['6:hours','Toutes les 6 heures'],
    ['1:days','Chaque jour'],['1:weeks','Chaque semaine']];
 const currentPreset=(schedule.every||5)+':'+(schedule.unit||'minutes');
 return '<div class="field"><span>Planification</span><select name="job_schedule_mode">'+option(modeOptions,mode)+'</select></div>'
  +'<div class="field schedule-detail" data-schedule-mode="once"><span>Date et heure de la seule exécution</span><input type="datetime-local" name="job_at" value="'+e(schedule.at||'')+'"></div>'
  +'<div class="schedule-detail field full" data-schedule-mode="interval"><span>Assistant de répétition</span><select name="job_interval_preset">'
  +option(presets,presets.some(x=>x[0]===currentPreset)?currentPreset:'')+'</select>'
  +'<div class="inline-picker"><input type="number" name="job_every" min="1" max="100000" value="'+e(schedule.every||5)+'">'
  +'<select name="job_unit">'+option(units,schedule.unit||'minutes')+'</select></div>'
  +'<small class="hint">Exécution récurrente suivant l’intervalle choisi.</small></div>'
  +'<div class="field schedule-detail" data-schedule-mode="cron"><span>Expression Cron (5 champs)</span><input name="job_cron" placeholder="*/5 * * * *" value="'+e(schedule.cron||'')+'">'
  +'<small class="hint">Minute · Heure · Jour du mois · Mois · Jour de semaine</small></div>'
  +'<div class="field schedule-timezone"><span>Fuseau horaire</span><input name="job_timezone" value="'+e(schedule.timezone||'Europe/Paris')+'"></div>'
  +'<div class="schedule-info hint">En mode manuel, le workflow est lancé à la demande ou par webhook, sans calendrier.</div>';
}
function showScheduleMode(){
 const root=document.querySelector('.modal');
 if(!root)return;
 const mode=root.querySelector('[name="job_schedule_mode"]')?.value||'manual';
 $$('[data-schedule-mode]',root).forEach(el=>el.hidden=el.dataset.scheduleMode!==mode);
 $$('.schedule-timezone',root).forEach(el=>el.hidden=mode==='manual');
 $$('.schedule-info',root).forEach(el=>el.hidden=mode!=='manual');
}
function applySchedulePreset(){
 const form=document.querySelector('.modal');
 if(!form)return;
 const preset=form.querySelector('[name="job_interval_preset"]')?.value;
 if(!preset)return;
 const [every,unit]=preset.split(':');
 const fieldEvery=form.querySelector('[name="job_every"]'),fieldUnit=form.querySelector('[name="job_unit"]');
 if(fieldEvery)fieldEvery.value=every;
 if(fieldUnit)fieldUnit.value=unit;
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
  ['username','Login SSH (identité réutilisable)','text','deploy'],['password','Mot de passe SSH','password',''],
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
  ['token','Secret du jeton API','password',''],
  ['ca_cert','CA TLS importée dans OrigamiTasks','ca-select',''],
  ['skip_tls_verification','Ignorer la vérification TLS pour cette connexion (déconseillé)','checkbox',false]
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
  if(typ==='ca-select')control='<select name="'+key+'">'+option(proxmoxCAChoices(existing),existing)+'</select>';
  else if(typ==='checkbox')control='<input type="checkbox" name="'+key+'" '+((typeof existing==='boolean'?existing:fallback)?'checked':'')+'>';
  else if(typ==='select')control='<select name="'+key+'">'+option(choices,existing||fallback)+'</select>';
  else if(typ==='textarea')control='<textarea name="'+key+'" rows="4" placeholder="'+e(present.includes(key)?'Déjà renseigné · vide = conserver':'Clé privée ou secret')+'"></textarea>';
  else control='<input name="'+key+'" type="'+typ+'" '+(typ==='number'?'min="1" max="65535" ':'')
    +' value="'+e(typ==='password'?'':existing||'')+'" placeholder="'+e(typ==='password'&&present.includes(key)?'Déjà renseigné · vide = conserver':fallback||'')+'" '
    +(kind==='smtp'&&key==='host'?'required':'')+'>';
  if(typ==='number')control='<input type="number" name="'+key+'" min="1" max="65535" value="'+e(existing||fallback||'')+'" required>';
  const cls=typ==='textarea'?'field full':typ==='checkbox'?'field checkbox':'field';
  return '<label class="'+cls+'" '+(kind==='smtp'&&['username','password'].includes(key)?'data-smtp-auth-field':'')+'><span>'+e(label)+'</span>'+control+'</label>';
 }).join('')+'</div>'
 +(kind==='smtp'?'<div class="notice">Pour le SMTP, configure le serveur, le port, la sécurité et l’authentification. La tâche utilise automatiquement ces paramètres lorsque sa connexion est sélectionnée.</div>':'')
 +(kind==='proxmox'?'<div class="field"><span>Importer une CA Proxmox publique</span><input type="file" name="proxmox_ca_file" accept=".crt,.cer,.pem,.der"></div><div class="notice">Le coffre conserve l’URL, le token chiffré et la CA TLS sélectionnée. Ajouter les DC directement dans le menu Proxmox : aucun réglage du .env.</div>':'')
 +(kind==='ssh'?'<div class="field full"><span>Importer le fichier de clé privée SSH du compte</span><input id="sshPrivateKeyFile" type="file" accept=".key,.pem,.openssh,.priv,.txt"><small class="hint">Le fichier reste dans le navigateur jusqu’à l’enregistrement du coffre. Formats OpenSSH ou PEM : BEGIN OPENSSH PRIVATE KEY, RSA PRIVATE KEY ou EC PRIVATE KEY. Pas la clé publique ssh-ed25519 ; jamais /etc/ssh/ssh_host_*_key.</small></div><div class="notice">Identité SSH indépendante des serveurs : indique le login et un mot de passe ou une clé privée utilisateur. La clé publique correspondante doit figurer dans authorized_keys sur le serveur cible. Ne jamais publier une clé privée. '+(profile.host?'Un ancien hôte reste enregistré pour compatibilité.':'')+'</div>':'');
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
   +'<div class="credential-list">'+(state.credentials.filter(c=>!target||target==='generic'||c.kind===target||(['matrix','proxmox'].includes(target)&&c.kind==='api')).map(c=>{
      const compatible=!target||target==='generic'||c.kind===target||(['matrix','proxmox'].includes(target)&&c.kind==='api');
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
  const modes=[['manual','Saisir un FQDN pour ce test'],['inventory','Choisir dans l’inventaire SSH']];
  if(publicData.host)modes.push(['legacy','Utiliser l’ancien hôte enregistré']);
  return '<p>Cette identité SSH est réutilisable : choisis un serveur seulement pour ce diagnostic. Le FQDN saisi manuellement ne sera pas enregistré.</p>'
   +'<div class="field"><span>Mode de sélection de la cible SSH</span><select name="ssh_target_mode">'+option(modes,'manual')+'</select></div>'
   +'<div class="ssh-test-mode" data-ssh-test-mode="manual"><div class="form-grid">'
   +testOptionInput('manual_host','FQDN du serveur à tester','serveur.fr')
   +'<div class="field"><span>Port SSH</span><input name="manual_port" type="number" min="1" max="65535" value="22"></div></div>'
   +'<p class="hint">Connexion ponctuelle : pas de création dans l’inventaire. La première clé publique du serveur est automatiquement mémorisée avec accept-new ; toute clé différente est refusée.</p></div>'
   +'<div class="ssh-test-mode" data-ssh-test-mode="inventory" hidden>'
   +sshTargetPicker('server_id','',false)
   +'<div class="tool-row"><button type="button" class="btn tiny" data-action="ssh-goto-inventory">Gérer les serveurs / groupes</button></div></div>'
   +(publicData.host?'<div class="ssh-test-mode" data-ssh-test-mode="legacy" hidden><p class="hint">Ancien serveur du coffre : '+e(publicData.host)+'. Aucun enregistrement modifié.</p></div>':'')
   +'<div class="ssh-trust-section"><button type="button" class="btn" data-action="ssh-hostkey-inspect">Afficher l’empreinte SSH (facultatif)</button>'
   +'<p class="hint">Identité SSH réutilisable. La validation manuelle n’est pas obligatoire en mode accept-new ; ce contrôle facultatif concerne uniquement la clé publique du serveur.</p>'
   +'<div id="sshHostTrustPanel" aria-live="polite"></div></div>'
   +testOptionCheck('check_sftp','Tester également le sous-système SFTP','Lit uniquement les métadonnées du répertoire courant.')
   +testOptionCheck('check_whoami','Exécuter la commande de diagnostic whoami','Commande fixe, en lecture seule ; aucune autre commande autorisée.');
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
  return '<p>Authentification du jeton Proxmox et lecture des permissions, sans toucher aux VM. L’URL enregistrée dans le coffre est autorisée automatiquement, sans PROXMOX_ALLOWED_HOSTS.</p>'
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
 if(form.dataset.id){
  const mode=form.elements.namedItem('ssh_target_mode')?.value;
  form.querySelectorAll('[data-ssh-test-mode]').forEach(el=>el.hidden=el.dataset.sshTestMode!==mode);
  const host=form.elements.namedItem('manual_host');
  const port=form.elements.namedItem('manual_port');
  const inventory=form.elements.namedItem('server_id');
  if(host)host.required=mode==='manual';
  if(port)port.required=mode==='manual';
  if(inventory)inventory.required=mode==='inventory';
 }
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
 if(kind==='ssh'){
  const mode=String(data.get('ssh_target_mode')||'manual');
  if(mode==='manual'){
   options.manual_host=String(data.get('manual_host')||'').trim();
   options.manual_port=Number(data.get('manual_port')||22);
  }else if(mode==='inventory'){
   if(!data.get('server_id'))throw Error('Sélectionner un serveur dans l’inventaire SSH');
   options.server_id=Number(data.get('server_id'));
  }else if(mode!=='legacy')throw Error('Mode SSH inconnu');
 }
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


function sshTrustTarget(form){
 const data=new FormData(form);
 const mode=String(data.get('ssh_target_mode')||'manual');
 if(mode==='manual'){
  const host=String(data.get('manual_host')||'').trim();
  if(!host)throw Error('Renseigner le FQDN du serveur à contrôler');
  const port=Number(data.get('manual_port')||22);
  if(!Number.isInteger(port)||port<1||port>65535)throw Error('Port SSH invalide');
  return {host,port};
 }
 if(mode==='inventory'){
  const id=Number(data.get('server_id'));
  const record=state.sshInventory.servers.find(x=>x.id===id);
  if(!record)throw Error('Sélectionner un serveur dans l’inventaire');
  return {host:record.fqdn,port:record.port};
 }
 if(mode==='legacy'){
  const cred=state.credentials.find(x=>x.id===Number(form.dataset.id));
  const host=cred?.public?.host;
  if(!host)throw Error('Cet identifiant n’a pas d’ancien serveur');
  return {host,port:Number(cred.public.port||22)};
 }
 throw Error('Mode de sélection SSH invalide');
}
async function inspectSSHHostKey(){
 const form=document.getElementById('credentialTestForm');
 const panel=document.getElementById('sshHostTrustPanel');
 if(!form||!panel)return;
 panel.innerHTML='<div class="notice">Lecture de la clé publique SSH présentée par le serveur…</div>';
 try{
  const target=sshTrustTarget(form);
  const result=await api('/ssh/host-key/inspect','POST',target);
  const changed=result.status==='changed';
  panel.innerHTML='<div class="ssh-trust-card" data-host="'+e(result.host)+'" data-port="'+e(result.port)+'" data-fingerprint="'+e(result.fingerprint)+'">'
   +'<strong>Empreinte SSH du serveur</strong>'
   +'<p class="hint">'+e(result.host)+':'+e(result.port)+' · '+e(result.algorithm)+'</p>'
   +'<code class="ssh-trust-fingerprint">'+e(result.fingerprint)+'</code>'
   +(result.trusted?'<div class="notice">Clé d’hôte déjà approuvée pour ce serveur.</div>':
     changed?'<div class="notice error">ATTENTION : la clé d’hôte existante est différente. Aucune approbation possible via l’interface ; contrôler ce changement sur la console du serveur.</div>':
     '<p class="hint">Comparer cette empreinte à celle vérifiée directement sur la console de la cible, par exemple avec <code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256</code> (adapter l’algorithme si besoin).</p>'
    +'<label class="field"><span>Recopier l’empreinte SHA256 vérifiée sur le serveur</span><input name="verified_fingerprint" autocomplete="off" spellcheck="false" placeholder="SHA256:..." maxlength="110"></label>'
    +'<label class="ssh-trust-checkbox"><input type="checkbox" name="verified_out_of_band"><span>J’ai comparé l’empreinte avec celle obtenue directement sur le serveur, par un canal fiable.</span></label>'
    +'<button type="button" class="btn primary" data-action="ssh-hostkey-approve">Approuver cette clé d’hôte</button>')
   +'</div>';
 }catch(ex){panel.innerHTML='<div class="notice error">'+e(ex.message)+'</div>'}
}
async function approveSSHHostKey(){
 const card=document.querySelector('#sshHostTrustPanel .ssh-trust-card');
 if(!card)return;
 const typed=card.querySelector('[name="verified_fingerprint"]')?.value.trim();
 const confirmed=card.querySelector('[name="verified_out_of_band"]')?.checked;
 if(!confirmed||typed!==card.dataset.fingerprint)
  return toast('Comparer sur la console du serveur puis recopier son empreinte SHA256 exacte',false);
 const button=card.querySelector('[data-action="ssh-hostkey-approve"]');
 if(button)button.disabled=true;
 try{
  const result=await api('/ssh/host-key/approve','POST',{
   host:card.dataset.host,port:Number(card.dataset.port),
   fingerprint:typed,verified_out_of_band:true
  });
  toast(result.already_trusted?'Clé d’hôte déjà approuvée':'Clé d’hôte SSH enregistrée dans known_hosts');
  await inspectSSHHostKey();
 }catch(ex){toast(ex.message,false)}
 finally{if(button)button.disabled=false}
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

const CONNECTION_STEP_TYPES={
 email:{manual:['host','port','ssl','tls_mode','from'],vault:['credential_id']},
 ssh:{manual:['host','port'],vault:['ssh_server_id']},
 sftp:{manual:['host','port'],vault:['ssh_server_id']},
 ansible:{manual:['host','port'],vault:['ssh_server_id']},
 ftp:{manual:['host','port','allow_plaintext'],vault:[]},
 matrix:{manual:['homeserver'],vault:[]},
 directory:{manual:['uri','starttls','base_dn'],vault:[]},
 http:{manual:['url'],vault:['path']}
};
const CONNECTION_MODE_HELP={
 email:{vault:'Hôte, port, chiffrement et expéditeur fournis par le coffre SMTP.',
 manual:'Paramètres SMTP du relais saisis ici, sans identifiant ni mot de passe inline. Pour SMTP authentifié, sélectionner le coffre.'},
 ssh:{vault:'Choisir un serveur inventorié et une identité SSH du coffre.',
 manual:'FQDN/port manuels, mais identité SSH du coffre obligatoire pour la clé ou le mot de passe.'},
 sftp:{vault:'Cible d’inventaire et identité SSH du coffre.',manual:'FQDN/port manuels, secrets conservés au coffre.'},
 ansible:{vault:'Runner inventorié et compte SSH du coffre.',manual:'Runner saisi manuellement, identité SSH du coffre.'},
 ftp:{vault:'Hôte, port, sécurité et compte depuis le coffre FTP.',manual:'Hôte/port manuels ; compte FTP du coffre facultatif si connexion anonyme.'},
 matrix:{vault:'Homeserver et token Matrix du coffre.',manual:'Homeserver manuel, token bot toujours issu du coffre.'},
 directory:{vault:'Utiliser le coffre LDAP ou l’administration centrale.',manual:'Paramètres réseau LDAP personnalisés, secrets toujours dans le coffre.'},
 http:{vault:'URL de base et token API depuis le coffre ; chemin relatif par étape.',manual:'URL saisie directement ; pour authentification, utiliser le coffre API.'}
};
function stepConnectionMode(step){
 if(step.config?.connection_mode)return step.config.connection_mode;
 return step.config?.credential_id?'vault':'manual';
}
function connectionModeWidget(step){
 if(!CONNECTION_STEP_TYPES[step.type])return '';
 const mode=stepConnectionMode(step),help=CONNECTION_MODE_HELP[step.type]||{};
 return '<div class="connection-source-select"><div class="field"><span>Configuration de la connexion</span><select name="cfg_connection_mode">'
  +option([['vault','Utiliser le coffre-fort (recommandé)'],['manual','Configuration réseau manuelle']],mode)
  +'</select></div><p class="hint">'+e(help[mode]||'')+'</p></div>';
}
function stepConfigFields(step){
 const schema=CONNECTION_STEP_TYPES[step.type],mode=stepConnectionMode(step);
 const fields=FIELDS[step.type]||[];
 return connectionModeWidget(step)+fields.filter(([key])=>{
  if(!schema)return true;
  if(key==='credential_id')return (step.type==='email'||step.type==='http')?mode==='vault':true;
  if(step.type==='email'&&key==='ssl'&&step.config?.connection_mode)return false;
  if(schema.manual.includes(key))return mode==='manual';
  if(schema.vault.includes(key))return mode==='vault';
  return true;
 }).map(([key,label,type])=>{
  const value=step.config?.[key]??'';
  if(type==='textarea')return '<div class="field full"><span>'+e(label)+'</span><textarea name="cfg_'+e(key)+'">'+e(value)+'</textarea></div>';
  if(type==='checkbox')return field(label,'cfg_'+key,value,'checkbox');
  if(type==='agent')return selectField(label,'cfg_'+key,[['','— Sélectionner —'],...state.agents.filter(a=>a.enrolled&&!a.revoked).map(a=>[a.id,a.name+' ('+a.platform+')'])],value);
  if(type==='datacenter')return selectField(label,'cfg_'+key,[['','— Sélectionner —'],...state.datacenters.map(d=>[d.id,d.name])],value);
  if(type==='ssh-target')return sshTargetPicker('cfg_'+key,value,false);
  if(type==='credential')return credentialField(label,key,value);
  if(type.startsWith('select:'))return selectField(label,'cfg_'+key,type.slice(7).split('|').map(x=>[x,x]),value);
  return field(label,'cfg_'+key,value,type);
 }).join('');
}
function branches(step){const s=state.editor.steps_json;const targets=[['','Automatique (étape suivante)'],['END','Fin avec succès'],['FAIL','Fin avec échec'],...s.filter(x=>x.id!==step.id).map(x=>[x.id,x.name])];return `${selectField('Si réussite','on_success',targets,step.on_success||'')}${selectField('Si échec','on_failure',[['','Arrêt en échec'],...targets.slice(1)],step.on_failure||'')}`}

function notificationFields(job){
 if(!roleAdmin())return '';
 const current=job.notification_json||{},choices=[['','— Choisir une connexion SMTP du coffre —'],
    ...state.credentials.filter(item=>item.kind==='smtp').map(item=>[item.id,item.name])];
 return '<div class="panel" style="margin:12px 0"><div class="panel-title"><h3>Rapport e-mail HTML du workflow</h3></div>'
  +'<p class="hint">Rapport automatique en fin d’exécution : objet [SUCCÈS] ou [ÉCHEC], tableau des étapes, tentatives, codes de retour et extraits des erreurs masquant les secrets du coffre.</p>'
  +'<label class="field checkbox"><input type="checkbox" name="notify_enabled" '+(current.enabled?'checked':'')+'>Activer les notifications</label>'
  +'<div class="form-grid">'+selectField('Connexion SMTP du coffre','notify_smtp',choices,current.credential_id||'')
  +field('Destinataires (séparés par virgules, maximum 5)','notify_recipients',current.recipients||'')+'</div>'
  +'<div class="form-grid"><label class="field checkbox"><input type="checkbox" name="notify_success" '+(current.on_success?'checked':'')+'>Notifier les réussites</label>'
  +'<label class="field checkbox"><input type="checkbox" name="notify_failure" '+(current.on_failure?'checked':'')+'>Notifier les échecs</label></div>'
  +'<p class="hint">Les rapports sont conservés dans Exécutions même si le SMTP échoue. Aucun mot de passe ni jeton du coffre n’est inclus dans les rapports.</p></div>';
}

function renderEditor(){const j=state.editor;const selected=j.steps_json[state.selectedStep];const body=`<div class="editor-meta">${field('Nom du workflow','job_name',j.name)}${folderEditorField(j.folder)}${field('Tags (séparés par des virgules)','job_tags',j.tags_json.join(', '))}</div><div class="editor-meta"><div class="field"><span>Description</span><input name="job_description" value="${e(j.description)}"></div>${scheduleFields(j.schedule_json||{})}</div><div class="editor-meta">${groupEditorField('Groupes lecture','view',j.acl_json.view)}${groupEditorField('Groupes exécution','run',j.acl_json.run)}${groupEditorField('Groupes modification','edit',j.acl_json.edit)}</div>${notificationFields(j)}<div class="field checkbox"><input type="checkbox" name="job_enabled" ${j.enabled?'checked':''}><span>Workflow actif</span></div><div class="editor-columns"><section class="editor-graph"><div class="panel-title"><h2>Graphe d’exécution</h2><span class="hint">Succès / échec</span></div><div class="graph-toolbar" role="group" aria-label="Zoom et déplacement du graphe"><button type="button" class="btn tiny" data-action="graph-zoom-out" title="Dézoomer" aria-label="Dézoomer">−</button><output id="graphZoomValue" aria-live="polite">${Math.round((state.graphZoom||1)*100)} %</output><button type="button" class="btn tiny" data-action="graph-zoom-in" title="Zoomer" aria-label="Zoomer">＋</button><button type="button" class="btn tiny" data-action="graph-zoom-reset">100 %</button><button type="button" class="btn tiny" data-action="graph-fit-width">Adapter largeur</button><button type="button" class="btn tiny" data-action="graph-fit-all">Vue globale</button></div><p class="graph-help">Déplacer : glisser le fond · Zoom : Ctrl + molette ou boutons</p><div class="graph-legend"><span>Succès</span><span>Échec</span></div><div class="graph-surface" id="workflowGraph">${renderGraph(j.steps_json,state.selectedStep)}</div><div class="step-list">${j.steps_json.map((s,i)=>`<button class="step-pill ${i===state.selectedStep?'selected':''}" data-action="select-step" data-index="${i}">${i+1}. ${e(s.name)}<small>${e(kindLabel[s.type])}</small></button>`).join('')}</div></section><section class="editor-side"><div class="panel-title"><h2>Configurer l’étape ${state.selectedStep+1}</h2><span class="hint">${j.steps_json.length} étapes</span></div><div class="editor-actions"><button class="btn tiny" data-action="add-step">＋ Ajouter</button><button class="btn tiny failure-add" data-action="add-failure-step">＋ Si échec</button><button class="btn tiny" data-action="step-up">↑</button><button class="btn tiny" data-action="step-down">↓</button><button class="btn tiny danger" data-action="delete-step">Supprimer</button></div><div class="form-grid">${field('Nom de l’étape','step_name',selected.name)}${selectField('Type','step_type',kinds.map(k=>[k,kindLabel[k]]),selected.type)}</div><div class="subheading">Paramètres de la tâche</div><div class="step-config">${stepConfigFields(selected)}</div><div class="divider"></div><div class="subheading">Gestion des erreurs</div><div class="form-grid">${field('Nouvelles tentatives (0–5)','step_retries',selected.retries||0,'number')}${field('Attente entre tentatives (s)','step_retry_delay',selected.retry_delay??2,'number')}</div><div class="divider"></div><div class="subheading">Branches de réussite / échec</div><p class="hint">En cas d’échec, le moteur suit la branche rouge. Cliquez sur « ＋ Si échec » pour créer une tâche reliée automatiquement.</p><div class="form-grid">${branches(selected)}</div><div class="divider"></div><div class="subheading">Condition avant exécution</div><div class="field checkbox"><input name="cond_enabled" type="checkbox" ${selected.condition?.enabled?'checked':''}><span>Activer cette condition</span></div><div class="form-grid">${selectField('Source','cond_source',[['last_status','État de la précédente étape'],['last_exit_code','Code retour précédent'],['last_output','Sortie précédente'],['variable','Variable']],selected.condition?.source||'last_status')}${selectField('Comparaison','cond_operator',[['equals','Égal'],['not_equals','Différent'],['contains','Contient'],['not_contains','Ne contient pas'],['exists','Existe']],selected.condition?.operator||'equals')}${field('Valeur attendue','cond_value',selected.condition?.value||'')}${field('Nom de la variable','cond_key',selected.condition?.key||'')}</div></section></div>`;
modal(j.id?'Modifier le workflow':'Nouveau workflow',body,`<button class="btn" data-action="modal-close">Annuler</button>${j.id&&roleAdmin()?`<button class="btn danger" data-action="delete-job" data-id="${j.id}">Supprimer</button>`:''}<button class="btn primary" data-action="save-job">✓ Enregistrer</button>`,true);showScheduleMode();restoreGraphViewport()}
function captureEditor(){const root=$('.modal');if(!root||!state.editor)return;saveGraphViewport();const get=n=>root.querySelector(`[name="${n}"]`),v=n=>get(n)?.value??'',ck=n=>!!get(n)?.checked;const j=state.editor;j.name=v('job_name');j.folder=v('job_folder');j.description=v('job_description');j.tags_json=v('job_tags').split(',').map(x=>x.trim()).filter(Boolean);j.schedule_json=captureSchedule(v);j.enabled=ck('job_enabled');j.acl_json=Object.fromEntries(['view','run','edit'].map(k=>[k,v('acl_'+k).split(',').map(x=>x.trim().toLowerCase()).filter(Boolean)]));if(roleAdmin()){j.notification_json=ck('notify_enabled')?{enabled:true,credential_id:Number(v('notify_smtp')||0),recipients:v('notify_recipients'),on_success:ck('notify_success'),on_failure:ck('notify_failure')}:{}}const s=j.steps_json[state.selectedStep];s.name=v('step_name');s.type=v('step_type');s.retries=Number(v('step_retries')||0);s.retry_delay=Number(v('step_retry_delay')||2);s.on_success=v('on_success');s.on_failure=v('on_failure');s.condition={enabled:ck('cond_enabled'),source:v('cond_source'),operator:v('cond_operator'),value:v('cond_value'),key:v('cond_key')};const keys=(FIELDS[s.type]||[]).map(x=>x[0]);const cfg={...s.config};for(const key of keys){const element=get('cfg_'+key);if(element){cfg[key]=element.type==='checkbox'?element.checked:element.type==='number'?Number(element.value||0):element.value}}const mode=get('cfg_connection_mode');if(mode)cfg.connection_mode=mode.value;s.config=cfg}

function saveGraphViewport(){
 const el=document.getElementById('workflowGraph');
 if(el)state.graphViewport={left:el.scrollLeft,top:el.scrollTop};
}
function restoreGraphViewport(){
 const el=document.getElementById('workflowGraph'),point=state.graphViewport;
 if(el&&point){el.scrollLeft=point.left;el.scrollTop=point.top}
}
function graphZoomTo(scale,clientX=null,clientY=null){
 const el=document.getElementById('workflowGraph');
 if(!el||!state.editor)return;
 const svg=el.querySelector('svg.workflow-svg');
 if(!svg)return;
 const old=state.graphZoom||1;
 const next=Math.min(2.5,Math.max(.08,Math.round(scale*100)/100));
 const rect=el.getBoundingClientRect();
 const x=clientX===null?el.clientWidth/2:clientX-rect.left;
 const y=clientY===null?el.clientHeight/2:clientY-rect.top;
 const left=(el.scrollLeft+x)*(next/old)-x;
 const top=(el.scrollTop+y)*(next/old)-y;
 state.graphZoom=next;
 svg.setAttribute('width',String(Math.round(Number(svg.dataset.baseWidth)*next)));
 svg.setAttribute('height',String(Math.round(Number(svg.dataset.baseHeight)*next)));
 el.scrollLeft=Math.max(0,left);
 el.scrollTop=Math.max(0,top);
 state.graphViewport={left:el.scrollLeft,top:el.scrollTop};
 const label=document.getElementById('graphZoomValue');
 if(label)label.textContent=Math.round(next*100)+' %';
}
function graphFit(mode){
 const el=document.getElementById('workflowGraph');
 const svg=el?.querySelector('svg.workflow-svg');
 if(!el||!svg)return;
 const availableWidth=Math.max(100,el.clientWidth-28);
 const widthRatio=availableWidth/Number(svg.dataset.baseWidth);
 const heightRatio=Math.max(100,el.clientHeight-28)/Number(svg.dataset.baseHeight);
 graphZoomTo(mode==='all'?Math.min(widthRatio,heightRatio):widthRatio);
 el.scrollLeft=0;el.scrollTop=0;
 state.graphViewport={left:0,top:0};
}
let graphPan=null;
function graphPanStart(ev){
 const surface=ev.target.closest('#workflowGraph');
 if(!surface||ev.button!==0||ev.target.closest('.step-node'))return;
 graphPan={surface,id:ev.pointerId,startX:ev.clientX,startY:ev.clientY,
           left:surface.scrollLeft,top:surface.scrollTop};
 surface.classList.add('graph-panning');
 try{surface.setPointerCapture(ev.pointerId)}catch{}
 ev.preventDefault();
}
function graphPanMove(ev){
 if(!graphPan||graphPan.id!==ev.pointerId)return;
 const p=graphPan;
 p.surface.scrollLeft=p.left+ p.startX-ev.clientX;
 p.surface.scrollTop=p.top+ p.startY-ev.clientY;
 state.graphViewport={left:p.surface.scrollLeft,top:p.surface.scrollTop};
}
function graphPanStop(ev){
 if(!graphPan||graphPan.id!==ev.pointerId)return;
 graphPan.surface.classList.remove('graph-panning');
 graphPan=null;
}

function renderGraph(steps,index){const coords=steps.map((_,i)=>({x:135,y:55+i*125}));const height=Math.max(375,steps.length*125+55);const zoom=state.graphZoom||1;let svg=`<svg class="workflow-svg" data-base-width="670" data-base-height="${height}" width="${Math.round(670*zoom)}" height="${Math.round(height*zoom)}" viewBox="0 0 670 ${height}" xmlns="http://www.w3.org/2000/svg"><defs><marker id="okArrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" fill="none" stroke="#51d5a5"/></marker><marker id="noArrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" fill="none" stroke="#f17883"/></marker></defs><rect x="14" y="16" width="78" height="27" rx="7" fill="#ffc34b22"/><text x="26" y="34" font-size="11" fill="#ffc34b">DÉPART</text><path d="M92 30 H120 V60 H135" class="edge-success" marker-end="url(#okArrow)"/>`;
steps.forEach((s,i)=>{const next=s.on_success||steps[i+1]?.id||'END';const fail=s.on_failure||'FAIL';const target=steps.findIndex(x=>x.id===next);const failure=steps.findIndex(x=>x.id===fail);const x=coords[i].x,y=coords[i].y; if(target>=0){const ty=coords[target].y+37;svg+=`<path d="M${x+230} ${y+27} H410 V${ty} H${coords[target].x}" class="edge-success" marker-end="url(#okArrow)"/>`}else{svg+=`<path d="M${x+230} ${y+27} H445" class="edge-success" marker-end="url(#okArrow)"/><text x="450" y="${y+31}" font-size="10" fill="#51d5a5">${next==='FAIL'?'FAIL':'END'}</text>`}if(failure>=0){const fy=coords[failure].y+14;svg+=`<path d="M${x+230} ${y+57} H535 V${fy} H${coords[failure].x}" class="edge-fail" marker-end="url(#noArrow)"/>`}else{svg+=`<path d="M${x+230} ${y+57} H565" class="edge-fail"/><text x="570" y="${y+61}" font-size="10" fill="#f17883">${fail==='END'?'END':'FAIL'}</text>`}});
steps.forEach((s,i)=>{const x=coords[i].x,y=coords[i].y;svg+=`<g class="step-node ${index===i?'selected':''}" data-action="select-step" data-index="${i}"><rect x="${x}" y="${y}" width="230" height="77" rx="11"/><circle cx="${x+22}" cy="${y+23}" r="10" fill="#ffc34b33"/><text x="${x+18}" y="${y+27}" font-size="12" fill="#ffc34b">${i+1}</text><text x="${x+41}" y="${y+28}" font-size="13" font-weight="700">${e(s.name.slice(0,25))}</text><text x="${x+17}" y="${y+54}" font-size="11" fill="#9396a2">${e(kindLabel[s.type])}</text></g>`});return svg+'</svg>'}
async function saveEditor(){captureEditor();const j=state.editor;if(!j.name.trim())return toast('Saisis un nom de workflow',false);try{if(j.id){await api('/jobs/'+j.id,'PUT',j)}else await api('/jobs','POST',j);toast('Workflow enregistré');closeModal();await refresh();showPage('jobs')}catch(ex){toast(ex.message,false)}}
async function runJob(id){try{const r=await api('/jobs/'+id+'/run','POST');toast('Exécution #'+r.run_id+' mise en file');await refresh();showPage('runs');await openRun(r.run_id)}catch(ex){toast(ex.message,false)}}
async function openRun(id){try{const r=await api('/runs/'+id);state.selectedRun=r;modal('Exécution #'+r.id+' — '+e(r.job_name),`<div class="tool-row">${labelStatus(r.status)}<span class="hint">Déclenché par ${e(r.requested_by)} · ${dt(r.queued_at)}</span></div>${r.error?`<div class="notice error">${e(r.error)}</div>`:''}${r.notification_status?`<div class="notice">Notification e-mail : ${e(r.notification_status==='sent'?'envoyée':r.notification_status==='failed'?'échec de l’envoi':r.notification_status==='skipped'?'non demandée pour ce statut':'désactivée')}${r.notification_error?' · '+e(r.notification_error):''}</div>`:''}<h3>Résultats par étape</h3>${r.steps.map(s=>`<div class="step-log"><div class="step-header">${labelStatus(s.status)}<b>${e(s.name)}</b><span class="muted">${e(s.type)} · essai ${s.attempt} · code ${s.exit_code??'—'}</span></div><pre>${e(s.output||'(aucune sortie)')}</pre></div>`).join('')||'<p class="muted">Exécution en attente. Réactualisez dans quelques secondes.</p>'}`,`<button class="btn" data-action="run-detail" data-id="${id}">↻ Actualiser</button><button class="btn primary" data-action="modal-close">Fermer</button>`,true)}catch(ex){toast(ex.message,false)}}
async function hydrateAdmins(){if(!roleAdmin())return;try{if(state.page==='users'){const users=(await api('/users')).items;$('#userTable').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Utilisateur</th><th>Source</th><th>Rôle</th><th>Groupes internes / LDAP</th></tr></thead><tbody>${users.map(u=>`<tr><td class="name-cell">${e(u.display_name)}<div class="tinytext muted">${e(u.username)}</div></td><td>${e(u.source)}</td><td>${e(u.role)}</td><td>${(u.groups_json||[]).map(x=>`<span class="chip">${e(x)}</span>`).join('')}</td></tr>`).join('')}</tbody></table></div>`}if(state.page==='audit'){const rows=(await api('/audit?category='+encodeURIComponent(state.auditCategory||'all')+'&search='+encodeURIComponent(state.auditSearch||''))).items;$('#auditTable').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Heure</th><th>Auteur</th><th>Action</th><th>Objet</th><th>Détails</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${dt(r.created_at)}</td><td>${e(r.actor)}</td><td>${e(r.action)}</td><td>${e(r.target)}</td><td>${e(r.detail)}</td></tr>`).join('')}</tbody></table></div>`}if(state.page==='updates'){const u=await api('/updates');$('#updateVersionSummary').innerHTML=renderUpdateVersion(u);$('#updateStatus').innerHTML=`<div class="hint" style="margin-bottom:14px">${u.worker?.message?e(u.worker.message):'Aucune information du service hôte. Lancez ops/update-worker.py.'}</div><div class="table-wrap"><table><thead><tr><th>Action</th><th>Statut</th><th>Auteur</th><th>Date</th><th>Message</th></tr></thead><tbody>${u.requests.map(r=>`<tr><td>${e(r.action)}</td><td>${labelStatus(r.status)}</td><td>${e(r.actor)}</td><td>${dt(r.created_at)}</td><td>${e(r.message)}</td></tr>`).join('')}</tbody></table></div>`}}catch(ex){toast(ex.message,false)}}
function bindLocal(){hydrateAdmins();if(state.page==='ldap')hydrateLDAP();if(state.page==='agents')scheduleAgentBatchPoll()}
async function refresh(){await load();render()}
async function handleClick(ev){const b=ev.target.closest('[data-action], [data-page]');if(!b)return;const a=b.dataset.action||'page',id=Number(b.dataset.id);try{if(a==='overview-tab'){state.dashboardTab=b.dataset.value||'tasks';render();return}
 if(a==='graph-zoom-in'){graphZoomTo((state.graphZoom||1)*1.25);return}
 if(a==='graph-zoom-out'){graphZoomTo((state.graphZoom||1)/1.25);return}
 if(a==='graph-zoom-reset'){graphZoomTo(1);return}
 if(a==='graph-fit-width'){graphFit('width');return}
 if(a==='graph-fit-all'){graphFit('all');return}
 if(a==='layer-close'){closeLayer(b.dataset.layer);return}
 if(a==='ssh-goto-inventory'){closeLayer('credentialTestLayer');closeLayer('credentialLayer');showPage('ssh-inventory');return}
 if(a==='ssh-group-new'){sshGroupForm();return}
 if(a==='ssh-group-edit'){sshGroupForm(id);return}
 if(a==='ssh-server-new'){sshServerForm();return}
 if(a==='ssh-server-edit'){sshServerForm(id);return}
 if(a==='ssh-group-remove'){
  if(!confirm('Supprimer ce groupe SSH ? Les serveurs resteront dans l’inventaire.'))return;
  await api('/ssh/groups/'+id,'DELETE');
  toast('Groupe supprimé, serveurs conservés');await refresh();return
 }
 if(a==='ssh-server-remove'){
  if(!confirm('Retirer ce serveur de l’inventaire ? Les workflows qui le référencent doivent être modifiés avant suppression.'))return;
  await api('/ssh/servers/'+id,'DELETE');
  toast('Serveur retiré de l’inventaire');await refresh();return
 }
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
 if(a==='ssh-hostkey-inspect'){await inspectSSHHostKey();return}
 if(a==='ssh-hostkey-approve'){await approveSSHHostKey();return}
 if(a==='credential-remove'){if(!confirm('Supprimer définitivement cette connexion ? Une connexion utilisée par un workflow ne pourra pas être supprimée.'))return;await api('/credentials/'+id,'DELETE');await reloadCredentials();drawCredentialBrowser();if(state.page==='vault')render();toast('Connexion supprimée');return}
 if(a==='group-browser'){await openGroupBrowser(b.dataset.role);return}
 if(a==='group-toggle'){const arr=state.editor.acl_json[state.groupRole]||[],group=b.dataset.name.toLowerCase();state.editor.acl_json[state.groupRole]=arr.includes(group)?arr.filter(x=>x!==group):[...arr,group];drawGroupBrowser();return}
 if(a==='group-done'){closeLayer('groupLayer');renderEditor();return}
 if(a==='ldap-ca-import'){
  const file=document.getElementById('ldapCAImportFile')?.files?.[0];
  if(!file)return toast('Sélectionner un certificat CA (.crt, .cer, .der ou .pem)',false);
  if(file.size>131072)return toast('Certificat trop volumineux (128 Kio maximum)',false);
  const original=b.textContent;b.disabled=true;b.textContent='Import en cours…';
  try{
   const bytes=new Uint8Array(await file.arrayBuffer());
   if(!bytes.length)throw Error('Fichier certificat vide');
   const textValue=new TextDecoder('utf-8',{fatal:false}).decode(bytes);
   let payload;
   if(textValue.includes('-----BEGIN CERTIFICATE-----')){
    payload={pem:textValue};
   }else{
    // Encode binary DER without large apply() arguments or Unicode corruption.
    let binary='';
    for(let i=0;i<bytes.length;i+=8192){
     binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    }
    payload={der_base64:btoa(binary)};
   }
   const result=await api('/admin/ldap/ca','POST',payload);
   const input=document.querySelector('#ldapAdminForm [name="ca_cert"]');
   if(input)input.value=result.path;
   const bypass=document.querySelector('#ldapAdminForm [name="skip_cert_verification"]');
   if(bypass)bypass.checked=false;
   toast(result.certificate_count+' certificat(s) CA importé(s).'+(result.ignored_non_ca_count?' '+result.ignored_non_ca_count+' certificat(s) serveur ignoré(s).':'')+' Enregistrer la configuration LDAP.');
   const output=document.getElementById('ldapTestResult');
   if(output)output.innerHTML='<div class="notice"><strong>CA importée</strong> : '+e(result.certificate_count)+' certificat(s) de confiance'
     +(result.ignored_non_ca_count?' · '+e(result.ignored_non_ca_count)+' certificat(s) serveur ignoré(s)':'')
     +'<p class="hint">'+(result.ca_subjects||[]).map(e).join(' · ')+'</p>'
     +'<strong>Vérification TLS réactivée dans le formulaire.</strong> Enregistrer la configuration puis tester LDAP.</div>';
  }catch(ex){toast(ex.message,false)}
  finally{b.disabled=false;b.textContent=original}
  return
 }
 if(a==='ldap-test'){
  const data=readLDAPForm(),target=document.getElementById('ldapTestResult');
  if(target)target.textContent='Test LDAP en cours…';
  try{const result=await api('/admin/ldap/test','POST',data);
   if(target)target.innerHTML='<span class="status success">Test réussi</span> '+e(result.message)+' · '+result.group_sample_count+' groupe(s) échantillonnés'+(result.user_tested?' · Utilisateur '+(result.user_found?'trouvé':'introuvable'):'');
  }catch(ex){if(target)target.innerHTML='<div class="notice error">'+e(ex.message)+'</div>'}return
 }
 if(a==='page'){showPage(b.dataset.page);return}if(a==='agent-deploy-form'){agentDeployForm();return}
 if(a==='agent-deploy-bulk-form'){agentBulkDeployForm();return}
 if(a==='dr-vm-pbs-backup'){
  const vm=(state.drProtectedVMs||[]).find(x=>x.id===id);
  if(!vm||vm.engine!=='pbs_backup_restore')return toast('Sélectionner la stratégie PBS sur la protection',false);
  const storage=prompt('Nom du stockage PBS configuré sur le cluster SOURCE (ex. pbs-backup) :');
  if(!storage)return;
  if(!confirm('Déclencher une vraie sauvegarde PVE vers '+storage+' pour la VM '+vm.source_vmid+' ? Cela consomme des ressources, mais ne démarre ni n’arrête les VM.'))return;
  try{
   const x=await api('/dr/vms/'+id+'/pbs/backup','POST',{storage,approved:true});
   modal('Sauvegarde PBS soumise','<div class="notice">Tâche Proxmox soumise : '+e(x.upid)+'. Le résultat n’est pas encore vérifié et aucune VM de secours n’a été créée.</div>',
    '<button class="btn primary" data-action="dr-vm-pbs-status" data-id="'+id+'" data-job="'+x.job_id+'">Vérifier le résultat</button>',true);
  }catch(ex){toast(ex.message,false)}
  return;
 }
 if(a==='dr-vm-pbs-status'){
  try{
   const x=await api('/dr/vms/'+id+'/pbs/backups/'+Number(b.dataset.job));
   modal('État de sauvegarde PBS','<div class="notice">État : '+e(x.status)+' · sortie : '+e(x.exitstatus||'en cours')+
     '. Sauvegarde terminée : '+(x.backup_verified?'oui':'non')+
     '. VM secours restaurée : non.</div>',
    '<button class="btn primary" data-action="dr-vm-pbs-status" data-id="'+id+'" data-job="'+e(b.dataset.job)+'">Actualiser le statut</button>',true);
  }catch(ex){toast(ex.message,false)}
  return;
 }
 if(a==='agent-update'){openAgentUpdateForm([id]);return}
 if(a==='agent-update-all'){
   const versions=state.agentUpdates?.available_versions||{};
   const ids=(state.agents||[]).filter(x=>x.status==='online'&&agentVersionOutdated(x.version,versions[x.platform])).map(x=>x.id);
   openAgentUpdateForm(ids);return
 }
 if(a==='agent-batch-refresh'){await refresh();return}
 if(a==='agent-batch-clean'){
  if(!confirm('Supprimer les anciens lots terminés ? Les lots actifs et le journal d’audit seront conservés.'))return;
  try{
   const result=await api('/agents/deploy/batches/cleanup','POST',{});
   state.agentDeployBatches=(await api('/agents/deploy/batches')).items;
   const panel=document.getElementById('agentBatchList');if(panel)panel.innerHTML=agentBatchPanel();
   toast(result.removed+' lot(s) terminés nettoyé(s)');
  }catch(ex){toast(ex.message,false)}
  return;
 }
 if(a==='agent-bulk-add-group'){
  const sel=document.getElementById('agentBulkInventoryGroup');
  const group=(state.sshInventory.groups||[]).find(g=>String(g.id)===sel?.value);
  if(!group)return toast('Sélectionner un groupe de serveurs',false);
  const area=document.querySelector('#agentBulkDeployForm [name="targets"]');
  if(!area)return;
  const targets=(state.sshInventory.servers||[]).filter(h=>group.server_ids.includes(h.id))
   .map(x=>x.fqdn+(x.port===22?'':':'+x.port));
  const lines=new Set([...area.value.split(/[\r\n,]+/).map(x=>x.trim()).filter(Boolean),...targets]);
  area.value=[...lines].join('\n');
  toast(targets.length+' serveur(s) ajoutés');return
 }if(a==='agent-modules'){openAgentModulesForm(id);return}
 if(a==='agent-uninstall'){openAgentUninstallForm(id);return}
 if(a==='agent-enroll-form'){agentEnrollForm();return}if(a==='agent-revoke'){if(!confirm('Révoquer cet agent et ses tâches ?'))return;await api('/agents/'+id+'/revoke','POST');toast('Agent révoqué');await refresh();return}if(a==='agent-settings-open'){closeModal();showPage('agents');document.querySelector('#agentConnectionSettingsForm input[name="controller_url"]')?.focus();return}
 if(a==='fleet-open'){await openClusterAgentWizard(id);return}
 if(a==='fleet-preview-refresh'){
   const form=document.getElementById('proxmoxFleetGroupForm');
   await openClusterAgentWizard(id,String(form?.elements.namedItem('domain_suffix')?.value||'').trim());return
 }
 if(a==='fleet-ssh-key'){await inspectClusterSSHKey(b);return}
 if(a==='dr-site-fleet'){await openPRAClusterSelector(id);return}
 if(a==='agents-status-refresh'||a==='agent-status-refresh'){
  state.agentUpdates=await api('/agents/updates');
  state.agents=(await api('/agents')).items;
  state.drSites=(await api('/dr/sites')).items;
  render();
  toast('Derniers états enregistrés rechargés ; le prochain heartbeat de chaque agent arrivera automatiquement.');
  return;
 }
 if(a==='dr-plans-refresh'){
  state.drPlans=(await api('/dr/plans')).items;
  state.drSites=(await api('/dr/sites')).items;
  render();toast('Groupes et sites PRA actualisés depuis la base');return;
 }
 if(a==='dr-plan-protected'){
  state.drVMPlanFilter=String(id);
  showPage('protected-vms');return;
 }
 if(a==='dr-vm-new'){openProtectedVMWizard();return}
 if(a==='protect-vm-resource'){
  closeModal();openProtectedVMWizard(null,id,Number(b.dataset.vmid));return
 }
 if(a==='dr-vm-recovery-readiness'){
   const r=await api('/dr/vms/'+id+'/recovery-readiness');
   const checks=(r.checks||[]).map(x=>'<tr><td>'+e(x.name)+'</td><td>'
     +(x.passed?'<span class="status success">Validé</span>':'<span class="status failed">Bloqué</span>')
     +'</td><td>'+e(x.detail)+'</td></tr>').join('');
   modal('Préparation bascule PRA · VM #'+e(r.source_vmid),
     '<div class="notice error"><b>Bascule réelle interdite.</b> '
     +'Le rapport liste les preuves nécessaires ; aucune action Proxmox ne sera déclenchée.</div>'
     +'<p><b>Dernier checkpoint vérifié :</b> '
     +(r.last_structural_sync?dt(r.last_structural_sync):'aucun')
     +' · <b>Stockage :</b> '+e(r.target_backend||'non confirmé')+'</p>'
     +'<div class="table-wrap"><table><thead><tr><th>Contrôle</th><th>Résultat</th><th>Explication</th></tr></thead><tbody>'
     +checks+'</tbody></table></div>'
     +'<p class="hint">'+e(r.safety_note)+'</p>',
     '<button class="btn primary" data-action="modal-close">Fermer</button>',true);
   return;
  }
  if(a==='dr-vm-lvm-lab-open'){
   await openPRALvmLabPanel(id);return;
  }
  if(a==='dr-vm-check-vmid'){
   const form=document.getElementById('drVMConfigForm'),r=state.drVMOptions;
   if(!form||!r)return;
   const field=form.elements.namedItem('target_vmid'),msg=document.getElementById('praVMIDStatus');
   msg.textContent='Vérification en cours...';
   try{
    const report=await api('/dr/vms/check-target-vmid?plan_id='+r.plan_id
       +'&source_dc_id='+r.source_dc_id+'&target_dc_id='+r.target_dc_id
       +'&vmid='+encodeURIComponent(field.value));
    msg.textContent=report.free?'VMID '+report.requested_vmid+' libre sur le cluster cible'
       :'VMID occupé. Proposition libre : '+report.suggested_vmid;
    if(!report.free){field.value=report.suggested_vmid;toast('VMID occupé : VMID '+report.suggested_vmid+' proposé')}
   }catch(ex){msg.textContent='Échec vérification : '+ex.message;toast(ex.message,false)}
   return;
  }
  if(a==='audit-refresh'){
   state.auditSearch=document.getElementById('auditSearch')?.value||'';
   state.auditCategory=document.getElementById('auditCategory')?.value||'all';
   hydrateAdmins();return;
  }
  if(a==='dr-vm-lvm-lab-diagnose-source'){
   try{
    const report=await api('/dr/vms/'+id+'/lvm-lab/diagnose-source');
    modal('Diagnostic QMP source — VM #'+e(report.vmid),
      '<div class="notice '+(report.ready?'':'error')+'"><b>'
       +(report.ready?'Source QMP accessible':'Source QMP non validée')
       +'</b> · Étape : '+e(report.stage)+' · '+e(report.message)+'</div>'
       +(report.ready?'<p>Disques détectés : '+e((report.slots||[]).join(', '))
            +' · Jobs Origami actifs : '+e(report.active_jobs)+'</p>':'')
       +'<p class="hint">Diagnostic en lecture seule : aucune copie, aucun arrêt de VM.</p>',
      '<button class="btn" data-action="modal-close">Fermer</button>',true);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-resume-source'){
   const confirmation=document.getElementById('praLvmLabConfirmation')?.value?.trim();
   if(confirmation!=='DISPOSABLE-LAB-ONLY')return toast('Saisir DISPOSABLE-LAB-ONLY',false);
   if(!confirm('Vérifier le QMP source et la propriété de REP_ arrêtée, et reprendre la première copie FULL uniquement si aucun transfert LVM n’existe ?'))return;
   try{
    const result=await api('/dr/vms/'+id+'/lvm-lab/resume-source','POST',{confirmation});
    toast('Vérification réussie ; activation #'+result.activation_id+' lancée');
    state.drProtectedVMs=(await api('/dr/vms')).items;
    await openPRALvmLabPanel(id);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-diagnose'){
   try{
    const report=await api('/dr/vms/'+id+'/lvm-lab/diagnose-standby');
    const rows=(report.checks||[]).map(x=>'<tr><td>'+e(x.stage)+'</td><td>'+labelStatus(x.ok?'completed':'failed')
      +'</td><td>'+e(x.message)+'</td></tr>').join('');
    modal('Diagnostic Proxmox en lecture seule — VMID '+e(report.vmid),
      '<div class="notice"><b>Aucune écriture ni création de VM.</b> '
      +(report.ready_for_creation?'Le VMID est libre, mais aucun transfert n’est autorisé par ce diagnostic.'
        :report.owned_standby?'VM REP_ appartenant à cette protection : arrêtée et isolée. Une reprise contrôlée est possible après vérification des checkpoints.'
        :report.target_exists?'Le VMID est occupé mais sa propriété n’est pas attestée : aucune suppression ni reprise.'
        :'Un prérequis Proxmox a échoué. Corriger ce contrôle avant la reprise.')+'</div>'
      +'<div class="table-wrap"><table><thead><tr><th>Étape</th><th>État</th><th>Diagnostic</th></tr></thead><tbody>'
      +rows+'</tbody></table></div>',
      '<button class="btn" data-action="modal-close">Fermer</button>',true);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-resume-owned'){
   const confirmation=document.getElementById('praLvmLabConfirmation')?.value?.trim();
   if(confirmation!=='DISPOSABLE-LAB-ONLY')return toast('Saisir DISPOSABLE-LAB-ONLY',false);
   if(!confirm('Vérifier directement sur Proxmox que REP_ existe, appartient à cette protection, '
     +'est arrêtée, sans réseau et sans checkpoint LVM ? Aucun disque ou VM ne sera supprimé. '
     +'Après validation, la première copie FULL pourra démarrer automatiquement.'))return;
   try{
    const result=await api('/dr/vms/'+id+'/lvm-lab/resume-owned','POST',{confirmation});
    toast('Réplique existante vérifiée, activation #'+result.activation_id+' lancée');
    state.drProtectedVMs=(await api('/dr/vms')).items;
    await openPRALvmLabPanel(id);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-reconcile-provision'){
   const confirmation=document.getElementById('praLvmLabConfirmation')?.value?.trim();
   if(confirmation!=='DISPOSABLE-LAB-ONLY')
    return toast('Saisir DISPOSABLE-LAB-ONLY',false);
   if(!confirm('Vérifier l’absence du VMID sur le cluster Proxmox cible avant toute nouvelle préparation ? Si la VM existe, la reprise sera refusée.'))return;
   try{
    const result=await api('/dr/vms/'+id+'/lvm-lab/reconcile-provision','POST',{confirmation});
    toast('Réconciliation confirmée : activation #'+result.activation_id+' lancée');
    state.drProtectedVMs=(await api('/dr/vms')).items;
    await openPRALvmLabPanel(id);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-retry-allowlist'){
   const confirmation=document.getElementById('praLvmLabConfirmation')?.value?.trim();
   if(confirmation!=='DISPOSABLE-LAB-ONLY')return toast('Confirmation de recette LAB-ONLY requise',false);
   if(!confirm('Reprendre seulement l’échec local avant toute commande Proxmox ? Cette reprise reste interdite pour les copies incertaines.'))return;
   try{
    const result=await api('/dr/vms/'+id+'/lvm-lab/retry-local-error','POST',{confirmation});
    toast('Nouvelle activation #'+result.activation_id+' lancée');
    state.drProtectedVMs=(await api('/dr/vms')).items;
    await openPRALvmLabPanel(id);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-activate'){
   const confirmation=document.getElementById('praLvmLabConfirmation')?.value?.trim();
   if(confirmation!=='DISPOSABLE-LAB-ONLY')
    return toast('Confirmer exactement DISPOSABLE-LAB-ONLY pour lancer le PRA de recette',false);
   if(!confirm('Créer la réplique REP_ et démarrer la première vraie copie QMP/LVM à chaud ? '
      +'Le secours reste arrêté et la bascule réelle reste interdite.'))return;
   try{
    const result=await api('/dr/vms/'+id+'/lvm-lab/activate','POST',{confirmation});
    toast('Activation PRA de recette #'+result.activation_id+' lancée : consulter les journaux.');
    state.drProtectedVMs=(await api('/dr/vms')).items;
    await openPRALvmLabPanel(id);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-prepare'){
   const confirmation=document.getElementById('praLvmLabConfirmation')?.value?.trim();
   if(confirmation!=='DISPOSABLE-LAB-ONLY'){
    toast('Confirmer exactement DISPOSABLE-LAB-ONLY pour préparer la VM de recette',false);return;
   }
   if(!confirm('Créer une VM de secours vide, isolée et arrêtée sur Proxmox ? Aucun disque ni réseau ne sera ajouté.'))return;
   try{
    const result=await api('/dr/vms/'+id+'/lvm-lab/prepare-standby','POST',{confirmation});
    toast(result.created?'VM de secours #'+result.vmid+' créée et vérifiée':'VM de secours #'+result.vmid+' déjà préparée et vérifiée');
    state.drProtectedVMs=(await api('/dr/vms')).items;
    await openPRALvmLabPanel(id);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-run'){
   const mode=b.dataset.mode;
   const confirmation=document.getElementById('praLvmLabConfirmation')?.value?.trim();
   if(confirmation!=='DISPOSABLE-LAB-ONLY'){
    toast('Saisir exactement DISPOSABLE-LAB-ONLY pour confirmer la recette',false);
    return;
   }
   if(!['full','incremental'].includes(mode))return;
   if(!confirm('Confirmer une VRAIE copie '+mode.toUpperCase()+' QMP vers des LV LVM de RECETTE ? '
     +'Les volumes restent alloués et la VM de secours reste arrêtée. Aucun failover.'))return;
   try{
    const result=await api('/dr/vms/'+id+'/lvm-lab/replicate','POST',{mode,confirmation});
    toast('Copie LVM de recette #'+result.run_id+' lancée. Suivre son état ici.');
    state.drProtectedVMs=(await api('/dr/vms')).items;
    await openPRALvmLabPanel(id);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-test-boot'){
   const confirmation=document.getElementById('praLvmLabConfirmation')?.value?.trim();
   const restore_run_id=Number(b.dataset.restoreRun);
   if(confirmation!=='DISPOSABLE-LAB-ONLY')
    return toast('Confirmer exactement DISPOSABLE-LAB-ONLY pour tester le clone',false);
   if(!Number.isInteger(restore_run_id)||restore_run_id<=0)
    return toast('Aucun clone indépendant vérifié pour le dernier checkpoint',false);
   if(!confirm('Démarrer puis arrêter réellement le clone PRA_TEST_ sans réseau ? Le système invité ne sera pas validé.'))
    return;
   try{
    const result=await api('/dr/vms/'+id+'/lvm-lab/test-boot','POST',{restore_run_id,confirmation});
    toast('Test isolé QEMU #'+result.test_id+' enregistré. Suivre les journaux ici.');
    await openPRALvmLabPanel(id);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-lvm-lab-restore'){
   const confirmation=document.getElementById('praLvmLabConfirmation')?.value?.trim();
   const idText=document.getElementById('praLvmLabCloneVMID')?.value?.trim()||'';
   const test_vmid=Number(idText);
   if(confirmation!=='DISPOSABLE-LAB-ONLY'){
    toast('Confirmer exactement DISPOSABLE-LAB-ONLY pour le clone de recette',false);return;
   }
   if(!/^[0-9]{3,9}$/.test(idText)||!Number.isSafeInteger(test_vmid)||test_vmid<100){
    toast('Choisir un VMID Proxmox libre et valide pour le clone',false);return;
   }
   if(!confirm('Créer réellement la VM de test PRA_TEST_'+test_vmid+' sur la cible LAB ? '
     +'Cette opération alloue de nouveaux LV, prépare une VM sans réseau et ne la démarre pas.'))return;
   try{
    const result=await api('/dr/vms/'+id+'/lvm-lab/restore-clone','POST',{test_vmid,confirmation});
    toast('Préparation du clone isolé #'+result.run_id+' démarrée. Suivre le résultat ci-dessous.');
    state.drProtectedVMs=(await api('/dr/vms')).items;
    await openPRALvmLabPanel(id);
   }catch(ex){toast(ex.message,false)}
   return;
  }
  if(a==='dr-vm-storage-readiness'){
   toast('Analyse des capacités de stockage en lecture seule…');
   const r=await api('/dr/vms/'+id+'/storage-readiness');
   const t=r.target_storage||{};
   const fmtGiB=n=>Number.isFinite(n)?(n/1073741824).toFixed(1)+' Gio':'inconnue';
   const rows=(r.source_disks||[]).map(x=>'<tr><td>'+e(x.slot||'—')+'</td><td>'+e(x.storage||'—')
     +'</td><td>'+e(x.storage_type||'—')+'</td><td>'+e(fmtGiB(x.size_bytes))+'</td></tr>').join('');
   const issues=(r.checks||[]).map(x=>'<p class="hint">• '+e(x)+'</p>').join('');
   const avail=Number.isFinite(r.available_bytes)?Math.round(r.available_bytes/1073741824)+' Gio':'non vérifié';
   modal('Compatibilité stockage PRA · VM #'+e(r.source_vmid),
     '<div class="notice"><b>Lecture seule.</b> Aucune réplication, aucun disque ni démarrage déclenché. '
     +'Un snapshot compatible ne signifie pas que le PRA est opérationnel.</div>'
     +'<p><b>Destination :</b> '+e(t.storage||'—')+' · '+e(t.type||'—')
     +' · '+(t.shared?'partagé':'non partagé')+'</p>'
     +'<p><b>Adaptateur :</b> '+e(r.backend)+' · <b>Nœud :</b> '+e(r.target_node)
     +' · '+(r.node_online?'en ligne':'hors ligne')
     +' · <b>Stockage actif :</b> '+(r.node_storage_active?'oui':'non confirmé')+'</p>'
     +'<p><b>Espace disponible :</b> '+e(avail)+' · <b>Chaînes QCOW2 LVM :</b> '
     +(t.snapshot_as_volume_chain?'activées (préversion)':'non détectées')+'</p>'
     +'<p><b>Disques virtuels :</b> '+e(fmtGiB(r.source_total_virtual_bytes))
     +' · <b>Réserve minimale pour un nouveau checkpoint LVM :</b> '
     +e(fmtGiB(r.estimated_lvm_generation_bytes))+' (LV épaisses, estimation)</p>'
     +'<div class="table-wrap"><table><thead><tr><th>Disque source</th><th>Stockage</th><th>Type</th><th>Taille</th></tr></thead><tbody>'
     +rows+'</tbody></table></div>'
     +'<div class="notice error"><b>Réplication LVM non activée. Bascule interdite.</b>'+issues+'</div>',
     '<button class="btn primary" data-action="modal-close">Fermer</button>',true);
   return;
  }
  if(a==='dr-vm-edit'){openProtectedVMWizard(id);return}
 if(a==='dr-vm-preflight'||a==='dr-vm-failover'||a==='dr-vm-failback'){
  const scenario=a==='dr-vm-preflight'?'replication_preflight':
                 a==='dr-vm-failback'?'failback_simulation':'failover_simulation';
  toast('Tests Proxmox en lecture seule en cours…');
  const result=await api('/dr/vms/'+id+'/simulate','POST',{scenario});
  state.drProtectedVMs=(await api('/dr/vms')).items;
  showProtectedVMTest(result);return;
 }
 if(a==='dr-vm-qmp-reconcile'){
  const vm=(state.drProtectedVMs||[]).find(x=>x.id===id);
  if(!vm || vm.replication_status!=='uncertain' || !vm.last_replication_run?.id)return;
  if(!confirm('Réconcilier la synchronisation incertaine de '+vm.source_name+' ? Un checkpoint INCOMPLET peut être abandonné après contrôle des jobs QMP. La prochaine copie sera complète. Aucune VM ne démarrera.'))return;
  try{
   const result=await api('/dr/vms/'+id+'/replications/'+vm.last_replication_run.id+'/reconcile','POST',{approved:true});
   state.drProtectedVMs=(await api('/dr/vms')).items;
   render();
   toast('Réconciliation confirmée. Prochaine synchronisation complète requise.');
  }catch(ex){toast(ex.message,false)}
  return;
 }
 if(a==='dr-vm-qmp-preflight'){
  try{
   const x=await api('/dr/vms/'+id+'/replication-preflight');
   modal('Prévol QMP — VM #'+id,'<div class="notice"><b>Prévol API valide.</b> Source : '+e(x.source_node)+
    ' → cible : '+e(x.target_node)+' · stockage DIR : '+e(x.storage)+
    ' · disques : '+e(x.disks.join(', '))+
    '. Les vérifications root/QMP et la création de la réplique n’auront lieu qu’au lancement effectif.</div>',
    '<button class="btn primary" data-action="modal-close">Fermer</button>',true);
  }catch(ex){toast(ex.message,false)}
  return;
 }
 if(a==='dr-vm-qmp-sync'){
  const vm=(state.drProtectedVMs||[]).find(x=>x.id===id);
  if(!vm || vm.engine!=='qmp_live_incremental')return;
  if(!confirm('Lancer une vraie réplication QEMU à chaud de '+vm.source_name+' vers '+vm.target_node+' ? La VM source reste allumée ; une VM REP_ arrêtée peut être créée sur stockage DIR INSCRIPTIBLE. Cela entraîne des écritures et consomme du réseau et du stockage. Aucun failover ne sera démarré.'))return;
  try{
   const result=await api('/dr/vms/'+id+'/replicate','POST',{approved:true});
   state.drProtectedVMs=(await api('/dr/vms')).items;
   render();
   toast('Réplication #'+result.run_id+' demandée. Actualise pour suivre le transfert.');
  }catch(ex){toast(ex.message,false)}
  return;
 }
 if(a==='dr-vm-history'){await openProtectedVMHistory(id);return}
 if(a==='dr-vm-refresh'){
  state.drProtectedVMs=(await api('/dr/vms')).items;
  render();toast('États, checkpoints et derniers tests actualisés');return;
 }
 if(a==='dr-vm-pause'){
  const vm=(state.drProtectedVMs||[]).find(x=>x.id===id);
  if(!vm)return;
  const action=vm.state==='paused'?'planned':'paused';
  await api('/dr/vms/'+id+'/state','POST',{state:action});
  state.drProtectedVMs=(await api('/dr/vms')).items;
  render();toast('Préparation VM '+(action==='paused'?'en pause':'reprise')+' ; aucune réplication réelle déclenchée');return;
 }
 if(a==='dr-vm-delete'){
  if(!confirm('Retirer ce plan de protection VM ? Aucune VM ou disque Proxmox ne sera supprimé.'))return;
  await api('/dr/vms/'+id,'DELETE');state.drProtectedVMs=(await api('/dr/vms')).items;
  render();toast('Plan VM retiré ; aucune action Proxmox');return;
 }
 if(a==='dr-plan-new'){openDRPlanForm();return}
 if(a==='dr-plan-edit'){openDRPlanForm(id);return}
 if(a==='dr-plan-delete'){
  const plan=(state.drPlans||[]).find(p=>p.id===id);
  if(!plan)return;
  if(!confirm('Supprimer le groupe PRA '+plan.name+' ? Il doit être vide.'))return;
  await api('/dr/plans/'+id,'DELETE');
  toast('Groupe PRA supprimé');await refresh();return
 }
 if(a==='dr-site-new'){openDRSiteForm(null,Number(b.dataset.planId)||null);return}
 if(a==='dr-site-edit'){openDRSiteForm(id);return}
 if(a==='dr-site-delete'){
  if(!confirm('Supprimer cette association PRA ? Les clusters, les agents et les VM ne seront pas modifiés.'))return;
  await api('/dr/sites/'+id,'DELETE');toast('Site PRA supprimé sans action sur les clusters');await refresh();return
 }
 if(a==='dr-site-deploy-agent'){
  const site=(state.drSites||[]).find(x=>x.id===id);
  if(!site)return;
  agentDeployForm();
  const form=document.getElementById('agentDeployForm');
  if(form){
   form.elements.namedItem('group').value='dr_site_'+id;
   form.elements.namedItem('name').value='pra-'+site.name.replace(/[^a-zA-Z0-9_-]/g,'-').slice(0,60);
  }
  toast('Installer un agent standard : il faudra ensuite le rattacher au site. Le service PRA autonome n’est pas encore développé.');
  return
 }
 if(a==='dr-simulation-form'){openDRSimulationForm();return}
 if(a==='new-datacenter'){openDatacenterForm();return}if(a==='edit-datacenter'){openDatacenterForm(id);return}if(a==='test-datacenter'){const result=await api('/datacenters/'+id+'/test');modal('Diagnostic Proxmox · #'+id,'<div class="notice">'+e(result.message)+'</div>'+(result.checks||[]).map(v=>'<p><b>'+e(v.name)+'</b> : '+e(v.detail)+'</p>').join(''),'<button class="btn primary" data-action="modal-close">Fermer</button>');return}if(a==='discover-datacenter'){const result=await api('/datacenters/'+id+'/discover');openProxmoxDiscovery(id,result);return}
 if(a==='proxmox-discovery-prev'||a==='proxmox-discovery-next'){
   if(state.proxmoxDiscovery){state.proxmoxDiscovery.page+=a==='proxmox-discovery-next'?1:-1;renderProxmoxDiscoveryResults();}
   return
 }
 if(a==='proxmox-discovery-refresh'){
   if(!state.proxmoxDiscovery)return;
   const targetId=state.proxmoxDiscovery.dcId;
   const result=await api('/datacenters/'+targetId+'/discover');
   openProxmoxDiscovery(targetId,result);return
 }if(a==='assess-dr'){const vmid=Number(b.dataset.vmid);const data=await api('/datacenters/'+id+'/vms/'+vmid+'/dr-assessment');modal('Évaluation PRA · VM '+vmid,`<p class="notice">Lecture seule : aucune réplication n’a été lancée. Le statut PRA n’est pas validé.</p><p><b>${e(data.name||('VM '+vmid))}</b> — ${e(data.node)} (${e(data.source_status||'inconnu')})</p><h3>Disques / stockages</h3><ul>${data.disks.map(x=>`<li>${e(x.slot)} · ${e(x.storage||'inconnu')} · ${e(x.storage_type)} ${x.shared?'(partagé)':''}</li>`).join('')}</ul><h3>Modes à étudier</h3><ul>${data.options.map(x=>`<li><b>${e(x.engine)}</b> — ${e(x.purpose)}</li>`).join('')}</ul><h3>Risques / prérequis</h3><ul>${data.risks.map(r=>`<li>${e(r)}</li>`).join('')}</ul>`,`<button class="btn" data-action="modal-close">Fermer</button>`,true);return}if(a==='delete-datacenter'){if(!confirm('Supprimer ce datacenter ?'))return;await api('/datacenters/'+id,'DELETE');toast('Datacenter supprimé');await refresh();return}if(a==='filter'){state.filter=b.dataset.value;render();return}if(a==='create'){openEditor();return}if(a==='edit'){openEditor(id);return}if(a==='clone'){openEditor(id,true);return}if(a==='webhook'){await webhookManager(id);return}if(a==='rotate-webhook'){await rotateWebhook(id);return}if(a==='disable-webhook'){await disableWebhook(id);return}if(a==='modal-close'){closeModal();return}if(a==='run-job'){await runJob(id);return}if(a==='run-detail'){await openRun(id);return}if(a==='new-credential'){openCredentialBrowser(null,'new');return}if(a==='save-job'){await saveEditor();return}if(a==='select-step'){captureEditor();state.selectedStep=Number(b.dataset.index);renderEditor();return}if(a==='add-failure-step'){
  captureEditor();
  const step=state.editor.steps_json[state.selectedStep];
  const next=addStep();
  next.name='Si échec · '+step.name.slice(0,35);
  next.on_success=step.on_failure&&step.on_failure!=='FAIL'&&step.on_failure!=='END'?
     step.on_failure:'FAIL';
  step.on_failure=next.id;
  state.editor.steps_json.push(next);
  state.selectedStep=state.editor.steps_json.length-1;
  renderEditor();
  toast('Étape liée à la branche d’échec');return
 }
 if(a==='add-step'){captureEditor();state.editor.steps_json.push(addStep());state.selectedStep=state.editor.steps_json.length-1;renderEditor();return}if(a==='step-up'||a==='step-down'){captureEditor();const arr=state.editor.steps_json,i=state.selectedStep,j=i+(a==='step-up'?-1:1);if(j>=0&&j<arr.length){[arr[i],arr[j]]=[arr[j],arr[i]];state.selectedStep=j}renderEditor();return}if(a==='delete-step'){captureEditor();if(state.editor.steps_json.length<=1)return toast('Un workflow doit conserver une étape dans cet éditeur',false);const removed=state.editor.steps_json.splice(state.selectedStep,1)[0];state.editor.steps_json.forEach(x=>{for(const edge of ['on_success','on_failure'])if(x[edge]===removed.id)x[edge]=''});state.selectedStep=Math.min(state.selectedStep,state.editor.steps_json.length-1);renderEditor();return}if(a==='delete-job'){if(!confirm('Supprimer définitivement ce workflow ? Son historique sera conservé.'))return;await api('/jobs/'+id,'DELETE');closeModal();toast('Workflow supprimé');await refresh();showPage('jobs');return}if(a==='delete-credential'){if(!confirm('Supprimer ce secret ? Les workflows qui l’utilisent ne pourront plus s’authentifier.'))return;await api('/credentials/'+id,'DELETE');toast('Secret supprimé');await refresh();return}if(a==='refresh'){await refresh();return}if(a==='update-request'){
  if(b.dataset.value==='apply'&&!confirm('Installer le dernier commit de la branche Git AUTORISÉE puis redémarrer les services ? Une sauvegarde SQLite est créée mais toutes les migrations ne sont pas nécessairement réversibles.'))return;
  await api('/updates/request','POST',{action:b.dataset.value});
  toast(b.dataset.value==='preflight'?'Simulation demandée : aucune installation ne sera effectuée':'Demande envoyée au service de maintenance');
  hydrateAdmins();return
 }if(a==='refresh-updates'){hydrateAdmins();return}}catch(ex){toast(ex.message,false)}}
async function handleSubmit(ev){
 if(ev.target.id==='drVMSourceForm'){
  ev.preventDefault();
  const f=new FormData(ev.target);
  const button=ev.target.querySelector('[type=submit]');
  if(button)button.disabled=true;
  try{
   await openProtectedVMConfig(Number(f.get('dr_vm_plan')),
    Number(f.get('dr_vm_source_dc')),Number(f.get('dr_vm_target_dc')));
  }catch(ex){toast(ex.message,false);if(button)button.disabled=false}
  return;
 }
 if(ev.target.id==='drVMConfigForm'){
  ev.preventDefault();
  const r=state.drVMOptions,f=new FormData(ev.target);
  if(!r)return toast('Découverte de VM expirée, recommencer.',false);
  const currentId=Number(ev.target.dataset.id)||0;
  const body={
   plan_id:r.plan_id,source_dc_id:r.source_dc_id,target_dc_id:r.target_dc_id,
   source_vmid:Number(f.get('source_vmid')),target_vmid:Number(f.get('target_vmid')),
   target_node:String(f.get('target_node')||''),target_storage:String(f.get('target_storage')||''),
   interval_minutes:Number(f.get('interval_minutes')),engine:String(f.get('engine')),
   reference:String(f.get('reference')||'').trim(),protection_note:String(f.get('protection_note')||'').trim()
  };
  if((r.target_vmids_in_use||[]).includes(body.target_vmid)&&!currentId)
   return toast('VMID cible déjà utilisé sur le dernier inventaire : sélectionner un VMID libre',false);
  if(!currentId && f.get('activate_lab')==='yes'){
   if(body.engine!=='qmp_live_incremental')return toast('Choisir le moteur QMP pour amorcer la protection',false);
   if(String(f.get('lab_confirmation')||'').trim()!=='DISPOSABLE-LAB-ONLY')
    return toast('Confirmer exactement DISPOSABLE-LAB-ONLY pour autoriser la copie de recette',false);
   body.activate_lab=true;
   body.lab_confirmation='DISPOSABLE-LAB-ONLY';
  }
  const button=ev.target.querySelector('[type=submit]');
  if(button)button.disabled=true;
  try{
   const result=await api(currentId?'/dr/vms/'+currentId:'/dr/vms',
     currentId?'PUT':'POST',body);
   closeModal();state.drProtectedVMs=(await api('/dr/vms')).items;
   state.drVMPlanFilter=String(body.plan_id);
   showPage('protected-vms');
   toast(result.activation
    ?'VM #'+body.source_vmid+' enregistrée : préparation REP_ et copie initiale lancées, consulter Réplication LVM'
    :'VM #'+body.source_vmid+' enregistrée : aucune copie lancée');
  }catch(ex){toast(ex.message,false);if(button)button.disabled=false}
  return;
 }

 if(ev.target.id==='drPlanForm'){
  ev.preventDefault();
  const f=new FormData(ev.target),id=Number(ev.target.dataset.id)||0;
  try{
   const result=await api(id?'/dr/plans/'+id:'/dr/plans',id?'PUT':'POST',{
    name:String(f.get('dr_plan_name')||'').trim(),
    description:String(f.get('dr_plan_description')||'').trim()
   });
   // Fetch these two resources independently of all other admin APIs;
   // one unrelated vault/API error must not leave a stale group title.
   state.drPlans=(await api('/dr/plans')).items;
   state.drSites=(await api('/dr/sites')).items;
   closeModal();showPage('datacenters');
   toast('Groupe PRA '+(result.plan?.name||String(f.get('dr_plan_name')||''))+' enregistré et actualisé');
  }catch(ex){toast(ex.message,false)}
  return
 }

 if(ev.target.id==='agentConnectionSettingsForm'){
  ev.preventDefault();
  const f=new FormData(ev.target);
  const input=ev.target.querySelector('button[type="submit"]');
  if(input)input.disabled=true;
  try{
   const result=await api('/admin/agent-settings','PUT',{
    controller_url:String(f.get('controller_url')||'').trim()
   });
   state.agentSettings=result;
   toast('URL HTTPS des agents enregistrée. Les prochains déploiements utiliseront '+result.controller_url);
   render();
  }catch(ex){toast(ex.message,false);if(input)input.disabled=false}
  return
 }

 if(ev.target.id==='proxmoxFleetGroupForm'){
  ev.preventDefault();
  const form=ev.target,f=new FormData(form),dcId=Number(form.dataset.id);
  const button=form.querySelector('[type="submit"]');if(button)button.disabled=true;
  try{
   const selected=f.getAll('fleet_node').map(String);
   if(!selected.length)throw Error('Sélectionner au moins un nœud pour le groupe.');
   const result=await api('/datacenters/'+dcId+'/ssh-group','POST',{
    domain_suffix:String(f.get('domain_suffix')||'').trim(),nodes:selected
   });
   state.sshInventory=await api('/ssh/inventory');
   toast('Groupe SSH #'+result.group_id+' lié au cluster · '+result.count+' nœud(s)');
   await openClusterAgentWizard(dcId,String(f.get('domain_suffix')||'').trim());
  }catch(ex){toast(ex.message,false);if(button)button.disabled=false}
  return
 }
 if(ev.target.id==='proxmoxFleetDeployForm'){
  ev.preventDefault();
  const form=ev.target,f=new FormData(form),dcId=Number(form.dataset.id);
  const fleet=state.proxmoxFleet;
  if(!fleet||fleet.datacenter_id!==dcId||!fleet.group_id)return toast('Synchroniser le groupe avant installation.',false);
  const ids=f.getAll('fleet_server').map(Number);
  if(!ids.length)return toast('Sélectionner les nœuds à équiper.',false);
  if(!f.get('credential_id'))return toast('Choisir une clé SSH du coffre.',false);
  if(!state.agentSettings?.configured)return toast('Configurer d’abord l’URL HTTPS des agents depuis Agents → Paramètres.',false);
  if(!f.has('approved'))return toast('Confirmer explicitement le déploiement.',false);
  if(f.has('enable_pra')&&!f.has('enable_proxmox'))return toast('Activer Proxmox avant le module PRA passif.',false);
  if(!confirm('Installer l’agent unique OrigamiTasks sur '+ids.length+' nœud(s) du cluster '+fleet.datacenter+' ?')){
   return
  }
  const button=form.querySelector('[type="submit"]');if(button)button.disabled=true;
  try{
   const result=await api('/datacenters/'+dcId+'/agents/deploy','POST',{
    group_id:fleet.group_id,server_ids:ids,
    credential_id:Number(f.get('credential_id')),
    enable_proxmox:f.has('enable_proxmox'),enable_pra:f.has('enable_pra'),
    approved:true
   });
   closeModal();
   toast('Lot #'+result.batch_id+' lancé : '+result.total+' nœud(s).');
   await refresh();showPage('agents');
  }catch(ex){toast(ex.message,false);if(button)button.disabled=false}
  return
 }

 if(ev.target.id==='agentModulesForm'){
  ev.preventDefault();
  const f=new FormData(ev.target),id=Number(ev.target.dataset.id);
  const body={standard:true,proxmox:f.has('proxmox'),pra:f.has('pra')};
  const btn=ev.target.querySelector('[type="submit"]');
  if(btn)btn.disabled=true;
  try{
   const result=await api('/agents/'+id+'/modules','PUT',body);
   closeModal();toast('Modules enregistrés · révision '+result.revision+' ; attente du prochain heartbeat');
   await refresh();
  }catch(ex){toast(ex.message,false);if(btn)btn.disabled=false}
  return;
 }

 if(ev.target.id==='agentUninstallForm'){
  ev.preventDefault();
  const f=new FormData(ev.target),agentId=Number(ev.target.dataset.id);
  const agent=(state.agents||[]).find(x=>x.id===agentId);
  if(!agent||String(f.get('uninstall_confirmation')||'')!==agent.name)
   return toast('Saisir le nom exact de l’agent pour confirmer',false);
  if(!f.has('uninstall_approved'))return toast('Confirmer explicitement la désinstallation',false);
  const host=String(f.get('uninstall_host')||'').trim();
  if(!confirm('Désinstaller réellement '+agent.name+' de '+host+' ? Cette opération arrête son service et révoque son token.'))
   return;
  const button=ev.target.querySelector('[type="submit"]');
  if(button)button.disabled=true;
  try{
   await api('/agents/'+agentId+'/uninstall','POST',{
    host,port:Number(f.get('uninstall_port')||22),
    credential_id:Number(f.get('uninstall_credential')||0),
    installed_controller_url:String(f.get('installed_controller_url')||'').trim(),approved:true
   });
   closeModal();toast('Agent désinstallé sur la cible et token révoqué');
   await refresh();
  }catch(ex){toast(ex.message,false);if(button)button.disabled=false}
  return
 }

 if(ev.target.id==='drSiteForm'){
  ev.preventDefault();
  const f=new FormData(ev.target),id=Number(ev.target.dataset.id)||0;
  const body={plan_id:Number(f.get('dr_plan'))||null,name:String(f.get('dr_site_name')||'').trim(),
              role:String(f.get('dr_role')||'other'),
              storage_profile:String(f.get('dr_storage')||'auto'),
              datacenter_ids:f.getAll('dr_dc').map(Number),
              agent_ids:f.getAll('dr_agent').map(Number)};
  try{
   await api(id?'/dr/sites/'+id:'/dr/sites',id?'PUT':'POST',body);
   closeModal();toast(id?'Site PRA mis à jour':'Site PRA déclaré');await refresh();showPage('datacenters');
  }catch(ex){toast(ex.message,false)}
  return
 }
 if(ev.target.id==='drSimulationForm'){
  ev.preventDefault();
  const f=new FormData(ev.target),selectedPlan=Number(f.get('dr_sim_plan'));
  const src=(state.drSites||[]).find(s=>s.id===Number(f.get('dr_source')));
  const dst=(state.drSites||[]).find(s=>s.id===Number(f.get('dr_target')));
  if(!src||!dst||src.plan_id!==selectedPlan||dst.plan_id!==selectedPlan)
   return toast('Simulation entre groupes PRA différents interdite',false);
  try{
   const result=await api('/dr/simulate','POST',{
    source_site_id:Number(f.get('dr_source')),target_site_id:Number(f.get('dr_target')),
    scenario:String(f.get('dr_scenario'))
   });
   state.drSimulations=(await api('/dr/simulations')).items;
   showDRSimulationResult(result);
  }catch(ex){toast(ex.message,false)}
  return
 }

 if(ev.target.id==='sshGroupForm'){
  ev.preventDefault();
  const f=new FormData(ev.target),id=Number(ev.target.dataset.id),name=String(f.get('name')||'').trim();
  if(!name)return toast('Renseigner un nom de groupe',false);
  const payload={name,description:String(f.get('description')||'').trim()};
  if(ev.target.dataset.managed!=='1')payload.servers=String(f.get('servers')||'').split(/[\r\n,]+/).map(x=>x.trim()).filter(Boolean);
  try{
   await api(id?'/ssh/groups/'+id:'/ssh/groups',id?'PUT':'POST',payload);
   closeLayer('sshInventoryLayer');toast('Groupe SSH enregistré');await refresh();
  }catch(ex){toast(ex.message,false)}return
 }
 if(ev.target.id==='sshServerForm'){
  ev.preventDefault();
  const f=new FormData(ev.target),id=Number(ev.target.dataset.id);
  const payload={fqdn:String(f.get('fqdn')||'').trim(),port:Number(f.get('port')||22),
                 label:String(f.get('label')||'').trim(),
                 group_ids:f.getAll('group_id').map(Number)};
  try{
   await api(id?'/ssh/servers/'+id:'/ssh/servers',id?'PUT':'POST',payload);
   closeLayer('sshInventoryLayer');toast('Serveur SSH enregistré');await refresh();
  }catch(ex){toast(ex.message,false)}return
 }

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
    if(kind==='proxmox'&&f.get('proxmox_ca_file')?.size){const ca=await registerProxmoxCA(f.get('proxmox_ca_file'));payload.ca_cert=ca.id;}
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

 if(ev.target.id==='agentBulkDeployForm'){
  ev.preventDefault();const f=new FormData(ev.target);
  if(!f.has('approved'))return toast('Confirmation requise',false);
  const hosts=String(f.get('targets')||'').split(/[\r\n,]+/).map(x=>x.trim()).filter(Boolean);
  if(!hosts.length||hosts.length>100)return toast('La liste doit contenir entre 1 et 100 FQDN',false);
  if(!f.get('credential_id'))return toast('Choisir une identité SSH du coffre',false);
  const button=ev.target.querySelector('button[type="submit"]');
  if(button)button.disabled=true;
  try{
   const result=await api('/agents/deploy/batch','POST',{
    platform:String(f.get('platform')),credential_id:Number(f.get('credential_id')),
    agent_group:String(f.get('agent_group')||'default'),
    targets:hosts.map(host=>({host})),approved:true
   });
   closeModal();toast('Lot #'+result.batch_id+' accepté : '+result.total+' serveur(s)');
   await refresh();showPage('agents');
  }catch(ex){toast(ex.message,false);if(button)button.disabled=false}
  return
 }
 if(ev.target.id==='agentUpdateForm'){
  ev.preventDefault();const f=new FormData(ev.target);
  const ids=String(f.get('agent_ids')||'').split(',').map(Number);
  if(!f.has('approved'))return toast('Confirmation requise',false);
  try{
   const result=await api('/agents/updates','POST',{agent_ids:ids,credential_id:Number(f.get('credential_id')),approved:true});
   closeModal();state.agentUpdates=await api('/agents/updates');render();
   toast(result.queued+' mise(s) à jour en file');
  }catch(ex){toast(ex.message,false)}return;
 }
 if(ev.target.id==='agentDeployForm'){ev.preventDefault();const f=new FormData(ev.target);if(!f.get('approved'))return toast('Confirmation requise',false);try{const r=await api('/agents/deploy','POST',{name:f.get('name')||f.get('host'),host:f.get('host'),port:Number(f.get('port')||22),platform:f.get('platform'),group:f.get('group'),credential_id:Number(f.get('credential_id')),approved:true});closeModal();toast('Agent #'+r.id+' : installation envoyée');await refresh()}catch(ex){toast(ex.message,false)}return}
 if(ev.target.id==='datacenterForm'){
  ev.preventDefault();
  const f=new FormData(ev.target),id=Number(ev.target.dataset.id)||0;
  const submit=ev.target.querySelector('button[type="submit"]');
  if(submit)submit.disabled=true;
  try{
   const payload={name:String(f.get('dc_name')||'').trim(),
                  kind:String(f.get('dc_kind')||'cluster'),
                  url:String(f.get('dc_url')||'').trim(),
                  ca_cert:String(f.get('dc_ca_cert')||''),
                  skip_tls_verification:f.has('dc_skip_tls')};
   const file=f.get('dc_ca_file');
   if(file&&file.size){
    const ca=await registerProxmoxCA(file);
    payload.ca_cert=ca.id;
   }
   const currentDc=id?(state.datacenters||[]).find(x=>x.id===id):null;
   if(payload.skip_tls_verification&&!currentDc?.skip_tls_verification&&!confirm('La vérification du certificat TLS sera désactivée pour ce serveur ou cluster Proxmox. L’identité du serveur ne pourra plus être authentifiée. Continuer ?')){if(submit)submit.disabled=false;return;}
   if(f.get('dc_auth_mode')==='new'){
    payload.token_username=String(f.get('dc_token_username')||'').trim();
    payload.token_secret=String(f.get('dc_token_secret')||'');
   }else payload.credential_id=Number(f.get('dc_credential')||0);
   await api(id?'/datacenters/'+id:'/datacenters',id?'PUT':'POST',payload);
   closeModal();toast(id?'Serveur / cluster mis à jour':'Serveur / cluster Proxmox ajouté');
   await refresh();showPage('datacenters');
  }catch(ex){toast(ex.message,false);if(submit)submit.disabled=false}
  return
 }if(ev.target.id==='loginForm'){ev.preventDefault();const form=new FormData(ev.target);$('#authError').textContent='';try{await api('/login','POST',{username:form.get('username'),password:form.get('password')});const r=await api('/me');state.user=r.user;$('#auth').hidden=true;$('#application').hidden=false;$('#userAvatar').textContent=initials();$('#adminNav').hidden=!roleAdmin();if(!roleAdmin())$$('.side-admin').forEach(x=>x.hidden=true);await refresh();showPage('dashboard')}catch(ex){$('#authError').textContent=ex.message}}else if(ev.target.id==='credentialForm'){ev.preventDefault();const f=new FormData(ev.target);const payload={};for(const k of ['username','password','token','private_key','passphrase'])if(f.get(k)?.trim())payload[k]=f.get(k);try{await api('/credentials','POST',{name:f.get('name'),kind:f.get('kind'),payload});closeModal();toast('Secret chiffré et enregistré');await refresh()}catch(ex){toast(ex.message,false)}}}
function initTheme(){let theme=localStorage.getItem('origami-theme')||'dark';document.body.className='theme-'+theme;$('#themeToggle').textContent={dark:'☾',light:'☀',cyber:'✧'}[theme]||'☾'}
async function bootstrap(){document.addEventListener('submit',handleSubmit);
 document.addEventListener('pointerdown',graphPanStart);
 document.addEventListener('pointermove',graphPanMove);
 document.addEventListener('pointerup',graphPanStop);
 document.addEventListener('pointercancel',graphPanStop);
 document.addEventListener('wheel',ev=>{
  if(!ev.ctrlKey||!ev.target.closest('#workflowGraph'))return;
  ev.preventDefault();
  graphZoomTo((state.graphZoom||1)*(ev.deltaY>0?0.9:1.1),ev.clientX,ev.clientY);
 },{passive:false});
try{initTheme();decorateSidebar()}catch(ex){console.error('OrigamiTasks initialisation:',ex)}try{const r=await api('/me');state.user=r.user;$('#auth').hidden=true;$('#application').hidden=false;$('#adminNav').hidden=!roleAdmin();if(!roleAdmin())$$('.side-admin').forEach(x=>x.hidden=true);$('#userAvatar').textContent=initials();await load();showPage('dashboard')}catch{$('#auth').hidden=false;$('#application').hidden=true}document.addEventListener('click',handleClick);document.addEventListener('input',ev=>{
 if(ev.target.closest('#drVMConfigForm')&&ev.target.name==='target_vmid'){
  const status=document.getElementById('praVMIDStatus'),occupied=state.drVMOptions?.target_vmids_in_use||[];
  if(status){const n=Number(ev.target.value);
    status.textContent=Number.isInteger(n)&&n>=100
       ?(occupied.includes(n)?'VMID occupé dans le dernier inventaire : utiliser Vérifier VMID':'VMID apparemment libre — vérifier avant la création')
       :'VMID non valide';
  }
 }
 if(ev.target.id==='praVMSearch'){
  const selector=document.querySelector('#drVMConfigForm [name="source_vmid"]'),q=ev.target.value.toLowerCase().trim();
  if(selector){const prev=selector.value;
    const choices=(state.drVMOptions?.vms||[]).filter(v=>(v.name+' '+v.vmid+' '+v.node).toLowerCase().includes(q))
       .sort((a,b)=>a.name.localeCompare(b.name,'fr',{sensitivity:'base',numeric:true}));
    selector.innerHTML=option(choices.map(v=>[v.vmid,v.name+' — #'+v.vmid+' · '+v.node+' ['+v.status+']']),prev);
    if(choices.some(v=>String(v.vmid)===prev))selector.value=prev;
  }
 }
 if(ev.target.id==='credentialSearch'){state.credentialSearch=ev.target.value;filterCredentialRows()}
 if(ev.target.matches('#credentialTestForm [name="manual_host"]')){
  const panel=document.getElementById('sshHostTrustPanel');
  if(panel)panel.innerHTML='';
 }
 if(ev.target.matches('[data-ssh-filter-search]'))sshRefreshPicker(ev.target.closest('[data-ssh-picker]'));
 if(ev.target.id==='sshInventorySearch'){state.sshInventorySearch=ev.target.value;
  $('.ssh-server-row[data-ssh-search]').forEach(row=>row.hidden=!row.dataset.sshSearch.includes(ev.target.value.toLowerCase()));
 }
 if(ev.target.id==='groupSearch'){state.groupQuery=ev.target.value;clearTimeout(state.groupTimer);state.groupTimer=setTimeout(searchGroups,250)}
 if(ev.target.id==='proxmoxResourceSearch'&&state.proxmoxDiscovery){state.proxmoxDiscovery.query=ev.target.value;state.proxmoxDiscovery.page=0;renderProxmoxDiscoveryResults();}
 if(ev.target.id==='proxmoxDcSearch'){const q=ev.target.value.toLowerCase().trim();$$('[data-dc-search]').forEach(card=>card.hidden=!card.dataset.dcSearch.includes(q));}
 if(ev.target.id==='catalogSearch'){const q=ev.target.value.toLowerCase().trim();$$('.catalog-search-item').forEach(card=>card.hidden=!card.dataset.search.includes(q));}});document.addEventListener('change',async ev=>{
 if(ev.target.id==='drVMPlanFilter'){
  state.drVMPlanFilter=String(ev.target.value||'all');render();return;
 }
 if(ev.target.name==='source_vmid'&&ev.target.closest('#drVMConfigForm')){
  const form=ev.target.closest('form'),r=state.drVMOptions;
  const vm=(r?.vms||[]).find(x=>x.vmid===Number(ev.target.value));
  if(vm&&!state.drVMWizard?.existingId){
   const used=new Set(r.target_vmids_in_use||[]);
   let candidate=vm.vmid;
   while(used.has(candidate)&&candidate<999999999)candidate++;
   if(!used.has(candidate)){
    form.elements.namedItem('target_vmid').value=candidate;
    const status=document.getElementById('praVMIDStatus');
    if(status)status.textContent='Proposition libre #'+candidate+' (dernier inventaire) — vérification en temps réel disponible';
   }
  }
  return;
 }
 if(ev.target.name==='dr_vm_plan'&&ev.target.closest('#drVMSourceForm')){
  const form=ev.target.closest('form'),planId=Number(ev.target.value);
  const src=drVMClusters(planId,'primary'),dst=drVMClusters(planId,'standby');
  form.elements.namedItem('dr_vm_source_dc').innerHTML=option(src.map(x=>[x.id,x.name]),src[0]?.id);
  form.elements.namedItem('dr_vm_target_dc').innerHTML=option(dst.map(x=>[x.id,x.name]),dst[0]?.id);
  return;
 }

 if(ev.target.name==='dr_sim_plan'&&ev.target.closest('#drSimulationForm')){
  const planId=Number(ev.target.value);
  const sites=(state.drSites||[]).filter(site=>site.plan_id===planId);
  const form=ev.target.closest('form');
  const sources=form.elements.namedItem('dr_source'),targets=form.elements.namedItem('dr_target');
  if(sources&&targets){
   const options=sites.map(site=>[site.id,site.name+' ('+site.role+')']);
   sources.innerHTML=option(options,sites.find(s=>s.role==='primary')?.id||sites[0]?.id);
   targets.innerHTML=option(options,sites.find(s=>s.role==='standby')?.id||sites[1]?.id);
   sources.value=String(sites.find(s=>s.role==='primary')?.id||sites[0]?.id||'');
   targets.value=String(sites.find(s=>s.role==='standby')?.id||sites[1]?.id||'');
  }
 }
 if(ev.target.matches('[data-ssh-filter-group]'))sshRefreshPicker(ev.target.closest('[data-ssh-picker]'));
 if(ev.target.id==='sshInventoryGroupFilter'){state.sshInventoryGroupFilter=ev.target.value;render();}
 if(ev.target.closest('#credentialTestForm')){
   if(ev.target.matches('[name="ssh_target_mode"],[name="manual_host"],[name="manual_port"],[name="server_id"],[data-ssh-filter-group]')){
    const panel=document.getElementById('sshHostTrustPanel');
    if(panel)panel.innerHTML='';
   }
   updateCredentialTestVisibility();
  }
 if(ev.target.name==='kind'&&ev.target.closest('#credentialManageForm')){
  document.getElementById('credentialProfile').innerHTML=credentialProfileFields(ev.target.value);
  smtpAuthFieldsVisibility();
 }
 if(ev.target.id==='sshPrivateKeyFile'){
  const file=ev.target.files?.[0],textarea=ev.target.closest('form')?.elements.namedItem('private_key');
  if(!file||!textarea)return;
  if(file.size>24576){toast('Clé privée trop volumineuse (24 Kio max.)',false);ev.target.value='';return}
  try{
   const raw=new Uint8Array(await file.arrayBuffer());
   const content=new TextDecoder('utf-8',{fatal:true}).decode(raw);
   if(!content.includes('-----BEGIN ')||!content.includes('PRIVATE KEY-----'))
    throw Error('Ce fichier ne contient pas une clé privée OpenSSH/PEM');
   textarea.value=content;
   toast('Fichier de clé privée chargé localement ; enregistrer le coffre pour le conserver');
  }catch(ex){toast(ex.message||'Clé SSH illisible',false)}
 }
 if(ev.target.name==='auth'&&ev.target.closest('#credentialManageForm'))smtpAuthFieldsVisibility();
 if(ev.target.name==='tls_mode'&&ev.target.closest('#credentialManageForm')&&ev.target.closest('form').elements.namedItem('kind')?.value==='smtp'){
  const port=ev.target.closest('form').elements.namedItem('port');
  if(port&&['25','465','587'].includes(port.value))port.value=ev.target.value==='ssl'?465:ev.target.value==='none'?25:587;
 }
 if(ev.target.name==='cfg_connection_mode'&&state.editor){captureEditor();renderEditor();return}
 if(ev.target.name==='job_interval_preset'){applySchedulePreset();return}
 if(['proxmoxResourceType','proxmoxResourceSort','proxmoxResourceDirection'].includes(ev.target.id)&&state.proxmoxDiscovery){
  const d=state.proxmoxDiscovery;
  d.type=document.getElementById('proxmoxResourceType').value;
  d.sort=document.getElementById('proxmoxResourceSort').value;
  d.descending=document.getElementById('proxmoxResourceDirection').value==='desc';
  d.page=0;renderProxmoxDiscoveryResults();
 }
 if(ev.target.name==='dc_auth_mode'&&ev.target.closest('#datacenterForm'))updateProxmoxAuthFields();if(ev.target.name==='job_schedule_mode')showScheduleMode();if(ev.target.name==='step_type'&&state.editor){const s=state.editor.steps_json[state.selectedStep];const newType=ev.target.value;if(s.type!==newType){captureEditor();s.config=CONNECTION_STEP_TYPES[newType]?{connection_mode:'vault'}:{};s.type=newType;renderEditor()}}});$('#themeToggle').addEventListener('click',()=>{const themes=['dark','light','cyber'],current=document.body.className.replace('theme-',''),next=themes[(themes.indexOf(current)+1)%themes.length];localStorage.setItem('origami-theme',next);initTheme()});$('#logoutBtn').addEventListener('click',async()=>{try{await api('/logout','POST')}catch{}state.user=null;$('#application').hidden=true;$('#auth').hidden=false;$('#loginForm').reset();showPage('dashboard')});$('#globalSearch').addEventListener('input',ev=>{state.query=ev.target.value;if(state.page!=='jobs'&&state.query)showPage('jobs');else if(state.page==='jobs')render()});$('#menuToggle').addEventListener('click',()=>$('.sidebar').classList.toggle('visible'));document.addEventListener('keydown',ev=>{if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='k'){ev.preventDefault();$('#globalSearch').focus()}if(ev.key==='Escape'&&document.getElementById('groupLayer')){closeLayer('groupLayer');renderEditor()}else if(ev.key==='Escape'&&document.getElementById('credentialLayer'))closeLayer('credentialLayer');else if(ev.key==='Escape'&&document.getElementById('folderLayer'))closeLayer('folderLayer');else if(ev.key==='Escape'&&$('.modal-overlay'))closeModal()});setInterval(async()=>{if(!state.user||$('.modal-overlay'))return;if(['dashboard','runs','jobs'].includes(state.page)){try{await refresh()}catch{}}},7000)}
bootstrap();
