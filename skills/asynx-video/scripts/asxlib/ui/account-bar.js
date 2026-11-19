(function (global) {
  'use strict';

  let _state = {
    token: '',
    theme: 'light',
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
    if (rem === null || rem === undefined || rem === '') return '不限额';
    const n = Number(rem);
    return Number.isFinite(n) ? `$${n.toFixed(2)}` : `$${rem}`;
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

  function injectStyles() {
    if (document.getElementById('asx-account-bar-styles')) return;
    const style = document.createElement('style');
    style.id = 'asx-account-bar-styles';
    style.textContent = `
      .asx-acct-root {
        position: relative;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
        font-size: 12px;
      }
      .asx-acct-pill {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 4px 10px;
        border-radius: 999px;
        border: 1px solid #e4e4e7;
        background: #fafafa;
        color: #18181b;
        cursor: pointer;
        font-size: 12px;
        line-height: 1.2;
        transition: all 0.15s ease;
      }
      .asx-acct-pill:hover {
        border-color: #a1a1aa;
        background: #f4f4f5;
      }
      .asx-acct-root.dark .asx-acct-pill {
        background: #27272a;
        border-color: #3f3f46;
        color: #f4f4f5;
      }
      .asx-acct-root.dark .asx-acct-pill:hover {
        background: #3f3f46;
      }
      .asx-acct-avatar {
        width: 20px;
        height: 20px;
        border-radius: 999px;
        background: #18181b;
        color: #ffffff;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 11px;
        font-weight: 700;
        flex-shrink: 0;
      }
      .asx-acct-root.dark .asx-acct-avatar {
        background: #3b82f6;
      }
      .asx-acct-meta {
        color: #71717a;
        font-variant-numeric: tabular-nums;
      }
      .asx-acct-root.dark .asx-acct-meta {
        color: #a1a1aa;
      }
      .asx-acct-menu {
        position: absolute;
        top: calc(100% + 6px);
        right: 0;
        min-width: 240px;
        background: #ffffff;
        border: 1px solid #e4e4e7;
        border-radius: 12px;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.14);
        padding: 8px;
        z-index: 200;
        color: #18181b;
        display: none;
      }
      .asx-acct-root.dark .asx-acct-menu {
        background: #18181b;
        border-color: #3f3f46;
        color: #f4f4f5;
      }
      .asx-acct-menu.open { display: block; }
      .asx-acct-menu-hdr {
        padding: 6px 8px 8px;
        border-bottom: 1px solid #e4e4e7;
        margin-bottom: 6px;
        font-size: 12px;
        line-height: 1.5;
      }
      .asx-acct-root.dark .asx-acct-menu-hdr {
        border-bottom-color: #27272a;
      }
      .asx-acct-menu-item {
        width: 100%;
        text-align: left;
        border: none;
        background: transparent;
        color: inherit;
        padding: 7px 10px;
        border-radius: 7px;
        font-size: 12px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .asx-acct-menu-item:hover {
        background: #f4f4f5;
      }
      .asx-acct-root.dark .asx-acct-menu-item:hover {
        background: #27272a;
      }
      .asx-acct-menu-item.danger {
        color: #ef4444;
      }
      .asx-acct-modal-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.52);
        z-index: 9999;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 16px;
      }
      .asx-acct-modal {
        width: 100%;
        max-width: 400px;
        background: #ffffff;
        color: #18181b;
        border: 1px solid #e4e4e7;
        border-radius: 14px;
        padding: 20px;
        box-shadow: 0 20px 50px rgba(0, 0, 0, 0.25);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
      }
      .asx-acct-modal.dark {
        background: #18181b;
        color: #f4f4f5;
        border-color: #3f3f46;
      }
      .asx-acct-modal h3 {
        font-size: 15px;
        font-weight: 600;
        margin-bottom: 12px;
      }
      .asx-acct-modal label {
        display: block;
        font-size: 12px;
        color: #71717a;
        margin-bottom: 4px;
      }
      .asx-acct-modal.dark label {
        color: #a1a1aa;
      }
      .asx-acct-modal input {
        width: 100%;
        padding: 8px 10px;
        border-radius: 8px;
        border: 1px solid #d4d4d8;
        background: #ffffff;
        color: #18181b;
        font-size: 13px;
        margin-bottom: 10px;
        outline: none;
      }
      .asx-acct-modal.dark input {
        background: #09090b;
        border-color: #3f3f46;
        color: #f4f4f5;
      }
      .asx-acct-modal-err {
        font-size: 12px;
        color: #ef4444;
        margin-bottom: 10px;
        min-height: 16px;
      }
      .asx-acct-modal-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 8px;
      }
      .asx-acct-btn {
        padding: 7px 14px;
        border-radius: 8px;
        border: 1px solid #d4d4d8;
        background: transparent;
        color: inherit;
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
      }
      .asx-acct-btn.primary {
        background: #18181b;
        border-color: #18181b;
        color: #ffffff;
      }
      .asx-acct-modal.dark .asx-acct-btn.primary {
        background: #3b82f6;
        border-color: #3b82f6;
      }
      .asx-acct-tabs {
        display: flex;
        gap: 6px;
        margin-bottom: 12px;
      }
      .asx-acct-tab {
        flex: 1;
        padding: 6px;
        border-radius: 7px;
        border: 1px solid #d4d4d8;
        background: transparent;
        color: inherit;
        font-size: 12px;
        cursor: pointer;
      }
      .asx-acct-tab.active {
        background: #18181b;
        color: #ffffff;
        border-color: #18181b;
      }
      .asx-acct-modal.dark .asx-acct-tab { border-color: #3f3f46; }
      .asx-acct-modal.dark .asx-acct-tab.active {
        background: #3b82f6;
        border-color: #3b82f6;
      }
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
    root.className = 'asx-acct-root' + (_state.theme === 'dark' ? ' dark' : '');

    if (!auth || !auth.authenticated) {
      const reason = (auth && auth.error && auth.error.message) || '请通过 sagein-community 授权（需 skill_jwt 权限）';
      root.innerHTML = `
        <button type="button" class="asx-acct-pill" title="${escapeHtml(reason)}">
          <span class="asx-acct-avatar">?</span>
          <span>未授权</span>
        </button>
      `;
    } else {
      root.innerHTML = `
        <button type="button" class="asx-acct-pill" id="asx-acct-refresh" title="点击刷新">
          <span class="asx-acct-avatar">A</span>
          <span class="asx-acct-meta" title="剩余额度">${escapeHtml(formatBudget(auth.identity))}</span>
          <span class="asx-acct-meta">·</span>
          <span class="asx-acct-meta" title="并发任务数">${escapeHtml(formatConcurrency(auth.identity))}</span>
        </button>
      `;
      root.querySelector('#asx-acct-refresh').onclick = () => refresh();
    }
    _state.container.innerHTML = '';
    _state.container.appendChild(root);
  }

  async function refresh() {
    try {
      const data = await apiJson('/api/auth/status', 'GET');
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
    _state.theme = opts.theme || 'light';
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
