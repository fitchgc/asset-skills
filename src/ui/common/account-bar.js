(function (global) {
  'use strict';

  let _state = {
    token: '',
    container: null,
    auth: null,
    menuOpen: false,
    onAuthChange: null,
    phase: "loading", error: "", requestId: 0, runtime: null, runtimeError: "", preparing: false,
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
      .asx-acct-details { max-height: min(640px, 80vh); overflow-y: auto; }
      .asx-acct-details textarea { box-sizing: border-box; width: 100%; margin: 12px 0; background: var(--n950); color: var(--text); border: 1px solid var(--n700); font: 400 13px/1.6 var(--cn); }
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
      signal: AbortSignal.timeout(path === '/api/runtime/prepare' ? 300000 : 15000),
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
    const auth = _state.auth, verified = auth && auth.authenticated;
    const loading = _state.phase === 'loading';
    const root = document.createElement('div');
    root.className = 'asx-acct-root' + (verified ? '' : ' off');
    const label = loading ? '正在检查授权与余额…' : _state.phase === 'error' ? '连接暂不可用' : verified ?
      (auth.identity_error ? '已授权 · 余额暂不可用' : formatBudget(auth.identity)) : '需要连接 Agent';
    const details = verified ? [auth.origin, `账号 ${(auth.identity || {}).subject || '--'}`,
      `账户额度 ${(auth.identity?.budget || {}).quota ?? '--'} ${(auth.identity?.budget || {}).currency ?? ''}`,
      `可用 ${formatBudget(auth.identity)}`, `预占 ${auth.identity?.budget?.reserved_amount ?? '--'}`,
      `已用 ${auth.identity?.budget?.used_amount ?? '--'}`, `查询时间 ${_state.checkedAt}`].join(' · ') :
      '本地浏览和编辑无需云授权。云端生成需由当前 Agent 完成社区授权并绑定本工作台；打开网页不会自动授予权限。';
    const runtime = _state.runtime, state = runtime && runtime.runtime;
    const ready = state && (state.status === 'ready' || (state.status !== 'invalid' && state.interpreter_pillow));
    const canPrepare = state && !ready && ['not_prepared', 'invalid'].includes(state.status);
    const prompt = '请只连接 https://dev.plaiers.ai，读取社区接入指南，检查当前 Agent 的独立身份、原账号及 read、skill_jwt 权限。有效授权只补权；已撤销时沿原身份显式重新授权，不删除其他 Agent 凭据。用固定版本安装器为本工作台绑定当前身份，再刷新工作台。不生成资产、不扣费。';
    root.innerHTML = `
      <details ${_state.menuOpen ? 'open' : ''}><summary class="asx-acct-pill" aria-label="账户与本地环境">
        <span class="asx-acct-avatar">${verified ? 'A' : '?'}</span>
        <span class="asx-acct-budget" role="status">${escapeHtml(label)}</span>
        ${verified ? `<span class="asx-acct-meta">${escapeHtml(formatConcurrency(auth.identity))}</span>` : ''}
      </summary><div class="asx-acct-details">
        <p>${escapeHtml(details)}</p>
        ${_state.error ? `<p role="alert">${escapeHtml(_state.error)}${verified ? ' 上次查询结果仅供参考。' : ''}</p>` : ''}
        ${auth && auth.error ? `<p>${escapeHtml(auth.error.message)}</p>` : ''}
        <button type="button" class="keycap ghost sm" id="asx-acct-refresh">重新检查连接与余额</button>
        <p><a href="https://dev.plaiers.ai/agent-center#asset-setup" target="_blank" rel="noreferrer">查看 dev 安装与授权步骤</a></p>
        <button type="button" class="keycap ghost sm" id="asx-connect-copy">复制给 Agent 的连接指令</button>
        <textarea id="asx-connect-prompt" aria-label="连接修复指令" readonly rows="4">${escapeHtml(prompt)}</textarea>
        <h3>本地编辑环境</h3>
        <p role="status">${_state.preparing ? '正在准备共享 Pillow，请稍候…' : !runtime ? '点击检查本地环境，查看共享 Pillow 和视频工具。' :
          `Pillow：${ready ? '可用' : state.status === 'unsupported' ? '当前平台无锁定安装包' : state.status === 'invalid' ? '校验失败' : '未安装'}；FFmpeg：${runtime.ffmpeg ? '可用' : '未安装（仅视频处理需要）'}；ffprobe：${runtime.ffprobe ? '可用' : '未安装'}`}</p>
        ${_state.runtimeError ? `<p role="alert">${escapeHtml(_state.runtimeError)}</p>` : ''}
        <button type="button" class="keycap ghost sm" id="asx-runtime-check" ${_state.preparing ? 'disabled' : ''}>检查本地环境</button>
        ${canPrepare ? `<p><label><input type="checkbox" id="asx-runtime-consent">同意下载并安装共享 Pillow，仅用于本地编辑，不产生资产生成费用。</label></p><button type="button" class="keycap sm" id="asx-runtime-prepare" disabled>安装共享 Pillow</button>` : ''}
        ${runtime && (!runtime.ffmpeg || !runtime.ffprobe) ? '<p>视频处理才需要 FFmpeg/ffprobe。请让 Agent 说明适合当前系统的官方安装来源，确认后再安装；图片编辑不需要。</p>' : ''}
      </div></details>`;
    root.querySelector('details').ontoggle = e => { _state.menuOpen = e.target.open; };
    root.querySelector('#asx-acct-refresh').onclick = () => refresh();
    root.querySelector('#asx-connect-copy').onclick = async () => {
      try { await navigator.clipboard.writeText(prompt); }
      catch (_) { root.querySelector('#asx-connect-prompt').select(); }
    };
    root.querySelector('#asx-runtime-check').onclick = () => checkRuntime();
    if (canPrepare) {
      const button = root.querySelector('#asx-runtime-prepare'), consent = root.querySelector('#asx-runtime-consent');
      consent.onchange = () => { button.disabled = !consent.checked || _state.preparing; };
      button.onclick = async () => {
        if (!consent.checked || _state.preparing) return;
        _state.preparing = true; _state.runtimeError = ''; render();
        try { await apiJson('/api/runtime/prepare', 'POST', {confirm_download: true}); }
        catch (error) { _state.runtimeError = error.message; }
        finally { _state.preparing = false; await checkRuntime(); }
      };
    }
    _state.container.innerHTML = '';
    _state.container.appendChild(root);
  }

  async function checkRuntime() {
    try { _state.runtime = await apiJson('/api/runtime/status', 'GET'); }
    catch (error) { _state.runtimeError = error.message; }
    render();
  }

  async function refresh() {
    const requestId = ++_state.requestId;
    _state.phase = 'loading'; _state.error = ''; render();
    try {
      const data = await apiJson('/api/auth/status', 'GET');
      if (requestId !== _state.requestId) return;
      if (data.auth_state === 'unavailable') throw new Error('Temporary authorization check failure');
      _state.checkedAt = new Date().toLocaleString(); _state.auth = data; _state.phase = 'ready';
      render();
      if (typeof _state.onAuthChange === 'function') _state.onAuthChange(data);
    } catch (_) {
      if (requestId !== _state.requestId) return;
      _state.phase = 'error'; _state.error = '连接或余额查询暂时失败，请重试。这不等于授权已撤销。'; render();
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
