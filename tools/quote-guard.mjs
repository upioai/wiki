#!/usr/bin/env node
/**
 * quote-guard —— 引语核验闸（CI 安全版，不含任何素材原文）
 *
 * 背景：2026-08-21/23 对 /akke 竞品专题页做了两轮事实核查，30 处错里 9 处是同一类——
 * 引语看着像原话，实际是压缩或改写（「以前设置一个问题」原文是「你设置一个问题」；
 * 「确保设置 10-50 条」界面写的是「建议」；「双击全部停止」界面是「双击空格停止」）。
 *
 * 为什么不把素材放进仓：本仓是 PUBLIC 仓库。竞品约访逐字稿含真实姓名、报价、通话
 * 对象信息，不能公开托管。所以这里只存**已核验引语的哈希**，不存原文——CI 能判
 * 「这句变了/是新的」，但读不到素材，也泄不出任何东西。
 *
 * 用法：
 *   node tools/quote-guard.mjs --check                 # CI：列出未核验的引语
 *   node tools/quote-guard.mjs --accept <page.html>    # 本地：核验完把该页引语记进台账
 *
 * 真正的逐字比对在 Akke 仓 scripts/quote-lint.ts（要素材，只能本地跑）。
 * 本闸只保证：**没被逐字比对过的引语，不会悄悄进 main。**
 */
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const LEDGER = "tools/quotes.verified.json";
const MIN_LEN = 12;          // 短词多是归纳标签不是引用
const SCOPE = "public/akke/";

const norm = (s) =>
  s.normalize("NFKC").toLowerCase()
   .replace(/[\s，。、？！,.?!:：;；「」『』“”"'（）()【】\[\]\-—–…·~]+/g, "");

const hash = (s) => crypto.createHash("sha256").update(norm(s)).digest("hex").slice(0, 16);

const stripTags = (html) =>
  html.replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<[^>]+>/g, "\n");

function quotesOf(file) {
  const t = stripTags(fs.readFileSync(file, "utf8"));
  return [...new Set((t.match(/「([^「」]{1,400})」/g) ?? []).map((q) => q.slice(1, -1).trim()))]
    .filter((q) => q.length >= MIN_LEN);
}

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith(".html")) out.push(p);
  }
  return out;
}

const loadLedger = () =>
  fs.existsSync(LEDGER) ? JSON.parse(fs.readFileSync(LEDGER, "utf8")) : { note: "", verified: {} };

function accept(pages) {
  const l = loadLedger();
  let added = 0;
  for (const p of pages) {
    for (const q of quotesOf(p)) {
      const h = hash(q);
      if (!l.verified[h]) {
        l.verified[h] = { page: p.replace(SCOPE, ""), len: q.length, at: process.env.VERIFY_DATE || "" };
        added++;
      }
    }
  }
  l.note = "已逐字回一手素材核验过的引语哈希（sha256 前16位，归一化后）。不含原文——本仓为公开仓库，素材不入库。新增/改动引语须先本地跑 Akke 仓 scripts/quote-lint.ts 核验，再 --accept 记账。";
  fs.writeFileSync(LEDGER, JSON.stringify(l, null, 2) + "\n");
  console.log(`已记账 ${added} 条，台账合计 ${Object.keys(l.verified).length} 条`);
}

function check(files) {
  const l = loadLedger();
  const miss = [];
  for (const p of files) {
    for (const q of quotesOf(p)) {
      if (!l.verified[hash(q)]) miss.push({ page: p.replace(SCOPE, ""), q });
    }
  }
  if (!miss.length) { console.log(`✅ 受检 ${files.length} 页，引语全部已核验（台账 ${Object.keys(l.verified).length} 条）`); return 0; }
  console.log(`❌ ${miss.length} 条引语未在核验台账中：\n`);
  for (const m of miss.slice(0, 40)) console.log(`  ${m.page}\n     「${m.q.slice(0, 90)}」`);
  if (miss.length > 40) console.log(`  …另有 ${miss.length - 40} 条`);
  console.log(`
怎么处理（二选一，别留中间态）：
  1) 这句确实是引用 → 在 Akke 仓本地跑逐字比对，对上了再回本仓：
       pnpm tsx scripts/quote-lint.ts --page=<该页> --src=<素材目录>
       node tools/quote-guard.mjs --accept public/akke/<该页>
  2) 这句本来就是我们自己的归纳、不是引用 → 去掉「」，或改写成不像引语的形式。`);
  return 1;
}

const args = process.argv.slice(2);
if (args[0] === "--accept") accept(args.slice(1));
else {
  const only = args.filter((a) => !a.startsWith("--"));
  const files = only.length ? only : walk(SCOPE);
  process.exit(check(files));
}
