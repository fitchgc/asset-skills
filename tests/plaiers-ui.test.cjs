// plPostWithChargeConsent + the shared-runtime (Pillow) prepare dialog, run against the real plaiers-ui.js.
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const MISSING = {
  ok: false,
  error: {code: 'dependency_missing', message: '本地图片处理需要可选依赖 Pillow（旧错误）', details: {prepare_command: 'python3 asynx.py runtime prepare'}},
};

// routes: url -> array of responses ({status, body} or an Error to throw), consumed in order.
function load({routes, confirms}) {
  const calls = [];
  const toasts = [];
  const toast = {set textContent(v) { toasts.push(v); }, setAttribute() {}};
  const context = {
    window: {confirm: () => confirms.shift()},
    document: {getElementById: () => toast, createElement: () => toast, body: {appendChild() {}}},
    setTimeout: () => 0, clearTimeout: () => {},
    fetch: async (url, init) => {
      calls.push({url, body: JSON.parse(init.body)});
      const next = routes[url].shift();
      if (next instanceof Error) throw next;
      return {ok: next.status < 400, status: next.status, json: async () => next.body};
    },
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../src/ui/common/plaiers-ui.js'), 'utf8'), context);
  return {post: context.window.plPostWithChargeConsent, calls, toasts};
}

const submits = calls => calls.filter(c => c.url === '/api/submit').length;

test('a failed prepare shows the prepare error and does not retry the request', async () => {
  const prepareError = {ok: false, error: {code: 'runtime_hash_mismatch', message: '下载的包与锁定哈希不一致，已拒绝安装'}};
  const {post, calls, toasts} = load({
    routes: {'/api/submit': [{status: 400, body: MISSING}], '/api/runtime/prepare': [{status: 400, body: prepareError}]},
    confirms: [true],
  });
  const {res, data} = await post('/api/submit', {a: 1}, {});
  assert.equal(submits(calls), 1, 'original request is not re-sent');
  assert.deepEqual(calls[1], {url: '/api/runtime/prepare', body: {user_confirmed: true}});
  assert.equal(res.status, 400);
  assert.equal(data.error.code, 'runtime_hash_mismatch');
  assert.equal(data.error.message, '下载的包与锁定哈希不一致，已拒绝安装');
  assert.ok(!JSON.stringify(data).includes('旧错误'), 'the stale dependency_missing error is not returned');
  assert.ok(toasts.includes('运行环境准备失败：下载的包与锁定哈希不一致，已拒绝安装'));
});

test('a prepare request that breaks off still reports a prepare failure, not the old error', async () => {
  const {post, calls} = load({
    routes: {'/api/submit': [{status: 400, body: MISSING}], '/api/runtime/prepare': [new TypeError('Failed to fetch')]},
    confirms: [true],
  });
  const {data} = await post('/api/submit', {}, {});
  assert.equal(submits(calls), 1);
  assert.equal(data.error.code, 'runtime_prepare_failed');
  assert.match(data.error.message, /Failed to fetch/);
});

test('declining the prepare dialog neither prepares nor retries, and is not reported as a failure', async () => {
  const {post, calls, toasts} = load({routes: {'/api/submit': [{status: 400, body: MISSING}], '/api/runtime/prepare': []}, confirms: [false]});
  const {res, data} = await post('/api/submit', {}, {});
  assert.deepEqual(calls.map(c => c.url), ['/api/submit']);
  assert.equal(res.ok, false);
  assert.equal(data.error.code, 'runtime_declined');
  assert.ok(!toasts.some(t => t.includes('准备失败')), 'no failure toast');
});

test('a successful prepare re-sends the original request once', async () => {
  const {post, calls} = load({
    routes: {
      '/api/submit': [{status: 400, body: MISSING}, {status: 200, body: {ok: true, result: {ok: true}}}],
      '/api/runtime/prepare': [{status: 200, body: {ok: true, prepared: true}}],
    },
    confirms: [true],
  });
  const {data} = await post('/api/submit', {a: 1}, {});
  assert.equal(data.ok, true);
  assert.deepEqual(calls.map(c => c.url), ['/api/submit', '/api/runtime/prepare', '/api/submit']);
  assert.deepEqual(calls[2].body, {a: 1});
});
