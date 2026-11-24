// Render the actual workbench account bar (and, for the Pillow entry, the shared plaiers-ui.js prepare flow).
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(path.resolve(__dirname, '../src/ui/common', name), 'utf8');
const json = body => ({ok: true, status: 200, json: async () => body});
const AUTHORIZED = {ok: true, state: 'authorized', authenticated: true, origin: 'dev.example.test',
  identity: {subject: 'account-42', budget: {currency: 'USD', remaining_amount: '1.915', reserved_amount: '0.085', used_amount: '0.170'}}};

// routes: url -> function(init) returning a response promise (or throwing). Unlisted /api/runtime/status -> no runtime row.
function mountWith(routes, {withUi = false, confirms = [], timeoutMs, timers = false} = {}) {
  const view = {html: ''};
  const calls = [];
  const container = {appendChild: node => { view.html = node.innerHTML; }};
  const toast = {set textContent(v) {}, setAttribute() {}};
  const context = {
    window: {confirm: () => confirms.shift()},
    document: {getElementById: () => toast, createElement: () => ({querySelector: () => ({}), ...toast}), body: {appendChild() {}}},
    fetch: async (url, init = {}) => {
      calls.push({url, body: init.body && JSON.parse(init.body)});
      const route = routes[url] || (url === '/api/runtime/status' ? async () => json({ok: true, runtime: null}) : null);
      return route(init);
    },
    ...(timers ? {setTimeout, clearTimeout, AbortController} : {setTimeout: () => 0, clearTimeout: () => {}}),
  };
  context.window.document = context.document;
  vm.createContext(context);
  if (withUi) vm.runInContext(read('plaiers-ui.js'), context);
  vm.runInContext(read('account-bar.js'), context);
  const bar = context.window.AsxAccountBar;
  return {view, calls, bar, mounted: bar.mount(container, {token: 'local-fixture', ...(timeoutMs ? {timeoutMs} : {})})};
}

test('preserves service precision, unit, account and query time', async () => {
  const {view, bar, mounted} = mountWith({'/api/auth/status': async () => json(AUTHORIZED)});
  await mounted;
  for (const value of ['USD 1.915', 'dev.example.test', 'account-42', '预占 0.085', '已用 0.170', '查询时间']) assert.ok(view.html.includes(value), value);
  assert.ok(!view.html.includes('1.92'));
  AUTHORIZED.identity.budget.remaining_amount = null;
  await bar.refresh();
  assert.ok(view.html.includes('不限额'));
  AUTHORIZED.identity.budget.remaining_amount = '1.915';
});

test('a slow status shows "checking", never "unauthorized", then the balance', async () => {
  let answer;
  const {view, mounted} = mountWith({'/api/auth/status': () => new Promise(resolve => { answer = resolve; })});
  await new Promise(r => setImmediate(r));
  assert.ok(view.html.includes('正在检查授权与余额'));
  assert.ok(!view.html.includes('未授权'), 'no unauthorized before the first answer');
  answer(json(AUTHORIZED));
  await mounted;
  assert.ok(view.html.includes('USD 1.915'));
  assert.ok(!view.html.includes('正在检查授权与余额'));
});
test('a status request that times out shows "connection unavailable"; re-check recovers the balance', async () => {
  let hang = true;
  const {view, bar, mounted} = mountWith({
    '/api/auth/status': init => hang
      ? new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted'))))
      : Promise.resolve(json(AUTHORIZED)),
  }, {timers: true, timeoutMs: 20});
  await mounted;
  assert.ok(view.html.includes('连接暂不可用'));
  assert.ok(view.html.includes('查询授权与余额超过 0 秒没有返回'));
  assert.ok(view.html.includes('授权仍然保留'));
  assert.ok(view.html.includes('重新检查'));
  assert.ok(!view.html.includes('未授权'));
  hang = false;
  await bar.refresh();
  assert.ok(view.html.includes('USD 1.915'));
});

test('a failed status request is reported as unavailable with its reason, not as unauthorized', async () => {
  const {view, mounted} = mountWith({'/api/auth/status': async () => { throw new Error('connection refused'); }});
  await mounted;
  assert.ok(view.html.includes('连接暂不可用'));
  assert.ok(view.html.includes('无法查询授权与余额：connection refused'));
  assert.ok(!view.html.includes('未授权'));
});

test('a gateway outage reported by the server keeps the authorization and shows its reason', async () => {
  const {view, mounted} = mountWith({'/api/auth/status': async () => json({ok: true, state: 'unavailable', authenticated: true,
    identity: null, identity_error: {code: 'network_error', message: '网络连接失败：timed out'}})});
  await mounted;
  assert.ok(view.html.includes('连接暂不可用'));
  assert.ok(view.html.includes('网络连接失败：timed out'));
  assert.ok(!view.html.includes('未授权'));
});

test('only an invalid credential asks to reconnect, with the reason and a re-check', async () => {
  const message = '当前 JWT 已被资产网关拒绝';
  const {view, mounted} = mountWith({'/api/auth/status': async () => json({ok: true, state: 'unauthorized', authenticated: false,
    error: {code: 'need_login', message}})});
  await mounted;
  assert.ok(view.html.includes('<details>'), 'clickable disclosure, not a bare button');
  assert.ok(view.html.includes('未授权'));
  assert.ok(view.html.includes(message));
  assert.ok(view.html.includes('重新连接'));
  assert.ok(view.html.includes('重新检查'));
});

test('an older status answer arriving after a newer one is ignored', async () => {
  const answers = [];
  const {view, bar} = mountWith({'/api/auth/status': () => new Promise(resolve => answers.push(resolve))});
  const second = bar.refresh();
  await new Promise(r => setImmediate(r));
  answers[1](json(AUTHORIZED));
  await second;
  answers[0](json({ok: true, state: 'unauthorized', authenticated: false, error: {code: 'need_login', message: 'stale'}}));
  await new Promise(r => setImmediate(r));
  assert.ok(view.html.includes('USD 1.915'));
  assert.ok(!view.html.includes('stale'));
});

// --- account-menu Pillow entry: the same plPrepareRuntime as the edit dialog --------------------

const NOT_PREPARED = {ok: true, runtime: {status: 'not_prepared', message: '共享运行环境未准备', prepare_command: 'python3 asynx.py runtime prepare'}};

function mountRuntime(prepare, confirms) {
  const runtimes = [NOT_PREPARED, {ok: true, runtime: {status: 'ready'}}];
  return mountWith({
    '/api/auth/status': async () => json(AUTHORIZED),
    '/api/runtime/status': async () => json(runtimes.length > 1 ? runtimes.shift() : runtimes[0]),
    '/api/runtime/prepare': prepare,
  }, {withUi: true, confirms});
}

const settle = () => new Promise(r => setImmediate(r));

test('account menu offers repair for an invalid shared runtime even with interpreter Pillow', async () => {
  const {view, mounted} = mountWith({
    '/api/auth/status': async () => json(AUTHORIZED),
    '/api/runtime/status': async () => json({ok: true, runtime: {...NOT_PREPARED.runtime, status: 'invalid', interpreter_pillow: '12.0'}}),
  }, {withUi: true});
  await mounted; await settle();
  assert.ok(view.html.includes('准备 Pillow'));
  assert.ok(!view.html.includes('已就绪'));
});

test('account menu: cancelling the Pillow dialog stops without preparing', async () => {
  const {view, calls, bar, mounted} = mountRuntime(async () => { throw new Error('must not be called'); }, [false]);
  await mounted; await settle();
  assert.ok(view.html.includes('准备 Pillow'));
  await bar.prepareRuntime();
  assert.ok(!calls.some(c => c.url === '/api/runtime/prepare'));
  assert.ok(view.html.includes('准备 Pillow'), 'still offered after cancel');
  assert.ok(!view.html.includes('准备失败'));
});

test('account menu: agreeing prepares with the shared protocol and shows the runtime ready', async () => {
  const {view, calls, bar, mounted} = mountRuntime(async () => json({ok: true, prepared: true}), [true]);
  await mounted; await settle();
  await bar.prepareRuntime();
  const prepares = calls.filter(c => c.url === '/api/runtime/prepare');
  assert.equal(prepares.length, 1);
  assert.deepEqual(prepares[0].body, {user_confirmed: true, confirm_download: true});
  assert.ok(view.html.includes('已就绪'));
  assert.ok(!calls.some(c => c.url === '/api/submit' || c.url === '/api/manager/action'), 'menu entry retries no edit');
});

test('account menu: a failed prepare shows the specific reason and can be tried again', async () => {
  const failure = {ok: false, status: 400, json: async () => ({ok: false, error: {code: 'runtime_hash_mismatch', message: '下载的包与锁定哈希不一致，已拒绝安装'}})};
  const {view, bar, mounted} = mountRuntime(async () => failure, [true]);
  await mounted; await settle();
  await bar.prepareRuntime();
  assert.ok(view.html.includes('运行环境准备失败：下载的包与锁定哈希不一致，已拒绝安装'));
  assert.ok(view.html.includes('准备 Pillow'));
});
