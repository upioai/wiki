#!/usr/bin/env node
/**
 * build-akke-map.js — 构建期生成 public/akke/index.html(Akke 知识库「项目地图」)
 *
 * 数据来源(都已提交,部署期不依赖 git):
 *   scripts/akke-map.shell.html  页面壳(head/CSS/hero/从这里开始/footer + 4 个占位符)
 *   scripts/akke-map.json        { featured:[...], overrides:{ href:{cat,title,desc,label,daily,archDate} } }
 *   scripts/akke-map.dates.json  { href: "YYYY-MM-DD" }  各页 git 创建日期缓存
 *
 * 规则:
 *   - 扫 public/akke/ 所有页面;跳过 index/intro/重定向 stub/置顶卡。
 *   - 分类:akke-map.json 的 override 优先,否则按文件名规则自动归类,再否则进「待归类」(绝不丢)。
 *   - 每类内按创建时间新→旧排,卡片左上角标日期。archive 用紧凑列表,个人日报单独成 strip。
 *   - 「最近新增」「个人日报」「cat-nav」全自动算。新页面 push 后自动归位。
 *
 * 本地刷新日期缓存:REFRESH_DATES=1 node scripts/build-akke-map.js (有 git 时把新页日期并入 dates.json)
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const AKKE = path.join(ROOT, 'public', 'akke');
const shell = fs.readFileSync(path.join(__dirname, 'akke-map.shell.html'), 'utf8');
const map = JSON.parse(fs.readFileSync(path.join(__dirname, 'akke-map.json'), 'utf8'));
const datesPath = path.join(__dirname, 'akke-map.dates.json');
let dates = JSON.parse(fs.readFileSync(datesPath, 'utf8'));
const overrides = map.overrides || {};

const BUILD_DATE = new Date().toISOString().slice(0, 10);

// ---- 分类配置(顺序即页面顺序) ----
const CATS = [
  { id: 'foundation', title: '入门 · 架构',       hint: '常驻 · 新人必读',            accent: '' },
  { id: 'tech',       title: '技术方案',           hint: '工程深潜 · PPT 三件套',       accent: '' },
  { id: 'ops',        title: '触达运营 · SOP',     hint: '一线操作手册',               accent: '' },
  { id: 'cloudpc',    title: '云电脑 · 无影通道',   hint: '阿里无影 + 抖音 PC 自动发',   accent: 'cat-cloud' },
  { id: 'multi',      title: '多通道触达 · 调研',   hint: '企微 / 外呼 / 内容',          accent: '' },
  { id: 'cases',      title: '用户案例库',         hint: '真实客户旅程',               accent: '' },
  { id: 'retro',      title: '项目复盘 · 0→1',     hint: '子项目从无到有的过程沉淀',     accent: 'cat-retro' },
  { id: 'archive',    title: '报告 · 复盘存档',     hint: '一次性快照 · 新 → 旧',        accent: 'cat-archive', kind: 'archive' },
  { id: 'uncat',      title: '待归类 · Uncategorized', hint: '新页面没配分类 — 请在 scripts/akke-map.json 的 overrides 里归类', accent: 'cat-archive', kind: 'archive' },
];
const CAT_MAP = Object.fromEntries(CATS.map(c => [c.id, c]));

// ---- 新页面自动归类规则(override 未命中时) ----
function classify(href) {
  if (/\/reports\/daily-/.test(href)) return { cat: 'archive', daily: true };
  const base = href.replace('/akke/', '').replace(/\/$/, '');
  if (/cloud-pc|wuying|second-touch/.test(base)) return { cat: 'cloudpc' };
  if (/\d{4}-?\d{2}-?\d{2}|intent|scrap|source|acquisition|feigua|video-data|valid-user|d0-baseline|openrouter|icebreak|dialogue|pricing|topic|supply|risk-control|potential-touch|meeting|weekly/.test(base)) return { cat: 'archive' };
  if (href.startsWith('/akke/reports/')) return { cat: 'archive' };
  return { cat: 'uncat' };
}

const isStub = (file) => {
  try { return /http-equiv=["']?refresh|window\.location/i.test(fs.readFileSync(file, 'utf8')); }
  catch { return false; }
};
const titleOf = (file) => {
  try { return (fs.readFileSync(file, 'utf8').match(/<title>([^<]*)<\/title>/) || ['', ''])[1].replace(/\s*·\s*Akke\s*$/, '').trim(); }
  catch { return ''; }
};
const dateOf = (href) => {
  if (dates[href]) return dates[href];
  const m = href.match(/(\d{4})-?(\d{2})-?(\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : BUILD_DATE;
};
const mmdd = (d) => (d && d.length >= 10 ? d.slice(5, 10) : '');
const esc = (s) => s; // 描述已是可信 HTML 片段(来自我们自己的 manifest)

// ---- 枚举所有页面 ----
const featuredHrefs = new Set(map.featured.map(f => f.href));
const HIDDEN = new Set(['/akke/index']); // intro 仍在「入门·架构」列卡;它同时也在 hero/从这里开始 置顶
const pages = []; // {href, file}
for (const f of fs.readdirSync(AKKE)) {
  if (f.endsWith('.html')) pages.push({ href: '/akke/' + f.replace(/\.html$/, ''), file: path.join(AKKE, f) });
}
for (const sub of ['cases', 'prompt-atlas']) {
  const p = path.join(AKKE, sub, 'index.html');
  if (fs.existsSync(p)) pages.push({ href: `/akke/${sub}/`, file: p });
}
const repDir = path.join(AKKE, 'reports');
if (fs.existsSync(repDir)) for (const d of fs.readdirSync(repDir)) {
  const p = path.join(repDir, d, 'index.html');
  if (fs.existsSync(p)) pages.push({ href: `/akke/reports/${d}/`, file: p });
}

// 本地刷新日期缓存(部署期不会进这分支,因 git 浅克隆无历史)
if (process.env.REFRESH_DATES) {
  let added = 0;
  for (const { href, file } of pages) {
    if (dates[href]) continue;
    try {
      const rel = path.relative(ROOT, file);
      const iso = execSync(`git log --diff-filter=A --follow --format=%aI -1 -- ${JSON.stringify(rel)}`, { cwd: ROOT, encoding: 'utf8' }).trim();
      if (iso) { dates[href] = iso.slice(0, 10); added++; }
    } catch {}
  }
  if (added) { fs.writeFileSync(datesPath, JSON.stringify(dates)); console.log(`[dates] +${added}`); }
}

// ---- 归类 ----
const items = [];
const warnings = [];
for (const { href, file } of pages) {
  if (HIDDEN.has(href) || featuredHrefs.has(href)) continue;
  if (isStub(file)) continue;
  const ov = overrides[href] || {};
  let cat = ov.cat, daily = !!ov.daily;
  if (!cat) { const c = classify(href); cat = c.cat; daily = daily || !!c.daily; if (cat === 'uncat') warnings.push(href); }
  if (!CAT_MAP[cat]) { warnings.push(`${href}(未知分类 ${cat})`); cat = 'uncat'; }
  if (/\/reports\/daily-/.test(href)) daily = true;
  items.push({
    href, cat, daily,
    title: ov.title || titleOf(file),
    desc: ov.desc || '',
    label: ov.label || '',
    date: dateOf(href),
  });
}
const byDateDesc = (a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : (a.href < b.href ? -1 : 1));

// ---- 渲染片段 ----
const docCard = (it) => {
  const d = mmdd(it.date);
  const num = it.label ? (d ? `${d} · ${it.label}` : it.label) : d;
  return `    <a class="doc-card" href="${it.href}">
      <div class="num">${num}</div>
      <h3>${it.title}</h3>
      <p>${esc(it.desc)}</p>
      <span class="arrow">查看 →</span>
    </a>`;
};
const archRow = (it) => `    <a class="arch-row" href="${it.href}"><span class="d">${mmdd(it.date)}</span><span class="t">${it.title}</span></a>`;

const featuredHtml = () => {
  const cards = map.featured.map(f => {
    const style = f.danger ? ' style="border-color: var(--danger-soft);"' : '';
    const numStyle = f.danger ? ' style="color: var(--danger);"' : '';
    return `    <a class="doc-card" href="${f.href}"${style}>
      <div class="num"${numStyle}>${f.badge}</div>
      <h3>${f.title}</h3>
      <p>${esc(f.desc)}</p>
      <span class="arrow">查看 →</span>
    </a>`;
  }).join('\n');
  return `  <h3 class="cat cat-live" id="live" style="margin-top: 8px;">★ 实时看板 <span class="hint">直连生产库 · 自动刷新 · 最常看</span></h3>
  <div class="doc-grid">
${cards}
  </div>`;
};

const recentHtml = () => {
  const recent = items.filter(it => !it.daily).sort(byDateDesc).slice(0, 8);
  return `  <h2 id="recent">最近新增 · Recently Added</h2>
  <p style="margin: 0 0 12px; color: var(--text-dim); font-size: 14.5px;">最新创建的页面排在最前（按入库时间倒序，自动生成）。个人日报每日例行更新，见底部「报告存档」的日报条。</p>
  <div class="archive-list">
${recent.map(archRow).join('\n')}
  </div>`;
};

const catNavHtml = () => {
  const chips = ['<a href="#live">★ 实时看板</a>', '<a href="#recent">🆕 最近新增</a>'];
  for (const c of CATS) {
    if (!items.some(it => it.cat === c.id && !it.daily)) continue; // 空类不出 chip
    chips.push(`<a href="#${c.id}">${c.title}</a>`);
  }
  return `  <nav class="cat-nav">\n    ${chips.join('\n    ')}\n  </nav>`;
};

const mapHtml = () => {
  const out = [];
  for (const c of CATS) {
    const list = items.filter(it => it.cat === c.id && !it.daily).sort(byDateDesc);
    if (!list.length) continue;
    const cls = `cat${c.accent ? ' ' + c.accent : ''}`;
    out.push(`  <h3 class="${cls}" id="${c.id}">${c.title} <span class="hint">${c.hint}</span></h3>`);
    if (c.kind === 'archive') {
      out.push(`  <div class="archive-list">\n${list.map(archRow).join('\n')}\n  </div>`);
    } else {
      out.push(`  <div class="doc-grid">\n${list.map(docCard).join('\n')}\n  </div>`);
    }
  }
  // 个人日报 strip
  const daily = items.filter(it => it.daily).sort(byDateDesc);
  if (daily.length) {
    const chips = daily.map(it => {
      const who = (it.title.split('·').pop() || '').replace(/（.*$/, '').trim();
      return `    <a href="${it.href}">${mmdd(it.date)} <span class="who">${who}</span></a>`;
    }).join('\n');
    out.push(`  <div class="sub-label">个人日报 · Daily</div>\n  <div class="daily-strip">\n${chips}\n  </div>`);
  }
  return out.join('\n\n');
};

// ---- 填充壳 ----
let html = shell
  .replace('  <!--FEATURED-->', featuredHtml())
  .replace('  <!--RECENT-->', recentHtml())
  .replace('  <!--CATNAV-->', catNavHtml())
  .replace('  <!--MAP-->', mapHtml());

fs.writeFileSync(path.join(AKKE, 'index.html'), html);

// ---- 报告 ----
const counts = {};
for (const it of items) counts[it.cat] = (counts[it.cat] || 0) + 1;
console.log(`[akke-map] 生成 index.html · 置顶 ${map.featured.length} · 条目 ${items.length}`);
console.log('  分类:', JSON.stringify(counts));
if (warnings.length) console.warn(`  ⚠️ 待归类/异常 ${warnings.length} 个:`, warnings.join(', '));
