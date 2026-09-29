#!/usr/bin/env node
/**
 * check-kb.js — 知识库索引的回归检查(跑在完整构建之后,读 public/timeline/articles.json)
 *
 * 失败即退出码 1:
 *   1. 有文章/案例没有日期(会掉进「日期未知」,新人按时间看不到它)
 *   2. 有日期晚于今天(日期缓存或 meta 写错,会一直霸占「最近更新」)
 *   3. 文件名带日期后缀的公开页没被收录(2026-09-29 的事故:hash 分享页正则把 -20260929 当成 hash,28 篇文章从时间线消失)
 *   4. 首页新人路线指向的页面不在清单里
 *   5. hash 后缀的分享页混进了清单
 */
const fs = require('fs');
const path = require('path');

const PUB = path.join(__dirname, '..', 'public');
const list = JSON.parse(fs.readFileSync(path.join(PUB, 'timeline', 'articles.json'), 'utf8'));
const hrefs = new Set(list.map(a => a.href));
const today = new Date(Date.now() + 24 * 3600e3).toISOString().slice(0, 10); // 放宽一天:CI 是 UTC,北京时间 0–8 点推当天的页不算未来
const errors = [];

const undated = list.filter(a => !a.date);
if (undated.length) errors.push(`没有日期 ${undated.length} 篇(在 <head> 加 <meta name="upio:date"> 或跑 REFRESH_DATES=1):${undated.map(a => a.href).join(', ')}`);

const future = list.filter(a => a.date > today);
if (future.length) errors.push(`日期晚于今天 ${future.length} 篇:${future.map(a => `${a.href}=${a.date}`).join(', ')}`);

function walk(dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out); else if (e.name.endsWith('.html')) out.push(p);
  }
  return out;
}
const sha = (f) => require('crypto').createHash('sha1').update(fs.readFileSync(f)).digest('hex');
const fileOf = (href) => [href.replace(/\/$/, '') + '.html', href.replace(/\/$/, '') + '/index.html'].map(r => path.join(PUB, r)).find(f => fs.existsSync(f));
const listedHashes = new Set(list.map(a => fileOf(a.href)).filter(Boolean).map(sha)); // build-timeline 按内容去重,重复副本不算漏收
const lost = [];
for (const f of walk(PUB, [])) {
  const href = '/' + path.relative(PUB, f).split(path.sep).join('/').replace(/\.html$/, '').replace(/\/index$/, '/');
  if (!/-(20\d{6}|20\d{2}-\d{2}(-\d{2})?)\/?$/.test(href)) continue;
  if (/^\/(internal|partners)\//.test(href) || /^\/akke\/(reports\/daily-|model-watch-|xiaoguotu\/|zhishi\/|kit\/)/.test(href)) continue; // 与 build-timeline.js EXCLUDE 一致
  if (/yeqiao|fanli|xiaxia|shiman|ziyang|fanny/i.test(href) || /野荞|饭粒|夏夏|狮蛮|谭伊格|子扬|董津瑄/.test(fs.readFileSync(f, 'utf8'))) continue; // 隐私跳过是有意的
  const html = fs.readFileSync(f, 'utf8');
  if (/<meta[^>]+name=["']robots["'][^>]*noindex/i.test(html) || /http-equiv=["']?refresh/i.test(html)) continue;
  if (!hrefs.has(href) && !listedHashes.has(sha(f))) lost.push(href);
}
if (lost.length) errors.push(`带日期后缀的公开页没进清单 ${lost.length} 篇(检查 build-timeline.js 的 EXCLUDE):${lost.join(', ')}`);

const idx = fs.readFileSync(path.join(__dirname, 'build-index.js'), 'utf8');
const onboarding = (idx.match(/const ONBOARDING = \[([\s\S]*?)\n\];/) || ['', ''])[1];
const badSteps = [...onboarding.matchAll(/href: '([^']+)'/g)].map(m => m[1]).filter(h => !hrefs.has(h));
if (badSteps.length) errors.push(`新人路线指向的页面不在清单里:${badSteps.join(', ')}`);

const leaked = list.filter(a => /-[0-9a-f]{8}(\/|$)/.test(a.href) && !/-20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(\/|$)/.test(a.href));
if (leaked.length) errors.push(`hash 分享页混进了清单:${leaked.map(a => a.href).join(', ')}`);

const docs = list.filter(a => a.kind === 'doc').length;
console.log(`[check-kb] 文章 ${docs} 篇,案例 ${list.length - docs} 个`);
if (errors.length) { for (const e of errors) console.error(`  ✗ ${e}`); process.exit(1); }
console.log('[check-kb] ✓ 全部通过');
