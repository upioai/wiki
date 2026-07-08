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

  // ---- 团队内部共享备注（存 Akke 后端 /api/public/case-notes；人人可见可编辑）----
  var NOTES_API = 'https://akke.vercel.app/api/public/case-notes';
  var NOTES_TOKEN = 'akke-cases-notes-2026';   // 轻量写入口令（内部页用；与后端 route 默认一致）
  var notes = { loaded: false, ok: false, map: {} };

  function injectNoteStyles() {
    if (document.getElementById('akke-note-style')) return;
    var css = '.case-item{display:block}'
      + '.case-note{margin:6px 0 0 74px}'
      + '.cn-ta{width:100%;min-height:36px;background:var(--bg-deep);border:1px solid var(--border-strong);color:var(--text);border-radius:8px;padding:7px 10px;font-size:13px;font-family:inherit;resize:vertical;box-sizing:border-box}'
      + '.cn-ta:focus{outline:none;border-color:var(--accent)}'
      + '.cn-bar{display:flex;align-items:center;gap:10px;margin-top:5px}'
      + '.cn-save{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:4px 12px;font-size:12.5px;cursor:pointer;font-family:inherit}'
      + '.cn-save:hover{filter:brightness(1.1)}.cn-save:disabled{opacity:.6;cursor:default}'
      + '.cn-meta{font-size:11.5px;color:var(--text-muted)}'
      + '@media(max-width:640px){.case-note{margin-left:0}}';
    var st = el('style'); st.id = 'akke-note-style'; st.textContent = css; document.head.appendChild(st);
  }

  function myName() {
    var n = null;
    try { n = localStorage.getItem('akke_note_by'); } catch (e) {}
    if (!n) { n = (window.prompt('你的名字（备注署名，只填一次）：') || '').trim(); try { if (n) localStorage.setItem('akke_note_by', n); } catch (e) {} }
    return n || null;
  }
  function fmtTime(iso) {
    try { var d = new Date(iso); var p = function (x) { return (x < 10 ? '0' : '') + x; };
      return (d.getMonth() + 1) + '-' + d.getDate() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()); } catch (e) { return ''; }
  }
  function metaText(cur) { return cur ? ((cur.updated_by ? '由 ' + cur.updated_by + ' · ' : '') + fmtTime(cur.updated_at)) : ''; }

  function buildNoteBox(slug) {
    var wrap = el('div', 'case-note'); wrap.dataset.slug = slug; wrap.style.display = 'none';
    var ta = el('textarea', 'cn-ta'); ta.placeholder = '写点备注…（团队共享，人人可编辑）';
    var bar = el('div', 'cn-bar');
    var btn = el('button', 'cn-save', '保存'); btn.type = 'button';
    var meta = el('span', 'cn-meta', '');
    bar.appendChild(btn); bar.appendChild(meta);
    wrap.appendChild(ta); wrap.appendChild(bar);
    btn.addEventListener('click', function () {
      btn.disabled = true; btn.textContent = '存…';
      var by = myName();
      fetch(NOTES_API, { method: 'POST', headers: { 'content-type': 'application/json', 'x-akke-notes-token': NOTES_TOKEN }, body: JSON.stringify({ slug: slug, note: ta.value, by: by }) })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function () { notes.map[slug] = { note: ta.value, updated_at: new Date().toISOString(), updated_by: by }; meta.textContent = (by ? '由 ' + by + ' · ' : '') + '刚刚已存 ✓'; })
        .catch(function () { meta.textContent = '保存失败，请重试'; })
        .then(function () { btn.disabled = false; btn.textContent = '保存'; });
    });
    return wrap;
  }

  function fillBoxes() {
    Array.prototype.forEach.call(document.querySelectorAll('.case-note'), function (w) {
      var cur = notes.map[w.dataset.slug];
      var ta = w.querySelector('.cn-ta'), meta = w.querySelector('.cn-meta');
      if (cur) { if (ta && document.activeElement !== ta) ta.value = cur.note || ''; if (meta) meta.textContent = metaText(cur); }
      w.style.display = 'block';   // 接口就绪才显示备注框
    });
  }
  function ensureNotes() {
    if (notes.loaded) { if (notes.ok) fillBoxes(); return; }
    fetch(NOTES_API, { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { notes.map = (d && d.notes) || {}; notes.ok = true; notes.loaded = true; fillBoxes(); })
      .catch(function () { notes.loaded = true; notes.ok = false; /* 接口未部署：备注框保持隐藏 */ });
  }

  // 渲染一批卡片到容器（每张卡片下方挂一个共享备注框）
  window.AkkeRender = function (containerId, list) {
    var box = document.getElementById(containerId);
    if (!box) return;
    box.innerHTML = '';
    if (!list.length) { box.appendChild(el('p', 'empty-hint', '这段时间没有生成案例。')); return; }
    injectNoteStyles();
    list.forEach(function (e) {
      var item = el('div', 'case-item');
      item.appendChild(window.AkkeCard(e));
      item.appendChild(buildNoteBox(e.slug));
      box.appendChild(item);
    });
    ensureNotes();
  };
})();
