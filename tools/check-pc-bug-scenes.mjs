#!/usr/bin/env node
// W-01–W-97 predate the scene requirement. Every later public PC bug needs one.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const page = 'public/akke/personal-wechat-pc-bugs.html';
const guideFile = 'public/akke/personal-wechat-pc-bug-guide.js';
const html = readFileSync(page, 'utf8');
const window = {};
runInNewContext(readFileSync(guideFile, 'utf8'), { window }, { timeout: 1000 });
const guide = window.PC_BUG_GUIDE;
const cutoff = 97;

function checkScenes(pageHtml, data) {
  const errors = [];
  const ids = [...pageHtml.matchAll(/\bid="(W-\d+)"/g)].map(match => match[1]);
  if (new Set(ids).size !== ids.length) errors.push('Bug 编号重复');
  for (const id of ids) {
    if (!data.entries?.[id]) errors.push(`${id}: 缺少阅读层条目`);
    if (Number(id.slice(2)) <= cutoff) continue;
    const scene = data.scenes?.[id];
    if (!scene) { errors.push(`${id}: 缺少微信场景还原`); continue; }
    if (!['wechat', 'log'].includes(scene.kind)) errors.push(`${id}: 场景须标明微信截图或日志还原`);
    if (typeof scene.source !== 'string' || scene.source.trim().length < 20) errors.push(`${id}: 缺少具体证据来源及还原边界`);
    if (!Array.isArray(scene.frames) || scene.frames.length < 2) {
      errors.push(`${id}: 至少还原事发前后两个画面`);
      continue;
    }
    scene.frames.forEach((frame, index) => {
      const content = frame.bubbles || frame.listRows;
      if (!frame.title || !frame.note || !Array.isArray(content) || !content.length)
        errors.push(`${id}: 第 ${index + 1} 帧缺少标题、画面内容或证据说明`);
    });
  }
  for (const id of Object.keys(data.entries || {})) {
    if (Number(id.slice(2)) > cutoff && !ids.includes(id)) errors.push(`${id}: 阅读层有条目但页面没有 Bug 卡片`);
  }
  return errors;
}

if (process.argv.includes('--self-test')) {
  const newCard = `${html}\n<div class="bug" id="W-98"></div>`;
  const sample = structuredClone(guide);
  sample.entries['W-98'] = ['vision', '示例原因'];
  assert(checkScenes(newCard, sample).some(error => error.includes('缺少微信场景还原')));
  sample.scenes['W-98'] = { kind: 'log', source: '', frames: [] };
  assert(checkScenes(newCard, sample).some(error => error.includes('缺少具体证据来源')));
  sample.scenes['W-98'] = {
    kind: 'log', source: '依据已核对的客户端日志还原；没有原始截图，具体位置无法确认。',
    frames: [
      { title: '事发前', listRows: ['待检查会话'], note: '日志确认进入检查。' },
      { title: '事发后', listRows: ['检查中断'], note: '日志确认中断。' }
    ]
  };
  assert.deepEqual(checkScenes(newCard, sample), []);
  console.log('场景规则自测通过：缺失/不完整被拦，合格样例通过');
}

const errors = checkScenes(html, guide);
if (errors.length) {
  console.error(errors.join('\n'));
  process.exitCode = 1;
} else {
  console.log('个人微信 Bug 场景检查通过：W-98 起每条均有来源与前后画面');
}
