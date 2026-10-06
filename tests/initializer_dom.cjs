/* Run only through initializer_dom.py: actual local API, actual shipped JS and crypto. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {webcrypto} = require('node:crypto');
const root=path.resolve(__dirname,'..');
const {JSDOM,VirtualConsole}=createRequire(path.join(root,'initializer/ui/package.json'))('jsdom');
const origin=process.env.TEST_ORIGIN;
let cookie='', dom, errors=[], downloads=[], csrf;
const blobs=new Map();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(check, title) { for(let i=0;i<150;i++){if(check())return;await sleep(100);}throw new Error('Timed out: '+title+' | UI: '+$('global-error').textContent+' | progress: '+$('progress-title').textContent+' | errors: '+errors.join(';')); }
async function request(url,options={}) {
  const address=new URL(url,origin);
  assert.equal(address.origin,origin,'No external requests in offline test');
  const headers=new Headers(options.headers||{});if(cookie)headers.set('Cookie',cookie);
  if(options.method==='POST')headers.set('Origin',origin);
  const r=await fetch(address,{...options,headers});
  const set=r.headers.get('set-cookie');if(set)cookie=set.split(';')[0];return r;
}
async function api(url,body) {
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)};
  const r=await request(url,options);assert.equal(r.status,200,await r.clone().text());return r.json();
}
async function load() {
  if(dom) {dom.window.dispatchEvent(new dom.window.Event('pagehide'));dom.window.close();}
  const storage=load.storage||{};
  const vc=new VirtualConsole();vc.on('jsdomError',e=>{if(!String(e).includes('Not implemented: navigation'))errors.push(String(e));});
  dom=new JSDOM(fs.readFileSync(path.join(root,'initializer/static/index.html'),'utf8'),{url:origin,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  const w=dom.window;
  Object.defineProperty(w,'crypto',{value:webcrypto});Object.defineProperty(w,'isSecureContext',{value:true});
  w.TextEncoder=class {encode(value){return new w.Uint8Array(new TextEncoder().encode(value));}};w.TextDecoder=TextDecoder;w.Blob=Blob;w.fetch=request;
  w.URL.createObjectURL=blob=>{const url='blob:fixture/'+blobs.size;blobs.set(url,blob);return url;};w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){if(this.download)downloads.push({name:this.download,blob:blobs.get(this.href)});};
  w.Element.prototype.scrollIntoView=function(){};w.confirm=()=>true;w.open=()=>{};
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async text=>{w.testCopied=text;}}});
  for(const [k,v] of Object.entries(storage))w.sessionStorage.setItem(k,v);
  w.addEventListener('error',e=>errors.push(String(e.error)));
  w.eval(fs.readFileSync(path.join(root,'initializer/static/crypto.js'),'utf8'));
  w.eval(fs.readFileSync(path.join(root,'initializer/static/app.js'),'utf8'));
  await wait(()=>$('mode-banner').hidden===false,'script initialized');
  csrf=(await api('/api/session')).csrf;
}
const $=id=>dom.window.document.getElementById(id);
function click(id){assert.equal($(id).disabled,false,id+' should be enabled');$(id).click();}
function check(id,checked=true){$(id).checked=checked;$(id).dispatchEvent(new dom.window.Event('change'));}
function saveStorage(){load.storage=Object.fromEntries(Object.keys(dom.window.sessionStorage).map(k=>[k,dom.window.sessionStorage.getItem(k)]));}
async function login(){click('github-connect');await sleep(100);saveStorage();await load();await wait(()=>$('github-status').textContent.includes('preview-user'),'login');}
async function passwordStep(){click('accounts-next');await sleep(30);click('download-password');assert(downloads.at(-1).blob);check('password-saved');check('launch-consent');}
async function main(){
  await load();assert($('begin').disabled);check('chatgpt-ok');check('temporary-ok');click('begin');await login();
  await passwordStep();const first=JSON.parse(await downloads.at(-1).blob.text());assert.match(first.owner_password,/^[A-Za-z0-9_-]{43}$/);
  dom.window.document.querySelector('[data-view="2"] [data-back="1"]').click();click('accounts-next');await sleep(20);click('download-password');
  assert.equal(JSON.parse(await downloads.at(-1).blob.text()).owner_password,first.owner_password,'Back retains password');
  // Generate a new value, which must invalidate the saved confirmation.
  $('regenerate-password').click();assert($('create-start').disabled);assert(!$('password-saved').checked);click('download-password');
  const second=JSON.parse(await downloads.at(-1).blob.text());assert.notEqual(first.owner_password,second.owner_password);
  check('password-saved');check('launch-consent');click('create-start');$('create-start').click();
  await wait(()=>$('mcp-url').value.endsWith('/mcp'),'full quick flow ready');
  let inspect=await api('/api/mock/inspect',{});assert.equal(inspect.owner_password_length,43,'Browser sealed box decrypts correctly');
  assert.equal(inspect.calls.filter(x=>x==='create_repository').length,1);assert.equal(inspect.calls.filter(x=>x==='dispatch').length,1);assert.equal(inspect.ledger_count,1);
  const op1=(await api('/api/session')).operation;assert.equal(op1.stage,'ready');
  assert(!JSON.stringify(op1).includes(second.owner_password),'Ledger never contains password plaintext');
  click('copy-mcp');await sleep(20);assert.equal(dom.window.testCopied,$('mcp-url').value);
  saveStorage();await load();await wait(()=>$('mcp-url').value.endsWith('/mcp'),'ready survives refresh');
  assert.equal((await api('/api/session')).operation.id,op1.id,'Refresh preserves operation ID');
  click('stop-preview');await wait(()=>$('progress-title').textContent==='这次预览已结束','cancel reaches terminal');
  click('restart-preview');await wait(()=>$('mcp-url').value.endsWith('/mcp') && !dom.window.document.querySelector('[data-view="4"]').hidden,'explicit restart');
  inspect=await api('/api/mock/inspect',{});assert.equal(inspect.calls.filter(x=>x==='create_repository').length,1);assert.equal(inspect.calls.filter(x=>x==='dispatch').length,2);
  // Cloudflare account without an active zone has a clear escape hatch.
  await api('/api/mock/reset',{});await api('/api/mock/scenario',{scenario:'no_zones'});load.storage={};await load();
  check('chatgpt-ok');check('temporary-ok');click('begin');await login();
  const named=dom.window.document.querySelector('[name="address"][value="named"]');named.checked=true;named.dispatchEvent(new dom.window.Event('change'));
  click('cf-connect');await sleep(100);saveStorage();await load();await wait(()=>!$('no-zones').hidden,'no active zone');assert($('accounts-next').disabled);
  click('use-quick');assert(!$('accounts-next').disabled);assert($('github-status').textContent.includes('preview-user'));
  await passwordStep();await api('/api/mock/scenario',{scenario:'dispatch_unknown'});click('create-start');
  await wait(()=>$('progress-title').textContent==='还不能确认这一步的结果','uncertain dispatch');
  assert($('retry-step').hidden);assert($('restart-preview').hidden);
  inspect=await api('/api/mock/inspect',{});click('check-status');await sleep(100);
  assert.deepEqual((await api('/api/mock/inspect',{})).calls,inspect.calls,'unknown does not repeat writes');
  // A known preflight failure can be retried only after the user explicitly clicks.
  await api('/api/mock/reset',{});await api('/api/mock/scenario',{scenario:'template_not_enabled'});load.storage={};await load();
  check('chatgpt-ok');check('temporary-ok');click('begin');await login();await passwordStep();click('create-start');
  await wait(()=>!$('retry-step').hidden,'known failure retry');await api('/api/mock/scenario',{scenario:'success'});click('retry-step');
  await wait(()=>$('mcp-url').value.endsWith('/mcp'),'explicit retry succeeds');
  assert.deepEqual(errors,[],'No script errors');
  dom.window.close();console.log(JSON.stringify({passed:true,checks:18,live_providers:false,browser_engine:'jsdom (not visual browser QA)',scenarios:['download confirmation gate','Web Crypto CSPRNG (Node test adapter)','browser libsodium to real Python SealedBox','double-click single operation/run','back retains password','regeneration resets confirmation','refresh retains op ID','copy endpoint','cancel terminal','explicit restart no new repo','no active zone fallback','GitHub state retained','unknown write stops','no blind retry','explicit known-failure retry']}));
}
main().catch(e=>{console.error(e);if(dom)dom.window.close();process.exitCode=1;});
