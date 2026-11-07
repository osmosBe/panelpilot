'use strict';
const $ = (id) => document.getElementById(id);
const fields = [...document.querySelectorAll('[data-setting]')];
const numeric = new Set(['dashboard_brightness','photo_brightness','idle_seconds','slide_seconds']);
let original = null, revision = '', csrf = '', busy = false, poll = null, currentView = 'overview';
const titles = {overview:'Übersicht', display:'Anzeige & Fotos', storage:'Fotospeicher', updates:'Updates & Neustart', dashboard:'Home Assistant', voice:'Sprachassistent', access:'Zugang'};
let systemState=null, localState=null, systemDirty=false, uploadBusy=false, brightnessSupported=true;

function notice(text, kind='') {
  $('message').textContent = text; $('message').className = 'notice '+kind; $('message').hidden = !text;
}
async function api(path, method='GET', body) {
  const options = {method, credentials:'same-origin', headers:{}};
  if (method !== 'GET') {
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-CSRF-Token'] = csrf;
    options.body = JSON.stringify(body || {});
  }
  const result = await fetch('/api/'+path, options);
  const value = await result.json().catch(() => ({}));
  if (!result.ok) {
    if (result.status === 401 && path !== 'login') showLogin('Bitte erneut anmelden.');
    const details = value.fields?.map(e => e.message).join(' ');
    const error = new Error(details || value.detail || 'Die Anfrage ist fehlgeschlagen.');
    error.status = result.status; throw error;
  }
  return value;
}
function readForm() {
  return Object.fromEntries(fields.map(input => [input.id, input.type === 'checkbox' ? input.checked : numeric.has(input.id) ? Number(input.value) : input.value.trim()]));
}
function changed() {
  const value = readForm();
  return original ? Object.keys(value).filter(key => value[key] !== original[key]).length : 0;
}
function syncForm() {
  for (const id of ['dashboard_brightness','photo_brightness']) $(id+'-value').textContent = $(id).value+' %';
  const count = changed();
  $('save').disabled = busy || !count; $('discard').disabled = busy || !count;
  $('save-state').textContent = busy ? 'Wird übernommen …' : count ? `${count} ungespeicherte ${count === 1 ? 'Änderung' : 'Änderungen'}` : 'Alles gespeichert';
  $('save-indicator').classList.toggle('dirty', count > 0);
  $('save-caption').textContent = count ? 'Noch nicht auf dem Panel aktiv.' : 'Änderungen werden gemeinsam übernommen.';
  $('check-photos').disabled = busy || $('photo_directory').value.trim() !== original?.photo_directory;
}
function fill(value) {
  original = {...value.settings}; revision = value.revision;
  fields.forEach(input => input.type === 'checkbox' ? input.checked = original[input.id] : input.value = original[input.id]);
  $('url-summary').textContent = original.dashboard_url;
  $('brightness-summary').textContent = original.photo_brightness+' %';
  $('interval-summary').textContent = `Alle ${original.slide_seconds} Sekunden`;
  $('rotation-summary').textContent = original.auto_rotate ? 'Eingeschaltet' : 'Ausgeschaltet';
  $('idle-summary').textContent = original.idle_mode === 'off' ? 'Dashboard bleibt sichtbar' : `Nach ${original.idle_seconds < 60 ? original.idle_seconds+' Sekunden' : (original.idle_seconds/60).toLocaleString('de', {maximumFractionDigits:1})+' Minuten'}`;
  syncForm();
}
function navigate(view) {
  if (!(view in titles)) view = 'overview';
  currentView = view;
  document.querySelectorAll('[data-view]').forEach(section => section.hidden = section.dataset.view !== view);
  document.querySelectorAll('[data-nav]').forEach(button => {
    button.classList.toggle('selected', button.dataset.nav === view);
    if (button.dataset.nav === view) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  });
  $('breadcrumb').textContent = titles[view];
  $('save-bar').hidden = ['access','updates','storage'].includes(view) && !changed();
  history.replaceState(null,'','#'+view);
}
async function status() {
  if (document.hidden || $('app-view').hidden) return;
  try {
    const value = await api('status');
    brightnessSupported=value.capabilities?.backlight !== false;
    $('dashboard_brightness').disabled=!brightnessSupported || busy;
    $('brightness-capability').hidden=brightnessSupported;
    $('voice_enabled').disabled=value.capabilities?.voice===false || busy;
    if(value.capabilities?.voice===false){$('voice-status').textContent='Nicht installiert';}
    $('session-tag').textContent = value.panel_active ? 'Panelsitzung aktiv' : 'Panel im Hintergrund';
    const names = {active:'Läuft', inactive:'Ausgeschaltet', failed:'Fehler', activating:'Wartet auf Start', deactivating:'Wird beendet'};
    for (const role of ['kiosk','voice','display']) $(role+'-status').textContent = names[value.services[role]?.ActiveState] || 'Nicht erreichbar';
    if(value.capabilities?.voice===false)$('voice-status').textContent='Nicht installiert';
    if (value.services.display?.ActiveState === 'active') $('display-status').textContent = original.idle_mode === 'photos' ? 'Diashow bereit' : original.idle_mode === 'black' ? 'Dunkelmodus bereit' : 'Dashboard sichtbar';
    $('voice-caption').textContent = original.voice_enabled ? (value.panel_active ? 'Unabhängig vom Display' : 'Mikrofon an Panelsitzung gebunden') : 'Mikrofonaufnahme beendet';
  } catch(error) {
    $('session-tag').textContent = 'Verbindung unterbrochen';
    for (const role of ['kiosk','voice','display']) $(role+'-status').textContent = 'Nicht erreichbar';
  }
}
function showLogin(message='') {
  $('app-view').hidden = true; $('login-view').hidden = false;
  $('login-error').textContent = message; $('login-error').hidden = !message;
  $('password').value = ''; csrf = ''; clearInterval(poll);
}
async function enter(session) {
  csrf = session.csrf;
  const config = await api('settings'); fill(config);
  $('demo-banner').hidden = !session.demo; $('version').textContent = 'PanelPilot '+session.version;
  $('app-view').hidden = false; $('login-view').hidden = true;
  navigate(location.hash.slice(1) || 'overview');
  await status(); clearInterval(poll); poll = setInterval(status, 15000);
  await Promise.all([loadSystem(),loadPhotos()]);
}
$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('login-error').hidden = true;
  try { const session = await api('login','POST',{username:$('username').value.trim(),password:$('password').value}); $('password').value = ''; await enter(session); }
  catch(error) { $('login-error').textContent = error.message; $('login-error').hidden = false; }
  finally { button.disabled = false; }
});
document.querySelectorAll('[data-nav]').forEach(button => button.addEventListener('click',()=>navigate(button.dataset.nav)));
document.querySelectorAll('[data-goto]').forEach(button => button.addEventListener('click',()=>navigate(button.dataset.goto)));
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
fields.forEach(input => input.addEventListener('input',syncForm));
$('discard').addEventListener('click',async()=>{
  try { fill(await api('settings')); notice('Gespeicherte Einstellungen neu geladen.'); } catch(error) { notice(error.message,'error'); }
});
$('settings-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy || !changed()) return;
  const invalid = fields.find(field => !field.checkValidity());
  if (invalid) { navigate(invalid.closest('[data-view]').dataset.view); invalid.reportValidity(); return; }
  busy = true; syncForm(); notice(''); fields.forEach(field=>field.disabled=true);
  try {
    const result = await api('settings','PUT',{settings:readForm(),revision}); fill(result);
    notice(result.warnings.length ? 'Gespeichert. '+result.warnings.join(' ') : 'Einstellungen übernommen.', result.warnings.length ? 'warning' : '');
    await status();
  } catch(error) { notice(error.status === 409 ? error.message+' Mit „Verwerfen“ lädst du den aktuellen Stand.' : error.message,'error'); }
  finally { fields.forEach(field=>field.disabled=false); $('dashboard_brightness').disabled=!brightnessSupported; busy = false; syncForm(); }
});
$('check-photos').addEventListener('click',async()=>{
  const button = $('check-photos'); button.disabled = true; $('photo-result').hidden = false; $('photo-result').textContent = 'Prüfe den gespeicherten Ordner …';
  try { const result = await api('photos/check','POST'); $('photo-result').textContent = result.status === 'ready' ? `${result.count} Bilddateien gefunden. Ein Bild wurde erfolgreich geladen.` : result.status === 'empty' ? 'Der Ordner ist erreichbar, enthält aber keine unterstützten Bilder.' : 'Ordner oder Bilder nicht lesbar. Bitte Pfad, Dateirechte und Bildformate prüfen.'; }
  catch(error) { $('photo-result').textContent = error.message; }
  finally { syncForm(); }
});
$('reload-dashboard').addEventListener('click',async()=>{
  const button=$('reload-dashboard'); button.disabled=true;
  try { await api('dashboard/reload','POST'); notice('Panelbrowser mit der gespeicherten Adresse neu gestartet.'); await status(); }
  catch(error) { notice(error.message,'error'); } finally { button.disabled=false; }
});
$('logout').addEventListener('click',async()=>{
  try { await api('logout','POST'); original=null; showLogin(); } catch(error) { notice(error.message,'error'); }
});
$('password-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if ($('new-password').value !== $('repeat-password').value) { notice('Die neuen Passwörter stimmen nicht überein.','error'); return; }
  const button=event.submitter; button.disabled=true;
  try { await api('password','POST',{current_password:$('current-password').value,new_password:$('new-password').value}); event.target.reset(); original=null; showLogin('Passwort geändert. Bitte mit dem neuen Passwort anmelden.'); }
  catch(error) { notice(error.message,'error'); } finally { button.disabled=false; }
});
window.addEventListener('beforeunload',event=>{if(changed() || systemDirty || uploadBusy){event.preventDefault();event.returnValue='';}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)status();});
function maintenanceForm() { return {enabled:$('maintenance-enabled').checked,update_time:$('update-time').value,only_ac:$('only-ac').checked,reboot_enabled:$('reboot-enabled').checked,reboot_day:$('reboot-day').value,reboot_time:$('reboot-time').value,timezone:$('maintenance-timezone').value.trim()}; }
function nfsForm() { return {enabled:$('nfs-enabled').checked,host:$('nfs-host').value.trim(),export:$('nfs-export').value.trim(),version:$('nfs-version').value}; }
function fillSystem(value,section=null) {
  systemState=value; const m=value.maintenance,n=value.nfs;
  if(section!=='nfs'){ $('maintenance-enabled').checked=m.enabled; $('update-time').value=m.update_time; $('only-ac').checked=m.only_ac;
  $('reboot-enabled').checked=m.reboot_enabled; $('reboot-day').value=m.reboot_day; $('reboot-time').value=m.reboot_time; $('maintenance-timezone').value=m.timezone; }
  if(section!=='maintenance'){ $('nfs-enabled').checked=n.enabled; $('nfs-host').value=n.host; $('nfs-export').value=n.export; $('nfs-version').value=n.version; }
  $('use-nfs').disabled=!n.enabled; $('snap-note').hidden=!value.snap;
  $('nfs-status').textContent=n.enabled ? 'NFS eingerichtet · '+(value.mounted ? 'eingebunden' : 'wird bei Zugriff eingebunden') : 'Keine NFS-Verbindung aktiviert.';
  $('maintenance-status').textContent='Gespeicherter Zeitplan · '+m.timezone;
  $('maintenance-next').replaceChildren();
  for (const [unit,title] of [['apt-daily.timer','Paketlisten'],['apt-daily-upgrade.timer','Installation'],['panelpilot-weekly-reboot.timer','Neustart']]) {
    const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd'); dt.textContent=title;
    dd.textContent=value.demo ? 'Beispielzeitplan in der Demo' : value.timers[unit]?.NextElapseUSecRealtime || 'Nicht geplant'; row.append(dt,dd); $('maintenance-next').append(row);
  }
  systemDirty=JSON.stringify(maintenanceForm())!==JSON.stringify(m) || JSON.stringify(nfsForm())!==JSON.stringify(n);
}
async function loadSystem() {
  try { fillSystem(await api('system')); $('nfs-fields').disabled=false; $('maintenance-fields').disabled=false; }
  catch(error) { $('maintenance-status').textContent=error.message; $('nfs-status').textContent=error.message; $('nfs-fields').disabled=true; $('maintenance-fields').disabled=true; }
}
function chooseSource(path) {
  $('photo_directory').value=path; $('idle_mode').value='photos'; syncForm(); navigate('display'); notice('Fotoquelle ausgewählt. Mit „Übernehmen“ auf dem Panel aktivieren.');
}
$('use-local').addEventListener('click',()=>{if(localState)chooseSource(localState.directory);});
$('use-nfs').addEventListener('click',()=>{if(systemState?.nfs.enabled)chooseSource(systemState.mountpoint);});
for (const id of ['maintenance-form','nfs-form']) $(id).addEventListener('input',()=>{systemDirty=true;});
$('maintenance-form').addEventListener('submit',async event=>{
  event.preventDefault(); const data=maintenanceForm(); $('maintenance-fields').disabled=true;
  try { const result=await api('system/maintenance','PUT',data); fillSystem(result,'maintenance'); notice('Zeitplan gespeichert. Kein sofortiger Neustart ausgelöst.'); }
  catch(error) { notice(error.message,'error'); } finally { $('maintenance-fields').disabled=false; }
});
$('nfs-form').addEventListener('submit',async event=>{
  event.preventDefault(); const data=nfsForm(); $('nfs-fields').disabled=true; $('nfs-status').textContent='Verbindung wird geprüft und eingerichtet …';
  try { fillSystem(await api('system/nfs','PUT',data),'nfs'); notice('NFS-Einstellungen gespeichert.'); }
  catch(error) { $('nfs-status').textContent=error.message; notice(error.message,'error'); } finally { $('nfs-fields').disabled=false; }
});
$('nfs-test').addEventListener('click',async()=>{
  const data=nfsForm(); $('nfs-fields').disabled=true; $('nfs-status').textContent='Prüfe Zugriff als Panelbenutzer …';
  try { const value=await api('system/nfs/test','POST',data); $('nfs-status').textContent=value.status==='ready' ? `Verbindung funktioniert. ${value.count} Fotos gefunden und ein Bild gelesen.` : value.status==='empty' ? 'Verbindung funktioniert. Der Ordner enthält noch keine unterstützten Fotos.' : 'Freigabe oder Bilder nicht lesbar. Bitte die NAS-Berechtigungen prüfen.'; }
  catch(error) { $('nfs-status').textContent=error.message; } finally { $('nfs-fields').disabled=false; }
});
async function loadPhotos() {
  try {
    localState=await api('photos/local'); $('local-gallery').replaceChildren(); $('local-empty').hidden=localState.files.length>0;
    $('photo-usage').textContent=`${localState.files.length} Fotos · ${(localState.used_bytes/1024/1024).toLocaleString('de',{maximumFractionDigits:1})} von 1024 MiB`;
    for(const file of localState.files) {
      const card=document.createElement('article'),img=document.createElement('img'),name=document.createElement('span'),button=document.createElement('button');
      card.className='photo-card'; img.src=file.url; img.alt=file.name; img.loading='lazy'; name.textContent=file.name; button.type='button'; button.className='text-button'; button.textContent='Löschen';
      button.addEventListener('click',async()=>{ if(!confirm(`„${file.name}“ vom lokalen Fotospeicher löschen?`))return; button.disabled=true; try {await api('photos/local/'+file.id,'DELETE'); await loadPhotos();}catch(error){notice(error.message,'error');button.disabled=false;} });
      card.append(img,name,button); $('local-gallery').append(card);
    }
  } catch(error) { $('photo-usage').textContent=error.message; }
}
function uploadFile(file,index,total) {
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest(); xhr.open('POST','/api/photos/upload'); xhr.setRequestHeader('X-CSRF-Token',csrf); xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name)); xhr.setRequestHeader('Content-Type',file.type || 'application/octet-stream'); xhr.timeout=120000;
    xhr.upload.onprogress=event=>{if(event.lengthComputable)$('upload-progress').value=event.loaded/event.total*100;};
    $('upload-result').textContent=`${index+1} / ${total}: ${file.name}`;
    xhr.onload=()=>{let value={};try{value=JSON.parse(xhr.responseText);}catch{} if(xhr.status>=200&&xhr.status<300)resolve(value);else reject(new Error(value.detail || 'Upload fehlgeschlagen.'));};
    xhr.onerror=xhr.ontimeout=()=>reject(new Error('Upload-Verbindung unterbrochen.')); xhr.send(file);
  });
}
$('photo-upload').addEventListener('change',async event=>{
  const files=[...event.target.files]; if(!files.length)return; uploadBusy=true; event.target.disabled=true; $('upload-progress').hidden=false; let done=0;
  try {for(const [i,file] of files.entries()){if(file.size>20*1024*1024)throw new Error(file.name+': größer als 20 MiB.'); await uploadFile(file,i,files.length);done++;} $('upload-result').textContent=`${done} Fotos hochgeladen. Über „Lokale Fotos als Quelle wählen“ in der Diashow verwenden.`;}
  catch(error){$('upload-result').textContent=`${done} Fotos hochgeladen. ${error.message}`;}
  finally{uploadBusy=false;event.target.disabled=false;event.target.value='';$('upload-progress').hidden=true;await loadPhotos();}
});
api('session').then(enter).catch(()=>showLogin());
