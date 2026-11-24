// Render the actual workbench account bar with the dev review's fractional balance.
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('preserves service precision, unit, account and query time', async () => {
  let html;
  const container = { appendChild: node => { html = node.innerHTML; } };
  const budget = {currency: 'USD', remaining_amount: '1.915', reserved_amount: '0.085', used_amount: '0.170'};
  const context = {AbortSignal, window: {}, document: {getElementById: () => true, createElement: () => ({querySelector: () => ({})})},
    fetch: async () => ({ok: true, json: async () => ({ok: true, authenticated: true, origin: 'dev.example.test', identity: {subject: 'account-42', budget}})})};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../src/ui/common/account-bar.js'), 'utf8'), context);
  await context.window.AsxAccountBar.mount(container, {token: 'local-fixture'});
  for (const value of ['USD 1.915', 'dev.example.test', 'account-42', '预占 0.085', '已用 0.170', '查询时间']) assert.ok(html.includes(value), value);
  assert.ok(!html.includes('1.92'));
  budget.remaining_amount = null;
  await context.window.AsxAccountBar.refresh();
  assert.ok(html.includes('不限额'));
});

function harness(fetcher) {
  let html = '', nodes = {};
  const container = {appendChild: node => {html = node.innerHTML;}};
  const context = {AbortSignal, navigator:{clipboard:{writeText:async()=>{}}}, window:{}, document:{getElementById:()=>true,
    createElement:()=>({querySelector:selector=>nodes[selector] ||= {}})}, fetch:fetcher};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../src/ui/common/account-bar.js'),'utf8'),context);
  return {api:context.window.AsxAccountBar,container,html:()=>html,nodes};
}
const response = data => ({ok:true,json:async()=>({ok:true,...data})});
test('slow balance is loading, failure is retryable, confirmed revoke clears prior identity', async()=>{
 let resolve, fail=false, revoked=false;
 const h=harness(()=>fail?Promise.reject(Error('offline')):revoked?Promise.resolve(response({authenticated:false,error:{message:'需要重新授权'}})):new Promise(r=>{resolve=r}));
 const pending=h.api.mount(h.container,{token:'fixture'});
 assert.match(h.html(),/正在检查授权与余额/);assert.ok(!h.html().includes('未授权'));
 resolve(response({authenticated:true,identity:{subject:'fixture-A',budget:{currency:'USD',remaining_amount:'1.915'}}}));await pending;
 fail=true;await h.api.refresh();assert.match(h.html(),/连接暂不可用/);assert.match(h.html(),/fixture-A/);assert.match(h.html(),/上次查询结果/);
 fail=false;revoked=true;await h.api.refresh();assert.match(h.html(),/需要连接 Agent/);assert.ok(!h.html().includes('fixture-A'));assert.match(h.html(),/复制给 Agent/);
});
test('older response cannot replace a later auth result',async()=>{
 const pending=[];const h=harness(()=>new Promise(r=>pending.push(r)));
 const first=h.api.mount(h.container,{token:'fixture'}),second=h.api.refresh();
 pending[1](response({authenticated:false}));await second;
 pending[0](response({authenticated:true,identity:{subject:'stale-account'}}));await first;
 assert.ok(!h.html().includes('stale-account'));assert.match(h.html(),/需要连接 Agent/);
});
test('Pillow prepare only happens after explicit consent and does not call generation',async()=>{
 const calls=[];const h=harness(async(path,init)=>{calls.push({path,init});return response(path==='/api/auth/status'?{authenticated:false}:path==='/api/runtime/status'?{runtime:{status:'not_prepared'},ffmpeg:false,ffprobe:false}:{prepared:true});});
 await h.api.mount(h.container,{token:'fixture'});assert.equal(calls.length,1);
 await h.nodes['#asx-runtime-check'].onclick();assert.match(h.html(),/同意下载并安装共享 Pillow/);
 await h.nodes['#asx-runtime-prepare'].onclick();assert.ok(!calls.some(x=>x.path==='/api/runtime/prepare'));
 h.nodes['#asx-runtime-consent'].checked=true;
 await h.nodes['#asx-runtime-prepare'].onclick();const submit=calls.filter(x=>x.path==='/api/runtime/prepare');assert.equal(submit.length,1);assert.equal(JSON.parse(submit[0].init.body).confirm_download,true);
 assert.ok(calls.every(x=>['/api/auth/status','/api/runtime/status','/api/runtime/prepare'].includes(x.path)));
});
