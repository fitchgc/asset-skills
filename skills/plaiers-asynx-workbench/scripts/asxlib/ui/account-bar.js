(function (global) {
  'use strict';

  // 桌接 60 秒 + 网关查询；超过后显示“连接暂不可用”，可重新检查。
  const STATUS_TIMEOUT_MS = 90000;

  let _state = {
    token: '',
    container: null,
    auth: null, // 最近一次完成的查询；状态见 view()
    checking: false,
    seq: 0, // 只采用最后一次发出的查询
    timeoutMs: STATUS_TIMEOUT_MS,
    runtime: null, // 共享 Pillow 运行环境状态（本机，与授权无关）
    runtimeBusy: false,
    runtimeError: null,
    menuOpen: false,
    onAuthChange: null,
  };

  function getSessionToken() {
    if (_state.token) return _state.token;
    return (
      new URLSearchParams(window.location.search).get('token') ||
      window.location.hash.replace('#token=', '') ||
      ''
    );
  }

  function escapeHtml(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatBudget(identity) {
    if (!identity || !identity.budget) return '--';
    const rem = identity.budget.remaining_amount;
    if (rem === null) return '不限额';
    if (rem === undefined || rem === '') return '--';
    return [identity.budget.currency, rem].filter(v => v != null).join(' ');
  }

  function formatConcurrency(identity) {
    if (!identity) return '并发 --';
    const active = identity.usage && identity.usage.active_tasks != null ? identity.usage.active_tasks : 0;
    const limit =
      identity.api_key && identity.api_key.concurrency_limit != null
        ? identity.api_key.concurrency_limit
        : '∞';
    return `并发 ${active}/${limit}`;
  }

  // 颜色、字体、圆角都取 plaiers.css 的令牌；宿主页面需先引入 plaiers.css
  function injectStyles() {
    if (document.getElementById('asx-account-bar-styles')) return;
    const style = document.createElement('style');
    style.id = 'asx-account-bar-styles';
    style.textContent = `
      .asx-acct-root { position: relative; display: inline-flex; align-items: center; font-family: var(--cn); }
      .asx-acct-pill {
        display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 14px 0 4px;
        border-radius: var(--r-pill); border: 2px solid var(--n700); background: var(--n850);
        color: var(--text); font: 700 13px/1 var(--cn); white-space: nowrap; cursor: pointer;
      }
      .asx-acct-pill:hover { border-color: var(--n500); }
      .asx-acct-avatar {
        width: 26px; height: 26px; border-radius: var(--r-pill); flex: none;
        display: inline-grid; place-items: center; font: 900 12px/1 var(--cn);
        background: var(--start); color: var(--cocoa);
      }
      .asx-acct-root.off .asx-acct-avatar { background: var(--n700); color: var(--n300); }
      .asx-acct-root.off .asx-acct-pill { color: var(--n300); }
      .asx-acct-budget { font-family: var(--en); font-weight: 400; font-size: 15px; letter-spacing: .03em; color: var(--coin); }
      .asx-acct-meta { color: var(--n400); font-weight: 500; font-variant-numeric: tabular-nums; }
      .asx-acct-root summary { list-style: none; }
      .asx-acct-root summary::-webkit-details-marker { display: none; }
      .asx-acct-details {
        position: absolute; right: 0; top: calc(100% + 12px); z-index: 80;
        width: min(360px, calc(100vw - 32px)); box-sizing: border-box; padding: 16px;
        white-space: normal; overflow-wrap: anywhere; color: var(--text);
        background: var(--n900); border: 2px solid var(--n700); border-radius: var(--r-lg);
        box-shadow: 4px 4px 0 var(--hard); font: 400 13px/1.7 var(--cn);
      }
      .asx-acct-details p { margin: 0 0 16px; white-space: pre-line; }
      .asx-acct-details .asx-acct-err { color: var(--danger); }
      .asx-acct-runtime { margin-top: 16px; padding-top: 16px; border-top: 2px solid var(--n700); }
      .asx-acct-details .keycap { min-height: 44px; }
    `;
    document.head.appendChild(style);
  }

  async function apiJson(path, method, body, signal) {
    const res = await fetch(path, {
      method: method || 'GET',
      ...(signal ? { signal } : {}),
      headers: {
        'X-Asx-Session-Token': getSessionToken(),
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      const msg = (data.error && data.error.message) || '请求失败';
      throw new Error(msg);
    }
    return data;
  }

  // checking：首次查询尚未返回；authorized：有余额；unavailable：查询失败或超时，授权仍保留；
  // unauthorized：凭据确实缺失、被撤销或被拒绝，需要重新连接。
  function view() {
    const auth = _state.auth;
    if (!auth) return 'checking';
    return auth.state || (auth.authenticated ? 'authorized' : 'unauthorized');
  }

  function errorText(auth) {
    const err = auth && (auth.error || auth.identity_error);
    return err && err.message ? err.message : '';
  }

  function runtimeHtml() {
    const rt = _state.runtime;
    if (!rt) return '';
    const canPrepare = rt.prepare_command && typeof global.plPrepareRuntime === 'function';
    const ready = rt.status === 'ready' || (rt.status !== 'invalid' && rt.interpreter_pillow);
    const line = ready
      ? '本地图片编辑（Pillow）：已就绪'
      : rt.status === 'unsupported'
        ? '本地图片编辑（Pillow）：当前平台没有锁定的运行环境'
        : (rt.message || '本地图片编辑（Pillow）：未准备');
    return `<div class="asx-acct-runtime"><p>${escapeHtml(line)}</p>${
      _state.runtimeError ? `<p class="asx-acct-err">${escapeHtml(_state.runtimeError)}</p>` : ''}${
      !ready && canPrepare
        ? `<button type="button" class="keycap ghost sm" id="asx-acct-runtime"${_state.runtimeBusy ? ' disabled' : ''}>${
          _state.runtimeBusy ? '正在准备…' : '准备 Pillow'}</button>`
        : ''}</div>`;
  }

  function render() {
    if (!_state.container) return;
    const auth = _state.auth;
    const state = view();
    const wasOpen = typeof _state.container.querySelector === 'function'
      && !!(_state.container.querySelector('details') || {}).open;
    const root = document.createElement('div');
    root.className = 'asx-acct-root' + (state === 'authorized' ? '' : ' off');
    const open = wasOpen ? ' open' : '';
    const recheck = _state.checking ? '正在检查…' : '重新检查';
    const disabled = _state.checking ? ' disabled' : '';

    if (state === 'authorized') {
      const id = auth.identity || {};
      const b = id.budget || {};
      const details = [auth.origin, `账号 ${id.subject ?? '--'}`, `账户额度 ${b.quota === null ? '不限额' : b.quota ?? '--'} ${b.currency ?? ''}`,
        `可用 ${formatBudget(id)}`, `预占 ${b.reserved_amount ?? '--'}`, `已用 ${b.used_amount ?? '--'}`,
        `查询时间 ${_state.checkedAt}`].join(' · ');
      root.innerHTML = `
        <details${open}><summary class="asx-acct-pill" aria-label="查看账户资产积分明细">
          <span class="asx-acct-avatar">A</span>
          <span class="asx-acct-budget" aria-label="可用资产积分">${escapeHtml(formatBudget(auth.identity))}</span>
          <span class="asx-acct-meta" title="并发任务数">${escapeHtml(formatConcurrency(auth.identity))}</span>
        </summary><div class="asx-acct-details"><p>${escapeHtml(details)}</p><button type="button" class="keycap ghost sm" id="asx-acct-refresh"${disabled}>${_state.checking ? '正在刷新…' : '刷新资产积分'}</button>${runtimeHtml()}</div></details>
      `;
    } else {
      // 每种状态都能点开：原因写在弹层里，并可重新检查（例如网络恢复、用户刚重新安装或授权完）
      const label = { checking: '正在检查授权与余额', unavailable: '连接暂不可用', unauthorized: '未授权' }[state];
      const reason = {
        checking: '正在检查授权与余额…',
        unavailable: `${errorText(auth) || '暂时无法连接 Plaiers 服务'}\n授权仍然保留，无需重新连接；网络恢复后点“重新检查”。`,
        unauthorized: `${errorText(auth) || '请经 plaiers-community 授权（需 skill_jwt 权限）并安装本 Skill'}\n请重新连接 Plaiers 授权后点“重新检查”。`,
      }[state];
      root.innerHTML = `
        <details${open}><summary class="asx-acct-pill" aria-label="查看${label}的原因">
          <span class="asx-acct-avatar">${state === 'checking' ? '…' : state === 'unavailable' ? '!' : '?'}</span>
          <span>${label}</span>
        </summary><div class="asx-acct-details"><p>${escapeHtml(reason)}</p>${
          state === 'checking' ? '' : `<button type="button" class="keycap ghost sm" id="asx-acct-refresh"${disabled}>${recheck}</button>`
        }${runtimeHtml()}</div></details>
      `;
    }
    const refreshBtn = root.querySelector('#asx-acct-refresh');
    if (refreshBtn) refreshBtn.onclick = () => refresh();
    const runtimeBtn = root.querySelector('#asx-acct-runtime');
    if (runtimeBtn) runtimeBtn.onclick = () => prepareRuntime();
    _state.container.innerHTML = '';
    _state.container.appendChild(root);
  }

  // 查询失败、超时或本地服务断开都只说明“连接暂不可用”，不把授权判为失效；只有服务端确认凭据失效才是 unauthorized。
  async function refresh() {
    const seq = ++_state.seq;
    _state.checking = true;
    render();
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    let timedOut = false;
    const timer = typeof setTimeout === 'function'
      ? setTimeout(() => { timedOut = true; if (controller) controller.abort(); }, _state.timeoutMs)
      : null;
    let data;
    try {
      data = await apiJson('/api/auth/status', 'GET', null, controller && controller.signal);
    } catch (err) {
      data = {
        ok: true, state: 'unavailable', authenticated: false, identity: null,
        error: { code: timedOut ? 'status_timeout' : 'status_unavailable',
          message: timedOut
            ? `查询授权与余额超过 ${Math.round(_state.timeoutMs / 1000)} 秒没有返回`
            : '无法查询授权与余额：' + err.message },
      };
    } finally {
      if (timer !== null) clearTimeout(timer);
    }
    if (seq !== _state.seq) return; // 已有更新的查询，这个回答作废
    _state.checking = false;
    _state.checkedAt = new Date().toLocaleString();
    _state.auth = data;
    render();
    if (typeof _state.onAuthChange === 'function') _state.onAuthChange(data);
  }

  async function loadRuntime() {
    try {
      const data = await apiJson('/api/runtime/status', 'GET');
      _state.runtime = data.runtime || null;
    } catch (_err) {
      _state.runtime = null; // 读不到就不显示这一栏；编辑弹窗遇到缺依赖时仍会询问
    }
    render();
  }

  // 与编辑弹窗同一个确认和准备流程（plaiers-ui.js 的 plPrepareRuntime）；账户菜单只准备，不重试任何编辑
  async function prepareRuntime() {
    const rt = _state.runtime;
    if (!rt || !rt.prepare_command || _state.runtimeBusy) return;
    _state.runtimeBusy = true;
    _state.runtimeError = null;
    render();
    const headers = { 'Content-Type': 'application/json', 'X-Asx-Session-Token': getSessionToken() };
    const prep = await global.plPrepareRuntime({ message: rt.message, details: { prepare_command: rt.prepare_command } }, headers);
    _state.runtimeBusy = false;
    if (prep.status === 'failed') {
      const err = prep.data && prep.data.error;
      _state.runtimeError = '运行环境准备失败：' + ((err && err.message) || `HTTP ${prep.res && prep.res.status}`);
      render();
      return;
    }
    if (prep.status === 'ready') await loadRuntime();
    else render();
  }

  function mount(target, options) {
    injectStyles();
    const opts = options || {};
    _state.token = opts.token || '';
    _state.onAuthChange = opts.onAuthChange || null;
    if (opts.timeoutMs) _state.timeoutMs = opts.timeoutMs;
    _state.container = typeof target === 'string' ? document.querySelector(target) : target;

    render();
    loadRuntime();
    return refresh();
  }

  global.AsxAccountBar = {
    mount,
    refresh,
    prepareRuntime,
  };
})(window);
