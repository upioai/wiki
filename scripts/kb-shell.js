/**
 * kb-shell.js — 首页(build-index.js)与全部文章页(build-timeline.js)共用的外壳:
 *   色板/字体 token、顶部导航、页脚、文章行渲染。取值与 public/design-system.html 一致。
 */
const SECTIONS = [
  { id: 'learn',    label: '知识分享', href: '/learn',     desc: '大模型基础、Agent 与 AI 工程、工具入门的讲解合集' },
  { id: 'akke',     label: 'Akke',     href: '/akke/',     desc: '门店 AI 获客：抖音评论、个微 / 企微接待、企业大脑' },
  { id: 'workflow', label: 'Workflow', href: '/workflow/', desc: 'AI 内容生产流水线：短视频复刻、B-roll、成片' },
  { id: 'softie',   label: 'Softie',   href: '/softie/',   desc: 'AI 陪伴聊天，美国市场（Web + Android）' },
  { id: 'vivi',     label: 'Vivi',     href: '/vivi/',     desc: 'Telegram Mini App，AI 角色 RP 对话' },
  { id: 'guide',    label: '通用指南', href: '',           desc: 'VPN、Claude Code、装机与本站设计规范' },
];
const SECTION_LABEL = Object.fromEntries(SECTIONS.map(s => [s.id, s.label]));

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const FONTS = `<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&family=Instrument+Serif&family=Noto+Sans+SC:wght@400;500;700&display=swap">`;

const BASE_CSS = `:root {
  --bg: #0a0d14;
  --surface: #121620;
  --surface-2: #181d29;
  --line: #222838;
  --line-strong: #2f3646;
  --text: #e8eaef;
  --dim: #a3abbb;
  --muted: #6f7788;
  --violet: #8b5cf6;
  --violet-soft: rgba(139, 92, 246, 0.14);
  --blue: #60a5fa;
  --green: #34d399;
  --pink: #f472b6;
  --amber: #fbbf24;
  --slate: #94a3b8;
  --display: 'Instrument Serif', 'Source Han Serif SC', 'Songti SC', Georgia, serif;
  --ui: 'Inter Tight', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: var(--ui); font-size: 15.5px; line-height: 1.65;
  -webkit-font-smoothing: antialiased; min-height: 100vh; overflow-x: hidden;
}
a { color: inherit; }
:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; border-radius: 4px; }
.wrap { max-width: 1120px; margin: 0 auto; padding: 0 24px; }

/* 顶栏 */
.topbar { border-bottom: 1px solid var(--line); background: rgba(10, 13, 20, 0.9);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); position: sticky; top: 0; z-index: 20; }
.topbar .wrap { display: flex; align-items: center; gap: 28px; height: 56px; }
.brand { font-family: var(--display); font-size: 24px; text-decoration: none; letter-spacing: -0.01em; white-space: nowrap; }
.brand span { color: var(--muted); font-family: var(--ui); font-size: 13px; margin-left: 8px; letter-spacing: 0; }
.nav { display: flex; gap: 4px; overflow-x: auto; scrollbar-width: none; min-width: 0; }
.nav::-webkit-scrollbar { display: none; }
.nav a { text-decoration: none; color: var(--dim); font-size: 14px; padding: 6px 10px; border-radius: 8px; white-space: nowrap; }
.nav a:hover { color: var(--text); background: var(--surface); }
.nav a[aria-current="page"] { color: var(--text); background: var(--surface-2); }

/* 分区色点:颜色即分区,全站一致 */
.dot { width: 7px; height: 7px; border-radius: 50%; flex: 0 0 7px; background: var(--slate); display: inline-block; }
.dot-learn { background: var(--violet); } .dot-akke { background: var(--blue); } .dot-workflow { background: var(--green); }
.dot-softie { background: var(--pink); } .dot-vivi { background: var(--amber); } .dot-guide { background: var(--slate); }

/* 文章行:日期脊 + 标题 + 分区/分类 */
.rows { list-style: none; margin: 0; padding: 0; }
.row { display: grid; grid-template-columns: 52px 1fr; gap: 14px; padding: 10px 12px; margin: 0 -12px;
  text-decoration: none; border-radius: 10px; }
.row:hover { background: var(--surface); }
.row-d { font-variant-numeric: tabular-nums; font-size: 13px; color: var(--muted); padding-top: 2px; }
.row:hover .row-d { color: var(--blue); }
.row-t { display: block; font-size: 15px; font-weight: 500; color: var(--text); overflow-wrap: anywhere; }
.row-m { display: flex; align-items: center; gap: 8px; margin-top: 3px; font-size: 12.5px; color: var(--muted); flex-wrap: wrap; }
.row-m .sep { width: 3px; height: 3px; border-radius: 50%; background: var(--line-strong); }
.kind-case { color: var(--amber); }

footer.site { border-top: 1px solid var(--line); margin-top: 96px; padding: 28px 0 48px; color: var(--muted); font-size: 13px; }
footer.site .wrap { display: flex; gap: 8px 24px; flex-wrap: wrap; justify-content: space-between; }
footer.site a { color: var(--dim); text-decoration: none; }
footer.site a:hover { color: var(--text); }

@media (max-width: 640px) {
  .wrap { padding: 0 16px; }
  .topbar .wrap { gap: 14px; }
  .brand span { display: none; }
  .row { grid-template-columns: 44px 1fr; gap: 10px; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition: none !important; animation: none !important; } html { scroll-behavior: auto; } }`;

function topbar(current) {
  const links = [
    { id: 'home', label: '首页', href: '/' },
    { id: 'all', label: '全部文章', href: '/timeline' },
    ...SECTIONS.filter(s => s.href).map(s => ({ id: s.id, label: s.label, href: s.href })),
  ];
  return `<header class="topbar"><div class="wrap">
  <a class="brand" href="/">upio.ai<span>团队知识库</span></a>
  <nav class="nav" aria-label="站点导航">${links.map(l => `<a href="${l.href}"${l.id === current ? ' aria-current="page"' : ''}>${l.label}</a>`).join('')}</nav>
</div></header>`;
}

function footer(note) {
  return `<footer class="site"><div class="wrap">
  <span>${note}</span>
  <span><a href="/timeline">全部文章</a> &nbsp; <a href="/design-system">设计规范</a> &nbsp; <a href="https://github.com/upioai/wiki">仓库</a></span>
</div></footer>`;
}

// 同一份函数在 Node 预渲染,并以 toString() 注入页面供前端复用,所以只能用自身参数与 ES5 语法
function rowHtml(it, labels) {
  var e = function (s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); };
  var meta = '<span class="dot dot-' + e(it.section) + '"></span><span>' + e(labels[it.section] || it.section) + '</span>';
  if (it.cat && it.cat !== labels[it.section]) meta += '<span class="sep"></span><span' + (it.kind === 'case' ? ' class="kind-case"' : '') + '>' + e(it.cat) + '</span>';
  return '<li><a class="row" href="' + e(it.href) + '"><time class="row-d" datetime="' + e(it.date) + '">' +
    (it.date ? it.date.slice(5).replace('-', '.') : '--') + '</time><span><span class="row-t">' + e(it.title) +
    '</span><span class="row-m">' + meta + '</span></span></a></li>';
}

module.exports = { SECTIONS, SECTION_LABEL, esc, FONTS, BASE_CSS, topbar, footer, rowHtml };
