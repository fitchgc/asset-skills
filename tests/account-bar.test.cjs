// Workbench account-bar and shared Pillow-consent regression tests.
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../src/ui/common');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const json = body => ({ok: true, status: 200, json: async () => body});
const authorized = () => ({ok: true, state: 'authorized', auth_state: 'authorized', authenticated: true,
  origin: 'https://dev.example.test', identity: {subject: 'account-42', budget: {
    currency: 'USD', remaining_amount: '1.915', reserved_amount: '0.085', used_amount: '0.170',
  }}});

function harness(routes, confirms = []) {
  let html = '';
  const calls = [];
  const nodes = new Map();
  const makeNode = () => ({
    setAttribute() {}, appendChild() {}, select() {},
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, {onclick: null, onchange: null, checked: false, disabled: false});
      return nodes.get(selector);
    },
  });
  const document = {
    head: {appendChild() {}}, body: {appendChild() {}},
    getElementById: () => null,
    createElement: () => makeNode(),
  };
  const window = {confirm: message => { harness.lastConfirm = message; return confirms.shift() ?? false; },
    location: {search: '', hash: ''}, document};
  const context = {
    window, document, navigator: {clipboard: {writeText: async () => {}}},
    URLSearchParams, AbortSignal,
    setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, init = {}) => {
      calls.push({url, body: init.body && JSON.parse(init.body), init});
      const route = routes[url];
      if (!route) throw new Error(`unexpected request ${url}`);
      return route(init);
    },
  };
  vm.createContext(context);
  vm.runInContext(read('plaiers-ui.js'), context);
  vm.runInContext(read('account-bar.js'), context);
  const container = {appendChild: node => { html = node.innerHTML; }};
  const bar = window.AsxAccountBar;
  return {bar, container, calls, window, html: () => html, node: selector => nodes.get(selector),
    mount: (options = {}) => bar.mount(container, {token: 'local-fixture', ...options})};
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const runtimeNotPrepared = () => ({ok: true, runtime: {status: 'not_prepared', message: '本地图片处理需要共享 Pillow。',
  prepare_command: 'python3 asynx.py runtime prepare'}, ffmpeg: true, ffprobe: true});

function dependencyMissing() {
  return {ok: false, error: {code: 'dependency_missing', message: '本地编辑需要 Pillow。', details: {
    prepare_command: 'python3 asynx.py runtime prepare',
  }}};
}

test('preserves service precision, unit, account and query time', async () => {
  const h = harness({'/api/auth/status': async () => json(authorized())});
  await h.mount();
  for (const value of ['USD 1.915', 'dev.example.test', 'account-42', '预占 0.085', '已用 0.170', '查询时间']) assert.ok(h.html().includes(value), value);
  assert.ok(!h.html().includes('1.92'));
});

test('slow initial status stays checking and then shows the balance', async () => {
  let resolve;
  const h = harness({'/api/auth/status': () => new Promise(done => { resolve = done; })});
  const mounted = h.mount();
  await settle();
  assert.match(h.html(), /正在检查授权与余额/);
  assert.doesNotMatch(h.html(), /需要连接 Agent|未授权/);
  resolve(json(authorized()));
  await mounted;
  assert.match(h.html(), /USD 1.915/);
});

test('a timed-out status is unavailable and a retry recovers', async () => {
  let pending = true;
  const h = harness({'/api/auth/status': init => pending
    ? new Promise((resolve, reject) => {
      const fail = () => reject(init.signal.reason || new Error('timed out'));
      if (init.signal.aborted) fail(); else init.signal.addEventListener('abort', fail, {once: true});
    })
    : Promise.resolve(json(authorized()))});
  const mounted = h.mount({timeoutMs: 20});
  await mounted;
  assert.match(h.html(), /连接暂不可用/);
  assert.match(h.html(), /不等于授权已撤销/);
  assert.doesNotMatch(h.html(), /需要连接 Agent/);
  assert.match(h.html(), /复制给 Agent 的连接指令/);
  pending = false;
  await h.bar.refresh();
  assert.match(h.html(), /USD 1.915/);
});

test('a server outage shows its exact reason without reconnecting or discarding the balance', async () => {
  let request = 0;
  const h = harness({'/api/auth/status': async () => ++request === 1 ? json(authorized()) : json({ok: true,
    state: 'unavailable', auth_state: 'unavailable', authenticated: false,
    error: {code: 'need_login', message: 'JWT request failed (HTTP 429).'}})});
  await h.mount();
  await h.bar.refresh();
  assert.match(h.html(), /连接暂不可用/);
  assert.match(h.html(), /JWT request failed \(HTTP 429\)/);
  assert.match(h.html(), /account-42/);
  assert.doesNotMatch(h.html(), /需要连接 Agent/);
});

test('only a confirmed invalid credential exposes the reconnect instruction', async () => {
  const h = harness({'/api/auth/status': async () => json({ok: true, state: 'unauthorized',
    auth_state: 'needs_connection', authenticated: false,
    identity_error: {code: 'need_login', message: 'Authorization expired or revoked; reconnect explicitly.'}})});
  await h.mount();
  assert.match(h.html(), /需要连接 Agent/);
  assert.match(h.html(), /Authorization expired or revoked/);
  assert.match(h.html(), /复制给 Agent 的连接指令/);
  assert.match(h.html(), /查看 dev 安装与授权步骤/);
});

test('account menu cancellation does not prepare Pillow', async () => {
  const h = harness({
    '/api/auth/status': async () => json(authorized()),
    '/api/runtime/status': async () => json(runtimeNotPrepared()),
    '/api/runtime/prepare': async () => { throw new Error('must not be called'); },
  }, [false]);
  await h.mount();
  await h.node('#asx-runtime-check').onclick();
  assert.match(h.html(), /FFmpeg：可用/);
  assert.match(h.html(), /安装共享 Pillow/);
  await h.node('#asx-runtime-prepare').onclick();
  assert.match(harness.lastConfirm, /python3 asynx.py runtime prepare/);
  assert.equal(h.calls.filter(call => call.url === '/api/runtime/prepare').length, 0);
});

test('account menu consent uses the shared prepare protocol and refreshes local status', async () => {
  let runtimeChecks = 0;
  const h = harness({
    '/api/auth/status': async () => json(authorized()),
    '/api/runtime/status': async () => json(++runtimeChecks === 1 ? runtimeNotPrepared() : {ok: true,
      runtime: {status: 'ready'}, ffmpeg: true, ffprobe: true}),
    '/api/runtime/prepare': async () => json({ok: true, prepared: true}),
  }, [true]);
  await h.mount();
  await h.node('#asx-runtime-check').onclick();
  await h.node('#asx-runtime-prepare').onclick();
  const prepares = h.calls.filter(call => call.url === '/api/runtime/prepare');
  assert.equal(prepares.length, 1);
  assert.deepEqual(prepares[0].body, {user_confirmed: true, confirm_download: true});
  assert.match(h.html(), /Pillow：可用/);
  assert.ok(h.calls.every(call => !['/api/submit', '/api/manager/action'].includes(call.url)));
});

test('account menu shows the specific Pillow prepare failure', async () => {
  const h = harness({
    '/api/auth/status': async () => json(authorized()),
    '/api/runtime/status': async () => json(runtimeNotPrepared()),
    '/api/runtime/prepare': async () => ({ok: false, status: 400, json: async () => ({ok: false,
      error: {code: 'runtime_hash_mismatch', message: '下载的包与锁定哈希不一致，已拒绝安装'}})}),
  }, [true]);
  await h.mount();
  await h.node('#asx-runtime-check').onclick();
  await h.node('#asx-runtime-prepare').onclick();
  assert.match(h.html(), /运行环境准备失败：下载的包与锁定哈希不一致，已拒绝安装/);
});

test('edit dialog cancellation makes no install and does not retry', async () => {
  const h = harness({'/api/submit': async () => json(dependencyMissing())}, [false]);
  const result = await h.window.plPostWithChargeConsent('/api/submit', {source: 'x'}, {});
  assert.equal(result.data.error.code, 'runtime_declined');
  assert.deepEqual(h.calls.map(call => call.url), ['/api/submit']);
});

test('edit dialog shares consent and retries the original edit exactly once after install', async () => {
  let submissions = 0;
  const h = harness({
    '/api/submit': async () => ++submissions === 1 ? json(dependencyMissing()) : json({ok: true, task_id: 'task-1'}),
    '/api/runtime/prepare': async () => json({ok: true, prepared: true}),
  }, [true]);
  const payload = {source: 'original'};
  const result = await h.window.plPostWithChargeConsent('/api/submit', payload, {});
  assert.equal(result.data.task_id, 'task-1');
  assert.deepEqual(h.calls.map(call => call.url), ['/api/submit', '/api/runtime/prepare', '/api/submit']);
  assert.deepEqual(h.calls[1].body, {user_confirmed: true, confirm_download: true});
  assert.deepEqual(h.calls[0].body, payload);
  assert.deepEqual(h.calls[2].body, payload);
});

test('edit dialog displays install error and never retries a failed prepare', async () => {
  const h = harness({
    '/api/submit': async () => json(dependencyMissing()),
    '/api/runtime/prepare': async () => ({ok: false, status: 400, json: async () => ({ok: false,
      error: {code: 'runtime_hash_mismatch', message: '下载的包与锁定哈希不一致，已拒绝安装'}})}),
  }, [true]);
  const result = await h.window.plPostWithChargeConsent('/api/submit', {source: 'x'}, {});
  assert.equal(result.data.error.message, '下载的包与锁定哈希不一致，已拒绝安装');
  assert.deepEqual(h.calls.map(call => call.url), ['/api/submit', '/api/runtime/prepare']);
});

test('a second dependency failure after successful install does not open another dialog', async () => {
  const h = harness({
    '/api/submit': async () => json(dependencyMissing()),
    '/api/runtime/prepare': async () => json({ok: true, prepared: true}),
  }, [true]);
  const result = await h.window.plPostWithChargeConsent('/api/submit', {source: 'x'}, {});
  assert.equal(result.data.error.code, 'dependency_missing');
  assert.deepEqual(h.calls.map(call => call.url), ['/api/submit', '/api/runtime/prepare', '/api/submit']);
});
