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

// ── 明细状态标签（正常 / 不全 / 缺失 / 人工接管）──
const statusStart = html.indexOf("const STATUS_META =");
const statusEnd = html.indexOf("function buildThreads(", statusStart);
const escLine = html.split("\n").find((line) => line.startsWith("const esc = "));
assert.ok(statusStart >= 0 && statusEnd > statusStart && escLine, "状态标签逻辑仍可从页面提取");
const { statusCounts, stgHtml } = runInNewContext(
  `${escLine}\n${html.slice(statusStart, statusEnd)}; ({ statusCounts, stgHtml })`,
);
const ev = (machine, kind) => ({ machine, status: kind ? { kind, reason: "" } : undefined });

test("状态计数：按设备筛选，没有状态字段的旧事件不计", () => {
  const events = [ev("gaoyirui", "ok"), ev("gaoyirui", "partial"), ev("xiaoyu", "ok"), ev("xiaoyu", "handoff"), ev("xiaoyu", null)];
  const gaps = [ev("xiaoyu", "missing")];
  assert.deepEqual({ ...statusCounts(events, gaps, "").counts }, { ok: 2, partial: 1, missing: 1, handoff: 1 });
  assert.deepEqual({ ...statusCounts(events, gaps, "xiaoyu").counts }, { ok: 1, partial: 0, missing: 1, handoff: 1 });
});

test("旧接口（事件没有 status、没有 statusGaps）不报错，any=false", () => {
  const r = statusCounts([{ machine: "xiaoyu" }, { machine: "gaoyirui" }], undefined, "");
  assert.equal(r.any, false);
  assert.deepEqual({ ...r.counts }, { ok: 0, partial: 0, missing: 0, handoff: 0 });
  assert.equal(stgHtml(undefined), "");
});

test("标签带图标 + 文字，原因转义，未知 kind 不渲染", () => {
  const html1 = stgHtml({ kind: "partial", reason: "文字已发，图只发出 0/2 张<script>" }, true);
  assert.match(html1, /class="stg partial"/);
  assert.match(html1, /◐<\/i>不全/);
  assert.ok(!html1.includes("<script>"));
  assert.equal(stgHtml({ kind: "weird", reason: "x" }), "");
});
