#!/usr/bin/env node
// 一次性回填：从 public/akke/cases/index.html 的案例卡 + git 提交日，生成
// public/akke/cases/manifest.json —— team-journey 壳页「今日生成的用户旅程」板块的数据源。
//
// 字段：{ slug, name, avatar, tag, quote, date(生成日=git 首次提交日), op(运营，可空) }
//
// ⚠️ manifest.json 一旦生成即为「真相源」，之后由 case-study skill 每生成一条新案例
//    APPEND 一行（带当天 date + op），不再整表重建——重建会丢掉 skill 写入的 op。
//    仅在 manifest 损坏/需重置时才重跑本脚本。
//
// 用法（在 upioai-wiki 根目录）：node tools/gen-cases-manifest.mjs

import { readFileSync, writeFileSync } from "node:fs";
import { execSync } from "node:child_process";

const CASES_DIR = "public/akke/cases";
const INDEX = `${CASES_DIR}/index.html`;
const OUT = `${CASES_DIR}/manifest.json`;

// 已在壳页手工标注过运营归属的案例（其余历史案例 op 留空——只影响分组展示，按天筛不受影响）。
const OP_MAP = {
  jiandan: "饭粒（一筑·全屋定制）",
  "meishan-2019": "饭粒（一筑·全屋定制）",
  zhuanshen: "饭粒（一筑·全屋定制）",
  "chaocai-buyaoguo": "野荞",
  "camille-zhongshan": "野荞",
  yuaner: "野荞",
};

const html = readFileSync(INDEX, "utf8");

// 按案例卡锚点切块，逐块抽字段
const blocks = html.split('<a class="case-card"').slice(1);
const pick = (re, s) => {
  const m = s.match(re);
  return m ? m[1].trim() : "";
};

const gitAddDate = (slug) => {
  try {
    const out = execSync(
      `git log --diff-filter=A --format=%ad --date=short -- "${CASES_DIR}/${slug}.html"`,
      { encoding: "utf8" },
    ).trim();
    const lines = out.split("\n").filter(Boolean);
    return lines.length ? lines[lines.length - 1] : ""; // 最早一次 = 新增日
  } catch {
    return "";
  }
};

const entries = [];
for (const b of blocks) {
  const slug = pick(/href="\/akke\/cases\/([^"]+)"/, b);
  if (!slug) continue;
  const avatar = pick(/class="case-avatar"\s+src="([^"]+)"/, b);
  const name = pick(/class="case-avatar"[^>]*\salt="([^"]*)"/, b);
  const tag = pick(/class="case-tag accent">([^<]*)</, b);
  const quote = pick(/class="quote">([^<]*)</, b);
  entries.push({
    slug,
    name,
    avatar,
    tag,
    quote,
    date: gitAddDate(slug),
    op: OP_MAP[slug] ?? null,
  });
}

// 按生成日倒序，方便人读
entries.sort((a, b) => (b.date < a.date ? -1 : b.date > a.date ? 1 : 0));

writeFileSync(OUT, JSON.stringify(entries, null, 2) + "\n");
const withDate = entries.filter((e) => e.date).length;
const withOp = entries.filter((e) => e.op).length;
console.log(`✅ ${OUT}: ${entries.length} 条（有生成日 ${withDate} / 有运营标注 ${withOp}）`);
const today = entries.filter((e) => e.date === "2026-06-22");
console.log(`   6/22 生成的: ${today.map((e) => `${e.slug}(${e.op ?? "—"})`).join(", ") || "无"}`);
