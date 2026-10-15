import { readFileSync } from "node:fs";
import { runInNewContext, Script } from "node:vm";
import { test } from "node:test";
import assert from "node:assert/strict";

const html = readFileSync(new URL("../public/akke/personal-wechat-pc-live.html", import.meta.url), "utf8");
const manualStart = html.indexOf("const MANUAL_RUNNING =");
const manualEnd = html.indexOf("const $ =", manualStart);
const start = html.indexOf("const NOT_WORKING =");
const end = html.indexOf("function renderMachines()", start);
assert.ok(manualStart >= 0 && manualEnd > manualStart && start >= 0 && end > start, "机器状态逻辑仍可从页面提取");
const { machineOverall, identityHeartbeatStatus, manualRunning } = runInNewContext(
  `${html.slice(manualStart, manualEnd)}\n${html.slice(start, end)}; ({ machineOverall, identityHeartbeatStatus, manualRunning })`,
);
const identity = (online, action = "idle", disabled = false) => ({ online, action, disabled });
const machine = (...identities) => ({ key: "unknown", identities });
const confirmedAt = Date.parse("2026-10-15T16:43:00+08:00");

test("页面内联脚本保持可解析", () => {
  const scriptStart = html.indexOf("<script>") + "<script>".length;
  const scriptEnd = html.indexOf("</script>", scriptStart);
  assert.ok(scriptStart > 0 && scriptEnd > scriptStart);
  assert.doesNotThrow(() => new Script(html.slice(scriptStart, scriptEnd)));
});

test("过期心跳不能直接推断电脑离线或停止运行", () => {
  assert.deepEqual([...machineOverall(machine(identity(false)), confirmedAt)], ["warning", "!", "心跳超时 · 运行待核实"]);
  assert.deepEqual([...identityHeartbeatStatus(identity(false))], ["warning", "!", "心跳超时"]);
});

test("部分身份心跳超时要保留异常，不覆盖正常身份", () => {
  assert.deepEqual([...machineOverall(machine(identity(true), identity(false)), confirmedAt)], ["warning", "!", "1 个身份心跳超时"]);
});

test("三台已人工确认运行的电脑短时区分运行与心跳，过期自动撤销确认", () => {
  for (const key of ["xiaoyu", "gaoyirui", "gaopeng"]) {
    const m = { key, identities: [identity(false)] };
    assert.equal(manualRunning(m, confirmedAt), true);
    assert.equal(machineOverall(m, confirmedAt)[2], "人工确认运行 · 心跳超时");
    assert.equal(manualRunning(m, Date.parse("2026-10-15T18:43:00+08:00")), false);
    assert.equal(machineOverall(m, Date.parse("2026-10-15T18:43:00+08:00"))[2], "心跳超时 · 运行待核实");
  }
});

test("无心跳记录、正常心跳、已停止和停用身份分别显示", () => {
  assert.equal(machineOverall(machine(identity(null)))[2], "没有心跳记录");
  assert.equal(machineOverall(machine(identity(true)))[2], "心跳正常 · 上次状态运行中");
  assert.equal(machineOverall(machine(identity(true, "stopped")))[2], "心跳正常，但未在干活");
  assert.equal(machineOverall(machine(identity(false, "idle", true)))[2], "身份已全部停用");
});

test("卡片文案将当前运行确认与过期的客户端上报明确区分", () => {
  assert.match(html, /人工确认仍在运行（10\/15 16:43）；后台心跳超时/);
  assert.match(html, /i\.online === true \? "在干什么" : "上次上报状态"/);
  assert.match(html, /不代表电脑关机或客户端停止运行/);
  assert.doesNotMatch(html.slice(start, html.indexOf("\/\/ ── 每日趋势", end)), /"离线"/);
});
