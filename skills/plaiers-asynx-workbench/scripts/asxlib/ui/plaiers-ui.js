// PLAIERS 工作台共用的小工具：toast 提示、读取颜色令牌、内联图标。配合 plaiers.css 使用。
(function () {
  let toastTimer = null;

  // 浮在页面底部的一句话提示，替代 alert()。kind: 'ok' | 'warn' | 'err'
  function plToast(message, kind = 'ok', ms = 3600) {
    let el = document.getElementById('plToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'plToast';
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      document.body.appendChild(el);
    }
    el.className = `pl-toast ${kind}`;
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, ms);
  }

  // canvas、WebGL、SVG 绘制需要具体颜色时从令牌读，不在 JS 里写色值
  function plColor(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // 图标：pi-* 来自品牌 sprite，wb-* 来自工作台补充 sprite
  function plIcon(id, cls = 'i') {
    const file = id.startsWith('pi-') ? '/plaiers-icons.svg' : '/wb-icons.svg';
    return `<svg class="${cls}" aria-hidden="true"><use href="${file}#${id}"></use></svg>`;
  }

  // 积分消耗确认：服务端先报价，返回 confirmation_required / quote_unavailable（HTTP 409，未提交任何任务）。
  // 这里把预计消耗和剩余积分给用户看，只有用户点了确认才带上 confirmed_quote 重发同一请求。
  // 用户取消时返回 charge_declined，不提交。登录、授权都不算同意消耗积分。
  function plChargeMessage(error) {
    const d = error.details || {};
    const lines = [error.message || '这次请求会消耗积分。'];
    const remaining = d.budget && (d.budget.remaining_amount ?? d.budget.remaining);
    if (remaining !== undefined && remaining !== null) lines.push(`剩余积分：${remaining}`);
    lines.push(error.code === 'quote_unavailable'
      ? '仍要在没有报价的情况下提交吗？这个决定会记入日志。'
      : '确认消耗并提交这一次请求吗？');
    return lines.join('\n');
  }

  // Pillow 缺失时只有在此处确认后才调用共享运行环境安装端点。
  function plRuntimeMessage(error) {
    const details = error.details || {};
    return [
      error.message || '本地图片处理需要可选依赖 Pillow。',
      '',
      '现在下载并准备吗？会联网下载按哈希锁定的 Pillow，安装到所有 Asset Skill 共用的运行环境，不修改系统 Python。',
      `等同于在终端运行：${details.prepare_command}`,
    ].join('\n');
  }

  async function plPrepareRuntime(error, headers) {
    if (!window.confirm(plRuntimeMessage(error))) return {status: 'declined'};
    plToast('正在下载并准备运行环境，可能需要一两分钟…', 'ok', 120000);
    let res;
    let data;
    try {
      res = await fetch('/api/runtime/prepare', {
        method: 'POST', headers,
        body: JSON.stringify({user_confirmed: true, confirm_download: true}),
      });
      data = await res.json();
    } catch (err) {
      res = {ok: false, status: res ? res.status : 0};
      data = {ok: false, error: {code: 'runtime_prepare_failed', message: `运行环境准备请求失败：${err.message}`}};
    }
    if (!res.ok || !data || !data.ok) {
      const message = data && data.error && data.error.message || `HTTP ${res.status}`;
      plToast('运行环境准备失败：' + message, 'err', 8000);
      return {status: 'failed', res, data};
    }
    plToast('运行环境已就绪', 'ok');
    return {status: 'ready'};
  }

  async function plPostWithChargeConsent(url, payload, headers) {
    let body = { ...payload };
    let prepared = false;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
      const data = await res.json();
      const error = data && data.error;
      const code = error && error.code;
      if ((code === 'dependency_missing' || code === 'runtime_invalid') && !prepared
          && error.details && error.details.prepare_command) {
        const result = await plPrepareRuntime(error, headers);
        if (result.status === 'failed') return {res: result.res, data: result.data};
        if (result.status === 'declined') return {
          res: {ok: false, status: 409},
          data: {ok: false, error: {code: 'runtime_declined', message: '已取消：没有准备运行环境（Pillow），未提交任务。'}},
        };
        prepared = true;
        body = {...payload};
        continue;
      }
      if (code !== 'confirmation_required' && code !== 'quote_unavailable') return {res, data};
      if (!window.confirm(plChargeMessage(error))) {
        return {
          res: { ok: false, status: 409 },
          data: { ok: false, error: { code: 'charge_declined', message: '已取消：没有确认积分消耗，未提交任务。' } },
        };
      }
      body = { ...payload };
      if (code === 'quote_unavailable') body.confirm_without_quote = true;
      else body.confirmed_quote = (error.details && error.details.confirm_args || [])[1];
    }
    return {
      res: { ok: false, status: 409 },
      data: { ok: false, error: { code: 'confirmation_required', message: '报价一直在变化，未提交任务，请稍后重试。' } },
    };
  }

  window.plPostWithChargeConsent = plPostWithChargeConsent;
  window.plPrepareRuntime = plPrepareRuntime;
  window.plToast = plToast;
  window.plColor = plColor;
  window.plIcon = plIcon;
})();
