/* No analytics, third-party scripts, localStorage, or plaintext password requests. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let session, op, step = 0, password = null, downloaded = false, saved = false;
  let busy = false, polling = false, pollTimer, closing = false;
  const selectedMode = () => document.querySelector('input[name="address"]:checked').value;
  const terminal = new Set(['failed', 'unknown', 'interrupted', 'ended']);
  const phaseOrder = ['repo', 'address', 'secret', 'launch', 'ready'];
  const phaseFor = {queued:0,creating_repository:0,checking_repository:0,creating_address:1,awaiting_password:2,saving_password:2,launching:3,waiting_ready:4,ready:5,stopping:5,ending:5,ended:5};
  function message(id, text) { $(id).textContent = text || ''; $(id).hidden = !text; }
  function textNode(tag, text) { const el = document.createElement(tag); el.textContent = text; return el; }
  function show(next, focus=true) {
    step = next;
    document.querySelectorAll('[data-view]').forEach(el => { el.hidden = Number(el.dataset.view) !== next; });
    document.querySelectorAll('[data-step]').forEach(el => {
      const n = Number(el.dataset.step); el.classList.toggle('current', n === next); el.classList.toggle('complete', n < next);
      if (n === next) el.setAttribute('aria-current', 'step'); else el.removeAttribute('aria-current');
    });
    if (focus) {
      const heading=document.querySelector(`[data-view="${next}"] h2`);
      heading?.focus({preventScroll:true});
      if (window.innerWidth < 860 && heading) {
        const banner=$('mode-banner');
        const clearance=(banner.hidden ? 0 : banner.getBoundingClientRect().height)+16;
        window.scrollTo({top:Math.max(0,window.scrollY+heading.getBoundingClientRect().top-clearance),behavior:'instant'});
      }
    }
    if (next === 2) { ensurePassword(); renderSummary(); }
  }
  async function api(path, body) {
    const options = {headers: {'Accept':'application/json'}, credentials:'same-origin', cache:'no-store'};
    if (body !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.headers['X-CSRF-Token'] = session.csrf; options.body = JSON.stringify(body); }
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) { const err = new Error(data.message || '这一步没有完成，请稍后检查。'); err.code = data.code; throw err; }
    return data;
  }
  function safeAction(fn) { return async (...args) => {
    if (busy) return; busy = true; updateButtons(); message('global-error','');
    try { await fn(...args); } catch (e) { message('global-error', e.message || '网络连接中断，请检查现有进度。'); }
    finally { busy = false; updateButtons(); }
  }; }
  function updateButtons() {
    $('begin').disabled = busy || !$('chatgpt-ok').checked || !$('temporary-ok').checked;
    $('github-connect').disabled = busy || !session?.github_ready;
    $('cf-connect').disabled = busy || !session?.cloudflare_ready || !session?.installed;
    $('accounts-next').disabled = busy || !session?.installed || (selectedMode() === 'named' && !session?.zones?.length);
    $('create-start').disabled = busy || !password || !saved || !$('launch-consent').checked;
    $('download-password').disabled = busy || !password;
    $('password-saved').disabled = !downloaded;
  }
  function renderAccounts() {
    $('mode-banner').hidden = session.mode !== 'mock';
    if (session.user) {
      $('github-status').textContent = `已连接 ${session.user.login}${session.installed ? ' · 授权已确认' : ' · 还需要安装 App'}`;
      $('github-connect').textContent = '重新连接';
    } else if (!session.github_ready) {
      $('github-status').textContent = '这个站点的 GitHub App 尚未配置，暂时无法真实初始化。';
      $('github-connect').textContent = '尚未开放';
    }
    $('install-help').hidden = !session.user || session.installed;
    $('zone').replaceChildren();
    for (const zone of session.zones || []) { const option = textNode('option', zone.name); option.value = zone.id; $('zone').append(option); }
    $('zones-wrap').hidden = !session.zones?.length;
    $('no-zones').hidden = !session.cloudflare_connected || Boolean(session.zones?.length);
    if (session.cloudflare_connected) $('cf-connect').textContent = '重新连接';
    if (!session.cloudflare_ready) $('cf-connect').textContent = '暂未配置，先用临时地址';
    addressChanged();
  }
  function addressChanged() {
    const named = selectedMode() === 'named';
    $('cloudflare-panel').hidden = !named; $('quick-info').hidden = named;
    sessionStorage.setItem('bridge-address-choice', named ? 'named' : 'quick');
    updateButtons();
  }
  async function authorize(provider) {
    session = await api('/api/session');
    const result = await api('/api/auth/' + provider, {});
    window.location.assign(result.redirect);
  }
  function ensurePassword() {
    if (password) return;
    if (!window.isSecureContext || !crypto?.getRandomValues) throw new Error('请使用 HTTPS 或本地安全页面生成密码。');
    const bytes = new Uint8Array(32); crypto.getRandomValues(bytes);
    password = btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replaceAll('=',''); bytes.fill(0);
    downloaded = false; saved = false; $('password-saved').checked = false;
    updateButtons();
  }
  function downloadPassword() {
    if (!password) return;
    const payload = {format:'desktop-bridge-owner-v1',owner_password:password,
      github_account:session.user.login,created_at:new Date().toISOString(),
      note:'只在你本次预览电脑的登录或授权页面输入。不要发到聊天、GitHub Issue 或代码仓库。网页无法找回此密码。'};
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
    const a = document.createElement('a'); a.href=url; a.download='agent-computer-password.json'; a.click();
    setTimeout(()=>URL.revokeObjectURL(url),10000);
    downloaded = true;
    $('download-status').textContent = '已发起下载。请打开文件确认后，再勾选下面的保存确认。'; updateButtons();
  }
  function renderSummary() {
    const list = $('plan-summary'); list.replaceChildren();
    for (const text of [`在 ${session.user?.login || '你的账号'} 下创建一个公开的专用仓库`,
      selectedMode() === 'quick' ? '使用无需账号的临时地址，每次启动会变化' : `在 ${$('zone').selectedOptions[0]?.textContent || op?.zone_name || '所选域名'} 下创建专属 Tunnel 和子域名`,
      '加密保存电脑密码，然后启动一次 60 分钟预览', '完成公网检查后，由你手动连接 ChatGPT']) list.append(textNode('li',text));
    if (op?.stage === 'awaiting_password') {
      list.firstChild.textContent = '已创建专用仓库，继续保存密码后启动这一次预览';
      $('create-start').textContent = '保存密码并启动这次预览 →';
    }
  }
  async function createStart() {
    if (!saved || !password) return;
    if (!op) op = await api('/api/operations', {mode:selectedMode(),zone_id:$('zone').value,saved_password:true,accepted_preview:true});
    show(3); renderOperation(); schedulePoll(200);
  }
  function resources() {
    const container = $('resource-list'); container.replaceChildren();
    const dl = document.createElement('dl');
    const append = (label,value,url) => {
      if (!value) return; dl.append(textNode('dt',label)); const dd = textNode('dd',value);
      if (url && session.mode !== 'mock') { const a=textNode('a',value+' ↗'); a.href=url; a.target='_blank'; a.rel='noreferrer'; dd.replaceChildren(a); }
      dl.append(dd);
    };
    append('专用仓库',op.repo_url,op.repo_url); append('GitHub 运行',op.run_id ? String(op.run_id) : '',op.run_url);
    append('固定子域名',op.hostname); append('Cloudflare Tunnel ID',op.tunnel_id); append('DNS 记录 ID',op.dns_id);
    if (dl.children.length) { container.append(textNode('strong','本次已创建的资源'),dl,textNode('p','关闭页面或停止电脑不会自动删除这些资源。')); }
  }
  function renderOperation() {
    resources();
    $('stop-pending').hidden=!op.run_id || terminal.has(op.stage) || ['stopping','ending'].includes(op.stage);
    if (op.stage === 'ready') { renderReady(); if (step !== 4) show(4); return; }
    if (step === 4) show(3);
    const n = op.stage==='ended' && !op.last_seen ? 4 : (phaseFor[op.stage] ?? phaseFor[op.failed_stage] ?? -1);
    const needsAttention=['unknown','failed','interrupted'].includes(op.stage);
    document.querySelectorAll('[data-phase]').forEach(el=>{
      const index=phaseOrder.indexOf(el.dataset.phase);
      el.classList.toggle('done',index<n);
      el.classList.toggle('active',index===n && !needsAttention && op.stage!=='ended');
      el.classList.toggle('attention',index===n && needsAttention);
      el.querySelector('span').textContent=index<n?'✓':(index===n && needsAttention?'?':'');
      const title=el.querySelector('strong');
      title.dataset.label ||= title.textContent;
      title.textContent=op.stage==='unknown' && index===n ? title.dataset.label+'（待核对）' : title.dataset.label;
    });
    if (!terminal.has(op.stage) && !op.error) { $('progress-title').textContent='让配置自己完成'; $('progress-description').textContent='保持这个页面打开。当前进度来自服务端，完成检查后会显示连接地址。'; }
    const bad = terminal.has(op.stage) || Boolean(op.error);
    $('retry-step').hidden = op.stage !== 'failed' || op.uncertain || !['template_not_enabled','template_version_changed','permission_denied','authorization_expired','actions_disabled','installation_permissions_missing','not_found','provider_unavailable','rate_limited'].includes(op.error);
    $('restart-preview').hidden = op.stage !== 'ended' || !op.secret_written;
    $('recovery').hidden = !bad && !(op.stage === 'awaiting_password' && !password);
    $('recover-password').hidden = op.stage !== 'awaiting_password';
    $('reauthorize-cf').hidden = op.mode !== 'named' || !['authorization_expired','permission_denied'].includes(op.error);
    $('reauthorize').hidden = !['authorization_expired','session_expired','installation_permissions_missing'].includes(op.error);
    message('operation-error',op.message);
    if (op.stage === 'ended') {
      $('progress-title').textContent='这次预览已结束';
      $('progress-description').textContent='运行已结束，临时文件和浏览器会话不会保留。仓库和固定地址配置仍保留。再次启动前请确认需要重新连接 ChatGPT。';
      $('check-status').textContent='检查运行记录';
    } else if (['stopping','ending'].includes(op.stage)) {
      $('progress-title').textContent='正在结束预览';
      $('progress-description').textContent='已隐藏连接入口。等待 GitHub 确认电脑停止，请不要再次启动。';
    } else if (op.stage === 'unknown') {
      $('progress-title').textContent='还不能确认这一步的结果';
      $('progress-description').textContent='请求可能已被服务接收。为避免重复创建或启动，向导已暂停。请先检查下面的资源和 Actions 记录。';
    } else if (op.stage === 'checking_status') {
      $('progress-title').textContent='正在重新核对连接';
      $('progress-description').textContent='刚才的连接检测暂时没有通过。确认公网重新可用前，不显示连接入口。';
    } else if (bad) {
      $('progress-title').textContent='这一步需要检查';
      $('progress-description').textContent='初始化没有完成，电脑尚未确认可用。我们保留了已完成的步骤和资源。';
    } else if (op.stage === 'awaiting_password' && !password) {
      $('progress-title').textContent='请重新打开你的密码文件';
      $('progress-description').textContent='配置已保留，但刷新清除了页面内存中的密码。请继续保存密码，再启动。';
    }
  }
  function renderReady() {
    const mock=session.mode==='mock';
    $('ready-title').textContent=mock?'模拟检查通过，体验最后一步':'电脑已就绪，去连接 ChatGPT';
    if ($('mcp-url').value !== op.origin+'/mcp') $('copy-mcp').textContent='复制';
    $('mcp-url').value=op.origin+'/mcp';
    $('copy-mcp').disabled=op.expires_at<=Date.now()/1000;
    $('open-desktop').disabled=mock;
    $('run-link').hidden=mock;
    if (op.run_url) $('run-link').href=op.run_url;
    $('reconnect-note').textContent=op.mode==='quick'
      ?'这是临时地址：停止后失效，下次启动地址会变化，需要在 ChatGPT 更新或重新创建连接。'
      :'这是你的固定地址：它不会保留电脑文件，也不会让电脑一直在线。重启后仍可能需要重新创建 ChatGPT 连接或再次授权。';
    countdown();
  }
  function countdown() {
    if (!op?.expires_at || op.stage!=='ready') return;
    const left=Math.max(0,Math.floor(op.expires_at-Date.now()/1000));
    $('remaining').textContent=`还剩 ${Math.floor(left/60)} 分 ${String(left%60).padStart(2,'0')} 秒`;
    if (!left) { $('open-desktop').disabled=true; $('copy-mcp').disabled=true; schedulePoll(0); }
  }
  function schedulePoll(ms=2500) { clearTimeout(pollTimer); if(!closing) pollTimer=setTimeout(poll,ms); }
  async function poll() {
    if (!op || polling) return; polling=true;
    try {
      op=await api('/api/operations/'+op.id);
      if (op.stage==='awaiting_password' && password && saved && $('launch-consent').checked) {
        const encrypted=await BridgeCrypto.sealPassword(password,op.public_key);
        op=await api('/api/operations/'+op.id+'/start',{saved_password:true,key_id:op.key_id,encrypted_value:encrypted});
        password=null; saved=false; downloaded=false;
      }
      renderOperation();
      if (!terminal.has(op.stage) && !(op.stage==='awaiting_password' && !password)) schedulePoll();
    } catch (e) {
      message('global-error',e.message);
      if (step===4) { show(3); $('progress-title').textContent='暂时无法确认电脑状态'; $('progress-description').textContent='连接状态检查中断，已隐藏连接入口。检查网络或重新连接 GitHub 后，再读取现有运行。'; }
      $('recovery').hidden=false;
      $('reauthorize').hidden=!['session_expired','authorization_expired'].includes(e.code);
    } finally { polling=false; }
  }
  $('reset-mock').onclick=safeAction(async()=>{await api('/api/mock/reset',{});sessionStorage.clear();sessionStorage.setItem('bridge-reset-scroll','1');window.scrollTo({top:0,behavior:'instant'});window.location.reload();});
  $('chatgpt-ok').addEventListener('change',updateButtons); $('temporary-ok').addEventListener('change',updateButtons);
  $('begin').onclick=()=>{sessionStorage.setItem('bridge-prerequisites','confirmed');show(1);};
  document.querySelectorAll('[data-back]').forEach(el=>el.onclick=()=>show(Number(el.dataset.back)));
  document.querySelectorAll('[name="address"]').forEach(el=>el.onchange=addressChanged);
  $('github-connect').onclick=safeAction(()=>authorize('github'));
  $('cf-connect').onclick=safeAction(()=>authorize('cloudflare'));
  $('install-github').onclick=safeAction(async()=>{const data=await api('/api/install-link'); window.location.assign(data.url);});
  $('check-install').onclick=safeAction(async()=>{await api('/api/installation',{});session=await api('/api/session');renderAccounts();});
  $('use-quick').onclick=()=>{document.querySelector('[value="quick"]').checked=true;addressChanged();};
  $('accounts-next').onclick=safeAction(async()=>show(2));
  $('download-password').onclick=downloadPassword;
  $('password-saved').onchange=()=>{saved=$('password-saved').checked;updateButtons();};
  $('launch-consent').onchange=updateButtons;
  $('regenerate-password').onclick=()=>{password=null;ensurePassword();$('download-status').textContent='新密码已生成。请下载并确认新文件；之前未上传的密码文件不再用于这次初始化。';};
  $('restore-password').onchange=safeAction(async()=>{
    const file=$('restore-password').files[0]; if(!file||file.size>8192) throw new Error('请选择向导下载的小型 JSON 密码文件。');
    let data; try{data=JSON.parse(await file.text());}catch{throw new Error('这个文件不是有效的密码文件。');}
    if(data.format!=='desktop-bridge-owner-v1'|| !/^[A-Za-z0-9_-]{43}$/.test(data.owner_password)) throw new Error('文件中没有有效的电脑主人密码。');
    if(data.github_account!==session.user.login) throw new Error('这个文件属于其他 GitHub 账号，请选择对应账号的密码文件。');
    password=data.owner_password; downloaded=true;saved=false;$('password-saved').checked=false;
    $('download-status').textContent='已在当前页面读取密码。文件没有上传；请再次确认保存后继续。';
  });
  $('create-start').onclick=safeAction(createStart);
  $('recover-password').onclick=()=>show(2);
  $('check-status').onclick=safeAction(poll);
  $('retry-step').onclick=safeAction(async()=>{op=await api('/api/operations/'+op.id+'/retry',{});renderOperation();schedulePoll(200);});
  $('restart-preview').onclick=safeAction(async()=>{
    if(!window.confirm('再次运行同一个专用仓库，启动一台新的 60 分钟临时电脑。旧文件不会恢复；临时地址会变化，ChatGPT 可能需要重新连接。现在启动吗？'))return;
    op=await api('/api/operations/'+op.id+'/restart',{accepted_preview:true});renderOperation();schedulePoll(200);
  });
  $('reauthorize').onclick=safeAction(()=>authorize('github'));
  $('reauthorize-cf').onclick=safeAction(()=>authorize('cloudflare'));
  $('copy-mcp').onclick=safeAction(async()=>{try{await navigator.clipboard.writeText($('mcp-url').value);$('copy-mcp').textContent='已复制';}catch{$('mcp-url').focus();$('mcp-url').select();throw new Error('无法自动复制，地址已选中，请手动复制。');}});
  $('open-desktop').onclick=()=>{if(session.mode!=='mock'&&op.stage==='ready')window.open(op.origin,'_blank','noopener,noreferrer');};
  const stopCurrent=safeAction(async()=>{
    if(!window.confirm('停止后，这台电脑的文件和浏览器会话会被清空。确认已经下载需要的文件，现在停止吗？'))return;
    op=await api('/api/operations/'+op.id+'/stop',{});renderOperation();schedulePoll(300);
  });
  $('stop-preview').onclick=stopCurrent;
  $('stop-pending').onclick=stopCurrent;
  setInterval(countdown,1000);
  window.addEventListener('pagehide',()=>{password=null;closing=true;clearTimeout(pollTimer);});
  window.addEventListener('pageshow',event=>{if(event.persisted)window.location.reload();});
  (async()=>{
    try{
      session=await api('/api/session');
      const choice=sessionStorage.getItem('bridge-address-choice');
      if(choice==='named')document.querySelector('[value="named"]').checked=true;
      const pre=sessionStorage.getItem('bridge-prerequisites')==='confirmed';
      $('chatgpt-ok').checked=pre;$('temporary-ok').checked=pre;
      const resetScroll=sessionStorage.getItem('bridge-reset-scroll')==='1';
      sessionStorage.removeItem('bridge-reset-scroll');
      renderAccounts();message('global-notice',session.notice);op=session.operation;
      if(op){show(op.stage==='ready'?4:3,false);renderOperation();if(!terminal.has(op.stage))schedulePoll(300);}
      else show(session.user||pre?1:0,false);
      if(resetScroll) requestAnimationFrame(()=>window.scrollTo({top:0,behavior:'instant'}));
    }catch(e){message('global-error',e.message);}
  })();
})();
