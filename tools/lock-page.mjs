#!/usr/bin/env node
/**
 * lock-page —— 给单个 upio 页面加密码（本仓公开，加密后 HTML 源码里只剩密文）
 *
 * 做法同 public/workflow/case-accounts-2026-10-15-25f18077.html：
 * PBKDF2-SHA256 310000 次 → AES-GCM-256，密文嵌进外壳页，浏览器输对密码才解密 document.write。
 *
 * 用法：
 *   node tools/lock-page.mjs <明文.html> <输出.html> --pw-file <密码文件>
 *   node tools/lock-page.mjs --unlock <已加锁.html> --pw-file <密码文件>   # 解出明文到 stdout，改完再加锁
 *
 * 密码文件和明文都放仓外（本机），不进仓库。发布顺序：
 *   明文 → privacy-guard --check 明文 → 加锁 → commit/push → 线上回读（对/错密码各解一次）
 * 加锁不覆盖：页面引用的图片直链、加锁前推过的明文在 git 历史里仍可见。
 */
import { readFileSync, writeFileSync } from "node:fs";
import { basename } from "node:path";
import { webcrypto as C } from "node:crypto";

const args = process.argv.slice(2);
const pwAt = args.indexOf("--pw-file");
if (pwAt < 0 || !args[pwAt + 1]) { console.error("缺 --pw-file <密码文件>"); process.exit(2); }
const pw = readFileSync(args[pwAt + 1], "utf8").trim();
if (pw.length < 10) { console.error("密码至少 10 位：密文公开，短密码可离线爆破"); process.exit(2); }
const rest = args.filter((_, i) => i !== pwAt && i !== pwAt + 1);
const b64 = (u) => Buffer.from(u).toString("base64");
const u8 = (s) => Uint8Array.from(Buffer.from(s, "base64"));

async function key(salt, n) {
  const k0 = await C.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
  return C.subtle.deriveKey({ name: "PBKDF2", salt, iterations: n, hash: "SHA-256" }, k0, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

async function unlock(file) {
  const m = readFileSync(file, "utf8").match(/const D=(\{.*?\});/);
  if (!m) throw new Error(`${file} 不是 lock-page 加锁的页面`);
  const D = JSON.parse(m[1]);
  const k = await key(u8(D.s), D.n);
  return new TextDecoder().decode(await C.subtle.decrypt({ name: "AES-GCM", iv: u8(D.i) }, k, u8(D.c)));
}

if (rest[0] === "--unlock") {
  process.stdout.write(await unlock(rest[1]));
  process.exit(0);
}

const [src, dst] = rest;
if (!src || !dst) { console.error("用法见文件头注释"); process.exit(2); }
const html = readFileSync(src, "utf8");
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
// 外壳只带标题和日期（项目地图按 <title> 列卡片、按 upio:date 排序），description 等正文信息不外露
const title = (html.match(/<title>([^<]*)<\/title>/) || [, "资料"])[1].trim();
const date = (html.match(/<meta name="upio:date" content="([^"]*)"/) || [, ""])[1];
const s = C.getRandomValues(new Uint8Array(16)), i = C.getRandomValues(new Uint8Array(12)), n = 310000;
const k = await key(s, n);
const c = new Uint8Array(await C.subtle.encrypt({ name: "AES-GCM", iv: i }, k, new TextEncoder().encode(html)));
const D = JSON.stringify({ s: b64(s), i: b64(i), n, c: b64(c) });
const store = "__lock_" + basename(dst).replace(/\.html$/, "");
const shell = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="robots" content="noindex,nofollow,noarchive">${date ? `<meta name="upio:date" content="${esc(date)}">` : ""}
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title>
<style>
:root{--bg:#ECEFEE;--surface:#fff;--ink:#16201E;--ink-2:#47524F;--line:rgba(22,32,30,.18);--accent:#0E6656;--err:#B53A2A}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111816;--surface:#18211F;--ink:#E4ECE9;--ink-2:#A9B7B3;--line:rgba(228,236,233,.2);--accent:#4CC2A6;--err:#EE7C69}}
:root[data-theme="dark"]{--bg:#111816;--surface:#18211F;--ink:#E4ECE9;--ink-2:#A9B7B3;--line:rgba(228,236,233,.2);--accent:#4CC2A6;--err:#EE7C69}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;padding:16px}
form{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:28px 24px;width:min(100%,380px);display:grid;gap:12px}
h1{font-size:20px;margin:0}p{margin:0;color:var(--ink-2);font-size:14px;line-height:1.6}
input{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--ink)}
button{font:inherit;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
#e{color:var(--err);min-height:1.2em}
</style></head><body>
<form id="f"><h1>${title}</h1><p>此页面需要密码才能查看。</p>
<input id="p" type="password" autocomplete="current-password" placeholder="密码" aria-label="密码" required>
<button type="submit">打开</button><p id="e" role="alert"></p></form>
<script>
const D=${D};
const u=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function open_(pw){
  const k0=await crypto.subtle.importKey("raw",new TextEncoder().encode(pw),"PBKDF2",false,["deriveKey"]);
  const k=await crypto.subtle.deriveKey({name:"PBKDF2",salt:u(D.s),iterations:D.n,hash:"SHA-256"},k0,{name:"AES-GCM",length:256},false,["decrypt"]);
  const pt=await crypto.subtle.decrypt({name:"AES-GCM",iv:u(D.i)},k,u(D.c));
  return new TextDecoder().decode(pt);
}
async function go(pw,remember){
  try{const h=await open_(pw);try{if(remember)sessionStorage.setItem(${JSON.stringify(store)},pw)}catch(e){}
    document.open();document.write(h);document.close();}
  catch(e){document.getElementById("e").textContent="密码不对，再试一次。";}
}
document.getElementById("f").addEventListener("submit",ev=>{ev.preventDefault();go(document.getElementById("p").value,true);});
try{const s=sessionStorage.getItem(${JSON.stringify(store)});if(s)go(s,false);}catch(e){}
</script></body></html>
`;
writeFileSync(dst, shell);
// 自检：从写出的外壳里解回来要和原文逐字节一致
if ((await unlock(dst)) !== html) throw new Error("自检失败：解密结果与原文不一致");
console.log("locked", src, "->", dst, "plain", html.length, "shell", shell.length);
