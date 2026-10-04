const $ = (id) => document.getElementById(id);
let csrf = '', rfb = null, screenKey = '', statusTimer = null, busy = false, currentMode = 'READY', connectionGeneration = 0;
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type':'application/json', 'X-CSRF-Token':csrf, ...options.headers}});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || data.error || `Request failed (${response.status})`);
  return data;
}
function error(e) { $('error').textContent = e.message; }
function loginView() {
  clearTimeout(statusTimer); connectionGeneration++; rfb?.disconnect(); rfb = null; screenKey = '';
  $('workspace').hidden = true; $('login').hidden = false;
}
async function connectScreen(mode, force = false) {
  const key = ['HUMAN','PRIVATE'].includes(mode) ? 'control' : 'view';
  if (!force && rfb && screenKey === key) return;
  const generation = ++connectionGeneration;
  rfb?.disconnect(); rfb = null; screenKey = key;
  $('connection-overlay').hidden = true;
  $('connection-label').textContent = 'Connecting…';
  const {default:RFB} = await import('/novnc/core/rfb.js');
  if (generation !== connectionGeneration || $('workspace').hidden) return;
  $('screen').dataset.connected = 'false';
  $('screen').replaceChildren();
  rfb = new RFB($('screen'), `${location.protocol==='https:'?'wss':'ws'}://${location.host}/desktop/${key}`);
  rfb.addEventListener('connect', () => { if (rfb === connection) { $('screen').dataset.connected = 'true'; $('connection-label').textContent = 'Connected'; $('connection-overlay').hidden = true; } });
  rfb.scaleViewport = true; rfb.resizeSession = false; rfb.viewOnly = key === 'view';
  const connection = rfb;
  rfb.addEventListener('disconnect', () => { if (rfb === connection) { screenKey = ''; $('screen').dataset.connected = 'false'; $('connection-label').textContent = 'Disconnected'; $('connection-overlay').hidden = false; } });
  rfb.addEventListener('securityfailure', () => error(new Error('Desktop connection rejected. Sign in again.')));
}
function showEvents(events) {
  if (!events.length) { $('events').textContent = 'Your session starts here.'; return; }
  $('events').replaceChildren(...events.slice(-8).reverse().map(e => {
    const li=document.createElement('li'), t=document.createElement('time');
    t.textContent=new Date(e.time*1000).toLocaleTimeString(); li.append(t,document.createTextNode(`${e.kind}: ${e.detail}`)); return li;
  }));
}
async function refresh() {
  clearTimeout(statusTimer);
  try {
    const s=await api('/api/status'); csrf=s.csrf;
    const pending = new URLSearchParams(location.search).get('authorize');
    if (pending) { const u = new URL(pending,location.origin); if(u.origin===location.origin && u.pathname==='/authorize'){location.replace(u);return;} }
    const entering = $('workspace').hidden;
    $('login').hidden=true; $('workspace').hidden=false;
    currentMode=s.state; $('status').textContent=s.state; $('status').parentElement.dataset.state=s.state;
    $('view-label').textContent=s.state==='PRIVATE' ? 'Private control · AI cannot observe' : s.state==='HUMAN' ? 'You are in control' : 'Read-only viewer';
    for (const button of document.querySelectorAll('[data-mode]')) button.setAttribute('aria-pressed', String(button.dataset.mode.toUpperCase()===s.state)); $('mcp-url').value=s.mcp_url; showEvents(s.events);
    $('control-note').textContent=s.in_flight ? 'Waiting for an in-flight action. New AI actions are blocked after takeover. Managed shell processes are being stopped. Detached external effects cannot be undone.' : s.state==='PRIVATE' ? 'Private takeover: AI observations and actions are blocked. You control the desktop.' : s.state==='HUMAN' ? 'You have control. AI writes are blocked until you hand back.' : s.state==='AGENT' ? 'AI has control. You are watching a server-enforced read-only stream.' : 'AI actions are paused. Hand back to AI to resume.';
    if (!s.in_flight) await connectScreen(s.state);
    if (entering) await files();
  } catch(e) {
    if (/Sign in|Login/.test(e.message)) loginView(); else error(e);
  } finally { if (!$('workspace').hidden) statusTimer=setTimeout(refresh,2500); }
}
$('login-form').addEventListener('submit', async e=>{
  e.preventDefault(); const submit=e.submitter; if(submit.disabled)return; submit.disabled=true; $('login-error').textContent='';
  try {const s=await api('/api/login',{method:'POST',body:JSON.stringify({token:$('token').value})}); csrf=s.csrf; $('token').value='';
    const next=new URLSearchParams(location.search).get('authorize');
    if(next){const u=new URL(next,location.origin);if(u.origin===location.origin && u.pathname==='/authorize'){location.assign(u);return;}}
    await refresh();
  } catch(e){$('login-error').textContent=e.message;} finally {submit.disabled=false;}
});
for(const button of document.querySelectorAll('[data-mode]')) button.addEventListener('click',async()=>{
  if(busy)return;busy=true;for(const control of document.querySelectorAll('[data-mode]'))control.disabled=true;$('error').textContent='';
  try{await api(`/api/control/${button.dataset.mode}`,{method:'POST'});rfb?.disconnect();rfb=null;await refresh();}catch(e){error(e);}finally{busy=false;for(const control of document.querySelectorAll('[data-mode]'))control.disabled=false;}
});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST'});loginView();}catch(e){error(e);}});
$('reconnect').addEventListener('click',()=>connectScreen(currentMode,true).catch(error));
$('copy-url').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('mcp-url').value);$('copy-feedback').textContent='Endpoint copied';}catch{$('mcp-url').focus();$('mcp-url').select();$('copy-feedback').textContent='Select and copy the endpoint manually.';}});
$('fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('screen-shell').requestFullscreen();}catch{error(new Error('Full screen is unavailable in this browser.'));}});
function formatBytes(bytes) { return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes/1024).toFixed(1)} KB` : `${(bytes/1048576).toFixed(1)} MB`; }
async function files(){
  const button=$('refresh-files');button.disabled=true;
  try{
    const data=await api('/api/artifacts');$('file-count').textContent=data.files.length;
    $('files').replaceChildren(...data.files.map(f=>{
      const li=document.createElement('li'),a=document.createElement('a'),size=document.createElement('span');
      a.textContent=f.path;a.href='/api/artifacts/'+f.path.split('/').map(encodeURIComponent).join('/');a.download=f.path.split('/').pop();
      size.className='file-size';size.textContent=formatBytes(f.bytes);li.append(a,size);return li;
    }));
    if(!data.files.length){const li=document.createElement('li'),hint=document.createElement('span');li.className='empty-state';li.textContent='No files yet';hint.textContent='Files saved to the workspace appear here.';li.append(hint);$('files').append(li);}
  }catch(e){error(e);}finally{button.disabled=false;}
}
$('refresh-files').addEventListener('click',files);
refresh();
