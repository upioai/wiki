#!/usr/bin/env node
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';

const page = readFileSync('public/akke/personal-wechat-pc-bugs.html', 'utf8');
const data = JSON.parse(readFileSync('public/akke/personal-wechat-pc-bug-status.json', 'utf8'));
const ids = new Set([...page.matchAll(/\bid="(W-\d+)"/g)].map(match => match[1]));
const levels = new Set(['PASS', 'CODE_ONLY', 'FAIL', 'UNVERIFIED']);

function checkStatus(source) {
  const errors = [];
  if (!/^\d{4}-\d{2}-\d{2}$/.test(source.updatedAt || '') ||
      Number.isNaN(Date.parse(source.updatedAt))) errors.push('updatedAt 必须是有效日期');
  if (!source.items || typeof source.items !== 'object') return [...errors, '缺少 items'];
  for (const [id, item] of Object.entries(source.items)) {
    if (!ids.has(id)) errors.push(`${id}: 页面里没有这个编号`);
    if (!levels.has(item.verification)) errors.push(`${id}: 检验结论无效`);
    if (typeof item.status !== 'string' || !item.status.trim()) errors.push(`${id}: 缺少修复进度`);
    if (typeof item.summary !== 'string' || item.summary.trim().length < 15) errors.push(`${id}: 缺少检验摘要`);
    if (typeof item.needsAttention !== 'boolean') errors.push(`${id}: 缺少待关注标记`);
    const reviewPath = `docs/runbooks/pc-bug-verification-${(item.review?.date || '').replaceAll('-', '')}.md`;
    if (!item.review || !/^\d{4}-\d{2}-\d{2}$/.test(item.review.date || '') ||
        item.review.by !== '独立检验 agent' ||
        item.review.url !== `https://github.com/upioai/wiki/blob/main/${reviewPath}` ||
        !existsSync(reviewPath) || !readFileSync(reviewPath, 'utf8').includes(id))
      errors.push(`${id}: 缺少独立检验者、日期或公开检验记录`);
    if (!Array.isArray(item.evidence) || !item.evidence.length ||
        item.evidence.some(link => !link.label || !/^https:\/\/github\.com\/(Akke-AI\/wecom-chat|upioai\/wiki)\/pull\/\d+$/.test(link.url || '')))
      errors.push(`${id}: 证据必须链接到相关仓库的 PR`);
    if (item.verification === 'PASS' && /待验收|待复核|待检验/.test(item.status))
      errors.push(`${id}: 完整验收通过不能同时写待验收`);
    if (item.verification !== 'PASS' && /已修复并验收|完整验收通过/.test(item.status))
      errors.push(`${id}: 未通过完整验收不能标记已验收`);
  }
  return errors;
}

// 台账数据脚本带 immutable 一年缓存，只靠 ?v= 换新；v 必须等于脚本内容哈希前 10 位，
// 否则改了脚本没改 v，已缓存的浏览器拿着旧脚本，新条目就退化成「没有日期、没有分类、全展开」的老样子。
const guideSource = readFileSync('public/akke/personal-wechat-pc-bug-guide.js');
const guideVersion = 'h' + createHash('sha256').update(guideSource).digest('hex').slice(0, 10);
function checkGuideVersion(html) {
  const found = html.match(/personal-wechat-pc-bug-guide\.js\?v=([\w-]+)/)?.[1];
  return found === guideVersion ? [] : [`personal-wechat-pc-bugs.html 的 guide.js 缓存版本是 ${found}，应改为 ?v=${guideVersion}（脚本内容变了必须同步）`];
}

if (process.argv.includes('--self-test')) {
  assert(checkGuideVersion(page.replace(/(bug-guide\.js\?v=)[\w-]+/, '$1w116-20261020')).length === 1);
  assert(checkStatus({...data, items: {'W-999': {verification:'PASS'}}}).some(error => error.includes('页面里没有')));
  assert(checkStatus({...data, items: {'W-94': {...data.items['W-94'], verification:'PASS', status:'待验收'}}}).some(error => error.includes('不能同时写待验收')));
  console.log('Bug 检验状态规则自测通过');
}

const errors = [...checkStatus(data), ...checkGuideVersion(page)];
if (errors.length) {
  console.error(errors.join('\n'));
  process.exitCode = 1;
} else {
  console.log(`Bug 检验状态检查通过：${Object.keys(data.items).length} 条独立检验记录`);
}
