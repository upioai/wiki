/**
 * build-index.js — 构建期生成 public/index.html(团队知识库首页)
 *
 * 依赖 build-timeline.js 产出的 public/timeline/articles.json(全站文章清单),
 * 所以必须排在 vercel.json buildCommand 的最后。
 * 手动维护的只有下面三个数组:ONBOARDING(新人路线)、STORE_PILLARS(门店系统)、GUIDES(常用指南)。
 * 「最近更新」「按项目浏览」和全站搜索都从 articles.json 自动生成,新页面不用在这里登记。
 */
const fs = require('fs');
const path = require('path');
const shell = require('./kb-shell.js');
const { SECTIONS, SECTION_LABEL, esc } = shell;

const publicDir = path.join(__dirname, '..', 'public');

// 新人路线:按阅读顺序排列,先懂业务、再装工具、最后补基础
const ONBOARDING = [
  { href: '/akke/business-tour', title: '业务导览', why: '先弄清我们在做什么：短视频获客、微信接待、企业大脑三条业务线。' },
  { href: '/akke/product-overview-2026-08-12', title: '产品完整介绍', why: '门店系统的每个功能，以及它们现在各做到哪一步。' },
  { href: '/learn/akke-tech-overview', title: '技术全景', why: '部署、数据管线、模型路由和执行层，一页读完。' },
  { href: '/akke/intern-handbook', title: '新人入职手册', why: '第一周每天做什么、找谁、交什么。' },
  { href: '/vpn-quick-start', title: '装好 VPN', why: '团队自建节点，从装客户端到分流规则。' },
  { href: '/claude-code-windows-guide', title: '装好 Claude Code', why: '团队日常开发和写文档都靠它，含常见坑。' },
  { href: '/learn/llm-glossary', title: '大模型术语图鉴', why: '看懂讨论里的 token、上下文、微调和 RAG。' },
];

// 门店系统三大能力：首页主推区，文案取自 /akke/product-overview-2026-08-12 与各专题页
const STORE_PILLARS = [
  {
    no: '01',
    title: '自动化社媒运营',
    status: '评论获客已上线 · 短视频内部演示',
    desc: '店长选题，产线完成改稿、配音、口型、B-roll 和字幕，店长看过再放行；抖音评论采集与意向分类，把触达和回复放回同一条客户旅程。',
    tags: ['短视频成片', '评论采集', '意向分类'],
    links: [
      { text: '一条视频里发生了什么', href: '/workflow/replica-workflow-overview-2026-08' },
      { text: '内容生产流水线', href: '/workflow/' },
    ],
  },
  {
    no: '02',
    title: '个人 / 企业微信自动化回复',
    status: '企微线上在跑 · 个微 2 台设备',
    desc: '个人微信（Windows）自动回复：设备负责认人、读消息和发送，服务端负责上下文、画像、话术与落库，群聊、系统号和身份不明的会话不自动发；企业微信由 AI 起草、人工逐条检查。',
    tags: ['个人微信（Windows）', '企业微信接待'],
    links: [
      { text: '个人微信 · 功能与案例', href: '/akke/personal-wechat-autoreply-cases-20260916' },
      { text: '个人微信 · 完整逻辑', href: '/akke/personal-wechat-logic-workflow-complete' },
      { text: '企业微信 · 从加好友到成交', href: '/akke/reports/wecom-autoreply-topic' },
    ],
  },
  {
    no: '03',
    title: '智能数据库 · 企业大脑',
    status: '行业侧已可用 · 门店侧在建',
    desc: '资料解析成知识积木，行业参考与门店承诺分开存；冲突由负责人裁决，未确认内容暂停使用。行业资料可追溯到原件，门店专属知识在证据链齐全前保持灰色。',
    tags: ['资料入箱', '知识积木', '冲突裁决', '版本发布'],
    links: [
      { text: '门店知识怎么出话', href: '/akke/enterprise-brain-knowledge-reply' },
      { text: '资料中心现状', href: '/akke/enterprise-brain-kb-2026-07-31' },
    ],
  },
];

const GUIDES = [
  {
    file: 'anthropic-founder-handbook-zh.html',
    title: 'Anthropic 创始人手册（中译）',
    desc: 'Karpathy 翻译整理的 Anthropic 工作手册中文版。AI native 创业心法。',
  },
  {
    file: 'claude-code-windows-guide.html',
    title: 'Claude Code · Windows 上手攻略',
    desc: 'Windows 用户从 0 到能跑 Claude Code 的完整路径，含常见坑。',
  },
  {
    file: 'mac-windows-arm64-mirror-guide.html',
    title: 'Mac 装 Windows on ARM 镜像指南',
    desc: 'M 系列 Mac 通过 Parallels / UTM 装 Windows 11 ARM 的完整流程。',
  },
  {
    file: 'vpn-quick-start.html',
    title: 'VPN 快速上手',
    desc: '团队自建 VPN 节点配置，从客户端安装到分流规则。',
  },
  {
    file: 'design-system.html',
    title: 'upio.ai 设计系统',
    desc: '本站统一的暗色主题设计规范：色板、排版、组件样式，做新页面/子站时照此取值。',
  },
];


// ---- 数据 ----
const articlesPath = path.join(publicDir, 'timeline', 'articles.json');
let ARTICLES = [];
try { ARTICLES = JSON.parse(fs.readFileSync(articlesPath, 'utf8')); }
catch { console.warn('[build-index] WARN: 读不到 timeline/articles.json(build-timeline.js 要先跑),首页的最近更新与搜索会是空的'); }
const byDateDesc = (a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : (a.title < b.title ? -1 : 1));
const docs = ARTICLES.filter(a => a.kind === 'doc').sort(byDateDesc);
const cases = ARTICLES.filter(a => a.kind === 'case');
const exists = (href) => ARTICLES.some(a => a.href === href) || fs.existsSync(path.join(publicDir, href.replace(/\/$/, '/index') + '.html'));

for (const o of ONBOARDING) if (!exists(o.href)) console.warn(`[build-index] WARN: 新人路线页面不存在 ${o.href}`);
const missing = GUIDES.filter(g => !fs.existsSync(path.join(publicDir, g.file)));
if (missing.length) console.warn(`[build-index] WARN: ${missing.length} curated guide(s) missing: ${missing.map(m => m.file).join(', ')}`);

const cn = (d) => (d ? `${+d.slice(5, 7)} 月 ${+d.slice(8, 10)} 日` : '');
const latest = docs.slice(0, 12);
const lastDate = docs.length ? docs[0].date : '';

// ---- 片段 ----
const steps = ONBOARDING.map((o, i) => `        <li class="step" data-href="${esc(o.href)}">
          <span class="step-n">${i + 1}</span>
          <div class="step-b"><a href="${esc(o.href)}">${esc(o.title)}</a><p>${esc(o.why)}</p></div>
          <label class="step-c"><input type="checkbox" aria-label="标记「${esc(o.title)}」已读"><span></span></label>
        </li>`).join('\n');

const sectionRows = SECTIONS.map(s => {
  const list = docs.filter(d => d.section === s.id);
  if (!list.length) return '';
  const nCases = cases.filter(c => c.section === s.id).length;
  const href = s.href || `/timeline#${s.id}`;
  return `      <article class="sec">
        <div class="sec-h">
          <h3><span class="dot dot-${s.id}"></span><a href="${href}">${s.label}</a></h3>
          <p>${esc(s.desc)}</p>
          <p class="sec-n">${list.length} 篇文章${nCases ? `，${nCases} 个案例` : ''}，最近更新 ${cn(list[0].date)}</p>
        </div>
        <ol class="rows">${list.slice(0, 3).map(it => shell.rowHtml(it, SECTION_LABEL)).join('')}</ol>
        <a class="more" href="/timeline#${s.id}">看 ${s.label} 的全部 ${list.length} 篇</a>
      </article>`;
}).join('\n');

const pillarCards = STORE_PILLARS.map(p => `      <article class="pillar">
        <p class="pillar-no">${p.no}</p>
        <h3>${p.title}</h3>
        <p class="pillar-st">${p.status}</p>
        <p>${p.desc}</p>
        <ul class="pillar-l">${p.links.map(l => `<li><a href="${l.href}">${l.text}</a></li>`).join('')}</ul>
      </article>`).join('\n');

const guideRows = GUIDES.filter(g => fs.existsSync(path.join(publicDir, g.file))).map(g =>
  `        <li><a href="/${g.file.replace(/\.html$/, '')}">${g.title}</a><p>${g.desc}</p></li>`).join('\n');

const searchData = JSON.stringify(ARTICLES.map(a => [a.href, a.title, a.date, a.section, a.cat, a.kind === 'case' ? 1 : 0])).replace(/</g, '\\u003c');

const CSS = `${shell.BASE_CSS}
.hero { padding: 72px 0 40px; max-width: 760px; }
.hero h1 { font-family: var(--display); font-weight: 400; font-size: 76px; line-height: 1; letter-spacing: -0.02em; margin: 0 0 20px; }
.hero .lede { font-size: 17px; color: var(--dim); margin: 0 0 8px; max-width: 640px; }
.hero .meta { font-size: 14px; color: var(--muted); margin: 0 0 28px; }
.hero .meta a { color: var(--blue); text-decoration: none; }
.find { position: relative; }
.find input { width: 100%; background: var(--surface); border: 1px solid var(--line-strong); border-radius: 14px; color: var(--text);
  font: inherit; font-size: 17px; padding: 15px 18px 15px 48px; outline: none; }
.find input:focus { border-color: var(--violet); box-shadow: 0 0 0 4px var(--violet-soft); }
.find svg { position: absolute; left: 17px; top: 17px; width: 20px; height: 20px; color: var(--muted); pointer-events: none; }
.find kbd { position: absolute; right: 14px; top: 14px; font: inherit; font-size: 12px; color: var(--muted); border: 1px solid var(--line-strong);
  border-radius: 6px; padding: 2px 7px; }
.results { margin-top: 14px; background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 8px 20px 14px; }
.results[hidden] { display: none; }
.results .rows { max-height: 420px; overflow-y: auto; }
.results-f { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; color: var(--muted); padding-top: 10px; border-top: 1px solid var(--line); margin-top: 6px; }
.results-f a { color: var(--blue); text-decoration: none; }
.results .none { color: var(--muted); padding: 12px 0; margin: 0; }

h2.h { font-family: var(--display); font-weight: 400; font-size: 36px; line-height: 1.1; margin: 0; letter-spacing: -0.01em; }
.h-row { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.h-row a, .h-row span { font-size: 14px; color: var(--blue); text-decoration: none; white-space: nowrap; }
.h-row span { color: var(--muted); }
.intro { color: var(--dim); font-size: 14.5px; margin: -8px 0 18px; }
.block { margin-top: 88px; }

.split { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: 64px; margin-top: 40px; }
.path { list-style: none; margin: 0; padding: 0; counter-reset: s; }
.step { display: grid; grid-template-columns: 40px 1fr 28px; gap: 14px; align-items: start; padding: 14px 0; border-top: 1px solid var(--line); position: relative; }
.step:last-child { border-bottom: 1px solid var(--line); }
.step-n { font-family: var(--display); font-size: 30px; line-height: 1; color: var(--violet); padding-top: 2px; }
.step-b a { font-size: 16px; font-weight: 600; text-decoration: none; }
.step-b a:hover { color: var(--violet); }
.step-b p { margin: 3px 0 0; font-size: 13.5px; color: var(--dim); line-height: 1.55; }
.step-c { position: relative; width: 22px; height: 22px; margin-top: 3px; cursor: pointer; }
.step-c input { position: absolute; inset: 0; opacity: 0; margin: 0; cursor: pointer; }
.step-c span { position: absolute; inset: 0; border: 1.5px solid var(--line-strong); border-radius: 6px; transition: background .15s, border-color .15s; }
.step-c input:checked + span { background: var(--green); border-color: var(--green); }
.step-c input:checked + span::after { content: ''; position: absolute; left: 7px; top: 3px; width: 5px; height: 10px; border: solid #0a0d14; border-width: 0 2px 2px 0; transform: rotate(45deg); }
.step-c input:focus-visible + span { outline: 2px solid var(--blue); outline-offset: 2px; }
.step.done .step-n { color: var(--green); }
.step.done .step-b a { color: var(--dim); }
.progress { height: 3px; background: var(--line); border-radius: 3px; margin: 0 0 4px; overflow: hidden; }
.progress i { display: block; height: 100%; width: 0; background: var(--green); transition: width .3s; }

.secs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 56px; }
.sec { padding: 26px 0 22px; border-top: 1px solid var(--line); display: flex; flex-direction: column; }
.sec-h h3 { margin: 0 0 4px; font-size: 20px; font-weight: 600; display: flex; align-items: center; gap: 10px; }
.sec-h h3 a { text-decoration: none; } .sec-h h3 a:hover { color: var(--blue); }
.sec-h p { margin: 0; color: var(--dim); font-size: 14px; }
.sec-h .sec-n { color: var(--muted); font-size: 13px; margin: 4px 0 8px; }
.sec .rows .row-m { display: none; }
.more { margin-top: auto; padding-top: 8px; font-size: 13.5px; color: var(--blue); text-decoration: none; }
.more:hover { text-decoration: underline; }

.pillars { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 32px; }
.pillar { border-top: 2px solid var(--blue); padding-top: 18px; }
.pillar-no { font-family: var(--display); font-size: 40px; line-height: 1; margin: 0 0 12px; color: var(--blue); }
.pillar h3 { margin: 0 0 6px; font-size: 18px; font-weight: 600; }
.pillar-st { font-size: 13px; color: var(--green); margin: 0 0 10px; }
.pillar p { font-size: 14px; color: var(--dim); margin: 0 0 12px; line-height: 1.65; }
.pillar-l { list-style: none; margin: 0; padding: 0; }
.pillar-l li { padding: 5px 0; border-top: 1px solid var(--line); }
.pillar-l a { font-size: 14px; text-decoration: none; } .pillar-l a:hover { color: var(--blue); }

.guides { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 56px; }
.guides li { padding: 14px 0; border-top: 1px solid var(--line); }
.guides a { font-weight: 600; text-decoration: none; } .guides a:hover { color: var(--blue); }
.guides p { margin: 2px 0 0; color: var(--dim); font-size: 14px; }

@media (max-width: 900px) {
  .split { grid-template-columns: 1fr; gap: 72px; }
  .pillars { grid-template-columns: 1fr; gap: 40px; }
}
@media (max-width: 640px) {
  .hero { padding-top: 44px; } .hero h1 { font-size: 52px; }
  .find kbd { display: none; }
  .secs, .guides { grid-template-columns: 1fr; }
  h2.h { font-size: 30px; }
  .block { margin-top: 64px; }
}`;

const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>upio.ai · 团队知识库</title>
<meta name="description" content="upio 团队知识库：门店 AI 获客、内容产线、AI 陪伴产品的讲解、方案、复盘和操作手册。">
${shell.FONTS}
<style>
${CSS}
</style>
</head>
<body>
${shell.topbar('home')}
<div class="wrap">
  <header class="hero">
    <h1>团队知识库</h1>
    <p class="lede">upio 在做门店 AI 获客系统、AI 内容产线和 AI 陪伴产品。团队写下的讲解、方案、复盘和操作手册都在这里。</p>
    <p class="meta">共 ${docs.length} 篇文章和 ${cases.length} 个客户案例，最近一次更新在 ${cn(lastDate)}。<a href="/timeline">按时间看全部文章</a></p>
    <div class="find" role="search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <input id="q" type="search" placeholder="搜索全部文章和案例，比如：企微、B-roll、VPN" aria-label="搜索全部文章和案例" autocomplete="off" aria-controls="results">
      <kbd>/</kbd>
    </div>
    <div class="results" id="results" hidden aria-live="polite"></div>
  </header>

  <div class="split">
    <section aria-labelledby="h-path">
      <div class="h-row"><h2 class="h" id="h-path">新人路线</h2><span id="path-count">已读 0 / ${ONBOARDING.length}</span></div>
      <p class="intro">第一次来，按这个顺序读。读完一篇打个勾，进度只保存在你自己的浏览器里。</p>
      <div class="progress" aria-hidden="true"><i id="path-bar"></i></div>
      <ol class="path" id="path">
${steps}
      </ol>
    </section>
    <section aria-labelledby="h-new">
      <div class="h-row"><h2 class="h" id="h-new">最近更新</h2><a href="/timeline">全部 ${docs.length} 篇</a></div>
      <ol class="rows">${latest.map(it => shell.rowHtml(it, SECTION_LABEL)).join('')}</ol>
    </section>
  </div>

  <section class="block" aria-labelledby="h-sec">
    <div class="h-row"><h2 class="h" id="h-sec">按项目浏览</h2></div>
    <div class="secs">
${sectionRows}
    </div>
  </section>

  <section class="block" aria-labelledby="h-store">
    <div class="h-row"><h2 class="h" id="h-store">门店系统</h2><a href="/akke/product-overview-2026-08-12">产品完整介绍</a></div>
    <p class="intro">把大模型放进一家门店的获客与接待里。从刷到视频、加上微信，到答疑和约到店，三块能力串成一条增长链，共用一个数据库，出站动作都有闸门把关。</p>
    <div class="pillars">
${pillarCards}
    </div>
  </section>

  <section class="block" aria-labelledby="h-guide">
    <div class="h-row"><h2 class="h" id="h-guide">常用指南</h2></div>
    <ul class="guides">
${guideRows}
    </ul>
  </section>
</div>
${shell.footer('首页和全部文章页在每次部署时自动生成，新文章放进 public/ 就会出现')}
<script type="application/json" id="kb-data">${searchData}</script>
<script>
(function () {
  var rowHtml = ${shell.rowHtml.toString()};
  var LABELS = ${JSON.stringify(SECTION_LABEL)};
  var DATA = JSON.parse(document.getElementById('kb-data').textContent).map(function (r) {
    return { href: r[0], title: r[1], date: r[2], section: r[3], cat: r[4], kind: r[5] ? 'case' : 'doc' };
  });
  var q = document.getElementById('q'), box = document.getElementById('results');
  function e(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function search() {
    var v = q.value.trim().toLowerCase();
    if (!v) { box.hidden = true; box.innerHTML = ''; return; }
    var words = v.split(/\\s+/);
    var hits = DATA.filter(function (d) {
      var hay = (d.title + ' ' + d.href + ' ' + (d.cat || '') + ' ' + (LABELS[d.section] || '')).toLowerCase();
      return words.every(function (w) { return hay.indexOf(w) >= 0; });
    }).sort(function (a, b) {
      if (a.kind !== b.kind) return a.kind === 'doc' ? -1 : 1; // 文章在前,案例在后
      return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
    });
    var link = '/timeline?q=' + encodeURIComponent(q.value.trim()) + (hits.some(function (h) { return h.kind === 'doc'; }) ? '' : '&kind=case');
    box.innerHTML = hits.length
      ? '<ol class="rows">' + hits.slice(0, 30).map(function (h) { return rowHtml(h, LABELS); }).join('') + '</ol>' +
        '<div class="results-f"><span>找到 ' + hits.length + ' 条' + (hits.length > 30 ? '，这里显示前 30 条' : '') + '</span><a href="' + e(link) + '">在全部文章里筛选</a></div>'
      : '<p class="none">没有标题里含「' + e(q.value.trim()) + '」的文章。试试更短的词，或到<a href="/timeline" style="color:var(--blue)">全部文章</a>里按分区找。</p>';
    box.hidden = false;
  }
  q.addEventListener('input', search);
  q.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { q.value = ''; search(); } });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === '/' && document.activeElement !== q && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { ev.preventDefault(); q.focus(); }
  });

  // 新人路线:已读进度只存在本机浏览器(localStorage 不可用时照常显示,只是不记)
  var KEY = 'upio-kb-onboarding';
  var done = {};
  try { done = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (err) { done = {}; }
  var steps = document.querySelectorAll('#path .step');
  function paint() {
    var n = 0;
    Array.prototype.forEach.call(steps, function (li) {
      var on = !!done[li.getAttribute('data-href')];
      li.classList.toggle('done', on); li.querySelector('input').checked = on; if (on) n++;
    });
    document.getElementById('path-count').textContent = '已读 ' + n + ' / ' + steps.length;
    document.getElementById('path-bar').style.width = (n / steps.length * 100) + '%';
  }
  Array.prototype.forEach.call(steps, function (li) {
    li.querySelector('input').addEventListener('change', function (ev) {
      var h = li.getAttribute('data-href');
      if (ev.target.checked) done[h] = 1; else delete done[h];
      try { localStorage.setItem(KEY, JSON.stringify(done)); } catch (err) {}
      paint();
    });
  });
  paint();
})();
</script>
</body>
</html>
`;

fs.writeFileSync(path.join(publicDir, 'index.html'), html);
console.log(`Built index: ${docs.length} articles, ${cases.length} cases, ${ONBOARDING.length} onboarding steps, ${STORE_PILLARS.length} store pillars, ${GUIDES.length - missing.length} guides`);
