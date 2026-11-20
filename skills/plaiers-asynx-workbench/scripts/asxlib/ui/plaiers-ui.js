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

  window.plToast = plToast;
  window.plColor = plColor;
  window.plIcon = plIcon;
})();
