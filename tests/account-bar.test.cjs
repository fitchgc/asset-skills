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
  const context = {window: {}, document: {getElementById: () => true, createElement: () => ({querySelector: () => ({})})},
    fetch: async () => ({ok: true, json: async () => ({ok: true, authenticated: true, origin: 'dev.example.test', identity: {subject: 'account-42', budget}})})};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../src/ui/common/account-bar.js'), 'utf8'), context);
  await context.window.AsxAccountBar.mount(container, {token: 'local-fixture'});
  for (const value of ['USD 1.915', 'dev.example.test', 'account-42', '预占 0.085', '已用 0.170', '查询时间']) assert.ok(html.includes(value), value);
  assert.ok(!html.includes('1.92'));
  budget.remaining_amount = null;
  await context.window.AsxAccountBar.refresh();
  assert.ok(html.includes('不限额'));
});
