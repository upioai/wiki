#!/usr/bin/env node
/**
 * 代码地图（public/akke/code-map/）的数据加密。upio.ai 谁都能打开、本仓也是公开仓，
 * 所以图谱数据只以密文进仓：把目录里的明文 JSON 加密成 <name>.enc 并删掉明文。
 * index.html 输对密码后在浏览器里解密，再交给 viewer。
 *
 *   CODE_MAP_PASSWORD=... node tools/code-map-encrypt.mjs public/akke/code-map
 *
 * 格式与 public/workflow/ 下加密码的页面一致：PBKDF2-SHA256 派生 AES-256-GCM 密钥。
 * 每次运行换一个 salt，四个文件共用这一个 salt（浏览器只派生一次密钥），每个文件各用一个 iv。
 * salt 和迭代次数写进 keyinfo.json，每个 .enc 里只放 iv 和密文。
 * keyinfo.json 还记一份明文的 sha256（src）：数据没变就不重新加密，免得每次同步都因为随机 salt 产生一次空提交。
 * 调用方：Akke 仓 .github/workflows/publish-code-map.yml（密码在 Akke 仓 secret CODE_MAP_PASSWORD）。
 */
import fs from 'node:fs';
import path from 'node:path';
import { webcrypto as crypto, createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

export const FILES = ['knowledge-graph.json', 'domain-graph.json', 'meta.json', 'config.json'];
export const ITERATIONS = 310000;
const b64 = (u8) => Buffer.from(u8).toString('base64');
const unb64 = (s) => new Uint8Array(Buffer.from(s, 'base64'));

async function deriveKey(password, salt, iterations, usage) {
  const k0 = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations, hash: 'SHA-256' }, k0, { name: 'AES-GCM', length: 256 }, false, [usage]);
}

export async function encryptDir(dir, password) {
  if (!password || password.length < 10) throw new Error('CODE_MAP_PASSWORD 没设或太短（至少 10 位）');
  const present = FILES.filter((f) => fs.existsSync(path.join(dir, f)));
  const hash = createHash('sha256');
  for (const f of present) hash.update(`${f}\n`).update(fs.readFileSync(path.join(dir, f)));
  const src = hash.digest('hex');
  const infoPath = path.join(dir, 'keyinfo.json');
  const prev = fs.existsSync(infoPath) ? JSON.parse(fs.readFileSync(infoPath, 'utf8')) : null;
  const sameKey = prev && prev.src === src && present.every((f) => fs.existsSync(path.join(dir, `${f}.enc`)))
    && await decryptFile(dir, present[0], password).then(() => true, () => false); // 换过密码就得重新加密
  if (sameKey) {
    // 数据和上次加密的一样、密码也没换：保留原有密文，删掉刚拷进来的明文。
    for (const f of present) fs.rmSync(path.join(dir, f));
    return { files: present, changed: false };
  }
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const key = await deriveKey(password, salt, ITERATIONS, 'encrypt');
  const done = [];
  for (const f of FILES) {
    const p = path.join(dir, f);
    if (!fs.existsSync(p)) continue;
    const plain = fs.readFileSync(p);
    JSON.parse(plain.toString('utf8'));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const c = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, plain));
    fs.writeFileSync(`${p}.enc`, JSON.stringify({ i: b64(iv), c: b64(c) }));
    fs.rmSync(p);
    done.push(f);
  }
  if (!done.includes('knowledge-graph.json')) throw new Error(`${dir} 下没有 knowledge-graph.json`);
  fs.writeFileSync(infoPath, JSON.stringify({ s: b64(salt), n: ITERATIONS, files: done, src }));
  return { files: done, changed: true };
}

export async function decryptFile(dir, file, password) {
  const { s, n } = JSON.parse(fs.readFileSync(path.join(dir, 'keyinfo.json'), 'utf8'));
  const { i, c } = JSON.parse(fs.readFileSync(path.join(dir, `${file}.enc`), 'utf8'));
  const key = await deriveKey(password, unb64(s), n, 'decrypt');
  return new TextDecoder().decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unb64(i) }, key, unb64(c)));
}

const isMain = process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const dir = process.argv[2];
  if (!dir) { console.error('用法：CODE_MAP_PASSWORD=... node tools/code-map-encrypt.mjs <code-map 目录>'); process.exit(2); }
  const password = process.env.CODE_MAP_PASSWORD;
  const { files: done, changed } = await encryptDir(dir, password);
  // 回读：用同一个密码解开，证明 index.html 那边能打开（密码换了而数据没变时，这里会报错）。
  for (const f of done) JSON.parse(await decryptFile(dir, f, password));
  const left = fs.readdirSync(dir).filter((f) => f.endsWith('.json') && f !== 'keyinfo.json');
  if (left.length) { console.error(`目录里还留着明文：${left.join(', ')}`); process.exit(1); }
  console.log(changed ? `已加密并回读：${done.join(', ')}` : `数据没变，沿用原密文（已回读）：${done.join(', ')}`);
}
