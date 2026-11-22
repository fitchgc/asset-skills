(function (global) {
  'use strict';

  let _state = {
    token: '',
    container: null,
    auth: null,
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
      .asx-acct-details p { margin: 0 0 16px; }
      .asx-acct-details .keycap { min-height: 44px; }
    `;
    document.head.appendChild(style);
  }

  async function apiJson(path, method, body) {
    const res = await fetch(path, {
      method: method || 'GET',
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

  function render() {
    if (!_state.container) return;
    const auth = _state.auth;
    const root = document.createElement('div');
    root.className = 'asx-acct-root' + (!auth || !auth.authenticated ? ' off' : '');

    if (!auth || !auth.authenticated) {
      const reason = (auth && auth.error && auth.error.message) || '请经 plaiers-community 授权（需 skill_jwt 权限）并用 get_skill 安装本 Skill';
      root.innerHTML = `
        <button type="button" class="asx-acct-pill" title="${escapeHtml(reason)}">
          <span class="asx-acct-avatar">?</span>
          <span>未授权</span>
        </button>
      `;
    } else {
      const id = auth.identity || {};
      const b = id.budget || {};
      const details = [auth.origin, `账号 ${id.subject ?? '--'}`, `账户额度 ${b.quota === null ? '不限额' : b.quota ?? '--'} ${b.currency ?? ''}`,
        `可用 ${formatBudget(id)}`, `预占 ${b.reserved_amount ?? '--'}`, `已用 ${b.used_amount ?? '--'}`,
        `查询时间 ${_state.checkedAt}`].join(' · ');
      root.innerHTML = `
        <details><summary class="asx-acct-pill" aria-label="查看账户资产积分明细">
          <span class="asx-acct-avatar">A</span>
          <span class="asx-acct-budget" aria-label="可用资产积分">${escapeHtml(formatBudget(auth.identity))}</span>
          <span class="asx-acct-meta" title="并发任务数">${escapeHtml(formatConcurrency(auth.identity))}</span>
        </summary><div class="asx-acct-details"><p>${escapeHtml(details)}</p><button type="button" class="keycap ghost sm" id="asx-acct-refresh">刷新资产积分</button></div></details>
      `;
      root.querySelector('#asx-acct-refresh').onclick = () => refresh();
    }
    _state.container.innerHTML = '';
    _state.container.appendChild(root);
  }

  async function refresh() {
    try {
      const data = await apiJson('/api/auth/status', 'GET');
      _state.checkedAt = new Date().toLocaleString();
      _state.auth = data;
      render();
      if (typeof _state.onAuthChange === 'function') {
        _state.onAuthChange(data);
      }
    } catch (_) {
      render();
    }
  }

  function mount(target, options) {
    injectStyles();
    const opts = options || {};
    _state.token = opts.token || '';
    _state.onAuthChange = opts.onAuthChange || null;
    _state.container = typeof target === 'string' ? document.querySelector(target) : target;

    render();
    return refresh();
  }

  global.AsxAccountBar = {
    mount,
    refresh,
  };
})(window);
