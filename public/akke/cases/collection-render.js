/* Akke 案例合集 · 共享卡片渲染器
   数据源：/akke/cases/manifest.json（唯一事实源）。
   conversation-cases 与 range 两页都用它，改案例进度只改 manifest，两页自动同步。 */
(function () {
  var COLOR = { win: 'g', mid: 'info', wait: 'w', cold: 'muted' };   // statusColor → case-tag 样式
  var DEPTH = { win: 4, wait: 3, mid: 2, cold: 1 };                  // 对话推进深度（排序用）

  function el(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  }
  function chip(cls, txt) { return el('span', 'case-tag ' + cls, txt); }
  function metaSpan(label, val) {
    var s = el('span');
    s.appendChild(el('strong', null, label));
    s.appendChild(document.createTextNode(' ' + val));
    return s;
  }

  // 单张卡片（字段缺失时优雅降级：没有 intent/status 就用 tag 当描述）
  window.AkkeCard = function (e) {
    var a = el('a', 'case-card');
    a.href = '/akke/cases/' + e.slug;
    a.target = '_blank'; a.rel = 'noopener';   // 卡片在新标签打开，不覆盖当前合集/筛选列表

    if (e.avatar) {
      var img = el('img', 'case-avatar');
      img.src = e.avatar; img.alt = e.name || e.slug;
      img.setAttribute('referrerpolicy', 'no-referrer'); img.loading = 'lazy';
      a.appendChild(img);
    } else {
      var ph = el('div', 'case-avatar', (e.name || '?').slice(0, 1));
      ph.style.cssText = 'display:flex;align-items:center;justify-content:center;font-weight:700;color:var(--text-dim);';
      a.appendChild(ph);
    }

    var body = el('div', 'case-body');
    var row = el('div', 'case-tag-row');
    if (e.intent) row.appendChild(chip('accent', e.intent));
    if (e.status) row.appendChild(chip(COLOR[e.statusColor] || 'muted', e.status));
    else if (!e.intent && e.tag) row.appendChild(chip('muted', e.tag));
    if (e.region) row.appendChild(chip('muted', e.region));
    if (e.date) row.appendChild(chip('info', e.date));
    body.appendChild(row);

    body.appendChild(el('h3', null, e.name || e.slug));
    if (e.quote) body.appendChild(el('p', 'quote', e.quote));

    var flowParts = [];
    if (e.date) flowParts.push('评论 ' + e.date);
    if (e.firstTouch) flowParts.push('首触 ' + e.firstTouch);
    if (flowParts.length) body.appendChild(el('div', 'flow', flowParts.join('  ·  ')));

    var meta = el('div', 'meta');
    meta.appendChild(metaSpan('触达账号', e.account || e.op || '—'));
    // 未补 status 的（区间视图里的普通案例）把 tag 去掉意向前缀当"要点"
    if (!e.status && e.tag) {
      var pt = e.tag.replace(/^[^·]*·\s*/, '');
      if (pt && pt !== e.tag) meta.appendChild(metaSpan('要点', pt));
    } else if (e.op && e.account && e.op !== e.account) {
      meta.appendChild(metaSpan('运营', e.op));
    }
    body.appendChild(meta);

    a.appendChild(body);
    return a;
  };

  window.AkkeDepth = function (e) { return DEPTH[e.statusColor] || 0; };

  // 拉 manifest，回调拿到数组
  window.AkkeLoadManifest = function (cb, onErr) {
    fetch('/akke/cases/manifest.json', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(cb)
      .catch(function (err) { if (onErr) onErr(err); });
  };

  // 渲染一批卡片到容器
  window.AkkeRender = function (containerId, list) {
    var box = document.getElementById(containerId);
    if (!box) return;
    box.innerHTML = '';
    if (!list.length) { box.appendChild(el('p', 'empty-hint', '这段时间没有生成案例。')); return; }
    list.forEach(function (e) { box.appendChild(window.AkkeCard(e)); });
  };
})();
