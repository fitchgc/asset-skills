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

  async function plPostWithChargeConsent(url, payload, headers) {
    let body = { ...payload };
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
      const data = await res.json();
      const error = data && data.error;
      const code = error && error.code;
      if (code !== 'confirmation_required' && code !== 'quote_unavailable') return { res, data };
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
  window.plToast = plToast;
  window.plColor = plColor;
  window.plIcon = plIcon;
})();
