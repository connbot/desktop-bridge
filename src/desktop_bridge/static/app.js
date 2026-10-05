const $ = (id) => document.getElementById(id);
let csrf = '', rfb = null, screenKey = '', statusTimer = null, busy = false, currentMode = 'READY', connectionGeneration = 0, authGeneration = 0;
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type':'application/json', 'X-CSRF-Token':csrf, ...options.headers}});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || data.error || `Request failed (${response.status})`);
  return data;
}
function error(e) { $('error').textContent = e.message; }
function loginView() {
  authGeneration++;
  clearTimeout(statusTimer); connectionGeneration++; rfb?.disconnect(); rfb = null; screenKey = '';
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  personalGeneration++; personalData = null; $('profile-details').replaceChildren(); $('personal-task-list').replaceChildren();
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
function controlNote(state) {
  if (state.in_flight) {
    return ['HUMAN', 'PRIVATE', 'PAUSED', 'STOPPED'].includes(state.state)
      ? 'Waiting for an in-flight action. New AI actions are blocked after takeover. Managed shell processes are being stopped. Detached external effects cannot be undone.'
      : 'AI is working. You can watch the desktop or request control.';
  }
  if (state.state === 'PRIVATE') return 'Private takeover: AI observations and actions are blocked. You control the desktop.';
  if (state.state === 'HUMAN') return 'You have control. AI writes are blocked until you hand back.';
  if (state.state === 'AGENT') return 'AI has control. You are watching a server-enforced read-only stream.';
  if (state.state === 'READY') return 'Ready for your AI client. Start a task in your connected chat.';
  return 'AI actions are paused. Hand back to AI to resume.';
}
async function refresh() {
  const generation = authGeneration;
  clearTimeout(statusTimer);
  try {
    const s=await api('/api/status');
    if (generation !== authGeneration) return;
    csrf=s.csrf;
    const pending = new URLSearchParams(location.search).get('authorize');
    if (pending) { const u = new URL(pending,location.origin); if(u.origin===location.origin && u.pathname==='/authorize'){location.replace(u);return;} }
    const entering = $('workspace').hidden;
    $('login').hidden=true; $('workspace').hidden=false;
    currentMode=s.state; $('status').textContent=s.state; $('status').parentElement.dataset.state=s.state;
    $('view-label').textContent=s.state==='PRIVATE' ? 'Private control · AI cannot observe' : s.state==='HUMAN' ? 'You are in control' : 'Read-only viewer';
    for (const button of document.querySelectorAll('[data-mode]')) button.setAttribute('aria-pressed', String(button.dataset.mode.toUpperCase()===s.state)); $('mcp-url').value=s.mcp_url; showEvents(s.events);
    $('control-note').textContent=controlNote(s);
    storageName = s.context_store?.provider === 'postgres' ? 'Postgres · Neon-compatible' : s.context_store?.provider === 'local' ? 'Local file' : 'Optional memory';
    loadPersonal().catch(contextUnavailable);
    if (entering) await files();
    if (!s.in_flight) await connectScreen(s.state);
  } catch(e) {
    if (generation !== authGeneration) return;
    if (/Sign in|Login/.test(e.message)) loginView(); else error(e);
  } finally { if (generation === authGeneration && !$('workspace').hidden) statusTimer=setTimeout(refresh,2500); }
}
$('login-form').addEventListener('submit', async e=>{
  e.preventDefault(); const submit=e.submitter; if(submit.disabled)return; submit.disabled=true; $('login-error').textContent='';
  try {const s=await api('/api/login',{method:'POST',body:JSON.stringify({token:$('token').value})}); authGeneration++; csrf=s.csrf; $('token').value='';
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
const taskExamples = {
  planner: 'Help me plan a weekend. Ask for my location, available time, budget, and interests if missing. Use the browser to check sources, distinguish facts from assumptions, then use Coding Tools MCP to create an editable plan in the workspace. Do not book or pay for anything.',
  files: 'Help organize files I provide in the workspace. First inspect and propose a structure. Use Coding Tools MCP to make a non-destructive organized copy and an index. Preserve originals and flag uncertain classifications. Ask me for files if none are available.',
  tool: 'Build a small personal tool for a routine I describe. Ask what it should do if needed. Use Coding Tools MCP to create the files and run checks, open the result in the shared browser, test its controls, and show me how to download it. Use clearly labeled sample data until I provide my own.'
};
for (const button of document.querySelectorAll('[data-task]')) button.addEventListener('click', async () => {
  const prompt = 'Check session_status. If optional memory is configured, read personal_context and use relevant saved preferences, goals, constraints, and task history; otherwise ask for missing details. ' + taskExamples[button.dataset.task] + ' If memory is configured, record progress and results with personal_record_task, linking real workspace files. Do not claim external actions are complete without checking.';
  try { await navigator.clipboard.writeText(prompt); $('task-feedback').textContent = 'Prompt copied. Paste it into your connected AI chat to begin.'; }
  catch { $('task-feedback').textContent = prompt; }
});

let storageName = 'Context storage', personalLoading = false, personalGeneration = 0;
let personalData = null, profileRevision = 0, taskRevision = 0, importRevision = 0, importedContext = null;
const taskStatusLabels = {planned: 'Planned', in_progress: 'In progress', needs_input: 'Needs your input', completed: 'Completed', cancelled: 'Cancelled'};
function feedback(text) { $('personal-feedback').textContent = text; $('personal-feedback').hidden = false; setTimeout(() => { $('personal-feedback').hidden = true; }, 7000); }
function contextUnavailable(e) {
  if ($('workspace').hidden) return;
  personalData = null; const disabled = storageName === 'Optional memory';
  $('context-provider').textContent = disabled ? 'Optional memory · off' : `${storageName} · unavailable`;
  $('profile-summary').textContent = e.message; $('profile-details').replaceChildren();
  $('personal-task-list').textContent = disabled ? 'Configure an optional memory provider to keep preferences and task results. Your desktop and Coding Tools MCP work without it.' : 'Saved context is unavailable. Your desktop and Coding Tools MCP still work.';
  for (const id of ['edit-profile', 'new-task', 'import-context']) $(id).disabled = true;
  $('export-context').setAttribute('aria-disabled', 'true');
}
async function loadPersonal() {
  if (personalLoading) return;
  personalLoading = true; const generation = personalGeneration;
  try {
    const data = await api('/api/personal');
    if (generation !== personalGeneration || $('workspace').hidden) return;
    if (personalData && personalData.revision > data.revision) return;
    personalData = data; $('context-provider').textContent = `${storageName} · connected`;
    for (const id of ['edit-profile', 'new-task', 'import-context']) $(id).disabled = false;
    $('export-context').removeAttribute('aria-disabled'); renderPersonal();
  } finally { personalLoading = false; }
}
function renderPersonal() {
  const profile = personalData.profile;
  $('profile-summary').textContent = profile.name ? `For ${profile.name}` : 'Context for your everyday tasks';
  const details = [];
  for (const [key, label] of [['preferences', 'I prefer'], ['goals', 'I’m working toward'], ['constraints', 'Keep in mind']]) {
    if (!profile[key]) continue;
    const term = document.createElement('dt'), description = document.createElement('dd');
    term.textContent = label; description.textContent = profile[key]; details.push(term, description);
  }
  if (!details.length) $('profile-summary').textContent = 'Tell your AI what matters to you, once. Add preferences, goals, and limits with Edit.';
  $('profile-details').replaceChildren(...details);
  $('task-count').textContent = personalData.tasks.length;
  if (!personalData.tasks.length) {
    const empty = document.createElement('p'); empty.className = 'context-empty'; empty.textContent = 'A weekend plan, organized files, a tool for your routine. Your AI records progress and links what it makes here.';
    $('personal-task-list').replaceChildren(empty); return;
  }
  $('personal-task-list').replaceChildren(...personalData.tasks.map(task => {
    const card = document.createElement('article'); card.className = 'personal-task';
    const top = document.createElement('div'); top.className = 'task-top';
    const title = document.createElement('h3'); title.textContent = task.title;
    const status = document.createElement('span'); status.className = 'task-status'; status.dataset.status = task.status; status.textContent = taskStatusLabels[task.status];
    top.append(title, status); card.append(top);
    if (task.summary) { const summary = document.createElement('p'); summary.textContent = task.summary; card.append(summary); }
    if (task.next_step) { const next = document.createElement('p'); next.className = 'task-next'; next.textContent = `Next: ${task.next_step}`; card.append(next); }
    if (task.evidence) { const evidence = document.createElement('details'), label = document.createElement('summary'), text = document.createElement('p'); label.textContent = 'Evidence / checks'; text.textContent = task.evidence; evidence.append(label, text); card.append(evidence); }
    if (task.artifact_details.length) {
      const links = document.createElement('div'); links.className = 'task-artifacts';
      for (const file of task.artifact_details) {
        const link = document.createElement(file.available ? 'a' : 'span');
        link.textContent = `${file.available ? '↓' : 'Unavailable:'} ${file.path}`;
        if (file.available) { link.href = '/api/artifacts/' + file.path.split('/').map(encodeURIComponent).join('/'); link.download = file.path.split('/').pop(); }
        else link.className = 'artifact-missing';
        links.append(link);
      }
      card.append(links);
    }
    const bottom = document.createElement('div'); bottom.className = 'task-bottom';
    const by = document.createElement('span'); by.textContent = `${task.updated_by === 'agent' ? 'AI-reported' : 'Owner-reported'} · ${task.updated_at ? new Date(task.updated_at).toLocaleDateString() : 'Imported'}`;
    const actions = document.createElement('div'), edit = document.createElement('button'), resume = document.createElement('button');
    edit.className = 'quiet'; edit.textContent = 'Edit'; edit.addEventListener('click', () => openTask(task));
    resume.className = 'quiet'; resume.textContent = 'Copy task prompt ↗'; resume.addEventListener('click', () => copyTask(task));
    actions.append(edit, resume); bottom.append(by, actions); card.append(bottom); return card;
  }));
}
function showProfile() {
  if (!personalData) return;
  profileRevision = personalData.revision;
  for (const key of ['name', 'preferences', 'goals', 'constraints']) $(`profile-${key}`).value = personalData.profile[key];
  $('profile-error').textContent = ''; $('profile-dialog').showModal();
}
$('edit-profile').addEventListener('click', showProfile);
for (const close of document.querySelectorAll('[data-close]')) close.addEventListener('click', () => $(close.dataset.close).close());
$('profile-form').addEventListener('submit', async event => {
  event.preventDefault(); if (event.submitter.disabled) return; event.submitter.disabled = true;
  const profile = Object.fromEntries(['name', 'preferences', 'goals', 'constraints'].map(key => [key, $(`profile-${key}`).value]));
  try { personalData = await api('/api/personal/profile', {method:'PUT', body:JSON.stringify({expected_revision:profileRevision, profile})}); renderPersonal(); $('profile-dialog').close(); feedback('Preferences saved. Your connected AI can read them before its next task.'); }
  catch (e) { $('profile-error').textContent = e.message; } finally { event.submitter.disabled = false; }
});
function openTask(task = null) {
  if (!personalData) return;
  taskRevision = personalData.revision; $('task-dialog-title').textContent = task ? 'Edit task' : 'New task';
  $('personal-task-id').value = task?.id || crypto.randomUUID();
  for (const key of ['title', 'summary', 'evidence']) $(`personal-task-${key}`).value = task?.[key] || '';
  $('personal-task-status').value = task?.status || 'planned'; $('personal-task-next-step').value = task?.next_step || '';
  $('personal-task-artifacts').value = task?.artifacts.join('\n') || ''; $('personal-task-error').textContent = ''; $('task-dialog').showModal();
}
$('new-task').addEventListener('click', () => openTask());
$('personal-task-form').addEventListener('submit', async event => {
  event.preventDefault(); if (event.submitter.disabled) return; event.submitter.disabled = true;
  const task = Object.fromEntries(['id', 'title', 'status', 'summary', 'evidence'].map(key => [key, $(`personal-task-${key}`).value]));
  task.next_step = $('personal-task-next-step').value; task.artifacts = $('personal-task-artifacts').value.split('\n').map(path => path.trim()).filter(Boolean);
  try { personalData = await api('/api/personal/tasks', {method:'POST', body:JSON.stringify({expected_revision:taskRevision, task})}); renderPersonal(); $('task-dialog').close(); feedback('Task saved. Copy its prompt to your connected AI chat to work on it.'); }
  catch (e) { $('personal-task-error').textContent = e.message; } finally { event.submitter.disabled = false; }
});
async function copyTask(task) {
  const prompt = `Read personal_context first. Continue task ${JSON.stringify(task.id)}: ${task.title}. Use relevant saved preferences, goals, constraints and previous results. ${task.next_step ? 'Next step: ' + task.next_step : 'Ask me about any essential missing details.'} Use Coding Tools MCP for files, editing and execution. Record progress and evidence with personal_record_task, linking real workspace artifacts. Only report external actions complete after verifying them; ask before commitments or sending anything.`;
  try { await navigator.clipboard.writeText(prompt); feedback('Task prompt copied. Paste it in your connected AI chat.'); }
  catch { feedback(prompt); }
}
$('import-context').addEventListener('click', () => $('context-file').click());
$('context-file').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 128 * 1024) throw new Error('Choose a context export smaller than 128 KiB.');
    importedContext = JSON.parse(await file.text());
    if (importedContext.schema_version !== 1 || !importedContext.profile || !Array.isArray(importedContext.tasks)) throw new Error('Choose an Agent Computer personal context export.');
    importRevision = personalData.revision; $('import-preview').textContent = `${file.name}: ${importedContext.tasks.length} task record(s).`;
    $('import-error').textContent = ''; $('import-dialog').showModal();
  } catch (e) { feedback(e.message); } finally { event.target.value = ''; }
});
$('import-form').addEventListener('submit', async event => {
  event.preventDefault(); if (event.submitter.disabled) return; event.submitter.disabled = true;
  try { personalData = await api('/api/personal/import', {method:'POST', body:JSON.stringify({expected_revision:importRevision, context:importedContext})}); renderPersonal(); $('import-dialog').close(); feedback('Context imported. Artifact files must be copied separately.'); }
  catch (e) { $('import-error').textContent = e.message; } finally { event.submitter.disabled = false; }
});
refresh();
