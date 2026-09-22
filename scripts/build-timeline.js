#!/usr/bin/env node
/**
 * build-timeline.js — 构建期生成 public/timeline/index.html(知识时间线,按时间排序)
 *
 * 收录规则:只收「已经被某个公开索引页链接」的页面——首页(通用指南)、/learn、/akke、/workflow、
 *   /softie、/vivi 的 index.html。所以必须排在 vercel.json buildCommand 的最后(那几张索引先生成)。
 *   不在任何索引里的页(隐藏分享页、public/internal/、public/partners/)永远不会被时间线带出来。
 *   另外跳过:按日自动生成的运营/监控报告(reports/daily-*、model-watch-*)、带 8 位 hash 后缀的分享页、
 *   vivi 角色卡、meta robots 含 noindex 的页、meta refresh 重定向 stub。
 *   public/internal/ 按 README 约定不进任何索引,这里也不生成内部索引。
 * 日期优先级:meta upio:date > scripts/timeline.dates.json > scripts/akke-map.dates.json
 *            > learn CATEGORIES.date > 文件名里的日期 > 未知(单独成组,排在最后)
 * 分类:learn 取 build-learn.js 的 CATEGORIES;akke 取 akke-map(override 优先,否则 classify);其余取分区名。
 *
 * 本地刷新日期缓存:REFRESH_DATES=1 node scripts/build-timeline.js
 *   (没有 meta 日期、也不在 akke-map.dates.json 的页,用 git 首次入仓日期并入 timeline.dates.json)
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const PUB = path.join(ROOT, 'public');
const { CATEGORIES } = require('./build-learn.js');
const akke = require('./build-akke-map.js');

const tdatesPath = path.join(__dirname, 'timeline.dates.json');
let tdates = {};
try { tdates = JSON.parse(fs.readFileSync(tdatesPath, 'utf8')); } catch { tdates = {}; }

const SECTIONS = [
  { id: 'learn',    label: '知识分享' },
  { id: 'akke',     label: 'Akke' },
  { id: 'workflow', label: 'Workflow' },
  { id: 'softie',   label: 'Softie' },
  { id: 'vivi',     label: 'Vivi' },
  { id: 'guide',    label: '通用指南' },
];
const SECTION_LABEL = Object.fromEntries(SECTIONS.map(s => [s.id, s.label]));

const learnMeta = {};
for (const c of CATEGORIES) for (const it of c.items) learnMeta[it.slug] = { cat: c.title, date: it.date };
const AKKE_CAT = Object.fromEntries(akke.CATS.map(c => [c.id, c.title.replace(/ · Uncategorized$/, '')]));

// ---- 小工具 ----
const read = (f) => fs.readFileSync(f, 'utf8');
const metaOf = (html, name) => {
  const re = new RegExp(`<meta[^>]+name=["']${name.replace(/[.:]/g, '\\$&')}["'][^>]*>`, 'i');
  const tag = (html.match(re) || [''])[0];
  return (tag.match(/content=["']([^"']*)["']/i) || ['', ''])[1].trim();
};
const isNoindex = (html) => /<meta[^>]+name=["']robots["'][^>]*noindex/i.test(html);
const isStub = (html) => /http-equiv=["']?refresh/i.test(html);
const decode = (s) => s
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
  .replace(/&nbsp;/g, ' ').replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n)).replace(/&amp;/g, '&');
const stripTags = (s) => s.replace(/<[^>]*>/g, '');
const titleOf = (html) => decode(((html.match(/<title>([^<]*)<\/title>/i) || ['', ''])[1]))
  .replace(/\s*[·|—–-]\s*(Akke|upio\.ai|Softie|Vivi|Workflow)\s*$/i, '').replace(/\s+/g, ' ').trim();
const isDate = (d) => /^\d{4}-\d{2}-\d{2}$/.test(d || '');
const dateFromName = (href) => {
  const m = href.match(/(20\d{2})-?(\d{2})-?(\d{2})/);
  if (!m) return '';
  const d = `${m[1]}-${m[2]}-${m[3]}`;
  return (+m[2] >= 1 && +m[2] <= 12 && +m[3] >= 1 && +m[3] <= 31) ? d : '';
};
// 不用 --follow:模板化页面(日报、model-watch)内容相近,--follow 会把它们误认成改名,拿到别的文件的入库日期
const gitFirstAdded = (rel) => {
  try {
    return execFileSync('git', ['log', '--diff-filter=A', '--format=%as', '--', rel],
      { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim().split('\n').pop();
  } catch { return ''; }
};
// 标题兜底脱敏:公开页标题里不出现团队成员昵称(源头应在页面 <title> 或 akke-map.json overrides 里改)
const NICKNAMES = /野荞|饭粒|夏夏|狮蛮|谭伊格|子扬|董津瑄/g;
const cleanTitle = (t) => t.replace(NICKNAMES, '运营同学');

// ---- 枚举:从已生成的公开索引页里收站内链接 ----
const INDEXES = [
  { section: 'guide',    file: 'index.html',          base: '/' },
  { section: 'learn',    file: 'learn/index.html',    base: '/learn/' },
  { section: 'akke',     file: 'akke/index.html',     base: '/akke/' },
  { section: 'workflow', file: 'workflow/index.html', base: '/workflow/' },
  { section: 'softie',   file: 'softie/index.html',   base: '/softie/' },
  { section: 'vivi',     file: 'vivi/index.html',     base: '/vivi/' },
];
const EXCLUDE = [
  /^\/akke\/reports\/daily-/,   // 个人日报,akke-bot 每日同步
  /^\/akke\/model-watch-/,       // 模型监控日报,akke-bot 每日同步
  /-[0-9a-f]{8}\/?$/,             // hash 后缀的分享页
  /^\/vivi\/characters(\/|$)/,   // 角色卡
];
const SECTION_ROOT = /^\/(timeline|internal|partners|learn|akke|workflow|softie|vivi)(\/|$)/;
function fileFor(href) {
  const clean = href.replace(/\/$/, '');
  const cands = href.endsWith('/') ? [clean + '/index.html'] : [clean + '.html', clean + '/index.html'];
  for (const c of cands) { const f = path.join(PUB, c); if (fs.existsSync(f)) return f; }
  return '';
}
function enumerate() {
  const seen = new Map(); // href -> {section, href, file}
  for (const ix of INDEXES) {
    const f = path.join(PUB, ix.file);
    if (!fs.existsSync(f)) { console.warn(`[timeline] ⚠️ 缺索引 ${ix.file}(先跑 buildCommand 里前面的脚本)`); continue; }
    for (const m of read(f).matchAll(/href=["']([^"'#?]+)["']/g)) {
      let href = m[1].trim();
      if (/^(https?:|mailto:|data:|javascript:|tel:|\/\/)/i.test(href)) continue;
      if (!href.startsWith('/')) href = ix.base + href.replace(/^\.\//, '');
      href = href.replace(/\.html$/, '').replace(/\/index$/, '/');
      if (ix.section === 'guide') {
        if (!/^\/[^/]+$/.test(href) || SECTION_ROOT.test(href)) continue; // 首页只收根目录单页
      } else if (!href.startsWith(ix.base) || href === ix.base) continue;
      if (href.startsWith('/internal/') || href.startsWith('/partners/')) continue;
      if (EXCLUDE.some(re => re.test(href)) || seen.has(href)) continue;
      const file = fileFor(href);
      if (file) seen.set(href, { section: ix.section, href, file });
    }
  }
  return [...seen.values()];
}

// ---- 取元数据 ----
function resolve(p) {
  const html = read(p.file);
  const slug = p.href.replace(/^\/[^/]+\//, '').replace(/\/$/, '');
  const metaDate = metaOf(html, 'upio:date');
  let date = '', dateSrc = '';
  const cands = [
    ['meta', metaDate],
    ['cache', tdates[p.href]],
    ['akke-map', p.section === 'akke' ? akke.dates[p.href] : ''],
    ['learn', p.section === 'learn' && learnMeta[slug] ? learnMeta[slug].date : ''],
    ['filename', dateFromName(p.href)],
  ];
  for (const [src, d] of cands) if (isDate(d)) { date = d; dateSrc = src; break; }

  let title = titleOf(html), cat = '';
  if (p.section === 'learn') {
    cat = learnMeta[slug] ? learnMeta[slug].cat : (metaOf(html, 'upio:category') || '未归类');
  } else if (p.section === 'akke') {
    const ov = akke.overrides[p.href] || {};
    let id = ov.cat || akke.classify(p.href).cat;
    if (!AKKE_CAT[id]) id = 'uncat';
    cat = AKKE_CAT[id];
    if (ov.title) title = stripTags(decode(ov.title));
  } else {
    cat = SECTION_LABEL[p.section];
  }
  return { ...p, html, title: cleanTitle(title || slug), cat, date, dateSrc, noindex: isNoindex(html), stub: isStub(html) };
}

// ---- 日期缓存刷新 ----
function refreshDates(pages) { // 只补缺,不覆盖已有键
  let added = 0;
  for (const p of pages) {
    if (isDate(metaOf(p.html, 'upio:date')) || tdates[p.href]) continue;
    if (p.section === 'akke' && akke.dates[p.href]) continue; // akke 已有缓存,保持与 akke 索引一致
    const d = gitFirstAdded(path.relative(ROOT, p.file));
    if (isDate(d)) { tdates[p.href] = d; added++; }
  }
  const sorted = Object.fromEntries(Object.keys(tdates).sort().map(k => [k, tdates[k]]));
  fs.writeFileSync(tdatesPath, JSON.stringify(sorted, null, 2) + '\n');
  tdates = sorted;
  console.log(`[timeline] dates cache +${added} (total ${Object.keys(sorted).length})`);
}

// ---- 渲染(同一份函数在 Node 预渲染,并原样注入页面供前端交互复用) ----
function renderList(items, order, sectionLabels) {
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const monthLabel = (k) => (k === '0000-00' ? '日期未知' : `${k.slice(0, 4)} 年 ${+k.slice(5, 7)} 月`);
  const sorted = items.slice().sort((a, b) => {
    if (!a.date !== !b.date) return a.date ? -1 : 1; // 未知日期永远垫底
    const c = a.date < b.date ? -1 : a.date > b.date ? 1 : (a.title < b.title ? -1 : 1);
    return order === 'asc' ? c : -c;
  });
  const groups = [];
  for (const it of sorted) {
    const k = it.date ? it.date.slice(0, 7) : '0000-00';
    if (!groups.length || groups[groups.length - 1].k !== k) groups.push({ k, list: [] });
    groups[groups.length - 1].list.push(it);
  }
  if (!groups.length) return '<p class="empty">没有匹配的页面。换个关键词或筛选试试。</p>';
  return groups.map(g => `<section class="month">
  <h2 class="month-h"><span>${monthLabel(g.k)}</span><span class="month-n">${g.list.length} 篇</span></h2>
  <ol class="rows">
${g.list.map(it => `    <li><a class="row" href="${esc(it.href)}">
      <time class="row-d" datetime="${esc(it.date)}">${it.date ? it.date.slice(5) : '--'}</time>
      <span class="row-main"><span class="row-t">${esc(it.title)}</span>${it.section !== 'internal' ? `<span class="row-meta"><span class="sec sec-${esc(it.section)}">${esc(sectionLabels[it.section] || it.section)}</span>${it.cat && it.cat !== sectionLabels[it.section] ? `<span class="cat">${esc(it.cat)}</span>` : ''}</span>` : ''}</span>
    </a></li>`).join('\n')}
  </ol>
</section>`).join('\n');
}

const CSS = `:root {
  --bg:           #0a0d14;
  --bg-elevated:  #131720;
  --bg-deep:      #1a1f2b;
  --border:        #232936;
  --border-strong: #2f3646;
  --text:       #e8eaef;
  --text-dim:   #9ba3b4;
  --text-muted: #6b7384;
  --accent:        #8b5cf6;
  --accent-soft:   rgba(139, 92, 246, 0.15);
  --accent-2:      #60a5fa;
  --accent-3:      #34d399;
  --font-display: 'Instrument Serif', 'Source Han Serif SC', Georgia, serif;
  --font-ui:      'Inter Tight', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
  --font-mono:    'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; padding: 0;
  background: var(--bg); color: var(--text);
  font-family: var(--font-ui);
  font-size: 15.5px; line-height: 1.7;
  font-feature-settings: 'cv11', 'ss01', 'tnum';
  -webkit-font-smoothing: antialiased;
  min-height: 100vh; overflow-x: hidden;
}
.container { max-width: 1080px; margin: 0 auto; padding: 56px 24px 120px; }
.breadcrumb { font-size: 12.5px; color: var(--text-muted); margin-bottom: 20px; font-family: var(--font-mono); }
.breadcrumb a { color: var(--text-dim); text-decoration: none; transition: color .2s; }
.breadcrumb a:hover { color: var(--accent); }
.breadcrumb span { margin: 0 8px; color: var(--border-strong); }
header.hero { padding: 8px 0 28px; border-bottom: 1px solid var(--border); margin-bottom: 8px; }
.tag {
  display: inline-block; padding: 4px 12px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent);
  font-family: var(--font-mono); font-size: 11px; font-weight: 600;
  letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 18px;
}
h1 {
  font-family: var(--font-display); font-style: italic;
  font-size: 64px; font-weight: 400;
  margin: 0 0 18px; letter-spacing: -0.02em; line-height: 1.05;
  background: linear-gradient(135deg, #f4f5f8 30%, var(--accent) 75%, var(--accent-2) 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.subtitle { font-size: 17px; color: var(--text-dim); margin: 0; max-width: 760px; line-height: 1.6; }
.stats { margin-top: 18px; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); letter-spacing: 0.04em; }
.stats b { color: var(--text); font-weight: 700; }

/* 控件 */
.controls { position: sticky; top: 0; z-index: 5; background: rgba(10,13,20,0.92); backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px); padding: 16px 0 12px; border-bottom: 1px solid var(--border); }
.ctl-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.ctl-row + .ctl-row { margin-top: 10px; }
.search { flex: 1 1 220px; min-width: 0; background: var(--bg-elevated); border: 1px solid var(--border-strong);
  border-radius: 10px; color: var(--text); font: inherit; font-size: 14px; padding: 8px 12px; outline: none; }
.search:focus { border-color: var(--accent); }
.btn { background: var(--bg-elevated); border: 1px solid var(--border-strong); color: var(--text-dim);
  border-radius: 10px; font-family: var(--font-mono); font-size: 12px; padding: 9px 12px; cursor: pointer; white-space: nowrap; }
.btn:hover { border-color: var(--accent); color: var(--text); }
.chips { display: flex; gap: 6px; flex-wrap: wrap; min-width: 0; }
.chip { background: transparent; border: 1px solid var(--border); color: var(--text-dim); border-radius: 999px;
  font: inherit; font-size: 12.5px; padding: 3px 11px; cursor: pointer; }
.chip:hover { border-color: var(--border-strong); color: var(--text); }
.chip[aria-pressed="true"] { background: var(--accent-soft); border-color: var(--accent); color: var(--text); }
.chip .n { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); margin-left: 4px; }
.shown { font-family: var(--font-mono); font-size: 11.5px; color: var(--text-muted); margin-left: auto; }

/* 月份分组 */
.month { margin-top: 36px; }
.month-h { font-family: var(--font-display); font-style: italic; font-weight: 400; font-size: 26px; margin: 0 0 10px;
  display: flex; align-items: baseline; gap: 12px; }
.month-h::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
  box-shadow: 0 0 8px var(--accent); flex-shrink: 0; align-self: center; }
.month-n { font-family: var(--font-mono); font-style: normal; font-size: 11px; color: var(--text-muted); letter-spacing: 0.08em; }
.rows { list-style: none; margin: 0; padding: 0; border-left: 1px solid var(--border); margin-left: 2px; }
.row { display: flex; gap: 14px; align-items: flex-start; padding: 9px 12px 9px 16px; text-decoration: none; color: inherit;
  border-radius: 0 10px 10px 0; transition: background .15s ease; }
.row:hover { background: var(--bg-elevated); }
.row-d { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); flex: 0 0 42px; padding-top: 2px; }
.row:hover .row-d { color: var(--accent-2); }
.row-main { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; min-width: 0; flex: 1 1 auto; }
.row-t { font-size: 15px; font-weight: 500; color: var(--text); overflow-wrap: anywhere; min-width: 0; }
.row:hover .row-t { color: #fff; }
.row-meta { display: inline-flex; gap: 6px; flex-wrap: wrap; }
.sec, .cat { font-family: var(--font-mono); font-size: 10.5px; letter-spacing: 0.03em; padding: 1px 7px; border-radius: 999px;
  border: 1px solid var(--border); color: var(--text-muted); white-space: nowrap; }
.sec-learn { color: var(--accent); border-color: rgba(139,92,246,0.4); }
.sec-akke { color: var(--accent-2); border-color: rgba(96,165,250,0.4); }
.sec-workflow { color: var(--accent-3); border-color: rgba(52,211,153,0.4); }
.sec-softie { color: #f472b6; border-color: rgba(244,114,182,0.4); }
.sec-vivi { color: #fbbf24; border-color: rgba(251,191,36,0.4); }
.empty { color: var(--text-muted); margin-top: 40px; }
footer { text-align: center; margin-top: 64px; padding-top: 28px; border-top: 1px solid var(--border);
  font-family: var(--font-mono); font-size: 11.5px; color: var(--text-muted); letter-spacing: 0.06em; }
noscript p { color: var(--text-muted); font-size: 13px; }
@media (max-width: 600px) {
  .container { padding: 40px 16px 80px; }
  h1 { font-size: 44px; }
  .month-h { font-size: 22px; }
  .row { padding-left: 12px; gap: 10px; }
  .shown { margin-left: 0; width: 100%; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition: none !important; } html { scroll-behavior: auto; } }`;

function page({ title, desc, crumb, tag, h1, subtitle, items, noindex, withFilters, footer }) {
  const data = items.map(it => ({ href: it.href, title: it.title, date: it.date, section: it.section, cat: it.cat }));
  const json = JSON.stringify(data).replace(/</g, '\\u003c');
  const initial = renderList(data, 'desc', SECTION_LABEL);
  const dated = data.filter(d => d.date).map(d => d.date).sort();
  const range = dated.length ? `${dated[0]} → ${dated[dated.length - 1]}` : '';
  const secCounts = {};
  for (const d of data) secCounts[d.section] = (secCounts[d.section] || 0) + 1;
  const secChips = withFilters
    ? [`<button class="chip" data-sec="all" aria-pressed="true">全部<span class="n">${data.length}</span></button>`]
      .concat(SECTIONS.filter(s => secCounts[s.id]).map(s => `<button class="chip" data-sec="${s.id}" aria-pressed="false">${s.label}<span class="n">${secCounts[s.id]}</span></button>`)).join('')
    : '';
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
${noindex ? '<meta name="robots" content="noindex, nofollow">\n' : ''}<title>${title}</title>
<meta name="description" content="${desc}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;500;700;800&display=swap">
<style>
${CSS}
</style>
</head>
<body>
  <div class="container">
    <div class="breadcrumb"><a href="/">upio.ai</a><span>/</span>${crumb}</div>
    <header class="hero">
      <span class="tag">${tag}</span>
      <h1>${h1}</h1>
      <p class="subtitle">${subtitle}</p>
      <div class="stats">共 <b>${data.length}</b> 篇${range ? ` · ${range}` : ''}</div>
    </header>

    <div class="controls">
      <div class="ctl-row">
        <input class="search" id="q" type="search" placeholder="搜索标题…" aria-label="搜索标题" autocomplete="off">
        <button class="btn" id="order" type="button" aria-label="切换排序">新 → 旧</button>
      </div>
${withFilters ? `      <div class="ctl-row"><div class="chips" id="secs" role="group" aria-label="按分区筛选">${secChips}</div></div>
      <div class="ctl-row" id="cat-row" hidden><div class="chips" id="cats" role="group" aria-label="按分类筛选"></div></div>
` : ''}      <div class="ctl-row"><span class="shown" id="shown">显示 ${data.length} / ${data.length}</span></div>
    </div>

    <main id="list">
${initial}
    </main>
    <noscript><p>搜索、筛选与排序需要启用 JavaScript；上面是按时间新 → 旧的完整列表。</p></noscript>

    <footer>${footer}</footer>
  </div>
<script type="application/json" id="tl-data">${json}</script>
<script>
(function () {
  var renderList = ${renderList.toString()};
  var LABELS = ${JSON.stringify(SECTION_LABEL)};
  var DATA = JSON.parse(document.getElementById('tl-data').textContent);
  var st = { order: 'desc', sec: 'all', cat: 'all', q: '' };
  var list = document.getElementById('list');
  var shown = document.getElementById('shown');
  var orderBtn = document.getElementById('order');
  var secs = document.getElementById('secs');
  var cats = document.getElementById('cats');
  var catRow = document.getElementById('cat-row');
  function escHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function drawCats() {
    if (!cats) return;
    if (st.sec === 'all') { catRow.hidden = true; cats.innerHTML = ''; return; }
    var counts = {}, order = [];
    DATA.forEach(function (d) { if (d.section === st.sec && d.cat) { if (!counts[d.cat]) { counts[d.cat] = 0; order.push(d.cat); } counts[d.cat]++; } });
    if (order.length < 2) { catRow.hidden = true; cats.innerHTML = ''; return; }
    var total = order.reduce(function (n, c) { return n + counts[c]; }, 0);
    cats.innerHTML = '<button class="chip" data-cat="all" aria-pressed="' + (st.cat === 'all') + '">全部分类<span class="n">' + total + '</span></button>' +
      order.map(function (c) { return '<button class="chip" data-cat="' + escHtml(c) + '" aria-pressed="' + (st.cat === c) + '">' + escHtml(c) + '<span class="n">' + counts[c] + '</span></button>'; }).join('');
    catRow.hidden = false;
  }
  function draw() {
    var q = st.q.trim().toLowerCase();
    var items = DATA.filter(function (d) {
      if (st.sec !== 'all' && d.section !== st.sec) return false;
      if (st.cat !== 'all' && d.cat !== st.cat) return false;
      if (q && (d.title + ' ' + d.href + ' ' + (d.cat || '')).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    list.innerHTML = renderList(items, st.order, LABELS);
    shown.textContent = '显示 ' + items.length + ' / ' + DATA.length;
    orderBtn.textContent = st.order === 'desc' ? '新 → 旧' : '旧 → 新';
  }
  document.getElementById('q').addEventListener('input', function (e) { st.q = e.target.value; draw(); });
  orderBtn.addEventListener('click', function () { st.order = st.order === 'desc' ? 'asc' : 'desc'; draw(); });
  if (secs) secs.addEventListener('click', function (e) {
    var b = e.target.closest('[data-sec]'); if (!b) return;
    st.sec = b.getAttribute('data-sec'); st.cat = 'all';
    Array.prototype.forEach.call(secs.querySelectorAll('[data-sec]'), function (x) { x.setAttribute('aria-pressed', String(x === b)); });
    drawCats(); draw();
  });
  if (cats) cats.addEventListener('click', function (e) {
    var b = e.target.closest('[data-cat]'); if (!b) return;
    st.cat = b.getAttribute('data-cat');
    Array.prototype.forEach.call(cats.querySelectorAll('[data-cat]'), function (x) { x.setAttribute('aria-pressed', String(x === b)); });
    draw();
  });
})();
</script>
</body>
</html>
`;
}

// ---- 主流程 ----
const raw = enumerate().map(resolve);
const pages = raw.filter(p => !p.noindex && !p.stub);

if (process.env.REFRESH_DATES) {
  refreshDates(pages);
  for (const p of pages) { // 用刷新后的缓存重算没有 meta 日期的页
    if (p.dateSrc !== 'meta' && p.dateSrc !== 'cache' && tdates[p.href]) { p.date = tdates[p.href]; p.dateSrc = 'cache'; }
  }
}

fs.mkdirSync(path.join(PUB, 'timeline'), { recursive: true });
fs.writeFileSync(path.join(PUB, 'timeline', 'index.html'), page({
  title: '知识时间线 · upio.ai',
  desc: 'upio.ai 全站知识页按时间排序：知识分享、Akke、Workflow、Softie、Vivi 与通用指南。',
  crumb: '知识时间线',
  tag: 'TIMELINE',
  h1: '知识时间线',
  subtitle: '全站知识页按入库时间排列，可按分区与分类筛选、按标题搜索。分区内的分类导览见 <a href="/learn" style="color: var(--accent-2);">知识分享</a> 与 <a href="/akke/" style="color: var(--accent-2);">Akke 项目地图</a>。',
  items: pages,
  noindex: false,
  withFilters: true,
  footer: `upio.ai · 知识时间线 · 构建时自动生成 · 共 ${pages.length} 篇`,
}));

// ---- sitemap:public/sitemap.xml 由 build-characters.js 每次重写(只含 vivi),这里只补 /timeline 入口 ----
{
  const smPath = path.join(PUB, 'sitemap.xml');
  if (fs.existsSync(smPath)) {
    const sm = read(smPath);
    if (!sm.includes('https://upio.ai/timeline<') && sm.includes('</urlset>')) {
      const today = process.env.BUILD_DATE || new Date().toISOString().slice(0, 10);
      fs.writeFileSync(smPath, sm.replace('</urlset>',
        `  <url><loc>https://upio.ai/timeline</loc><lastmod>${today}</lastmod><priority>0.8</priority></url>\n</urlset>`));
    }
  }
}

// ---- 报告 ----
const bySec = {}, bySrc = {};
for (const p of pages) { bySec[p.section] = (bySec[p.section] || 0) + 1; bySrc[p.dateSrc || 'none'] = (bySrc[p.dateSrc || 'none'] || 0) + 1; }
console.log(`[timeline] 生成 timeline/index.html · ${pages.length} 篇(扫描 ${raw.length},跳过 noindex/stub ${raw.length - pages.length})`);
console.log('  分区:', JSON.stringify(bySec), ' 日期来源:', JSON.stringify(bySrc));
const undated = pages.filter(p => !p.date).map(p => p.href);
if (undated.length) console.warn(`  ⚠️ 无日期 ${undated.length} 篇:`, undated.join(', '));
// 回归防线:入库日期早于文件名自带日期,多半是日期缓存取错了(例如 git --follow 追错文件)
const early = pages.filter(p => { const n = dateFromName(p.href); return n && p.date && p.date < n && p.dateSrc !== 'meta'; });
if (early.length) console.warn(`  ⚠️ 日期早于文件名日期 ${early.length} 篇(检查 timeline/akke-map 日期缓存):`, early.map(p => `${p.href}=${p.date}`).join(', '));
