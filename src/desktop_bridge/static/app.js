const $ = (id) => document.getElementById(id);
let csrf = '', rfb = null, screenKey = '', statusTimer = null, busy = false;
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type':'application/json', 'X-CSRF-Token':csrf, ...options.headers}});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || data.error || `Request failed (${response.status})`);
  return data;
}
function error(e) { $('error').textContent = e.message; }
function loginView() {
  clearTimeout(statusTimer); rfb?.disconnect(); rfb = null; screenKey = '';
  $('workspace').hidden = true; $('login').hidden = false;
}
async function connectScreen(mode, force = false) {
  const key = ['HUMAN','PRIVATE'].includes(mode) ? 'control' : 'view';
  if (!force && rfb && screenKey === key) return;
  rfb?.disconnect(); rfb = null; screenKey = key;
  const {default:RFB} = await import('/novnc/core/rfb.js');
  $('screen').dataset.connected = 'false';
  $('screen').replaceChildren();
  rfb = new RFB($('screen'), `${location.protocol==='https:'?'wss':'ws'}://${location.host}/desktop/${key}`);
  rfb.addEventListener('connect', () => { if (rfb === connection) $('screen').dataset.connected = 'true'; });
  rfb.scaleViewport = true; rfb.resizeSession = false; rfb.viewOnly = key === 'view';
  const connection = rfb;
  rfb.addEventListener('disconnect', () => { if (rfb === connection) { screenKey = ''; $('screen').dataset.connected = 'false'; } });
  rfb.addEventListener('securityfailure', () => error(new Error('Desktop connection rejected. Sign in again.')));
}
function showEvents(events) {
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
    $('login').hidden=true; $('workspace').hidden=false;
    $('status').textContent=s.state; $('mcp-url').value=s.mcp_url; showEvents(s.events);
    $('control-note').textContent=s.in_flight ? 'Waiting for an in-flight action. New AI actions are blocked after takeover. Managed shell processes are being stopped. Detached external effects cannot be undone.' : s.state==='PRIVATE' ? 'Private takeover: AI observations and actions are blocked. You control the desktop.' : s.state==='HUMAN' ? 'You have control. AI writes are blocked until you hand back.' : s.state==='AGENT' ? 'AI has control. You are watching a server-enforced read-only stream.' : 'AI actions are paused. Hand back to AI to resume.';
    if (!s.in_flight) await connectScreen(s.state);
  } catch(e) {
    if (/Sign in|Login/.test(e.message)) loginView(); else error(e);
  } finally { if (!$('workspace').hidden) statusTimer=setTimeout(refresh,2500); }
}
$('login-form').addEventListener('submit', async e=>{
  e.preventDefault(); $('login-error').textContent='';
  try {const s=await api('/api/login',{method:'POST',body:JSON.stringify({token:$('token').value})}); csrf=s.csrf; $('token').value='';
    const next=new URLSearchParams(location.search).get('authorize');
    if(next){const u=new URL(next,location.origin);if(u.origin===location.origin && u.pathname==='/authorize'){location.assign(u);return;}}
    await refresh(); await files();
  } catch(e){$('login-error').textContent=e.message;}
});
for(const button of document.querySelectorAll('[data-mode]')) button.addEventListener('click',async()=>{
  if(busy)return;busy=true;button.disabled=true;$('error').textContent='';
  try{await api(`/api/control/${button.dataset.mode}`,{method:'POST'});rfb?.disconnect();rfb=null;await refresh();}catch(e){error(e);}finally{busy=false;button.disabled=false;}
});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST'});loginView();}catch(e){error(e);}});
$('reconnect').addEventListener('click',()=>connectScreen($('status').textContent,true).catch(error));
$('copy-url').addEventListener('click',()=>navigator.clipboard.writeText($('mcp-url').value).catch(error));
async function files(){try{const data=await api('/api/artifacts');$('files').replaceChildren(...data.files.map(f=>{const li=document.createElement('li'),a=document.createElement('a');a.textContent=f.path;a.href='/api/artifacts/'+f.path.split('/').map(encodeURIComponent).join('/');a.download=f.path.split('/').pop();li.append(a,document.createTextNode(` · ${f.bytes} B`));return li;}));if(!data.files.length)$('files').textContent='No files yet';}catch(e){error(e);}}
$('refresh-files').addEventListener('click',files);
refresh();
