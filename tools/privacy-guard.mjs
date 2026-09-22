#!/usr/bin/env node
/**
 * privacy-guard —— 隐私闸（CI 安全版：命中样本一律打码输出）
 *
 * 背景：2026-09-22 在 81 个 Akke 页面与数据文件里清出了终端客户的完整手机号、住址、
 * 车牌式客户编码（闽D12345 这类）和微信号（提交 758512e）。本仓是 PUBLIC 仓库，
 * 这些信息一旦推上去，任何人都能在 GitHub 历史里翻到。本闸拦的是「下一次再带进来」。
 *
 * 为什么输出要打码：Actions 日志和仓库一样公开。闸如果把命中的原文打出来，
 * 等于把号码再发一遍。所以只打「文件:行:列 类型 打码样本」，原文请本地打开文件看。
 *
 * 用法：
 *   node tools/privacy-guard.mjs --check <文件...>     # 查指定文件
 *   node tools/privacy-guard.mjs                       # 查相对 origin/main 的改动（含未提交、未跟踪）
 *   node tools/privacy-guard.mjs --base <ref>          # 查相对 <ref> 的改动（CI 用）
 *   node tools/privacy-guard.mjs --all                 # 全量扫 public/
 *
 * 范围：public/ 下 html/json/js/md/txt/csv 的内容；另外所有改动文件的**路径**本身
 * （截图文件名里带客户编码也算泄露）。
 * 放行：tools/privacy-guard.allow.json 的精确白名单；手机号中间四位为 0000 的占位号；
 * 紧挨着 * 的已打码样本。
 */
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ALLOW_FILE = fileURLToPath(new URL("./privacy-guard.allow.json", import.meta.url));
const ROOT = path.resolve(path.dirname(ALLOW_FILE), "..");   // 仓根：从哪个目录跑都行
const SCOPE = "public/";
const TEXT_EXT = /\.(html?|json|js|md|txt|csv)$/i;
const MAX_PRINT = +process.env.PRIVACY_GUARD_MAX || 80;   // 本地排查可调大
const PROV = "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼";

// 手机号左边界：不能紧贴数字或字母（哈希串 3e4ed18676444668a03… 里常夹着 11 位数字），
// 但「加V139…」「vx139…」「Tel139…」「+86139…」这类营销写法照拦
const PHONE_L = String.raw`(?:(?<![\dA-Za-z])|(?<=\+86|(?<![A-Za-z])(?:[Vv][Xx]?|[Ww][Xx]|[Tt]el|TEL)))`;
// 户号 = 楼层 + 两位户号（1502、402）；3 位数且末两位不是 0x 的多是户型面积（「5号楼162现代简约」
// 「16号楼130平」），后面跟 平/㎡/方/户型 的也是面积，都不算
const ROOM = String.raw`(?:\d{4}|\d0[1-9])(?![\d平㎡方m]|[A-Za-z]?户)`;
const UNIT = String.raw`(?:\d+|[一二三四五六七八九十]+)单元`;
const RULES = [
  { type: "手机号", re: new RegExp(String.raw`${PHONE_L}1[3-9]\d{9}(?!\d)`, "g") },
  { type: "手机号", re: new RegExp(String.raw`${PHONE_L}1[3-9]\d[- ]\d{4}[- ]\d{4}(?!\d)`, "g") },
  { type: "客户编码", re: new RegExp(`[${PROV}][A-Z]\\d{4,8}`, "g") },
  { type: "住址", re: new RegExp(String.raw`\d+号楼\d{3,4}(?=[室号])|\d+号楼${ROOM}|\d+栋\d+室|\d+栋${ROOM}|${UNIT}\d+(?:室|号)|${UNIT}${ROOM}`, "g") },
  { type: "微信号", re: /wxid_[a-z0-9]{6,}/g },
];

const digitsOf = (s) => s.replace(/\D/g, "");

// 打码口径与 README 一致：手机号前 3 后 2；客户编码留省份+字母；住址数字全遮；微信号留前 2 位
function mask(type, v) {
  if (type === "手机号") { const d = digitsOf(v); return `${d.slice(0, 3)}******${d.slice(-2)}`; }
  if (type === "客户编码") return v.slice(0, 2) + "*".repeat(v.length - 2);
  if (type === "住址") return v.replace(/\d/g, "*");
  if (type === "微信号") return v.slice(0, 7) + "****";
  return "****";
}

// 抽取文本：内联 base64 资源整段抹成空格（列号不变）；再单独做一份「去掉行内标签 +
// 解实体/\u 转义」的视图，接住 <b>139</b>12345678、&#49;39…、JSON 里 \u 转义的省份字这类拆开写的
const blankDataUri = (s) =>
  s.includes("base64,") ? s.replace(/data:[\w.+-]+\/[\w.+-]+;base64,[A-Za-z0-9+/=]+/g, (m) => " ".repeat(m.length)) : s;
const INLINE_TAG = /<\/?(?:span|b|strong|em|i|mark|code|u|s|small|sup|sub|a|font)\b[^>]*>/gi;
const decode = (s) =>
  s.replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
   .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
   .replace(/&nbsp;/g, " ")
   .replace(/\\u([0-9a-fA-F]{4})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));

// 白名单条目：value（或 values 一组共用理由）+ why；可选 files（路径前缀）限定页面、
// near（同一行必须含这段字）限定上下文——短串（「2号楼901」）不加 near 容易误放别处的真住址
function loadAllow() {
  const a = JSON.parse(fs.readFileSync(ALLOW_FILE, "utf8"));
  return (a.exact ?? []).flatMap((e) =>
    (e.values ?? [e.value]).map((v) => ({ ...e, key: /^[\d\s+-]+$/.test(v) ? digitsOf(v) : v })));
}

function isAllowed(type, value, line, start, end, file, allow) {
  if (type === "手机号" && digitsOf(value).slice(3, 7) === "0000") return true;
  if (/[*＊]/.test(line[start - 1] ?? "") || /[*＊]/.test(line[end] ?? "")) return true;
  const key = type === "手机号" ? digitsOf(value).slice(-11) : value;
  return allow.some((e) => e.key === key && (!e.files || e.files.some((f) => file.startsWith(f)))
    && (!e.near || line.includes(e.near)));
}

function scanLine(text, raw, lineNo, file, allow, seen, hits) {
  for (const { type, re } of RULES) {
    for (const m of text.matchAll(re)) {
      const v = m[0];
      const k = `${lineNo}\0${type}\0${v}`;
      if (seen.has(k)) continue;
      seen.add(k);
      if (isAllowed(type, v, text, m.index, m.index + v.length, file, allow)) continue;
      hits.push({ file, line: lineNo, col: raw ? m.index + 1 : null, type, sample: mask(type, v) });
    }
  }
}

function scanFile(file, display, allow, hits) {
  // 路径本身（图片等二进制文件也查）
  scanLine(display, false, 0, display, allow, new Set(), hits);
  if (!TEXT_EXT.test(file)) return false;
  const lines = fs.readFileSync(file, "utf8").split("\n");
  const seen = new Set();
  lines.forEach((l, i) => {
    const t = blankDataUri(l);
    scanLine(t, true, i + 1, display, allow, seen, hits);
    const v = decode(t.replace(INLINE_TAG, ""));
    if (v !== t) scanLine(v, false, i + 1, display, allow, seen, hits);
  });
  return true;
}

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.isFile()) out.push(p);
  }
  return out;
}

const git = (...a) =>
  execFileSync("git", ["-c", "core.quotePath=false", ...a], { encoding: "utf8", maxBuffer: 1 << 28 });
const list0 = (s) => s.split("\0").filter(Boolean);

// 不传 --base：和 origin/main 的分叉点比，工作区里未提交、未跟踪的也算（本地推之前跑）
function changedFiles(base) {
  let b = base;
  if (!b) { try { b = git("-C", ROOT, "merge-base", "origin/main", "HEAD").trim(); } catch { b = "origin/main"; } }
  const files = list0(git("-C", ROOT, "diff", "--name-only", "-z", "--diff-filter=d", b, "--", SCOPE));
  if (!base) files.push(...list0(git("-C", ROOT, "ls-files", "-z", "--others", "--exclude-standard", "--", SCOPE)));
  return { base: b, files: [...new Set(files)] };
}

function report(hits, scanned, allowN) {
  if (!hits.length) {
    console.log(`✅ 隐私闸：受检 ${scanned} 个文件，0 命中（白名单 ${allowN} 条）`);
    return 0;
  }
  console.log(`❌ 隐私闸：${hits.length} 处疑似终端客户个人信息（受检 ${scanned} 个文件，样本已打码）：\n`);
  for (const h of hits.slice(0, MAX_PRINT)) {
    const loc = h.line === 0 ? `${h.file}（文件路径）` : `${h.file}:${h.line}${h.col ? ":" + h.col : ""}`;
    console.log(`  ${loc}  ${h.type}  ${h.sample}`);
  }
  if (hits.length > MAX_PRINT) console.log(`  …另有 ${hits.length - MAX_PRINT} 处`);
  console.log(`
怎么处理（改原文，不要只加白名单）：
  - 手机号 → 保留前 3 后 2，如 139******78
  - 住址   → 写到小区为止，楼栋门牌去掉
  - 客户编码（闽D12345 这类）→ 闽X******
  - 客户姓名 → 改成角色（客户 / 业主 / 老板娘）
  - 商家对外公开的售后热线、明显的示例号 → 加进 tools/privacy-guard.allow.json 并写明理由
  注意：已经推上 main 的，改原文只能让站点不再显示，原文还留在 git 历史里。`);
  return 1;
}

const args = process.argv.slice(2);
const allow = loadAllow();
const hits = [];
let scanned = 0;

if (args.includes("--all")) {
  for (const f of walk(path.join(ROOT, SCOPE))) scanned += scanFile(f, path.relative(ROOT, f), allow, hits) ? 1 : 0;
} else {
  const bi = args.indexOf("--base");
  const explicit = args.filter((a, i) => !a.startsWith("--") && !(bi >= 0 && i === bi + 1));
  if (explicit.length) {
    for (const f of explicit) scanned += scanFile(f, f, allow, hits) ? 1 : 0;
  } else {
    const { base, files } = changedFiles(bi >= 0 ? args[bi + 1] : null);
    console.log(`对比基准 ${base.slice(0, 12)}，public/ 下改动 ${files.length} 个文件`);
    for (const f of files) scanned += scanFile(path.join(ROOT, f), f, allow, hits) ? 1 : 0;
  }
}
process.exit(report(hits, scanned, allow.length));
