// node --test tools/code-map-encrypt.test.mjs   （口令全部现场随机生成，不含任何真实口令）
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomBytes, webcrypto as crypto, createHash } from 'node:crypto';
import { encryptDir, decryptFile, assertStrongPassword } from './code-map-encrypt.mjs';

const strong = () => randomBytes(24).toString('base64'); // 32 字符
const HTML = '<!doctype html><title>t</title><p>graph</p>';
const META = JSON.stringify({ nodes: 1, source: 'graphify' });
function tmpDir() {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-'));
  fs.writeFileSync(path.join(d, 'graph.html'), HTML);
  fs.writeFileSync(path.join(d, 'meta.json'), META);
  return d;
}
const info = (d) => JSON.parse(fs.readFileSync(path.join(d, 'keyinfo.json'), 'utf8'));

test('短口令和低熵口令被拒', () => {
  for (const bad of ['', 'a'.repeat(12), randomBytes(6).toString('hex'), 'a'.repeat(40), 'abcd'.repeat(10), 'abc123'.repeat(5)]) {
    assert.throws(() => assertStrongPassword(bad), /CODE_MAP_PASSWORD/);
  }
  assert.doesNotThrow(() => assertStrongPassword(strong()));
  assert.doesNotThrow(() => assertStrongPassword(randomBytes(16).toString('hex')));
});

test('encryptDir 拒绝 12 位口令，且不留下半成品', async () => {
  const d = tmpDir();
  await assert.rejects(encryptDir(d, randomBytes(9).toString('base64')), /CODE_MAP_PASSWORD/);
  assert.ok(fs.existsSync(path.join(d, 'graph.html')));
  assert.ok(!fs.existsSync(path.join(d, 'keyinfo.json')));
});

test('新密文迭代数 >= 600000，且能解开', async () => {
  const d = tmpDir(); const pw = strong();
  await encryptDir(d, pw);
  assert.ok(info(d).n >= 600000);
  assert.equal(await decryptFile(d, 'graph.html', pw), HTML);
});

test('旧密文（n=310000）仍能解；数据没变时也会升级迭代数', async () => {
  const d = tmpDir(); const pw = strong();
  // 手工按旧格式造一份 n=310000 的密文
  const salt = crypto.getRandomValues(new Uint8Array(16)); const n = 310000;
  const k0 = await crypto.subtle.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: n, hash: 'SHA-256' }, k0, { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
  const h = createHash('sha256');
  for (const f of ['graph.html', 'meta.json']) {
    const buf = fs.readFileSync(path.join(d, f)); h.update(`${f}\n`).update(buf);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const c = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, buf));
    fs.writeFileSync(path.join(d, `${f}.enc`), JSON.stringify({ i: Buffer.from(iv).toString('base64'), c: Buffer.from(c).toString('base64') }));
    fs.rmSync(path.join(d, f));
  }
  fs.writeFileSync(path.join(d, 'keyinfo.json'), JSON.stringify({ s: Buffer.from(salt).toString('base64'), n, files: ['graph.html', 'meta.json'], src: h.digest('hex') }));
  assert.equal(await decryptFile(d, 'graph.html', pw), HTML); // 旧密文照样解得开
  // 同一份数据再跑一次：应重新加密到新迭代数
  fs.writeFileSync(path.join(d, 'graph.html'), HTML); fs.writeFileSync(path.join(d, 'meta.json'), META);
  const r = await encryptDir(d, pw);
  assert.equal(r.changed, true);
  assert.ok(info(d).n >= 600000);
  assert.equal(await decryptFile(d, 'graph.html', pw), HTML);
});
