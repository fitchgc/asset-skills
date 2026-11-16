(function (global) {
  'use strict';

  const USERNAME_RE = /^[a-z0-9_]{3,32}$/;
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
    const isDark = _state.theme === 'dark';

    const root = document.createElement('div');
    root.className = 'asx-acct-root' + (isDark ? ' dark' : '');

    if (!auth || !auth.authenticated) {
      root.innerHTML = `
        <button type="button" class="asx-acct-pill" id="asx-acct-signin-btn">
          <span class="asx-acct-avatar">?</span>
          <span>未登录 · 点击登录</span>
        </button>
      `;
      _state.container.innerHTML = '';
      _state.container.appendChild(root);
      root.querySelector('#asx-acct-signin-btn').onclick = () => openSwitchAccountModal('login');
      return;
    }

    const user = auth.user || {};
    const displayName = user.nickname || user.username || '已登录';
    const initial = String(displayName).charAt(0).toUpperCase() || 'A';
    const budgetText = formatBudget(auth.identity);
    const concText = formatConcurrency(auth.identity);

    root.innerHTML = `
      <button type="button" class="asx-acct-pill" id="asx-acct-toggle">
        <span class="asx-acct-avatar">${escapeHtml(initial)}</span>
        <span style="font-weight: 600;">${escapeHtml(displayName)}</span>
        <span class="asx-acct-meta">·</span>
        <span class="asx-acct-meta" title="剩余额度">${escapeHtml(budgetText)}</span>
        <span class="asx-acct-meta">·</span>
        <span class="asx-acct-meta" title="并发任务数">${escapeHtml(concText)}</span>
      </button>
      <div class="asx-acct-menu ${_state.menuOpen ? 'open' : ''}" id="asx-acct-menu">
        <div class="asx-acct-menu-hdr">
          <div style="font-weight: 600;">${escapeHtml(displayName)} <span style="opacity: 0.7;">(@${escapeHtml(user.username || '-')})</span></div>
          <div style="opacity: 0.75;">额度：${escapeHtml(budgetText)} · ${escapeHtml(concText)}</div>
          <div style="opacity: 0.6; font-size: 11px; word-break: break-all;">${escapeHtml(auth.login_svr_url || '')}</div>
        </div>
        <button type="button" class="asx-acct-menu-item" data-act="refresh">刷新额度与状态</button>
        <button type="button" class="asx-acct-menu-item" data-act="change-password">修改密码</button>
        <button type="button" class="asx-acct-menu-item" data-act="switch-account">切换账号</button>
        <button type="button" class="asx-acct-menu-item danger" data-act="logout">退出登录</button>
      </div>
    `;

    _state.container.innerHTML = '';
    _state.container.appendChild(root);

    root.querySelector('#asx-acct-toggle').onclick = (e) => {
      e.stopPropagation();
      _state.menuOpen = !_state.menuOpen;
      root.querySelector('#asx-acct-menu').classList.toggle('open', _state.menuOpen);
    };

    root.querySelector('[data-act="refresh"]').onclick = async () => {
      _state.menuOpen = false;
      await refresh();
    };
    root.querySelector('[data-act="change-password"]').onclick = () => {
      _state.menuOpen = false;
      render();
      openChangePasswordModal();
    };
    root.querySelector('[data-act="switch-account"]').onclick = () => {
      _state.menuOpen = false;
      render();
      openSwitchAccountModal('login');
    };
    root.querySelector('[data-act="logout"]').onclick = async () => {
      _state.menuOpen = false;
      try {
        await apiJson('/api/auth/logout', 'POST', {});
      } catch (_) {}
      await refresh();
    };
  }

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const open = document.querySelectorAll('.asx-acct-modal-backdrop');
    if (open.length) open[open.length - 1].remove();
  });

  function openChangePasswordModal() {
    const backdrop = document.createElement('div');
    backdrop.className = 'asx-acct-modal-backdrop';
    backdrop.innerHTML = `
      <div class="asx-acct-modal ${_state.theme === 'dark' ? 'dark' : ''}">
        <h3>修改账号密码</h3>
        <label>原密码</label>
        <input type="password" id="asx-cp-old" autocomplete="current-password" />
        <label>新密码（6–64 位字符）</label>
        <input type="password" id="asx-cp-new" autocomplete="new-password" />
        <div class="asx-acct-modal-err" id="asx-cp-err"></div>
        <div class="asx-acct-modal-actions">
          <button type="button" class="asx-acct-btn" id="asx-cp-cancel">取消</button>
          <button type="button" class="asx-acct-btn primary" id="asx-cp-submit">确认修改</button>
        </div>
      </div>
    `;
    document.body.appendChild(backdrop);
    backdrop.querySelector('#asx-cp-cancel').onclick = () => backdrop.remove();
    backdrop.querySelector('#asx-cp-submit').onclick = async () => {
      const oldPassword = backdrop.querySelector('#asx-cp-old').value;
      const newPassword = backdrop.querySelector('#asx-cp-new').value;
      const errEl = backdrop.querySelector('#asx-cp-err');
      errEl.textContent = '';
      if (!oldPassword || newPassword.length < 6 || newPassword.length > 64) {
        errEl.textContent = '请输入原密码，且新密码长度为 6–64 位字符';
        return;
      }
      try {
        await apiJson('/api/auth/change-password', 'POST', { oldPassword, newPassword });
        backdrop.remove();
        await refresh();
      } catch (err) {
        errEl.textContent = err.message;
      }
    };
  }

  function openSwitchAccountModal(initialTab) {
    let tab = initialTab || 'login';
    const currentUrl = (_state.auth && _state.auth.login_svr_url) || '';
    const backdrop = document.createElement('div');
    backdrop.className = 'asx-acct-modal-backdrop';
    backdrop.innerHTML = `
      <div class="asx-acct-modal ${_state.theme === 'dark' ? 'dark' : ''}">
        <h3>切换 / 登录 Asynx 账号</h3>
        <div class="asx-acct-tabs">
          <button type="button" class="asx-acct-tab active" data-tab="login">登录</button>
          <button type="button" class="asx-acct-tab" data-tab="register">注册</button>
        </div>
        <label>用户名</label>
        <input type="text" id="asx-sw-user" placeholder="alice_01" />
        <div id="asx-sw-user-hint" style="font-size: 11px; margin: -6px 0 8px; opacity: 0.75; display: none;"></div>
        <label>密码</label>
        <input type="password" id="asx-sw-pass" placeholder="6–64 位字符" />
        <div id="asx-sw-nick-wrap" style="display: none;">
          <label>昵称（可选）</label>
          <input type="text" id="asx-sw-nick" placeholder="显示名称" />
        </div>
        <details style="margin: 4px 0 10px; font-size: 12px;">
          <summary style="cursor: pointer; opacity: 0.75;">Identity Service 地址设置</summary>
          <input type="url" id="asx-sw-svr" value="${escapeHtml(currentUrl)}" placeholder="http://localhost:3006" style="margin-top: 6px;" />
        </details>
        <div class="asx-acct-modal-err" id="asx-sw-err"></div>
        <div class="asx-acct-modal-actions">
          <button type="button" class="asx-acct-btn" id="asx-sw-cancel">取消</button>
          <button type="button" class="asx-acct-btn primary" id="asx-sw-submit">登录</button>
        </div>
      </div>
    `;
    document.body.appendChild(backdrop);

    const setTab = (nextTab) => {
      tab = nextTab;
      backdrop.querySelectorAll('.asx-acct-tab').forEach((el) => {
        el.classList.toggle('active', el.dataset.tab === tab);
      });
      backdrop.querySelector('#asx-sw-nick-wrap').style.display = tab === 'register' ? 'block' : 'none';
      backdrop.querySelector('#asx-sw-user-hint').style.display = tab === 'register' ? 'block' : 'none';
      backdrop.querySelector('#asx-sw-submit').textContent = tab === 'register' ? '注册并登录' : '登录';
      backdrop.querySelector('#asx-sw-err').textContent = '';
    };

    backdrop.querySelectorAll('.asx-acct-tab').forEach((el) => {
      el.onclick = () => setTab(el.dataset.tab);
    });
    setTab(tab);

    let checkTimer = null;
    backdrop.querySelector('#asx-sw-user').oninput = () => {
      if (tab !== 'register') return;
      const u = backdrop.querySelector('#asx-sw-user').value.trim().toLowerCase();
      const hint = backdrop.querySelector('#asx-sw-user-hint');
      if (!USERNAME_RE.test(u)) {
        hint.textContent = '格式：^[a-z0-9_]{3,32}$';
        return;
      }
      hint.textContent = '检查可用性...';
      clearTimeout(checkTimer);
      checkTimer = setTimeout(async () => {
        try {
          const svr = backdrop.querySelector('#asx-sw-svr').value.trim();
          const qs = new URLSearchParams({ username: u });
          if (svr) qs.set('login_svr_url', svr);
          const d = await apiJson('/api/auth/check-username?' + qs.toString(), 'GET');
          hint.textContent = d.available ? '✓ 用户名可用' : '✕ 用户名已被注册';
        } catch (err) {
          hint.textContent = err.message;
        }
      }, 250);
    };

    backdrop.querySelector('#asx-sw-cancel').onclick = () => backdrop.remove();
    backdrop.querySelector('#asx-sw-submit').onclick = async () => {
      const username = backdrop.querySelector('#asx-sw-user').value.trim().toLowerCase();
      const password = backdrop.querySelector('#asx-sw-pass').value;
      const nickname = backdrop.querySelector('#asx-sw-nick').value.trim();
      const login_svr_url = backdrop.querySelector('#asx-sw-svr').value.trim();
      const errEl = backdrop.querySelector('#asx-sw-err');
      errEl.textContent = '';
      try {
        if (tab === 'register') {
          await apiJson('/api/auth/register', 'POST', {
            username,
            password,
            ...(nickname ? { nickname } : {}),
            ...(login_svr_url ? { login_svr_url } : {}),
          });
        } else {
          await apiJson('/api/auth/login', 'POST', {
            username,
            password,
            ...(login_svr_url ? { login_svr_url } : {}),
          });
        }
        backdrop.remove();
        await refresh();
      } catch (err) {
        errEl.textContent = err.message;
      }
    };
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

    document.addEventListener('click', () => {
      if (_state.menuOpen) {
        _state.menuOpen = false;
        const menu = _state.container && _state.container.querySelector('#asx-acct-menu');
        if (menu) menu.classList.remove('open');
      }
    });

    render();
    return refresh();
  }

  global.AsxAccountBar = {
    mount,
    refresh,
    openLogin: () => openSwitchAccountModal('login'),
  };
})(window);
