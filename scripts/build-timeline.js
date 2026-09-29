#!/usr/bin/env node
/**
 * build-timeline.js — 构建期生成 public/timeline/index.html(知识时间线,按时间排序)
 *
 * 收录规则:扫描 public/ 下全部 html,不依赖索引页手工登记——放进 public/ 的新页面自动出现。
 *   跳过:public/internal/、public/partners/、分区首页、按日自动生成的日报(reports/daily-*、model-watch-*)、
 *   带 8 位 hash 后缀的分享页、vivi 角色卡、门店对客物料(akke/xiaoguotu|zhishi|kit)、meta robots 含 noindex 的页、
 *   meta refresh 重定向 stub。客户案例单页(akke/cases/<slug>)不走扫描,改由 cases/manifest.json 收录为 kind=case。
 * 产物:public/timeline/index.html(全部文章页)与 public/timeline/articles.json(首页读取),均不进仓。
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

const shell = require('./kb-shell.js');
const { SECTIONS, SECTION_LABEL } = shell;

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
  .replace(/\s*[·|—–-]\s*(Akke|upio\.ai|Softie|Vivi|Workflow)(\s*知识分享)?\s*$/i, '').replace(/\s+/g, ' ').trim();
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
const NICKNAMES = /野荞|饭粒|夏夏|狮蛮|谭伊格|子扬|董津瑄/;
const cleanTitle = (t) => t.replace(new RegExp(NICKNAMES.source, 'g'), '运营同学');

// ---- 枚举:扫描 public/ 下全部公开页(不依赖索引页手工登记,新页面自动进时间线) ----
const SECTION_DIRS = ['learn', 'akke', 'workflow', 'softie', 'vivi'];
const EXCLUDE = [
  /^\/(internal|partners|timeline)(\/|$)/,   // 内部页、合作方页、时间线自身
  /^\/(learn|akke|workflow|softie|vivi)\/$/, // 分区首页是导航,不是文章
  /^\/akke\/reports\/daily-/,   // 个人日报,akke-bot 每日同步
  /^\/akke\/model-watch-/,       // 模型监控日报,akke-bot 每日同步
  /-(?!20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(\/|$))[0-9a-f]{8}(\/|$)/, // hash 后缀的分享页(含其子页);合法的 8 位日期后缀(-20260929)不算 hash
  /^\/vivi\/characters(\/|$)/,   // 角色卡
  /^\/akke\/(xiaoguotu|zhishi|kit)(\/|$)/, // 门店对客物料(效果图/知识卡/素材包),不是团队文章
];
function walk(dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith('.html')) out.push(p);
  }
  return out;
}
const LINKED = linkedHrefs();
const privacySkipped = [];
let CASE_SLUGS = new Set();
try { CASE_SLUGS = new Set(JSON.parse(read(path.join(PUB, 'akke', 'cases', 'manifest.json'))).map(c => c.slug)); } catch {}
// 隐私:没被任何索引页链接过、正文或路径含团队成员昵称的页,不因为全量扫描被带上首页和时间线
const NICK_SLUG = /yeqiao|fanli|xiaxia|shiman|ziyang|fanny/i;
function linkedHrefs() {
  const out = new Set();
  for (const f of ['learn/index.html', 'akke/index.html', 'workflow/index.html', 'softie/index.html', 'vivi/index.html',
    'akke/cases/index.html', 'vivi/cases/index.html', 'softie/cases/index.html']) {
    const file = path.join(PUB, f);
    if (!fs.existsSync(file)) continue;
    const base = '/' + path.posix.dirname(f) + '/';
    for (const m of read(file).matchAll(/href=["']([^"'#?]+)["']/g)) {
      let u = m[1].trim();
      if (/^(https?:|mailto:|data:|javascript:|tel:|\/\/)/i.test(u)) continue;
      if (!u.startsWith('/')) u = base + u.replace(/^\.\//, '');
      out.add(u.replace(/\.html$/, '').replace(/\/index$/, '/'));
    }
  }
  return out;
}
function enumerate() {
  const seen = new Map(); // href -> {section, href, file, kind}
  for (const file of walk(PUB, [])) {
    const rel = path.relative(PUB, file).split(path.sep).join('/');
    let href = '/' + rel.replace(/\.html$/, '');
    if (href === '/index') continue;
    href = href.replace(/\/index$/, '/');
    const top = href.split('/')[1];
    const section = SECTION_DIRS.includes(top) ? top : (/^\/[^/]+$/.test(href) ? 'guide' : '');
    if (!section || EXCLUDE.some(re => re.test(href)) || seen.has(href)) continue;
    // manifest 登记过的客户案例单页改由 enumerateCases() 收录(kind=case);cases/ 下没登记的报告页照常当文章
    if (href.startsWith('/akke/cases/') && CASE_SLUGS.has(href.slice('/akke/cases/'.length))) continue;
    // Softie / Vivi 的用户案例单页与 Akke 案例同属 kind=case,默认不混进文章流
    const kind = /^\/(vivi|softie)\/cases\/[^/]+$/.test(href) ? 'case' : 'doc';
    if (!LINKED.has(href) && (NICK_SLUG.test(href) || NICKNAMES.test(read(file)))) { privacySkipped.push(href); continue; }
    seen.set(href, { section, href, file, kind });
  }
  return [...seen.values()];
}
// 客户案例:manifest 是案例库的数据源(akke-bot 同步 + 生成流程追加),标题与日期都取自它
function enumerateCases() {
  const mf = path.join(PUB, 'akke', 'cases', 'manifest.json');
  let list = [];
  try { list = JSON.parse(read(mf)); } catch { console.warn('[timeline] ⚠️ 读不到 akke/cases/manifest.json'); return []; }
  return list.filter(c => {
    const f = path.join(PUB, 'akke', 'cases', c.slug + '.html');
    return c.slug && fs.existsSync(f) && !isNoindex(read(f)) && !isStub(read(f)); // 案例页自己标了 noindex 的不收
  }).map(c => ({
    section: 'akke', kind: 'case', href: `/akke/cases/${c.slug}`,
    title: cleanTitle([c.name, c.tag].filter(Boolean).join(':')),
    cat: c.collection === 'wechat' ? '个微案例' : c.collection === 'conv' ? '对话案例' : '用户案例',
    date: isDate(c.date) ? c.date : '', dateSrc: isDate(c.date) ? 'manifest' : '',
  }));
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
    const parent = slug.includes('/') ? learnMeta[slug.split('/')[0]] : null; // 系列子页继承父页分类
    cat = learnMeta[slug] ? learnMeta[slug].cat : (metaOf(html, 'upio:category') || (parent ? parent.cat : slug.includes('/') ? '专题系列' : '未归类'));
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

// ---- 渲染(renderList 同一份函数在 Node 预渲染,并原样注入页面供前端交互复用) ----
function renderList(items, order, labels, rowHtml) {
  var monthLabel = function (k) { return k === '0000-00' ? '日期未知' : k.slice(0, 4) + ' 年 ' + (+k.slice(5, 7)) + ' 月'; };
  var sorted = items.slice().sort(function (a, b) {
    if (!a.date !== !b.date) return a.date ? -1 : 1; // 未知日期永远垫底
    var c = a.date < b.date ? -1 : a.date > b.date ? 1 : (a.title < b.title ? -1 : 1);
    return order === 'asc' ? c : -c;
  });
  var groups = [];
  sorted.forEach(function (it) {
    var k = it.date ? it.date.slice(0, 7) : '0000-00';
    if (!groups.length || groups[groups.length - 1].k !== k) groups.push({ k: k, list: [] });
    groups[groups.length - 1].list.push(it);
  });
  if (!groups.length) return '<p class="empty">没有匹配的文章。换个关键词，或把上面的筛选切回「全部」。</p>';
  return groups.map(function (g) {
    return '<section class="month"><h2 class="month-h">' + monthLabel(g.k) + '<span class="month-n">' + g.list.length + ' 篇</span></h2><ol class="rows">' +
      g.list.map(function (it) { return rowHtml(it, labels); }).join('') + '</ol></section>';
  }).join('');
}

const CSS = `${shell.BASE_CSS}
.hero { padding: 64px 0 28px; }
.hero h1 { font-family: var(--display); font-weight: 400; font-size: 60px; line-height: 1.05; margin: 0 0 14px; letter-spacing: -0.015em; }
.hero p { margin: 0; color: var(--dim); max-width: 680px; }
.hero p a { color: var(--blue); text-decoration: none; }
.controls { position: sticky; top: 56px; z-index: 10; background: rgba(10, 13, 20, 0.94); backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px); padding: 14px 0; border-bottom: 1px solid var(--line); }
.ctl { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.ctl + .ctl { margin-top: 10px; }
.search { flex: 1 1 260px; min-width: 0; background: var(--surface); border: 1px solid var(--line-strong); border-radius: 10px;
  color: var(--text); font: inherit; font-size: 15px; padding: 9px 14px; outline: none; }
.search:focus { border-color: var(--violet); }
.seg { display: inline-flex; background: var(--surface); border: 1px solid var(--line-strong); border-radius: 10px; padding: 3px; }
.seg button { background: none; border: 0; color: var(--dim); font: inherit; font-size: 13.5px; padding: 5px 12px; border-radius: 7px; cursor: pointer; white-space: nowrap; }
.seg button[aria-pressed="true"] { background: var(--surface-2); color: var(--text); }
.seg .n { color: var(--muted); font-variant-numeric: tabular-nums; margin-left: 5px; font-size: 12px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; min-width: 0; }
.chip { display: inline-flex; align-items: center; gap: 7px; background: transparent; border: 1px solid var(--line); color: var(--dim);
  border-radius: 999px; font: inherit; font-size: 13px; padding: 3px 12px; cursor: pointer; }
.chip:hover { border-color: var(--line-strong); color: var(--text); }
.chip[aria-pressed="true"] { background: var(--violet-soft); border-color: var(--violet); color: var(--text); }
.chip .n { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12px; }
.shown { color: var(--muted); font-size: 13px; margin-left: auto; font-variant-numeric: tabular-nums; }
.month { margin-top: 40px; }
.month-h { font-family: var(--display); font-weight: 400; font-size: 30px; margin: 0 0 8px; display: flex; align-items: baseline; gap: 12px;
  padding-bottom: 8px; border-bottom: 1px solid var(--line); }
.month-n { font-family: var(--ui); font-size: 13px; color: var(--muted); }
.empty { color: var(--muted); margin-top: 48px; }
noscript p { color: var(--muted); font-size: 13px; }
@media (max-width: 640px) {
  .hero { padding-top: 40px; } .hero h1 { font-size: 42px; }
  .month-h { font-size: 24px; } .shown { margin-left: 0; width: 100%; }
}`;

function page({ items, kindCounts }) {
  const data = items.map(it => ({ href: it.href, title: it.title, date: it.date, section: it.section, cat: it.cat, kind: it.kind }));
  const json = JSON.stringify(data).replace(/</g, '\\u003c');
  const initial = renderList(data.filter(d => d.kind === 'doc'), 'desc', SECTION_LABEL, shell.rowHtml);
  const docs = data.filter(d => d.kind === 'doc');
  const secCounts = {};
  for (const d of docs) secCounts[d.section] = (secCounts[d.section] || 0) + 1;
  const secChips = [`<button class="chip" data-sec="all" aria-pressed="true">全部分区<span class="n">${docs.length}</span></button>`]
    .concat(SECTIONS.filter(s => secCounts[s.id]).map(s => `<button class="chip" data-sec="${s.id}" aria-pressed="false"><span class="dot dot-${s.id}"></span>${s.label}<span class="n">${secCounts[s.id]}</span></button>`)).join('');
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>全部文章 · upio.ai</title>
<meta name="description" content="upio.ai 团队知识库的全部文章，按发布时间从新到旧排列，可按分区、分类筛选和搜索。">
${shell.FONTS}
<style>
${CSS}
</style>
</head>
<body>
${shell.topbar('all')}
<div class="wrap">
  <header class="hero">
    <h1>全部文章</h1>
    <p>知识库里的每一篇文章，按发布时间从新到旧排列。共 ${kindCounts.doc} 篇文章，另有 ${kindCounts.case} 个客户案例。第一次来可以先看<a href="/">首页的新人路线</a>。</p>
  </header>
  <div class="controls">
    <div class="ctl">
      <input class="search" id="q" type="search" placeholder="搜索标题、分类或路径" aria-label="搜索文章" autocomplete="off">
      <div class="seg" id="kind" role="group" aria-label="内容类型">
        <button type="button" data-kind="doc" aria-pressed="true">文章<span class="n">${kindCounts.doc}</span></button>
        <button type="button" data-kind="case" aria-pressed="false">客户案例<span class="n">${kindCounts.case}</span></button>
        <button type="button" data-kind="all" aria-pressed="false">全部<span class="n">${kindCounts.doc + kindCounts.case}</span></button>
      </div>
      <div class="seg"><button type="button" id="order" aria-pressed="true">新到旧</button></div>
    </div>
    <div class="ctl"><div class="chips" id="secs" role="group" aria-label="按分区筛选">${secChips}</div></div>
    <div class="ctl" id="cat-row" hidden><div class="chips" id="cats" role="group" aria-label="按分类筛选"></div></div>
    <div class="ctl"><span class="shown" id="shown">显示 ${docs.length} 篇</span></div>
  </div>
  <main id="list">
${initial}
  </main>
  <noscript><p>搜索和筛选需要启用 JavaScript。上面是全部文章，按时间从新到旧排列。</p></noscript>
</div>
${shell.footer(`全部文章页在每次部署时自动生成，放进 public/ 的新页面会自动出现在这里`)}
<script type="application/json" id="tl-data">${json}</script>
<script>
(function () {
  var rowHtml = ${shell.rowHtml.toString()};
  var renderList = ${renderList.toString()};
  var LABELS = ${JSON.stringify(SECTION_LABEL)};
  var DATA = JSON.parse(document.getElementById('tl-data').textContent);
  var st = { order: 'desc', kind: 'doc', sec: 'all', cat: 'all', q: '' };
  var $ = function (id) { return document.getElementById(id); };
  var list = $('list'), shown = $('shown'), orderBtn = $('order'), secs = $('secs'), cats = $('cats'), catRow = $('cat-row'), kind = $('kind');
  function e(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function inKind(d) { return st.kind === 'all' || d.kind === st.kind; }
  function press(box, attr, val) { Array.prototype.forEach.call(box.querySelectorAll('[' + attr + ']'), function (x) { x.setAttribute('aria-pressed', String(x.getAttribute(attr) === val)); }); }
  function drawSecs() {
    var counts = {}, total = 0;
    DATA.forEach(function (d) { if (inKind(d)) { counts[d.section] = (counts[d.section] || 0) + 1; total++; } });
    Array.prototype.forEach.call(secs.querySelectorAll('[data-sec]'), function (b) {
      var id = b.getAttribute('data-sec'), n = id === 'all' ? total : (counts[id] || 0);
      b.querySelector('.n').textContent = n; b.hidden = id !== 'all' && !n;
    });
  }
  function drawCats() {
    if (st.sec === 'all') { catRow.hidden = true; cats.innerHTML = ''; return; }
    var counts = {}, order = [];
    DATA.forEach(function (d) { if (inKind(d) && d.section === st.sec && d.cat) { if (!counts[d.cat]) { counts[d.cat] = 0; order.push(d.cat); } counts[d.cat]++; } });
    if (order.length < 2) { catRow.hidden = true; cats.innerHTML = ''; return; }
    var total = order.reduce(function (n, c) { return n + counts[c]; }, 0);
    cats.innerHTML = '<button class="chip" data-cat="all" aria-pressed="' + (st.cat === 'all') + '">全部分类<span class="n">' + total + '</span></button>' +
      order.map(function (c) { return '<button class="chip" data-cat="' + e(c) + '" aria-pressed="' + (st.cat === c) + '">' + e(c) + '<span class="n">' + counts[c] + '</span></button>'; }).join('');
    catRow.hidden = false;
  }
  function draw() {
    var q = st.q.trim().toLowerCase();
    var items = DATA.filter(function (d) {
      if (!inKind(d)) return false;
      if (st.sec !== 'all' && d.section !== st.sec) return false;
      if (st.cat !== 'all' && d.cat !== st.cat) return false;
      if (q && (d.title + ' ' + d.href + ' ' + (d.cat || '') + ' ' + (LABELS[d.section] || '')).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    list.innerHTML = renderList(items, st.order, LABELS, rowHtml);
    shown.textContent = '显示 ' + items.length + ' 篇';
    orderBtn.textContent = st.order === 'desc' ? '新到旧' : '旧到新';
  }
  $('q').addEventListener('input', function (ev) { st.q = ev.target.value; draw(); });
  orderBtn.addEventListener('click', function () { st.order = st.order === 'desc' ? 'asc' : 'desc'; draw(); });
  kind.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-kind]'); if (!b) return;
    st.kind = b.getAttribute('data-kind'); st.sec = 'all'; st.cat = 'all';
    press(kind, 'data-kind', st.kind); press(secs, 'data-sec', 'all'); drawSecs(); drawCats(); draw();
  });
  secs.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-sec]'); if (!b) return;
    st.sec = b.getAttribute('data-sec'); st.cat = 'all'; press(secs, 'data-sec', st.sec); drawCats(); draw();
  });
  cats.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-cat]'); if (!b) return;
    st.cat = b.getAttribute('data-cat'); press(cats, 'data-cat', st.cat); draw();
  });
  var sp = new URLSearchParams(location.search);
  if (sp.get('kind') === 'case' || sp.get('kind') === 'all') { var kb = kind.querySelector('[data-kind="' + sp.get('kind') + '"]'); if (kb) kb.click(); }
  if (sp.get('q')) { $('q').value = sp.get('q'); st.q = sp.get('q'); draw(); }
  var m = location.hash.match(/^#(learn|akke|workflow|softie|vivi|guide|cases)$/);
  if (m) { var t = m[1] === 'cases' ? kind.querySelector('[data-kind="case"]') : secs.querySelector('[data-sec="' + m[1] + '"]'); if (t) t.click(); }
})();
</script>
</body>
</html>
`;
}

// ---- 主流程 ----
const raw = enumerate().map(resolve);
// 同一份报告被发到多个路径时(字节完全相同),只留路径最短的那个,免得「最近更新」里连排三条同名
const byContent = new Map();
for (const p of raw.filter(p => !p.noindex && !p.stub)) {
  const k = require('crypto').createHash('sha1').update(p.html).digest('hex');
  const cur = byContent.get(k);
  if (!cur || p.href.length < cur.href.length) byContent.set(k, p);
}
const docs = [...byContent.values()];
const dupes = raw.filter(p => !p.noindex && !p.stub).length - docs.length;

if (process.env.REFRESH_DATES) {
  refreshDates(docs);
  for (const p of docs) { // 用刷新后的缓存重算没有 meta 日期的页
    if (p.dateSrc !== 'meta' && p.dateSrc !== 'cache' && tdates[p.href]) { p.date = tdates[p.href]; p.dateSrc = 'cache'; }
  }
}
const cases = docs.filter(p => p.kind === 'case').concat(enumerateCases());
const articles = docs.filter(p => p.kind === 'doc');
const pages = articles.concat(cases);

fs.mkdirSync(path.join(PUB, 'timeline'), { recursive: true });
fs.writeFileSync(path.join(PUB, 'timeline', 'index.html'), page({ items: pages, kindCounts: { doc: articles.length, case: cases.length } }));
// 首页(build-index.js)读这份数据渲染「最近更新」「按项目浏览」与全站搜索;构建产物,不进仓
fs.writeFileSync(path.join(PUB, 'timeline', 'articles.json'), JSON.stringify(pages.map(p => ({
  href: p.href, title: p.title, date: p.date, section: p.section, cat: p.cat, kind: p.kind,
}))));

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
console.log(`[timeline] 生成 timeline/index.html · 文章 ${articles.length} 篇 + 案例 ${cases.length} 个(扫描 ${raw.length},跳过 noindex/stub ${raw.length - docs.length - dupes},内容重复 ${dupes})`);
console.log('  分区:', JSON.stringify(bySec), ' 日期来源:', JSON.stringify(bySrc));
const undated = pages.filter(p => !p.date).map(p => p.href);
if (privacySkipped.length) console.warn(`  ⚠️ 未被索引链接且含成员昵称,不收录 ${privacySkipped.length} 篇:`, privacySkipped.join(', '));
if (undated.length) console.warn(`  ⚠️ 无日期 ${undated.length} 篇:`, undated.join(', '));
// 回归防线:入库日期早于文件名自带日期,多半是日期缓存取错了(例如 git --follow 追错文件)
const early = pages.filter(p => { const n = dateFromName(p.href); return n && p.date && p.date < n && p.dateSrc !== 'meta'; });
if (early.length) console.warn(`  ⚠️ 日期早于文件名日期 ${early.length} 篇(检查 timeline/akke-map 日期缓存):`, early.map(p => `${p.href}=${p.date}`).join(', '));
