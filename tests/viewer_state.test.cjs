// Execute the real viewer script with a tiny DOM/network harness. This checks
// asynchronous UI state, not browser rendering or backend authorization.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../src/desktop_bridge/static/app.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = data => ({ok: true, json: async () => data});
const status = {csrf:'test-only', state:'READY', events:[], mcp_url:'http://localhost/mcp', context_store:{provider:'disabled'}, in_flight:true};

function harness() {
  const elements = new Map(), timers = [], requests = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      hidden:id === 'workspace', textContent:'', value:'', dataset:{}, parentElement:{dataset:{}}, disabled:false, handlers:{},
      addEventListener(name,handler){this.handlers[name]=handler;}, setAttribute(){}, removeAttribute(){}, replaceChildren(){}, append(){}, close(){},
      focus(){}, select(){}, classList:{add(){},remove(){}}
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    document:{getElementById:element, querySelectorAll:()=>[], createElement:()=>element('new'), createTextNode:x=>x},
    fetch: async url => {
      if (url === '/api/status') return new Promise((resolve,reject) => requests.push({resolve,reject}));
      if (url === '/api/artifacts') return response({files:[]});
      if (url === '/api/login') return response({csrf:'new-login-only'});
      return {ok:false,json:async()=>({message:'Optional memory is off'})};
    },
    URLSearchParams, URL, location:{search:'',origin:'http://localhost'},
    setTimeout:()=>timers.push(true), clearTimeout(){}, console, navigator:{}, crypto:{}, Date, JSON, Error, Array, Object, String, Promise
  });
  vm.runInContext(source, context);
  return {element, requests, timers, run:code=>vm.runInContext(code,context)};
}

test('an old status success cannot reopen the viewer after logout', async()=>{
  const h=harness();
  h.run('loginView()');
  h.requests[0].resolve(response(status));
  await tick();
  assert.equal(h.element('workspace').hidden,true);
  assert.equal(h.element('login').hidden,false);
  assert.equal(h.element('mcp-url').value,'');
  assert.equal(h.timers.length,0);
});

test('an old status error cannot replace a newer login state', async()=>{
  const h=harness();
  h.run('loginView(); refresh()');
  h.requests[1].resolve(response(status));
  await tick();
  assert.equal(h.element('workspace').hidden,false);
  h.requests[0].resolve({ok:false,json:async()=>({message:'Login required'})});
  await tick();
  assert.equal(h.element('workspace').hidden,false);
  assert.equal(h.element('login').hidden,true);
});

test('a current status success still opens the viewer and schedules polling', async()=>{
  const h=harness();
  h.requests[0].resolve(response(status));
  await tick();
  assert.equal(h.element('workspace').hidden,false);
  assert.equal(h.element('login').hidden,true);
  assert.equal(h.element('mcp-url').value,status.mcp_url);
  assert.ok(h.timers.length>0);
});

test('a current authentication error still returns to login', async()=>{
  const h=harness();
  h.element('workspace').hidden=false;
  h.element('login').hidden=true;
  h.requests[0].resolve({ok:false,json:async()=>({message:'Login required'})});
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
  assert.equal(h.requests.length,2);
  h.requests[1].resolve(response(status));
  await login;
  assert.equal(h.element('workspace').hidden,false);
  h.requests[0].resolve({ok:false,json:async()=>({message:'Login required'})});
  await tick();
  assert.equal(h.element('workspace').hidden,false);
  assert.equal(h.element('login').hidden,true);
  assert.equal(submit.disabled,false);
});


test('ready and active-action messages do not claim the AI is blocked', ()=>{
  const h=harness();
  assert.match(h.run('controlNote({state:"READY",in_flight:false})'),/Ready for your AI client/);
  assert.match(h.run('controlNote({state:"AGENT",in_flight:true})'),/AI is working/);
  assert.match(h.run('controlNote({state:"PRIVATE",in_flight:true})'),/New AI actions are blocked/);
  assert.match(h.run('controlNote({state:"PAUSED",in_flight:false})'),/Hand back to AI/);
});
