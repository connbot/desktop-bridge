// Execute the real viewer script with a tiny DOM/network harness. Only noVNC's
// dynamic import is stubbed: these checks cover UI state, not rendering or server authorization.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../src/desktop_bridge/static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../src/desktop_bridge/static/index.html'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = data => ({ok:true, json:async () => data});
const failure = message => ({ok:false, json:async () => ({message})});
const status = {csrf:'test-only', state:'READY', events:[], mcp_url:'http://localhost/mcp', context_store:{provider:'disabled'}, in_flight:false};

function harness({defer = [], deferImport = false} = {}) {
  const elements = new Map(), timers = new Map(), requests = [], pending = [], calls = [], connections = [], imports = [];
  let timerId = 0;
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      hidden:id === 'workspace', open:false, textContent:'', value:'', dataset:{}, parentElement:{dataset:{}}, disabled:false, handlers:{}, attrs:{},
      addEventListener(name,handler){this.handlers[name]=handler;},
      setAttribute(name,value){this.attrs[name]=value;}, removeAttribute(name){delete this.attrs[name];},
      replaceChildren(){}, append(){}, close(){}, focus(){}, select(){}, classList:{add(){},remove(){}}
    });
    return elements.get(id);
  }
  const controls = ['paused','agent','private'].map(mode => { const button=element(mode); button.dataset.mode=mode; return button; });
  class RFB {
    constructor(screen,url){this.url=url;this.handlers={};this.disconnectCalls=0;connections.push(this);}
    addEventListener(name,handler){this.handlers[name]=handler;}
    emit(name){this.handlers[name]?.();}
    disconnect(){this.disconnectCalls++;this.emit('disconnect');}
  }
  const context = vm.createContext({
    document:{getElementById:element, querySelectorAll:selector=>selector==='[data-mode]'?controls:[], createElement:()=>element('new'), createTextNode:x=>x},
    fetch: async (url,options) => {
      calls.push({url,options});
      if (url === '/api/status') return new Promise((resolve,reject) => requests.push({resolve,reject}));
      if (defer.includes(url)) return new Promise((resolve,reject) => pending.push({url,resolve,reject}));
      if (url === '/api/artifacts') return response({files:[]});
      if (url === '/api/login') return response({csrf:'new-login-only'});
      if (url === '/api/logout' || url.startsWith('/api/control/')) return response({});
      return failure('Optional memory is off');
    },
    loadRFB:()=>deferImport ? new Promise(resolve=>imports.push(()=>resolve({default:RFB}))) : Promise.resolve({default:RFB}),
    URLSearchParams, URL, location:{search:'',origin:'http://localhost',protocol:'http:',host:'localhost'},
    setTimeout:(callback,ms)=>{const id=++timerId;timers.set(id,{callback,ms});return id;}, clearTimeout:id=>timers.delete(id),
    console, navigator:{}, crypto:{}, Date, JSON, Error, Array, Object, String, Promise
  });
  assert.ok(source.includes("await import('/novnc/core/rfb.js')"));
  vm.runInContext(source.replace("await import('/novnc/core/rfb.js')",'await loadRFB()'),context);
  return {
    element, controls, requests, pending, calls, connections, imports, timers, run:code=>vm.runInContext(code,context),
    async open(state=status){requests[0].resolve(response(state));await tick();},
    poll(){const [id,timer]=[...timers].find(([,timer])=>timer.ms===2500);timers.delete(id);return timer.callback();},
    expireConnect(){const [id,timer]=[...timers].find(([,timer])=>timer.ms===15000);timers.delete(id);return timer.callback();}
  };
}

test('an old status success cannot reopen the viewer after logout', async()=>{
  const h=harness();
  h.run('loginView()');
  h.requests[0].resolve(response(status));
  await tick();
  assert.equal(h.element('workspace').hidden,true);
  assert.equal(h.element('login').hidden,false);
  assert.equal(h.element('mcp-url').value,'');
  assert.equal(h.timers.size,0);
  assert.equal(h.connections.length,0);
});

test('an old status error cannot replace a newer login state', async()=>{
  const h=harness();
  h.run('loginView(); refresh()');
  h.requests[1].resolve(response(status));
  await tick();
  h.requests[0].resolve(failure('Login required'));
  await tick();
  assert.equal(h.element('workspace').hidden,false);
  assert.equal(h.element('login').hidden,true);
});

test('the latest status request wins even within the same login session', async()=>{
  const h=harness();
  h.run('refresh()');
  h.requests[1].resolve(response({...status,state:'PRIVATE'}));
  await tick();
  h.requests[0].resolve(response(status));
  await tick();
  assert.equal(h.element('status').textContent,'Private');
  assert.match(h.element('control-note').textContent,/until you resume/);
  assert.equal([...h.timers.values()].filter(timer=>timer.ms===2500).length,1);
});

test('login opens an interactive shared screen even while AI is working', async()=>{
  const h=harness();
  await h.open({...status,state:'AGENT',in_flight:true});
  assert.equal(h.element('workspace').hidden,false);
  assert.equal(h.element('login').hidden,true);
  assert.equal(h.element('mcp-url').value,status.mcp_url);
  assert.equal(h.connections[0].url,'ws://localhost/desktop/shared');
  assert.equal(h.connections[0].viewOnly,false);
  assert.equal([...h.timers.values()].filter(timer=>timer.ms===2500).length,1);
  assert.deepEqual(h.calls.map(call=>call.url),['/api/status']);
  assert.match(h.element('view-label').textContent,/Click or type/);
  assert.equal(h.element('paused').hidden,false);
  assert.equal(h.element('agent').hidden,true);
});

test('a current authentication error still returns to login', async()=>{
  const h=harness();
  h.element('workspace').hidden=false;
  h.element('login').hidden=true;
  h.requests[0].resolve(failure('Login required'));
  await tick();
  assert.equal(h.element('workspace').hidden,true);
  assert.equal(h.element('login').hidden,false);
});

test('a delayed initial authentication error cannot undo successful login', async()=>{
  const h=harness();
  h.element('token').value='synthetic-test-token';
  const submit={disabled:false};
  const login=h.element('login-form').handlers.submit({preventDefault(){},submitter:submit});
  await tick();
  h.requests[1].resolve(response(status));
  await login;
  h.requests[0].resolve(failure('Login required'));
  await tick();
  assert.equal(h.element('workspace').hidden,false);
  assert.equal(h.element('login').hidden,true);
  assert.equal(submit.disabled,false);
});

test('a delayed login response cannot reopen a logged-out viewer', async()=>{
  const h=harness({defer:['/api/login']});
  const submit={disabled:false};
  const login=h.element('login-form').handlers.submit({preventDefault(){},submitter:submit});
  h.run('loginView()');
  h.pending[0].resolve(response({csrf:'old-login'}));
  await login;
  assert.equal(h.requests.length,1);
  assert.equal(h.element('workspace').hidden,true);
  assert.equal(h.run('csrf'),'');
});

test('private and paused states keep the same owner screen and expose explicit resume', async()=>{
  const h=harness();
  await h.open();
  for (const state of ['PRIVATE','PAUSED','HUMAN','STOPPED']) {
    const polling=h.poll();
    h.requests.at(-1).resolve(response({...status,state}));
    await polling;
    assert.equal(h.connections.length,1);
    assert.equal(h.connections[0].disconnectCalls,0);
    assert.equal(h.element('agent').hidden,false);
    assert.equal(h.element('paused').hidden,true);
  }
  assert.equal(h.calls.some(call=>call.url.startsWith('/api/control/')),false);
});

test('disconnected shared screens reconnect through polling without a mode change', async()=>{
  const h=harness();
  await h.open();
  h.connections[0].emit('connect');
  h.connections[0].emit('disconnect');
  assert.equal([...h.timers.values()].some(timer=>timer.ms===15000),false);
  assert.equal(h.element('connection-overlay').hidden,false);
  assert.equal(h.element('connection-label').textContent,'Reconnecting…');
  const polling=h.poll();
  h.requests[1].resolve(response(status));
  await polling; await tick();
  assert.equal(h.connections.length,2);
  h.connections[1].emit('connect');
  assert.equal(h.element('connection-overlay').hidden,true);
  h.connections[0].emit('disconnect');
  assert.equal(h.element('connection-label').textContent,'Connected');
  assert.equal(h.element('screen').dataset.connected,'true');
  assert.equal(h.calls.some(call=>call.url.startsWith('/api/control/')),false);
});

test('a handshake with no events times out and retries without trusting late socket callbacks', async()=>{
  const h=harness();
  await h.open();
  const timedOut=h.connections[0];
  h.expireConnect();
  assert.equal(timedOut.disconnectCalls,1);
  assert.equal(h.element('connection-label').textContent,'Reconnecting…');
  assert.equal(h.element('connection-overlay').hidden,false);
  assert.equal(h.run('rfb'),null);
  const polling=h.poll();
  h.requests[1].resolve(response(status));
  await polling; await tick();
  assert.equal(h.connections.length,2);
  timedOut.emit('connect');
  assert.equal(h.element('screen').dataset.connected,'false');
  assert.equal(h.element('connection-label').textContent,'Connecting…');
  h.connections[1].emit('connect');
  timedOut.emit('disconnect');
  assert.equal(h.element('connection-label').textContent,'Connected');
  assert.equal(h.element('screen').dataset.connected,'true');
  assert.equal([...h.timers.values()].some(timer=>timer.ms===15000),false);
});

test('a timed-out noVNC import cannot create a stale screen after polling retries', async()=>{
  const h=harness({deferImport:true});
  await h.open();
  h.expireConnect();
  const polling=h.poll();
  h.requests[1].resolve(response(status));
  await polling;
  assert.equal(h.imports.length,2);
  h.imports[0]();
  await tick();
  assert.equal(h.connections.length,0);
  h.imports[1]();
  await tick();
  assert.equal(h.connections.length,1);
  h.connections[0].emit('connect');
  assert.equal(h.element('screen').dataset.connected,'true');
  assert.equal([...h.timers.values()].some(timer=>timer.ms===15000),false);
});

test('a pending noVNC import cannot create a screen after logout', async()=>{
  const h=harness({deferImport:true});
  await h.open();
  h.run('loginView()');
  h.imports[0]();
  await tick();
  assert.equal(h.connections.length,0);
  assert.equal(h.timers.size,0);
});

test('a delayed control response cannot refresh a logged-out session', async()=>{
  const h=harness({defer:['/api/control/paused']});
  await h.open();
  const pause=h.element('paused').handlers.click();
  h.run('loginView()');
  h.pending[0].resolve(response({}));
  await pause;
  assert.equal(h.requests.length,1);
  assert.equal(h.element('workspace').hidden,true);
  assert.equal(h.timers.size,0);
});

test('optional context errors do not delay the desktop or change a newer session', async()=>{
  const h=harness({defer:['/api/personal']});
  await h.open();
  h.element('personal-settings').open=true;
  h.element('personal-settings').handlers.toggle();
  h.run('loginView(); refresh()');
  h.requests[1].resolve(response(status));
  await tick();
  h.element('profile-summary').textContent='New session context';
  h.pending[0].resolve(failure('Old optional context error'));
  await tick();
  assert.equal(h.element('profile-summary').textContent,'New session context');
  assert.equal(h.element('workspace').hidden,false);
  assert.equal(h.connections.length,2);
});

test('late optional file and profile responses cannot repopulate a signed-out viewer', async()=>{
  const h=harness({defer:['/api/artifacts','/api/personal/profile']});
  await h.open();
  const files=h.run('files()');
  const save=h.element('profile-form').handlers.submit({preventDefault(){},submitter:{disabled:false}});
  h.run('loginView()');
  h.element('file-count').textContent='0';
  h.pending.find(request=>request.url==='/api/artifacts').resolve(response({files:[{path:'old.txt',bytes:100}]}));
  h.pending.find(request=>request.url==='/api/personal/profile').resolve(response({revision:1,profile:{name:'Old session'},tasks:[]}));
  await files; await save;
  assert.equal(h.element('file-count').textContent,'0');
  assert.equal(h.run('personalData'),null);
  assert.equal(h.element('personal-feedback').hidden,true);
  assert.equal(h.element('refresh-files').disabled,false);
});

test('a slow status response from before pause cannot overwrite the pause result', async()=>{
  const h=harness();
  await h.open();
  const oldPolling=h.poll();
  const pause=h.element('paused').handlers.click();
  await tick();
  h.requests[2].resolve(response({...status,state:'PAUSED'}));
  await pause;
  h.requests[1].resolve(response(status));
  await oldPolling;
  assert.equal(h.element('status').textContent,'AI paused');
  assert.equal(h.element('agent').hidden,false);
  assert.equal(h.connections.length,1);
});

test('shared and private messages describe automatic input and explicit privacy correctly', ()=>{
  const h=harness();
  assert.match(h.run('controlNote({state:"READY",in_flight:false})'),/Watching does not pause/);
  assert.match(h.run('controlNote({state:"AGENT",in_flight:true})'),/between AI actions/);
  assert.match(h.run('controlNote({state:"AGENT",in_flight:true,human_input_pending:true})'),/No hand-back needed/);
  assert.match(h.run('controlNote({state:"PRIVATE",in_flight:true})'),/observations and actions are blocked/);
  assert.match(h.run('controlNote({state:"PRIVATE",in_flight:false})'),/until you resume/);
  assert.match(h.run('controlNote({state:"PAUSED",in_flight:false})'),/Resume AI/);
});

test('the desktop precedes collapsed setup and optional content, with no takeover ritual', ()=>{
  const screen=html.indexOf('id="screen-shell"');
  assert.ok(screen>0);
  for (const id of ['connection-settings','workspace-details','personal-settings']) {
    const match=html.match(new RegExp(`<details[^>]*id="${id}"[^>]*>`));
    assert.ok(match,`${id} is a native collapsible section`);
    assert.ok(match.index>screen);
    assert.doesNotMatch(match[0],/\sopen(?:\s|>|=)/);
  }
  assert.doesNotMatch(html,/Take control|Hand back|id="reconnect"|data-mode="human"|Read-only viewer/);
});
