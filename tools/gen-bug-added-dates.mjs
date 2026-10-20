#!/usr/bin/env node
// 批量取每个 W 编号在 personal-wechat-pc-bugs.html 的 git 历史里「第一次出现」的日期，
// 输出 JSON（供 personal-wechat-pc-bug-guide.js 的 added 字段核对/重生成）。
// 还没提交过的新编号用今天的日期。用法：node tools/gen-bug-added-dates.mjs [--check]
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const page = 'public/akke/personal-wechat-pc-bugs.html';
const log = execFileSync('git', ['log', '--reverse', '--format=C %H %ad', '--date=short', '-p', '--', page],
  { encoding: 'utf8', maxBuffer: 1 << 29 });
const first = {};
let day = '';
for (const line of log.split('\n')) {
  if (line.startsWith('C ')) { day = line.split(' ')[2]; continue; }
  const m = line.match(/^\+.*<div class="bug" id="(W-\d+)"/);
  if (m && !first[m[1]]) first[m[1]] = day;
}
const today = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);
const ids = [...readFileSync(page, 'utf8').matchAll(/<div class="bug" id="(W-\d+)"/g)].map(m => m[1]);
const out = Object.fromEntries(ids.map(id => [id, first[id] || today]));
if (process.argv.includes('--check')) {
  const w = {};
  runInNewContext(readFileSync('public/akke/personal-wechat-pc-bug-guide.js', 'utf8'), { window: w });
  const bad = ids.filter(id => !/^\d{4}-\d{2}-\d{2}$/.test(w.PC_BUG_GUIDE.added?.[id] || '') || (first[id] && w.PC_BUG_GUIDE.added[id] !== first[id]));
  if (bad.length) { console.error('添加日期缺失或与 git 首现日不符：' + bad.join(' ')); process.exit(1); }
  console.log(`添加日期检查通过：${ids.length} 条`);
} else console.log(JSON.stringify(out, null, 1));
