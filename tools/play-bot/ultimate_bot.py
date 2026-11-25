#!/usr/bin/env python3
"""飞盘 Ultimate 4v4 对战版 —— 自动跑局验收脚本（Playwright）。

用法：  python3 -I ultimate_bot.py <html路径> [参数]
常用：  --matches 100  --workers 6  --out DIR  --only matches,drills,flow,smoke
详见 --help。退出码：0 全部达标；1 有项目不达标；2 脚本自身/环境出错。

设计要点（为什么这么写）
- 批量对局用「虚拟帧」驱动：init 脚本接管 requestAnimationFrame 和 Math.random，
  每帧推进固定 50ms*speed 的游戏时间，之后立刻让页内机器人决策。好处：不依赖真实刷新率、
  100 场几分钟跑完、同一个种子完全可复现（卡死场次可原样重放）。
- 真实输入冒烟（键盘 / 触摸）走真实时间 + 真实事件（page.keyboard / CDP Input.dispatchTouchEvent），
  不走 __game.inject。
- 所有「0 次 / 没有」结论都带阳性对照（见 selftest()），避免取错字段而全绿。
"""
import argparse, json, math, os, random, statistics, subprocess, sys, time, traceback
from pathlib import Path

# ----------------------------------------------------------------------------------------
# 页内脚本 1：init（在游戏脚本之前执行）——接管 rAF / Math.random
# ----------------------------------------------------------------------------------------
INIT_JS = r"""
(() => {
  const SEED = __SEED__;
  let a = SEED >>> 0;
  Math.random = function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  window.__jsErrors = [];
  window.addEventListener('error', (e) => window.__jsErrors.push('window.onerror: ' + e.message));
  window.addEventListener('unhandledrejection', (e) => window.__jsErrors.push('unhandledrejection: ' + (e.reason && e.reason.message || e.reason)));
  if (__VIRTUAL__) {
    const nativeRaf = window.requestAnimationFrame.bind(window);
    let cb = null, vnow = null;
    window.requestAnimationFrame = (f) => { cb = f; return 1; };
    window.cancelAnimationFrame = () => {};
    window.__vstep = (ms) => {
      if (vnow === null) vnow = performance.now();
      vnow += ms;
      const f = cb; cb = null;
      if (f) f(vnow);
      return !!f;
    };
    // 截图前调用：恢复真实 rAF，让 Playwright 截图/等待不被卡住
    window.__vrestore = () => { window.requestAnimationFrame = nativeRaf; const f = cb; cb = null; if (f) nativeRaf(f); };
  }
})();
"""

# ----------------------------------------------------------------------------------------
# 页内脚本 2：机器人 + 驱动。页面加载后 evaluate 注入（需要 window.__game 已存在）
# ----------------------------------------------------------------------------------------
BOT_JS = r"""
(() => {
  const g = window.__game, G = g.G, IN = g.IN, SET = g.SET;
  const FL = 40, FWD = 20, EZ = 5, BLOCK_R = 0.7, DEF_V = 6.6;
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const wrap = (a) => { while (a > Math.PI) a -= 2 * Math.PI; while (a < -Math.PI) a += 2 * Math.PI; return a; };
  const other = (t) => t === 'B' ? 'R' : 'B';
  const team = (t) => G.teams[t];
  const dirOf = (t) => G.side[t];
  const guardOf = (p) => G.teams[other(p.team)].find((q) => q.n === p.n);
  const markSide = (h) => h.y < FWD / 2 ? -1 : 1;
  const discSpeed = (d) => 12 + d * 0.18;
  const flightT = (d, k) => k === 'high' ? d / (discSpeed(d) * 0.62) + 0.25 : d / discSpeed(d);
  const PROG = { throw: 1, catch: 1, turnover: 1, score: 1, pull: 1, pullDone: 1, end: 1, lineup: 1, go: 1 };
  const KEEP = { throw: 1, catch: 1, turnover: 1, score: 1, pull: 1, end: 1, ctl: 1, lineup: 1, switch: 1, pullDone: 1 };

  const cfg = { risk: -0.25, minHold: 1.0, forceStall: 6.5, forceHold: 7.0, switchProb: 0.08, stuckSec: 45, manualSwitch: true, camp: false };
  const st = {};
  function reset() {
    Object.assign(st, {
      frame: 0, seen: null, counts: {}, log: [], lastProg: G.time, prevTime: G.time, lastProgType: null,
      aim: null, holdKey: '', holdStart: G.time, sprint: false, lastSwitchT: -9, swKey: '',
      sw: { calls: 0, effective: 0, pending: null }, passCalls: 0, passRejects: 0, jumpCalls: 0, jumpOk: 0,
      stuck: null, plans: 0, forced: 0, noPlan: 0,
    });
  }
  reset();

  // ---- 传盘评估（复刻红方 AI 的 margin 写法：值越大＝防守越晚到＝越安全） ----
  function leadPoint(from, r, kind) {
    let P = { x: r.x, y: r.y };
    const cut = r.state === 'cut', vx = cut ? r.vx : r.vx * 0.5, vy = cut ? r.vy : r.vy * 0.5;
    for (let k = 0; k < 4; k++) {
      const T = flightT(dist(from, P), kind);
      P = { x: clamp(r.x + vx * T, 0.6, FL - 0.6), y: clamp(r.y + vy * T, 0.6, FWD - 0.6) };
    }
    return P;
  }
  function markBlocks(h, mk, P, kind) {   // 自动手：绕开手 ×0.6，高盘 ×0.55
    if (!mk || dist(h, mk) > 2) return false;
    const a1 = Math.atan2(P.y - h.y, P.x - h.x), a2 = Math.atan2(mk.y - h.y, mk.x - h.x);
    let c = 0.95; if (kind === 'high') c *= 0.55; c *= 0.6;
    return Math.abs(wrap(a1 - a2)) < c;
  }
  function aimRecv(h, ang) {               // 复刻 aimTarget 的选人规则
    let best = null, bs = 9;
    for (const p of team(h.team)) {
      if (p === h || dist(h, p) < 2) continue;
      const da = Math.abs(wrap(Math.atan2(p.y - h.y, p.x - h.x) - ang));
      if (da < 0.62) { const s = da + dist(h, p) * 0.004; if (s < bs) { bs = s; best = p; } }
    }
    return best;
  }
  function evalThrow(h, r, kind, loose) {
    const mk = guardOf(h), P = leadPoint(h, r, kind), L = dist(h, P);
    if (!loose && (L < 3 || (kind === 'high' && L < 7))) return null;
    if (!loose && markBlocks(h, mk, P, kind)) return null;
    const T = flightT(L, kind); let margin = Infinity;
    for (const b of team('R')) {
      if (b === mk) continue;
      for (let k = 1; k <= 10; k++) {
        const u = k / 10; if (kind === 'high' && u < 0.62) continue;
        const Q = { x: h.x + (P.x - h.x) * u, y: h.y + (P.y - h.y) * u };
        margin = Math.min(margin, (dist(b, Q) - BLOCK_R) / DEF_V - u * T);
      }
    }
    const d = dirOf('B'), gain = (P.x - h.x) * d, zone = d > 0 ? P.x > FL - EZ : P.x < EZ;
    const score = margin * 2 + gain * 0.06 + (zone ? 0.8 : 0) - (kind === 'high' ? 0.3 : 0);
    return { who: r.name, n: r.n, kind, L: +L.toFixed(1), margin: +margin.toFixed(2), score, ang: Math.atan2(r.y - h.y, r.x - h.x), blocked: markBlocks(h, mk, P, kind) };
  }
  // 最佳传盘方案；angles 给定时只评估这些方向（键盘 8 向用）；loose=true 时放宽（强制出手）
  function plan(loose, angles) {
    const h = G.holder; if (!h || h.team !== 'B') return null;
    let best = null;
    const tryAng = (ang, r) => {
      for (const kind of ['low', 'high']) {
        const c = evalThrow(h, r, kind, loose); if (!c) continue;
        if (angles) c.ang = ang;
        if (!best || c.score > best.score) best = c;
      }
    };
    if (angles) { for (const a of angles) { const r = aimRecv(h, a); if (r) tryAng(a, r); } }
    else for (const r of team('B')) { if (r === h) continue; const a = Math.atan2(r.y - h.y, r.x - h.x); if (aimRecv(h, a) === r) tryAng(a, r); }
    return best;
  }

  const stick = (x, y) => g.inject.stick(x, y);
  function setSprint(v) {
    if (v === st.sprint) return; st.sprint = v;
    try { v ? g.inject.press('sprint') : g.inject.release('sprint'); } catch (e) {}
  }
  function goto_(me, t, sprintFar) {
    const dx = t.x - me.x, dy = t.y - me.y, d = Math.hypot(dx, dy);
    if (d < 0.25) { stick(0, 0); setSprint(false); return d; }
    const mag = clamp(d / 0.8, 0, 1);
    stick(dx / d * mag, dy / d * mag);
    setSprint(d > sprintFar);
    return d;
  }
  function tryJump(me) {
    st.jumpCalls++;
    let ok = false; try { ok = g.inject.jump(); } catch (e) {}
    if (ok) st.jumpOk++;
    return ok;
  }

  // ---- 事件采集（G.events 最多 4000 条会滚动，这里增量取走，计数不会丢） ----
  function harvest() {
    const ev = G.events; if (!ev.length) return;
    let i = st.seen ? ev.lastIndexOf(st.seen) : -1;
    const fresh = i >= 0 ? ev.slice(i + 1) : ev.slice();
    if (!fresh.length) return;
    st.seen = ev[ev.length - 1];
    for (const e of fresh) {
      st.counts[e.type] = (st.counts[e.type] || 0) + 1;
      if (e.type === 'ctl' && e.kind) st.counts['ctl:' + e.kind] = (st.counts['ctl:' + e.kind] || 0) + 1;
      if (KEEP[e.type]) st.log.push(e);
      if (PROG[e.type]) { st.lastProg = G.time; st.lastProgType = e.type; }
    }
  }

  function tick() {
    st.frame++;
    if (G.time < st.prevTime - 0.5) { st.seen = null; st.lastProg = G.time; }  // 新开了一局（G.time 归零）
    st.prevTime = G.time;
    harvest();
    if (G.mode !== 'menu' && !G.paused && G.time - st.lastProg >= cfg.stuckSec && !st.stuck) {
      st.stuck = { at: +G.time.toFixed(1), since: +st.lastProg.toFixed(1), lastType: st.lastProgType };
    }
    // 上一帧的手动切人结果
    if (st.sw.pending) { if (G.ctl !== st.sw.pending) st.sw.effective++; st.sw.pending = null; }
    const live = G.mode !== 'menu' && !G.paused && G.cd <= 0 && G.pause <= 0 && !G.pull && G.phase === 'play';
    if (!live) { stick(0, 0); setSprint(false); st.aim = null; return; }
    const key = `${G.possN}|${G.holder ? G.holder.n : 0}|${G.passN}`;
    if (key !== st.holdKey) { st.holdKey = key; st.holdStart = G.time; }
    const disc = G.disc, me = G.ctl;

    if (G.off === 'B') {
      if (disc.state === 'held' && G.holder && G.holder.team === 'B') {
        const h = G.holder; setSprint(false);
        if (me !== h) { stick(0, 0); return; }          // 手动切走了：等自动归位
        if (st.aim) {                                    // 第二拍：摇杆已生效，出手
          const a = st.aim; st.aim = null;
          if (G.holder === h) { st.passCalls++; const ok = g.inject.pass(a.kind); if (!ok) st.passRejects++; }
          stick(0, 0); return;
        }
        const held = G.time - st.holdStart;
        const force = G.stall >= cfg.forceStall || held >= cfg.forceHold;
        let p = null;
        if (held >= cfg.minHold || force) {
          p = plan(false);
          if (p && !(p.margin > cfg.risk || force)) p = null;
          if (!p && force) { p = plan(true); if (p) st.forced++; else st.noPlan++; }
        }
        if (p) { st.plans++; stick(Math.cos(p.ang), Math.sin(p.ang)); st.aim = { kind: p.kind }; }
        else stick(0, 0);
      } else if (disc.state === 'air' && me) {
        const u = disc.t / disc.T, to = disc.to || disc;
        const d = goto_(me, to, 2.5);
        if (u > 0.7 && dist(me, disc) < 2.4 && (me.jumpCd || 0) <= 0 && me.jumpT <= 0) tryJump(me);
      } else { stick(0, 0); setSprint(false); }       // 捡盘：自动跑
    } else {
      st.aim = null;
      if (!me) { stick(0, 0); return; }
      const dR = dirOf('R');
      if (disc.state === 'held' && G.holder) {
        const h = G.holder, mk = guardOf(h);
        if (cfg.manualSwitch && key !== st.swKey) {     // 每次换持盘人最多尝试一次手动切人
          st.swKey = key;
          if (Math.random() < cfg.switchProb && G.time - st.lastSwitchT > 0.6 && G.stall < 6) {
            const before = G.ctl; st.sw.calls++; st.lastSwitchT = G.time; g.inject.switchCtl(); st.sw.pending = before;
          }
        }
        let tgt;
        if (me === mk) tgt = { x: h.x + dR * 0.35, y: h.y + markSide(h) * 1.1 };
        else { const man = team('R').find((q) => q.n === me.n) || h; tgt = { x: man.x + dR * 0.9, y: man.y }; }
        goto_(me, tgt, 3.5);
      } else if (disc.state === 'air' && disc.from && disc.to) {
        const u0 = disc.t / disc.T; let tgt = null, bestD = 1e9, near = null;
        for (let k = 1; k <= 12; k++) {
          const u = clamp(u0 + (k / 12) * (1 - u0), 0, 1);
          if (disc.kind === 'high' && u < 0.62) continue;
          const Q = { x: disc.from.x + (disc.to.x - disc.from.x) * u, y: disc.from.y + (disc.to.y - disc.from.y) * u };
          const dq = dist(me, Q);
          if (!tgt && dq / 7.2 <= u * disc.T - disc.t) tgt = Q;
          if (dq < bestD) { bestD = dq; near = Q; }
        }
        goto_(me, tgt || near || disc.to, 1.5);
        const uu = disc.t / disc.T;
        const win = disc.kind === 'high' ? (uu > 0.5 && uu < 0.97) : (uu > 0.1 && uu < 0.85);
        if (win && dist(me, disc) < 2.4 && (me.jumpCd || 0) <= 0 && me.jumpT <= 0) tryJump(me);
      } else {
        // 红方持有的散盘：默认在 2.5 米外等（不蹲在盘上挡着对方捡盘）；camp=true 时故意走到盘上站着（专项探针用）
        if (cfg.camp || dist(me, disc) > 2.5) goto_(me, disc, 3); else { stick(0, 0); setSprint(false); }
      }
    }
  }

  // ---- 驱动（虚拟帧）----
  const FRAME_MS = 50;
  function errs() { return window.__jsErrors.slice(); }
  function step() {
    try { if (!window.__vstep(FRAME_MS)) throw new Error('virtual rAF has no pending frame (rAF restored by screenshot, or game loop died)'); tick(); return null; }
    catch (e) { const m = 'frame exception: ' + (e && e.stack || e); window.__jsErrors.push(m); return m; }
  }
  function runMatch(maxGameSec) {
    const t0 = G.time; let n = 0;
    for (;;) {
      const e = step(); n++;
      if (e) return { status: 'exception', frames: n };
      if (G.phase === 'over') return { status: 'over', frames: n };
      if (st.stuck) return { status: 'stuck', frames: n };
      if (G.time - t0 > maxGameSec) return { status: 'overtime', frames: n };
      if (G.mode === 'menu') return { status: 'menu', frames: n };
    }
  }
  // 教学关：跑到 #msg 弹窗出现（过关）或超时。返回用时与尝试次数
  function runDrill(limitSec) {
    const t0 = G.time, i0 = G.drill, c0 = (st.counts.turnover || 0); let n = 0;
    const msg = document.getElementById('msg');
    for (;;) {
      const e = step(); n++;
      if (e) return { status: 'exception', drill: i0, frames: n };
      if (!msg.hidden) return { status: 'pass', drill: i0, t: +(G.time - t0).toFixed(1), attempts: 1 + (st.counts.turnover || 0) - c0, title: document.getElementById('msg-title').textContent };
      if (G.time - t0 > limitSec) return { status: 'timeout', drill: i0, t: +(G.time - t0).toFixed(1), attempts: 1 + (st.counts.turnover || 0) - c0, snap: g.snapshot() };
    }
  }
  // 再跑 sec 秒游戏时间（用于「再来一局」后确认能继续打）
  function runFor(sec, until) {
    const t0 = G.time; let n = 0;
    for (;;) {
      const e = step(); n++;
      if (e) return { status: 'exception' };
      if (until && until()) return { status: 'until', t: +(G.time - t0).toFixed(1) };
      if (G.phase === 'over' || G.mode === 'menu') return { status: 'ended', t: +(G.time - t0).toFixed(1) };
      if (G.time - t0 > sec) return { status: 'time', t: +(G.time - t0).toFixed(1) };
      if (st.stuck) return { status: 'stuck' };
    }
  }
  function dump() { harvest(); return { counts: st.counts, log: st.log, sw: { calls: st.sw.calls, effective: st.sw.effective }, passCalls: st.passCalls, passRejects: st.passRejects, jumpCalls: st.jumpCalls, jumpOk: st.jumpOk, forced: st.forced, noPlan: st.noPlan, plans: st.plans, stuck: st.stuck, lastProg: st.lastProg }; }

  window.__bot = { cfg, st, reset, tick, plan, aimRecv, evalThrow, step, runMatch, runDrill, runFor, dump, errs, harvest, FRAME_MS };
  window.__runUntil = (fn) => runFor(3000, fn);
})();
"""

# ----------------------------------------------------------------------------------------
# Python 端
# ----------------------------------------------------------------------------------------
def now(): return time.time()

def log(*a):
    print(*a, file=sys.stderr, flush=True)

def file_url(p): return Path(p).resolve().as_uri()

# 控制台里「音乐文件不存在」这类与规则无关的环境噪声：单独计数，不算 JS 错误
ENV_NOISE = ('music/', 'ERR_FILE_NOT_FOUND', 'Failed to load resource')

class PageWatch:
    """收集 pageerror / console.error，区分环境噪声。"""
    def __init__(self, page):
        self.errors, self.console_errors, self.noise = [], [], []
        page.on('pageerror', lambda e: self.errors.append(str(e)[:400]))
        def on_console(m):
            if m.type == 'error':
                t = m.text[:400]
                (self.noise if any(k in t or k in (m.location.get('url', '') if m.location else '') for k in ENV_NOISE) else self.console_errors).append(t)
        page.on('console', on_console)
    def all_errors(self):
        return self.errors + self.console_errors

def new_page(browser, html, seed, virtual=True, viewport=None, touch=False, scale=1):
    kw = dict(viewport=viewport or {'width': 1280, 'height': 720}, device_scale_factor=scale)
    if touch: kw.update(has_touch=True, is_mobile=True)
    ctx = browser.new_context(**kw)
    page = ctx.new_page()
    watch = PageWatch(page)
    page.add_init_script(INIT_JS.replace('__SEED__', str(int(seed) & 0xFFFFFFFF)).replace('__VIRTUAL__', 'true' if virtual else 'false'))
    page.goto(file_url(html))
    page.wait_for_function('window.__game && window.__game.G')
    page.evaluate(BOT_JS)
    return ctx, page, watch

def shot(page, path):
    try:
        page.evaluate('window.__vrestore && window.__vrestore()')
        page.screenshot(path=str(path), timeout=8000)
        return str(path)
    except Exception as e:
        return f'screenshot failed: {e}'

# ---------------------------- 批量对局 ----------------------------------------------------
def match_config(i, args):
    rnd = random.Random(args.seed * 100003 + i)
    goal = 7 if rnd.random() < args.goal7_frac else 5
    diff = i % 3
    form = 'horz' if rnd.random() < args.horz_frac else 'vert'
    ids = ['anning', 'tudou', 'siqi', 'yeqiao', 'xiaochuan', 'anni', 'ajie', 'dahai']
    rnd.shuffle(ids)
    return dict(i=i, seed=args.seed * 100003 + i, goal=goal, diff=diff, form=form, lineup=ids[:4])

def run_match(browser, html, c, args, outdir):
    t0 = now()
    ctx, page, watch = new_page(browser, html, c['seed'])
    rec = dict(c, lineup=list(c['lineup']))
    try:
        page.evaluate("""(c) => { const g = window.__game; g.SET.goal = c.goal; g.SET.diff = c.diff; g.SET.formation = c.form;
            g.G.lineup = c.lineup.slice(); g.setSpeed(%s); g.newGame('match'); __bot.cfg.stuckSec = %s; __bot.reset(); }""" % (args.speed, args.stuck_sec), c)
        res = page.evaluate('(m) => __bot.runMatch(m)', args.max_game_sec)
        rec['status'] = res['status']; rec['frames'] = res['frames']
        d = page.evaluate('__bot.dump()')
        snap = page.evaluate('__game.snapshot()')
        rec['game_time'] = snap['time']; rec['play_time'] = snap['playT']; rec['score'] = snap['score']
        rec['bot'] = {k: d[k] for k in ('sw', 'passCalls', 'passRejects', 'jumpCalls', 'jumpOk', 'forced', 'noPlan', 'plans')}
        rec['event_counts'] = d['counts']; rec['events_total'] = sum(v for k, v in d['counts'].items() if ':' not in k)
        rec['events_in_buffer'] = page.evaluate('__game.events.length')
        # 传盘统计：来源一＝游戏自己的 G.ts / matches 汇总；来源二＝事件流。两者必须对得上
        m = page.evaluate('(__game.matches.length ? __game.matches[__game.matches.length-1] : null)')
        rec['summary'] = m
        lg = d['log']
        T = {t: dict(att=0, comp=0, why={'block': 0, 'mblock': 0, 'drop': 0, 'oob': 0, 'stall': 0}) for t in 'BR'}
        for e in lg:
            if e['type'] == 'throw': T[e['team']]['att'] += 1
            elif e['type'] == 'catch' and e.get('from'): T[e['team']]['comp'] += 1
            elif e['type'] == 'turnover' and e['why'] in T[e['team']]['why']: T[e['team']]['why'][e['why']] += 1
        rec['ev_stats'] = T
        if m: rec['ts_stats'] = {t: dict(att=m['ts'][t]['att'], comp=m['ts'][t]['comp']) for t in 'BR'}
        rec['ctl_events'] = {k: v for k, v in d['counts'].items() if k.startswith('ctl')}
        rec['pulls'] = d['counts'].get('pull', 0); rec['scores'] = d['counts'].get('score', 0)
        rec['ui_result_visible'] = page.evaluate("!document.getElementById('result').hidden")
        # telemetry（有就读回，没有跳过）
        tel = page.evaluate("""() => { const t = window.__game.telemetry; if (!t) return null;
            try { const s = typeof t.summary === 'function' ? t.summary() : null; return { has: true, summary: JSON.stringify(s === undefined ? null : s).slice(0, 1500) }; }
            catch (e) { return { has: true, error: String(e) }; } }""")
        rec['telemetry'] = tel
        if res['status'] == 'stuck':
            rec['stuck'] = d['stuck']; rec['stuck_snapshot'] = snap
            rec['stuck_recent_events'] = lg[-12:]
            rec['stuck_shot'] = shot(page, outdir / f"stuck-match{c['i']:03d}-seed{c['seed']}.png")
            (outdir / f"stuck-match{c['i']:03d}-seed{c['seed']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        elif res['status'] in ('overtime', 'exception'):
            rec['snapshot'] = snap
            rec['shot'] = shot(page, outdir / f"{res['status']}-match{c['i']:03d}-seed{c['seed']}.png")
    except Exception as e:
        rec['status'] = 'harness-error'; rec['error'] = ''.join(traceback.format_exception_only(type(e), e))[:600]
    rec['js_errors'] = list(watch.all_errors()) + [x for x in (page.evaluate('window.__jsErrors') if not page.is_closed() else [])]
    rec['env_noise'] = len(watch.noise)
    rec['wall_s'] = round(now() - t0, 1)
    ctx.close()
    return rec

def worker_main(args):
    from playwright.sync_api import sync_playwright
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    idx = [i for i in range(args.matches) if i % args.nworkers == args.worker]
    fh = open(outdir / f'matches.part{args.worker}.jsonl', 'w')
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--mute-audio'])
        for i in idx:
            rec = run_match(browser, args.html, match_config(i, args), args, outdir)
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n'); fh.flush()
            log(f"[w{args.worker}] match {i:03d} goal{rec['goal']} d{rec['diff']} {rec['status']} t={rec.get('game_time')} score={rec.get('score')} wall={rec['wall_s']}s")
        browser.close()
    fh.close()

# ---------------------------- 教学 6 关 ----------------------------------------------------
def run_drills(browser, html, args, outdir):
    out = {'trials': [], 'per_drill': {}, 'js_errors': []}
    n_drill = 6
    for tr in range(args.drill_trials):
        seed = 777000 + tr
        ctx, page, watch = new_page(browser, html, seed)
        trial = {'seed': seed, 'drills': []}
        try:
            page.evaluate("() => { const g = __game; g.SET.diff = 1; g.setSpeed(%s); g.newGame('tutorial'); __bot.cfg.manualSwitch = false; __bot.cfg.stuckSec = 1e9; __bot.reset(); }" % args.speed)
            for d in range(n_drill):
                cur = page.evaluate('__game.G.drill')
                if cur != d:   # 前一关超时，强制跳到本关继续测后面的关
                    page.evaluate("(d) => { __game.G.drill = d; __game.G.pause = 0; document.getElementById('msg').hidden = true; __game.startDrill(); }", d)
                r = page.evaluate('(s) => __bot.runDrill(s)', args.drill_limit)
                r['drill_no'] = d + 1
                if r['status'] == 'timeout':
                    r['shot'] = shot(page, outdir / f'drill{d+1}-timeout-seed{seed}.png')
                if r['status'] == 'pass':
                    page.evaluate("document.getElementById('msg-ok').click()")
                    if d == n_drill - 1:
                        r['chain_end_select_visible'] = page.evaluate("!document.getElementById('select').hidden")
                trial['drills'].append(r)
                if r['status'] == 'exception': break
        except Exception as e:
            trial['error'] = ''.join(traceback.format_exception_only(type(e), e))[:500]
        out['js_errors'] += watch.all_errors() + page.evaluate('window.__jsErrors')
        ctx.close()
        out['trials'].append(trial)
    for d in range(1, n_drill + 1):
        rs = [x for t in out['trials'] for x in t['drills'] if x.get('drill_no') == d]
        ok = [x for x in rs if x['status'] == 'pass']
        out['per_drill'][d] = dict(
            trials=len(rs), passed=len(ok),
            time_median=round(statistics.median([x['t'] for x in ok]), 1) if ok else None,
            time_max=max([x['t'] for x in ok]) if ok else None,
            attempts_mean=round(statistics.mean([x['attempts'] for x in ok]), 2) if ok else None,
            attempts_max=max([x['attempts'] for x in ok]) if ok else None,
            timeouts=len(rs) - len(ok))
    return out

# ---------------------------- 终局路径 ------------------------------------------------------
def run_flow(browser, html, args, outdir):
    """得分 → 结算弹窗 →「再来一局」→ 比分归零继续打；另测换边、再选阵容、回主菜单。"""
    R = {'checks': {}, 'detail': {}}
    ctx, page, watch = new_page(browser, html, 4242)
    ck = R['checks']
    try:
        # 走真实 UI 开局：主菜单 → 快速比赛 → 随机阵容 → 开始比赛（DOM click，触发真实事件处理器）
        page.evaluate("() => { __game.setSpeed(%s); __game.SET.goal = 2; }" % args.speed)
        ck['menu_visible_on_load'] = page.evaluate("!document.getElementById('menu').hidden")
        page.evaluate("document.getElementById('b-quick').click()")
        ck['select_opened'] = page.evaluate("!document.getElementById('select').hidden")
        page.evaluate("document.getElementById('b-rand').click()")
        ck['start_enabled_after_rand'] = page.evaluate("!document.getElementById('b-start').disabled")
        page.evaluate("document.getElementById('b-start').click()")
        page.evaluate("() => { __bot.cfg.stuckSec = 90; __bot.reset(); }")
        ck['match_started'] = page.evaluate("__game.G.mode === 'match'")
        # 第一个球：应触发换边
        r = page.evaluate("() => __runUntil(() => __game.G.score.B + __game.G.score.R >= 1 && __game.G.phase !== 'celebrate' && __game.G.pause <= 0 && !__game.G.pull)")
        R['detail']['first_goal_run'] = r
        d = page.evaluate('__bot.dump()')
        side = page.evaluate('__game.G.side')
        first_score = [e for e in d['log'] if e['type'] == 'score']
        ck['goal_event_seen'] = len(first_score) >= 1
        ck['side_flipped_after_goal'] = side == {'B': -1, 'R': 1}
        ck['switch_event_seen'] = d['counts'].get('switch', 0) >= 1
        ck['lineup_event_after_goal'] = d['counts'].get('lineup', 0) >= 2     # 开局一次 + 得分后一次
        # 打到结束（抢 2）
        r = page.evaluate("() => __runUntil(() => __game.G.phase === 'over')")
        R['detail']['to_over'] = r
        ck['result_popup_visible'] = page.evaluate("!document.getElementById('result').hidden")
        R['detail']['result_text'] = page.evaluate("""() => ({ title: document.getElementById('res-title').textContent, big: document.getElementById('res-big').textContent,
              sum: document.getElementById('res-sum').textContent, rows: document.querySelectorAll('#res-table tr').length, score: __game.score, n_matches: __game.matches.length })""")
        rt = R['detail']['result_text']
        ck['result_score_matches_game'] = rt['big'].replace(' ', '') == f"{rt['score']['B']}:{rt['score']['R']}"
        ck['result_table_rows'] = rt['rows'] >= 1 + 2 + 8     # 表头 + 两个队行 + 8 名球员
        ck['matches_array_appended'] = rt['n_matches'] == 1
        # 再来一局
        page.evaluate("document.getElementById('b-again').click()")
        page.evaluate("() => { __bot.cfg.stuckSec = 90; __bot.reset(); }")
        s = page.evaluate('__game.snapshot()')
        ck['again_score_zeroed'] = s['score'] == {'B': 0, 'R': 0}
        ck['again_result_hidden'] = not s['ui']['result']
        ck['again_mode_match'] = s['mode'] == 'match' and s['phase'] in ('countdown', 'pull', 'play')
        ck['again_side_reset'] = s['side'] == {'B': 1, 'R': -1}
        r = page.evaluate("() => __runUntil(() => __game.G.phase === 'play' && __game.G.passN >= 2)")
        r2 = page.evaluate('(() => { const d = __bot.dump(); return { throws: d.counts.throw || 0, catches: d.counts.catch || 0, time: __game.G.time }; })()')
        R['detail']['again_play'] = {'run': r, **r2}
        ck['again_can_play_on'] = r['status'] == 'until' and r2['throws'] >= 2
        # 打完第二局
        page.evaluate("() => { __game.SET.goal = 1; }")
        r = page.evaluate("() => __runUntil(() => __game.G.phase === 'over')")
        ck['second_match_ends'] = r['status'] == 'until'
        ck['second_result_visible'] = page.evaluate("!document.getElementById('result').hidden")
        ck['matches_array_two'] = page.evaluate('__game.matches.length') == 2
        # 换阵容 → 选人页；再开始
        page.evaluate("document.getElementById('b-reselect').click()")
        ck['reselect_opens_select'] = page.evaluate("!document.getElementById('select').hidden && __game.G.mode === 'menu'")
        page.evaluate("document.getElementById('b-start').click()")
        ck['restart_from_select'] = page.evaluate("__game.G.mode === 'match' && __game.G.score.B === 0 && __game.G.score.R === 0")
        page.evaluate("() => { __bot.cfg.stuckSec = 90; __bot.reset(); }")
        r = page.evaluate("() => __runUntil(() => __game.G.phase === 'over')")
        page.evaluate("document.getElementById('b-res-menu').click()")
        ck['result_to_menu'] = page.evaluate("!document.getElementById('menu').hidden && __game.G.mode === 'menu'")
        # 暂停面板
        page.evaluate("document.getElementById('b-quick').click()")
        page.evaluate("document.getElementById('b-rand').click()")
        page.evaluate("document.getElementById('b-start').click()")
        page.evaluate("() => { __bot.reset(); }")
        page.evaluate("__bot.runFor(8)")
        t_before = page.evaluate('__game.G.time')
        page.evaluate("document.getElementById('btn-pause').click()")
        ck['pause_panel_visible'] = page.evaluate("!document.getElementById('pausev').hidden && __game.G.paused")
        page.evaluate("() => { for (let i = 0; i < 60; i++) { window.__vstep(50); } }")
        ck['pause_freezes_time'] = abs(page.evaluate('__game.G.time') - t_before) < 0.01
        page.evaluate("document.getElementById('b-resume').click()")
        ck['resume_unpauses'] = not page.evaluate('__game.G.paused')
        # telemetry 读回（有才测）
        tel = page.evaluate("""() => { const t = window.__game.telemetry; if (!t) return null; const o = { has: true };
            for (const k of ['summary', 'export', 'clear']) { try { if (typeof t[k] === 'function') { const v = t[k](); o[k] = (v === undefined ? 'undefined' : JSON.stringify(v).slice(0, 300)); } else o[k] = 'missing'; } catch (e) { o[k] = 'THROW ' + e; } }
            return o; }""")
        R['detail']['telemetry'] = tel
    except Exception as e:
        R['error'] = ''.join(traceback.format_exception_only(type(e), e))[:600]
        R['shot'] = shot(page, outdir / 'flow-error.png')
    R['js_errors'] = watch.all_errors() + page.evaluate('window.__jsErrors')
    ctx.close()
    R['pass'] = ('error' not in R) and all(R['checks'].values()) and not R['js_errors']
    return R

# ---------------------------- 真实输入冒烟 ---------------------------------------------------
WASD = {'KeyD': (1, 0), 'KeyA': (-1, 0), 'KeyS': (0, 1), 'KeyW': (0, -1)}
DIRS8 = [((1, 0), ['KeyD']), ((1, 1), ['KeyD', 'KeyS']), ((0, 1), ['KeyS']), ((-1, 1), ['KeyA', 'KeyS']),
         ((-1, 0), ['KeyA']), ((-1, -1), ['KeyA', 'KeyW']), ((0, -1), ['KeyW']), ((1, -1), ['KeyD', 'KeyW'])]

def ev_since(page, n0, types):
    """取 bot 采集日志里第 n0 条之后的指定类型事件（bot.log 只增不减，比 G.events 稳）。"""
    return page.evaluate("([n0, types]) => { __bot.harvest(); return __bot.st.log.slice(n0).filter(e => types.includes(e.type)); }", [n0, types])

def log_len(page): return page.evaluate('(__bot.harvest(), __bot.st.log.length)')

def pump_real(page, ms=60):
    page.wait_for_timeout(ms)

def wait_my_turn(page, timeout_s):
    """等到：比赛中、蓝方进攻、持盘、可操作。"""
    t0 = now()
    while now() - t0 < timeout_s:
        s = page.evaluate("""() => { const G = __game.G; return { ok: G.mode === 'match' && G.phase === 'play' && G.cd <= 0 && G.pause <= 0 && !G.pull && G.off === 'B' && G.disc.state === 'held' && !!G.holder,
              ctlIsHolder: G.ctl === G.holder, ph: G.phase }; }""")
        if s['ok']: return s
        pump_real(page, 50)
    return None

def start_match_real(page, click):
    """真实点击：快速比赛 → 随机阵容 → 开始比赛。click(selector) 是点击函数（鼠标或触摸）。"""
    click('#b-quick'); page.wait_for_timeout(150)
    click('#b-rand'); page.wait_for_timeout(100)
    click('#b-start'); page.wait_for_timeout(150)

def smoke_keyboard(browser, html, args, outdir):
    R = {'name': 'desktop_keyboard', 'checks': {}, 'detail': {}}
    ctx, page, watch = new_page(browser, html, 31337, virtual=False, viewport={'width': 1280, 'height': 720})
    ck = R['checks']
    try:
        page.evaluate("() => { __bot.cfg.stuckSec = 1e9; __bot.reset(); }")
        start_match_real(page, lambda sel: page.click(sel))
        ck['started_with_real_clicks'] = page.evaluate("__game.G.mode === 'match'")
        n0 = log_len(page)
        attempts, tap_ok, hold_ok, kind_mismatch, caught_pass = [], 0, 0, 0, 0
        switch_done = False; switch_detail = None
        t_end = now() + args.smoke_timeout
        want_kind = 'low'
        while now() < t_end and not (caught_pass >= 1 and switch_done and any(a['kind'] == 'high' for a in attempts)):
            s = wait_my_turn(page, 25)
            if not s: continue
            # 先测切人（真实按 Q）：只要比赛中就按一次，看操控对象有没有变
            if not switch_done:
                before = page.evaluate("__game.G.ctl && __game.G.ctl.n")
                nlog = log_len(page)
                page.keyboard.press('KeyQ'); page.wait_for_timeout(250)
                after = page.evaluate("__game.G.ctl && __game.G.ctl.n")
                ctl_ev = ev_since(page, nlog, ['ctl'])
                if after == before:   # 可能撞冷却，等 0.6 秒再按一次
                    page.wait_for_timeout(600); page.keyboard.press('KeyQ'); page.wait_for_timeout(250)
                    after = page.evaluate("__game.G.ctl && __game.G.ctl.n"); ctl_ev = ev_since(page, nlog, ['ctl'])
                switch_done = True
                switch_detail = dict(before=before, after=after, ctl_events=ctl_ev)
                ck['switch_changes_ctl'] = after != before
                # 切回持盘人（最多按 4 次，每次间隔 0.6 秒，兼容冷却/撤销）
                for _ in range(5):
                    if page.evaluate('__game.G.ctl === __game.G.holder'): break
                    page.wait_for_timeout(600); page.keyboard.press('KeyQ'); page.wait_for_timeout(200)
                continue
            if not s['ctlIsHolder']:
                page.wait_for_timeout(150)
                if not page.evaluate('__game.G.ctl === __game.G.holder'):
                    page.keyboard.press('KeyQ'); page.wait_for_timeout(600)
                continue
            # 8 向键盘瞄准：在 8 个方向里挑得分最高的
            angs = [math.atan2(dy, dx) for (dx, dy), _ in DIRS8]
            p = page.evaluate("(a) => __bot.plan(false, a)", angs)
            held = page.evaluate("__game.G.holdT")
            if not p and held > 4: p = page.evaluate("(a) => __bot.plan(true, a)", angs)
            if not p or (p['margin'] < -0.25 and held < 5):
                page.wait_for_timeout(100); continue
            ki = min(range(8), key=lambda i: abs(math.atan2(math.sin(angs[i] - p['ang']), math.cos(angs[i] - p['ang']))))
            keys = DIRS8[ki][1]
            kind = want_kind if (want_kind == 'low' or p['L'] >= 7) else 'low'
            nlog = log_len(page)
            for k in keys: page.keyboard.down(k)
            page.wait_for_timeout(120)
            key_pass = 'Space' if len(attempts) % 2 else 'KeyJ'      # 空格与 J 各测一次
            page.keyboard.down(key_pass); page.wait_for_timeout(450 if kind == 'high' else 60); page.keyboard.up(key_pass)
            for k in keys: page.keyboard.up(k)
            page.wait_for_timeout(2400)
            evs = ev_since(page, nlog, ['throw', 'catch', 'turnover'])
            th = [e for e in evs if e['type'] == 'throw' and e['team'] == 'B']
            ca = [e for e in evs if e['type'] == 'catch' and e['team'] == 'B' and e.get('from')]
            a = dict(kind=kind, key=key_pass, threw=bool(th), thrown_kind=th[0]['kind'] if th else None, caught=bool(ca), plan=p)
            attempts.append(a)
            if th and th[0]['kind'] != kind: kind_mismatch += 1
            if th and ca:
                caught_pass += 1
                if kind == 'low': tap_ok += 1
                else: hold_ok += 1
            if th: want_kind = 'high' if want_kind == 'low' else 'low'
        R['detail'] = dict(attempts=attempts, switch=switch_detail, kind_mismatch=kind_mismatch, tap_low_success=tap_ok, hold_high_success=hold_ok)
        ck['key_pass_thrown'] = any(a['threw'] for a in attempts)
        ck['key_pass_completed'] = caught_pass >= 1
        ck['key_kind_matches_hold'] = kind_mismatch == 0 and any(a['threw'] for a in attempts)
        ck.setdefault('switch_changes_ctl', False)
    except Exception as e:
        R['error'] = ''.join(traceback.format_exception_only(type(e), e))[:600]
        R['shot'] = shot(page, outdir / 'smoke-keyboard-error.png')
    R['js_errors'] = watch.all_errors() + page.evaluate('window.__jsErrors')
    R['env_noise'] = len(watch.noise)
    ctx.close()
    R['pass'] = ('error' not in R) and all(R['checks'].values()) and not R['js_errors']
    return R

class Touch:
    """CDP 触摸：保持多指（左手摇杆 + 右手按键）。"""
    def __init__(self, page):
        self.page = page; self.cdp = page.context.new_cdp_session(page); self.pts = {}
    def _send(self, typ):
        self.cdp.send('Input.dispatchTouchEvent', {'type': typ, 'touchPoints': [dict(x=x, y=y, id=i, radiusX=4, radiusY=4, force=1) for i, (x, y) in self.pts.items()]})
    def down(self, i, x, y): self.pts[i] = (x, y); self._send('touchStart')
    def move(self, i, x, y): self.pts[i] = (x, y); self._send('touchMove')
    def up(self, i):
        # CDP：touchEnd 的 touchPoints 列的是「这次松开的那几根」
        if i in self.pts:
            x, y = self.pts.pop(i)
            self.cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': [dict(x=x, y=y, id=i, radiusX=4, radiusY=4, force=1)]})
    def tap(self, x, y, hold_ms=60, i=9):
        self.down(i, x, y); self.page.wait_for_timeout(hold_ms); self.up(i)

def center(page, sel):
    return page.evaluate("(s) => { const r = document.querySelector(s).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, r.width, r.height]; }", sel)

def layout_report(page):
    return page.evaluate("""() => {
      const iw = innerWidth, ih = innerHeight, out = { iw, ih, rot: null, app: null, canvas: null, buttons: {} };
      const app = document.getElementById('app'), r = app.getBoundingClientRect(), c = document.getElementById('game');
      out.app = { w: Math.round(r.width), h: Math.round(r.height), transform: app.style.transform };
      out.canvas = { w: c.width, h: c.height };
      out.rot = app.style.transform.includes('rotate(90deg)');
      document.querySelectorAll('.vb').forEach((b) => { const q = b.getBoundingClientRect(); out.buttons[b.id] = { x: Math.round(q.left), y: Math.round(q.top), w: Math.round(q.width), h: Math.round(q.height),
         inside: q.left >= -1 && q.top >= -1 && q.right <= iw + 1 && q.bottom <= ih + 1, hit: (() => { const e = document.elementFromPoint(q.left + q.width / 2, q.top + q.height / 2); return !!e && (e === b || b.contains(e)); })() }; });
      const z = document.getElementById('stick').getBoundingClientRect();
      const ze = document.elementFromPoint(z.left + z.width / 2, z.top + z.height / 2);
      out.stick = { x: Math.round(z.left), y: Math.round(z.top), w: Math.round(z.width), hit_zone: !!ze && ze.id === 'zone' };
      return out; }""")

def smoke_touch(browser, html, args, outdir, portrait):
    name = 'mobile_portrait' if portrait else 'mobile_landscape'
    vp = {'width': 390, 'height': 844} if portrait else {'width': 844, 'height': 390}
    R = {'name': name, 'viewport': vp, 'checks': {}, 'detail': {}}
    ctx, page, watch = new_page(browser, html, 2468 if portrait else 1357, virtual=False, viewport=vp, touch=True, scale=2)
    ck = R['checks']
    try:
        page.evaluate("() => { __bot.cfg.stuckSec = 1e9; __bot.reset(); }")
        T = Touch(page)
        def tap_sel(sel):
            x, y, _, _ = center(page, sel); T.tap(x, y, 70)
        start_match_real(page, tap_sel)
        ck['started_with_touch_taps'] = page.evaluate("__game.G.mode === 'match'")
        # 布局：旋转、按键在视口内、能点中
        wait_my_turn(page, 40)
        L = layout_report(page); R['detail']['layout'] = L
        if portrait:
            ck['page_rotated'] = bool(L['rot'])
            ck['app_covers_viewport'] = abs(L['app']['w'] - L['iw']) <= 2 and abs(L['app']['h'] - L['ih']) <= 2
            ck['canvas_is_landscape_buffer'] = L['canvas']['w'] > L['canvas']['h']
        else:
            ck['not_rotated'] = not L['rot']
        ck['buttons_inside_viewport'] = all(b['inside'] for b in L['buttons'].values())
        ck['buttons_hit_testable'] = all(b['hit'] for b in L['buttons'].values())
        ck['stick_zone_hit_testable'] = bool(L['stick']['hit_zone'])
        # 屏幕位移 → 游戏位移：横屏 (dx,dy)；竖屏 game_x = screen_dy, game_y = -screen_dx
        def to_screen(gx, gy): return (gx, gy) if not portrait else (-gy, gx)
        def to_game(sx, sy): return (sx, sy) if not portrait else (sy, -sx)
        # 1) 摇杆方向映射：向 4 个方向各拖一次，核对 IN.tx/ty 与期望游戏向量同向
        zx, zy, zw, _ = center(page, '#stick')
        rad = zw * 0.5
        mapping = []
        for (sdx, sdy) in [(1, 0), (0, 1), (-1, 0), (0, -1)]:
            T.down(1, zx, zy); page.wait_for_timeout(60)
            ox, oy, _, _ = center(page, '#stick')       # 游戏会把按下点夹进安全范围，摇杆圆心以按下后 #stick 的真实位置为准
            T.move(1, ox + sdx * rad * 0.9, oy + sdy * rad * 0.9); page.wait_for_timeout(120)
            v = page.evaluate('[__game.IN.tx, __game.IN.ty]')
            T.up(1); page.wait_for_timeout(80)
            ex, ey = to_game(sdx, sdy)
            dot = (v[0] * ex + v[1] * ey) / ((math.hypot(*v) or 1) * math.hypot(ex, ey))
            mapping.append(dict(screen=(sdx, sdy), expect_game=(ex, ey), got=[round(v[0], 2), round(v[1], 2)], cos=round(dot, 3)))
        R['detail']['stick_mapping'] = mapping
        ck['stick_maps_to_game_axes'] = all(m['cos'] > 0.95 for m in mapping)
        # 2) 按键：手动/加速/跳接/切人 都真实点一下，看到各自的副作用
        hand0 = page.evaluate('__game.IN.hand'); tap_sel('#v-hand'); page.wait_for_timeout(120)
        hand1 = page.evaluate('__game.IN.hand'); R['detail']['hand'] = [hand0, hand1]
        ck['btn_hand_toggles'] = hand0 != hand1
        for _ in range(2): tap_sel('#v-hand'); page.wait_for_timeout(100)   # 轮回自动
        page.evaluate('__game.inject.hand("auto")')
        x, y, _, _ = center(page, '#v-sprint'); T.down(5, x, y); page.wait_for_timeout(120)
        sp = page.evaluate('__game.IN.sprint'); T.up(5); page.wait_for_timeout(80)
        ck['btn_sprint_holds'] = bool(sp) and not page.evaluate('__game.IN.sprint')
        ctl0 = page.evaluate('__game.G.ctl && __game.G.ctl.n'); tap_sel('#v-switch'); page.wait_for_timeout(250)
        ctl1 = page.evaluate('__game.G.ctl && __game.G.ctl.n')
        if ctl1 == ctl0: page.wait_for_timeout(600); tap_sel('#v-switch'); page.wait_for_timeout(250); ctl1 = page.evaluate('__game.G.ctl && __game.G.ctl.n')
        R['detail']['switch'] = [ctl0, ctl1]
        ck['btn_switch_changes_ctl'] = ctl0 != ctl1
        for _ in range(5):
            if page.evaluate('__game.G.ctl === __game.G.holder'): break
            page.wait_for_timeout(600); tap_sel('#v-switch'); page.wait_for_timeout(200)
        # 跳接：此刻 ctl 存在即可（起跳在 press 里直接判）；可能撞冷却/不在可跳状态，所以只要见到过 jumpT>0 即算
        jumped = False
        for _ in range(3):
            tap_sel('#v-jump'); page.wait_for_timeout(80)
            if page.evaluate('__game.G.players.some(p => p.team === "B" && p.jumpT > 0)'): jumped = True; break
            page.wait_for_timeout(500)
        ck['btn_jump_starts_jump'] = jumped
        # 3) 传盘：左手按住摇杆瞄准，右手点传盘按键；要求 throw 事件的接收人 == 计划的接收人（证明坐标映射端到端正确）
        done = False; tries = []
        t_end = now() + args.smoke_timeout
        while now() < t_end and not done:
            s = wait_my_turn(page, 25)
            if not s: continue
            if not s['ctlIsHolder']:
                page.wait_for_timeout(150)
                if not page.evaluate('__game.G.ctl === __game.G.holder'):
                    tap_sel('#v-switch'); page.wait_for_timeout(650)
                continue
            held = page.evaluate("__game.G.holdT")
            p = page.evaluate("__bot.plan(false)")
            if not p and held > 4: p = page.evaluate("__bot.plan(true)")
            if not p or (p['margin'] < -0.25 and held < 5): page.wait_for_timeout(100); continue
            sx, sy = to_screen(math.cos(p['ang']), math.sin(p['ang']))
            nlog = log_len(page)
            T.down(1, zx, zy); page.wait_for_timeout(60)
            ox, oy, _, _ = center(page, '#stick')
            T.move(1, ox + sx * rad * 0.9, oy + sy * rad * 0.9); page.wait_for_timeout(150)
            px, py, _, _ = center(page, '#v-pass')
            T.down(2, px, py); page.wait_for_timeout(40)
            ang = page.evaluate("""(cmd) => { const IN = __game.IN; const a = Math.atan2(IN.y, IN.x); let d = Math.abs(a - cmd); if (d > Math.PI) d = 2 * Math.PI - d; return { err: d, mag: Math.hypot(IN.x, IN.y) }; }""", p['ang'])   # 摇杆实际方向 vs 我命令手指拖向的游戏方向
            page.wait_for_timeout(20 if p['kind'] == 'low' else 400); T.up(2)
            page.wait_for_timeout(100); T.up(1)
            page.wait_for_timeout(2400)
            evs = ev_since(page, nlog, ['throw', 'catch', 'turnover'])
            th = [e for e in evs if e['type'] == 'throw' and e['team'] == 'B']
            ca = [e for e in evs if e['type'] == 'catch' and e['team'] == 'B' and e.get('from')]
            t = dict(plan=p, threw=bool(th), to=th[0]['to'] if th else None, kind=th[0]['kind'] if th else None, caught=bool(ca), aim=ang)
            tries.append(t)
            if th and ca: done = True
        R['detail']['pass_tries'] = tries
        ck['touch_aim_matches_planned_direction'] = any(t['threw'] and t['aim'] and t['aim']['err'] < 0.2 and t['aim']['mag'] > 0.5 for t in tries)
        ck['touch_pass_completed'] = done
        R['detail']['kind_ok'] = all((t['kind'] == t['plan']['kind']) for t in tries if t['threw'])
        ck['touch_hold_gives_high_tap_gives_low'] = R['detail']['kind_ok'] and any(t['threw'] for t in tries)
    except Exception as e:
        R['error'] = ''.join(traceback.format_exception_only(type(e), e))[:600] + traceback.format_exc()[-600:]
        R['shot'] = shot(page, outdir / f'smoke-{name}-error.png')
    # 总是留一张现场图
    R['screenshot'] = shot(page, outdir / f'smoke-{name}.png')
    R['js_errors'] = watch.all_errors() + page.evaluate('window.__jsErrors')
    R['env_noise'] = len(watch.noise)
    ctx.close()
    R['pass'] = ('error' not in R) and all(R['checks'].values()) and not R['js_errors']
    return R

def layout_matrix(browser, html, outdir):
    """多种屏幕尺寸下按键都在视口内、可点中（不用开局，直接进游戏状态看布局）。"""
    rows = []
    for name, vp in [('844x390', (844, 390)), ('667x375', (667, 375)), ('932x430', (932, 430)), ('390x844', (390, 844)), ('360x780', (360, 780)), ('1280x720', (1280, 720))]:
        ctx, page, watch = new_page(browser, html, 1, virtual=True, viewport={'width': vp[0], 'height': vp[1]}, touch=vp[0] < 1000)
        page.evaluate("() => { __game.newGame('match'); __bot.reset(); for (let i = 0; i < 40; i++) window.__vstep(50); }")
        L = layout_report(page)
        rows.append(dict(viewport=name, rot=L['rot'], all_inside=all(b['inside'] for b in L['buttons'].values()), all_hit=all(b['hit'] for b in L['buttons'].values()),
                         stick_hit=L['stick']['hit_zone'], bad=[k for k, b in L['buttons'].items() if not (b['inside'] and b['hit'])], errors=watch.all_errors()))
        ctx.close()
    return rows


# ---------------------------- 专项探针：散盘蹲守死锁 ------------------------------------------------
def probe_loose_disc_camp(browser, html, args, outdir):
    """红方得到球权后飞盘落在地上，蓝方防守者站在盘旁 0.27 米（背向红方捡盘者一侧）不动：
    红方捡盘者应当在 camp_sec 游戏秒内捡到盘。基线 100 场里 12 场因此卡死：捡盘者被 0.8 米碰撞半径挡在 >0.45 米外，
    捡盘判定又要求 <0.45 米，且没有超时/辅助捡盘。为了确定性复现，站位是直接摆放的（与卡死现场几何一致），共试 N 个种子。"""
    R = {'name': 'loose_disc_camp', 'trials': []}
    n_tr = 5
    for k in range(n_tr):
        ctx, page, watch = new_page(browser, html, 100 + k)
        t = {'seed': 100 + k}
        try:
            page.evaluate("() => { __game.SET.goal = 5; __game.SET.diff = 1; __game.setSpeed(%s); __game.newGame('match'); __bot.cfg.camp = true; __bot.cfg.manualSwitch = false; __bot.cfg.stuckSec = 1e9; __bot.reset(); }" % args.speed)
            r = page.evaluate("() => __runUntil(() => { const G = __game.G; return G.mode === 'match' && G.phase === 'play' && !G.pull && G.pause <= 0 && G.off === 'R' && G.disc.state === 'loose' && !!G.ctl; })")
            t['reached'] = r['status'] == 'until'
            if t['reached']:
                t['setup'] = page.evaluate("""() => { const G = __game.G, d = G.disc, red = G.teams.R.slice().sort((a, b) => Math.hypot(a.x - d.x, a.y - d.y) - Math.hypot(b.x - d.x, b.y - d.y))[0];
                    const ux = d.x - red.x, uy = d.y - red.y, m = Math.hypot(ux, uy) || 1, me = G.ctl;
                    me.x = d.x + ux / m * 0.27; me.y = d.y + uy / m * 0.27; me.vx = me.vy = 0;
                    return { disc: [d.x, d.y], red_dist_before: +m.toFixed(2), picker: red.name }; }""")
                r2 = page.evaluate("() => __bot.runFor(%s, () => !!__game.G.holder)" % args.camp_sec)
                t['picked_up_while_camped'] = r2['status'] == 'until'
                if not t['picked_up_while_camped']:
                    t['snapshot'] = page.evaluate('__game.snapshot()')
                    page.evaluate("() => { __bot.cfg.camp = false; }")
                    r3 = page.evaluate("() => __bot.runFor(10, () => !!__game.G.holder)")
                    t['picked_up_after_release'] = r3['status'] == 'until'
                    if 'shot' not in R: R['shot'] = shot(page, outdir / 'probe-loose-disc-camp.png')   # 截图会还回真实 rAF，必须放最后
        except Exception as e:
            t['error'] = ''.join(traceback.format_exception_only(type(e), e))[:400]
        t['js_errors'] = watch.all_errors() + page.evaluate('window.__jsErrors')
        ctx.close()
        R['trials'].append(t)
    R['deadlocks'] = sum(1 for t in R['trials'] if t.get('reached') and not t.get('picked_up_while_camped'))
    R['all_reached'] = all(t.get('reached') for t in R['trials'])
    R['released_ok'] = all(t.get('picked_up_after_release', True) for t in R['trials'])
    R['js_errors'] = [e for t in R['trials'] for e in t['js_errors']]
    R['pass'] = R['all_reached'] and R['deadlocks'] == 0 and not R['js_errors'] and not any('error' in t for t in R['trials'])
    return R

# ---------------------------- 脚本自证（阳性对照） --------------------------------------------
def selftest(browser, html, outdir):
    """每个「0 次 / 没有」结论背后的探测器，都先在已知必有的场景里确认它能数到。"""
    res = {}
    # A. 页面错误探测器：故意抛一个异步异常、打一条 console.error，看 watch 能收到
    ctx, page, watch = new_page(browser, html, 5, virtual=False)
    page.evaluate("() => { setTimeout(() => { throw new Error('SELFTEST-pageerror'); }, 0); console.error('SELFTEST-console-error'); }")
    page.wait_for_timeout(300)
    res['pageerror_detector_works'] = any('SELFTEST-pageerror' in e for e in watch.errors)
    res['console_error_detector_works'] = any('SELFTEST-console-error' in e for e in watch.console_errors)
    res['window_error_hook_works'] = page.evaluate("window.__jsErrors.some(e => e.includes('SELFTEST-pageerror'))")
    ctx.close()
    # B. 事件流探测器：打一局的前 60 秒，必须数到 throw / catch / turnover 事件，
    #    且「事件流统计」与游戏自带 G.ts 的 att/comp 一致（两个独立来源互相印证）
    ctx, page, watch = new_page(browser, html, 99)
    page.evaluate("() => { __game.SET.goal = 5; __game.SET.diff = 1; __game.setSpeed(2); __game.newGame('match'); __bot.reset(); }")
    page.evaluate('__bot.runFor(120)')
    d = page.evaluate('__bot.dump()'); ts = page.evaluate('__game.G.ts')
    c = d['counts']
    res['counts_throw_catch_turnover_seen'] = c.get('throw', 0) > 0 and c.get('catch', 0) > 0 and c.get('turnover', 0) > 0
    att = {t: sum(1 for e in d['log'] if e['type'] == 'throw' and e['team'] == t) for t in 'BR'}
    comp = {t: sum(1 for e in d['log'] if e['type'] == 'catch' and e['team'] == t and e.get('from')) for t in 'BR'}
    res['event_stream_matches_G_ts'] = all(att[t] == ts[t]['att'] and comp[t] == ts[t]['comp'] for t in 'BR')
    res['detail_B'] = dict(att_ev=att, comp_ev=comp, ts={t: dict(att=ts[t]['att'], comp=ts[t]['comp']) for t in 'BR'}, counts=c)
    # C. 卡死探测器：人为制造「暂停 99 秒且没有后续」的卡死（G.pause=99），探测器必须在 stuck_sec 内判卡死
    page.evaluate("() => { __bot.cfg.stuckSec = 20; __bot.reset(); __game.G.pause = 99; __game.G.afterPause = null; }")
    r = page.evaluate('__bot.runMatch(200)')
    res['stuck_detector_fires_on_forced_hang'] = r['status'] == 'stuck'
    ctx.close()
    # D. 无机器人对照：不出手的蓝方应该靠读秒/被断产生换攻，传盘成功率应明显低于有机器人时
    #    （证明统计的是机器人的行为，而不是游戏自己随便跑出来的）
    ctx, page, watch = new_page(browser, html, 99)
    page.evaluate("() => { __game.SET.goal = 5; __game.SET.diff = 1; __game.setSpeed(2); __game.newGame('match'); __bot.reset(); }")
    page.evaluate("() => { for (let i = 0; i < 2400; i++) { window.__vstep(50); __bot.harvest(); } }")   # 120 游戏秒，只推进、不决策
    c2 = page.evaluate('__bot.dump().counts')
    ts2 = page.evaluate('__game.G.ts')
    res['idle_blue_never_throws'] = ts2['B']['att'] == 0
    res['idle_blue_stalls'] = ts2['B']['stall'] >= 1
    res['detail_idle'] = dict(ts_B=ts2['B'], counts=c2)
    ctx.close()
    res['pass'] = all(v for k, v in res.items() if k.startswith(('pageerror', 'console', 'window', 'counts', 'event', 'stuck', 'idle_blue')) and isinstance(v, bool))
    return res

# ---------------------------- 汇总 ------------------------------------------------------------
def pct(a, b): return round(100.0 * a / b, 1) if b else None

def aggregate(recs, args):
    A = {'n': len(recs)}
    A['status'] = {}
    for r in recs: A['status'][r['status']] = A['status'].get(r['status'], 0) + 1
    ok = [r for r in recs if r['status'] == 'over']
    A['finished'] = len(ok)
    def blk(rs):
        o = {'n': len(rs)}
        if not rs: return o
        gt = sorted(r['game_time'] for r in rs); pt = sorted(r['play_time'] for r in rs)
        o['game_time_s'] = dict(median=round(statistics.median(gt), 1), min=gt[0], max=gt[-1], mean=round(statistics.mean(gt), 1))
        o['play_time_s'] = dict(median=round(statistics.median(pt), 1), min=pt[0], max=pt[-1])
        for t, nm in (('B', 'blue'), ('R', 'red')):
            att = sum(r['ts_stats'][t]['att'] for r in rs); comp = sum(r['ts_stats'][t]['comp'] for r in rs)
            per = [r['ts_stats'][t]['comp'] / r['ts_stats'][t]['att'] for r in rs if r['ts_stats'][t]['att']]
            o[f'pass_{nm}'] = dict(att=att, comp=comp, pooled_pct=pct(comp, att), per_match_median_pct=round(100 * statistics.median(per), 1) if per else None,
                                   per_match_min_pct=round(100 * min(per), 1) if per else None, per_match_max_pct=round(100 * max(per), 1) if per else None)
            why = {k: sum(r['ev_stats'][t]['why'][k] for r in rs) for k in ('block', 'mblock', 'drop', 'oob', 'stall')}
            tot = sum(why.values())
            o[f'turnovers_lost_by_{nm}'] = dict(total=tot, **why, **{f'{k}_pct': pct(v, tot) for k, v in why.items()})
        o['score_blue_win_pct'] = pct(sum(1 for r in rs if r['score']['B'] > r['score']['R']), len(rs))
        o['events_per_match_median'] = statistics.median([r['events_total'] for r in rs])
        o['throws_per_match_median'] = statistics.median([r['event_counts'].get('throw', 0) for r in rs])
        return o
    A['overall'] = blk(ok)
    A['goal5'] = blk([r for r in ok if r['goal'] == 5]); A['goal7'] = blk([r for r in ok if r['goal'] == 7])
    A['by_diff'] = {str(d): blk([r for r in ok if r['diff'] == d]) for d in range(3)}
    A['by_formation'] = {f: blk([r for r in ok if r['form'] == f]) for f in ('vert', 'horz')}
    # 事件流 vs 游戏汇总对账（每场）
    mism = [r['i'] for r in ok if any(r['ev_stats'][t]['att'] != r['ts_stats'][t]['att'] or r['ev_stats'][t]['comp'] != r['ts_stats'][t]['comp'] for t in 'BR')]
    A['ev_vs_ts_mismatch_matches'] = mism
    A['stuck_matches'] = [r['i'] for r in recs if r['status'] == 'stuck']
    A['overtime_matches'] = [r['i'] for r in recs if r['status'] == 'overtime']
    A['exception_matches'] = [r['i'] for r in recs if r['status'] in ('exception', 'harness-error')]
    A['not_ok_result_popup'] = [r['i'] for r in ok if not r.get('ui_result_visible')]
    A['js_errors_total'] = sum(len(r['js_errors']) for r in recs)
    A['js_error_samples'] = sorted({e[:160] for r in recs for e in r['js_errors']})[:8]
    A['env_noise_total'] = sum(r.get('env_noise', 0) for r in recs)
    ctl = {}
    for r in recs:
        for k, v in r.get('ctl_events', {}).items(): ctl[k] = ctl.get(k, 0) + v
    A['ctl_events_total'] = ctl
    A['bot_manual_switch'] = dict(calls=sum(r['bot']['sw']['calls'] for r in recs if 'bot' in r), effective=sum(r['bot']['sw']['effective'] for r in recs if 'bot' in r))
    A['bot_jump'] = dict(calls=sum(r['bot']['jumpCalls'] for r in recs if 'bot' in r), ok=sum(r['bot']['jumpOk'] for r in recs if 'bot' in r))
    A['bot_forced_throws'] = sum(r['bot']['forced'] for r in recs if 'bot' in r)
    A['bot_pass_rejects'] = sum(r['bot']['passRejects'] for r in recs if 'bot' in r)
    A['telemetry_seen'] = sum(1 for r in recs if r.get('telemetry'))
    A['telemetry_errors'] = [r['telemetry'].get('error') for r in recs if r.get('telemetry') and r['telemetry'].get('error')][:3]
    A['wall_s_total_worker_time'] = round(sum(r['wall_s'] for r in recs), 0)
    return A

def judge(R, args):
    """返回 [(项, 是否达标, 说明)]。"""
    J = []
    M = R.get('matches')
    if M:
        A = M['aggregate']; O = A['overall']
        J.append(('对局全部正常结束', A['finished'] == A['n'] and not A['exception_matches'] and not A['overtime_matches'], f"{A['finished']}/{A['n']} 场结束 {A['status']}"))
        J.append(('卡死场次', len(A['stuck_matches']) <= args.max_stuck, f"{len(A['stuck_matches'])} 场 {A['stuck_matches'][:8]}"))
        J.append(('JS 异常/console error', A['js_errors_total'] <= args.max_js_errors, f"{A['js_errors_total']} 条 {A['js_error_samples'][:2]}"))
        if O.get('n'):
            for nm, lab in (('blue', '蓝方'), ('red', '红方')):
                p = O[f'pass_{nm}']['pooled_pct']
                J.append((f'{lab}传盘成功率', p is not None and args.pass_min * 100 <= p <= args.pass_max * 100, f"{p}% ({O[f'pass_{nm}']['comp']}/{O[f'pass_{nm}']['att']}) 要求 {args.pass_min*100:.0f}–{args.pass_max*100:.0f}%"))
            g5 = A['goal5']
            if g5.get('n'):
                med = g5['game_time_s']['median']
                J.append(('抢5 游戏内时长中位数', args.dur_min <= med <= args.dur_max, f"{med}s = {med/60:.1f}分 (n={g5['n']}) 要求 {args.dur_min/60:.0f}–{args.dur_max/60:.0f}分"))
        J.append(('事件流与游戏统计对账', not A['ev_vs_ts_mismatch_matches'], f"不一致场次 {A['ev_vs_ts_mismatch_matches'][:8]}"))
        J.append(('每场结束都出结算弹窗', not A['not_ok_result_popup'], f"缺弹窗 {A['not_ok_result_popup'][:8]}"))
    D = R.get('drills')
    if D:
        bad = [k for k, v in D['per_drill'].items() if v['passed'] < v['trials']]
        J.append(('教学 6 关全过', not bad and not D['js_errors'], f"未全过的关 {bad}; JS错误 {len(D['js_errors'])}"))
    F = R.get('flow')
    if F: J.append(('终局路径（得分→结算→再来一局）', F['pass'], f"未过项 {[k for k, v in F['checks'].items() if not v] or '无'}{' err=' + F['error'][:120] if 'error' in F else ''}"))
    for key in ('smoke_keyboard', 'smoke_landscape', 'smoke_portrait'):
        S = R.get(key)
        if S: J.append((f'真实输入冒烟·{S["name"]}', S['pass'], f"未过项 {[k for k, v in S['checks'].items() if not v] or '无'}{' err=' + S['error'][:140] if 'error' in S else ''}{' jserr=' + str(len(S['js_errors'])) if S['js_errors'] else ''}"))
    PC = R.get('probe_camp')
    if PC: J.append(('专项·蓝方蹲在散盘上不会卡死红方捡盘', PC['pass'], f"{len(PC['trials'])} 次试验里死锁 {PC['deadlocks']} 次（站在盘旁 0.27m {args.camp_sec:.0f}s 内红方捡不到盘）；让开后都能捡起={PC['released_ok']}；全部到达场景={PC['all_reached']}"))
    LM = R.get('layout_matrix')
    if LM: J.append(('多屏幕布局（按键在视口内且可点中）', all(r['all_inside'] and r['all_hit'] and r['stick_hit'] and not r['errors'] for r in LM), f"问题屏幕 {[(r['viewport'], r['bad']) for r in LM if r['bad'] or not r['stick_hit']] or '无'}"))
    ST = R.get('selftest')
    if ST: J.append(('脚本自证（阳性对照）', ST['pass'], f"未过 {[k for k, v in ST.items() if isinstance(v, bool) and not v] or '全部通过'}"))
    return J

def print_summary(R, J, args):
    out = []
    out.append('=' * 78)
    out.append(f"被测文件: {args.html}")
    M = R.get('matches')
    if M:
        A = M['aggregate']; O = A['overall']
        out.append(f"批量对局: {A['n']} 场 (结束 {A['finished']}, 状态 {A['status']}), 种子 {args.seed}, speed {args.speed}, 墙钟 {M['wall_s']}s")
        if O.get('n'):
            gt = O['game_time_s']; pt = O['play_time_s']
            out.append(f"  时长(游戏内) 中位 {gt['median']}s [{gt['min']}–{gt['max']}]  纯比赛时间 中位 {pt['median']}s;  抢5中位 {A['goal5'].get('game_time_s', {}).get('median')}s (n={A['goal5'].get('n')}), 抢7中位 {A['goal7'].get('game_time_s', {}).get('median')}s (n={A['goal7'].get('n')})")
            for nm, lab in (('blue', '蓝'), ('red', '红')):
                P = O[f'pass_{nm}']; W = O[f'turnovers_lost_by_{nm}']
                out.append(f"  {lab}方传盘 {P['comp']}/{P['att']}={P['pooled_pct']}% (每场中位 {P['per_match_median_pct']}% [{P['per_match_min_pct']}–{P['per_match_max_pct']}]);  丢盘 {W['total']}: 被断 {W['block']+W['mblock']} (其中封堵 {W['mblock']}) / 落地掉盘 {W['drop']} / 出界 {W['oob']} / 读秒 {W['stall']}")
            out.append(f"  按难度 蓝成功率: " + ', '.join(f"d{d}={A['by_diff'][d]['pass_blue']['pooled_pct']}%/红{A['by_diff'][d]['pass_red']['pooled_pct']}%" for d in A['by_diff'] if A['by_diff'][d].get('n')))
            out.append(f"  蓝方胜率 {O['score_blue_win_pct']}%;  每场事件数中位 {O['events_per_match_median']}, 传盘中位 {O['throws_per_match_median']}")
        out.append(f"  机器人: 手动切人 {A['bot_manual_switch']}, 跳接 {A['bot_jump']}, 强制出手 {A['bot_forced_throws']}, ctl 事件 {A['ctl_events_total'] or '无(版本不含 ctl 事件)'}, telemetry {'有' if A['telemetry_seen'] else '无'}")
    D = R.get('drills')
    if D:
        out.append('教学 6 关 (每关 %d 次试验, 上限 %ss):' % (args.drill_trials, args.drill_limit))
        for k, v in D['per_drill'].items():
            out.append(f"  第{k}关 过 {v['passed']}/{v['trials']}  用时中位 {v['time_median']}s 最长 {v['time_max']}s  尝试次数均值 {v['attempts_mean']} 最多 {v['attempts_max']}")
    out.append('-' * 78)
    w = max(len(x[0]) for x in J) if J else 10
    for name, ok, note in J:
        out.append(f"[{'PASS' if ok else 'FAIL'}] {name}{' ' * max(1, 28 - len(name) * 2)}{note}")
    out.append('=' * 78)
    out.append('总判定: ' + ('全部达标' if all(x[1] for x in J) else '有不达标项 -> 退出码 1'))
    return '\n'.join(out)

# ---------------------------- 主流程 ------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(description='飞盘 Ultimate 4v4 对战版 自动跑局验收（Playwright）')
    ap.add_argument('html', help='被测 html 路径（file:// 打开）')
    ap.add_argument('--out', default=None, help='结果目录（默认 <脚本目录>/results/<html名>-<时间>）')
    ap.add_argument('--only', default='selftest,matches,drills,flow,probes,layout,smoke', help='逗号分隔：selftest,matches,drills,flow,probes,layout,smoke')
    ap.add_argument('--matches', type=int, default=100, help='批量对局场数 (默认 100)')
    ap.add_argument('--goal7-frac', type=float, default=0.10, help='抢 7 占比 (默认 0.10)')
    ap.add_argument('--horz-frac', type=float, default=0.20, help='横排站位占比 (默认 0.20，其余竖排)')
    ap.add_argument('--workers', type=int, default=min(6, os.cpu_count() or 2), help='并行 Chromium 进程数')
    ap.add_argument('--seed', type=int, default=1, help='总种子；同种子同版本完全可复现')
    ap.add_argument('--speed', type=int, default=2, help='setSpeed 倍速；每个虚拟帧推进 0.05*speed 游戏秒，机器人每帧决策一次 (默认 2 = 0.1s 一拍)')
    ap.add_argument('--stuck-sec', type=float, default=45, help='卡死判据：游戏内连续多少秒没有传盘/换攻/得分事件 (默认 45)')
    ap.add_argument('--max-game-sec', type=float, default=1500, help='单场游戏内时长硬上限，超过记 overtime (默认 1500)')
    ap.add_argument('--drill-trials', type=int, default=5, help='教学每关重复试验次数 (默认 5)')
    ap.add_argument('--drill-limit', type=float, default=60, help='教学每关游戏内时限秒 (默认 60)')
    ap.add_argument('--camp-sec', type=float, default=15, help='散盘蹲守探针：红方应在多少游戏秒内捡到盘 (默认 15)')
    ap.add_argument('--smoke-timeout', type=float, default=120, help='每个真实输入冒烟的墙钟上限秒 (默认 120)')
    ap.add_argument('--pass-min', type=float, default=0.60); ap.add_argument('--pass-max', type=float, default=0.88)
    ap.add_argument('--dur-min', type=float, default=180, help='抢 5 时长中位数下限秒 (默认 180)')
    ap.add_argument('--dur-max', type=float, default=480, help='抢 5 时长中位数上限秒 (默认 480)')
    ap.add_argument('--max-stuck', type=int, default=0); ap.add_argument('--max-js-errors', type=int, default=0)
    ap.add_argument('--headed', action='store_true', help='有头模式（调试）')
    # 内部：子进程分片
    ap.add_argument('--worker', type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument('--nworkers', type=int, default=1, help=argparse.SUPPRESS)
    return ap

def main():
    args = build_parser().parse_args()
    args.html = str(Path(args.html).resolve())
    if not Path(args.html).exists(): log('html 不存在:', args.html); return 2
    if args.worker is not None: worker_main(args); return 0
    if args.out is None:
        args.out = str(Path(__file__).resolve().parent / 'results' / f"{Path(args.html).stem}-{time.strftime('%Y%m%d-%H%M%S')}")
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(','))
    R = {'html': args.html, 'started': time.strftime('%F %T'), 'args': {k: v for k, v in vars(args).items() if k not in ('worker', 'nworkers')}}
    from playwright.sync_api import sync_playwright
    procs = []
    t0 = now()
    if 'matches' in only and args.matches > 0:
        nw = max(1, min(args.workers, args.matches))
        for w in range(nw):
            cmd = [sys.executable, '-I', os.path.abspath(__file__), args.html, '--worker', str(w), '--nworkers', str(nw), '--out', str(outdir),
                   '--matches', str(args.matches), '--seed', str(args.seed), '--speed', str(args.speed), '--stuck-sec', str(args.stuck_sec),
                   '--max-game-sec', str(args.max_game_sec), '--goal7-frac', str(args.goal7_frac), '--horz-frac', str(args.horz_frac)]
            procs.append(subprocess.Popen(cmd, stderr=subprocess.PIPE if False else None))
        log(f'已启动 {nw} 个对局进程，共 {args.matches} 场 ...')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed, args=['--mute-audio'])
            if 'selftest' in only: log('自证 ...'); R['selftest'] = selftest(browser, args.html, outdir)
            if 'drills' in only: log('教学 6 关 ...'); R['drills'] = run_drills(browser, args.html, args, outdir)
            if 'flow' in only: log('终局路径 ...'); R['flow'] = run_flow(browser, args.html, args, outdir)
            if 'probes' in only: log('专项探针 ...'); R['probe_camp'] = probe_loose_disc_camp(browser, args.html, args, outdir)
            browser.close()
        for pr in procs: pr.wait()
        if procs:
            recs = []
            for w in range(len(procs)):
                f = outdir / f'matches.part{w}.jsonl'
                if f.exists(): recs += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
            recs.sort(key=lambda r: r['i'])
            R['matches'] = {'wall_s': round(now() - t0, 1), 'records': recs, 'aggregate': aggregate(recs, args), 'expected': args.matches}
            if len(recs) != args.matches: R['matches']['missing'] = sorted(set(range(args.matches)) - {r['i'] for r in recs})
        # 真实输入与多屏布局：等对局进程都退出、机器空了再跑，避免真实时间的 rAF 被抢 CPU
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed, args=['--mute-audio'])
            if 'layout' in only: log('多屏幕布局 ...'); R['layout_matrix'] = layout_matrix(browser, args.html, outdir)
            if 'smoke' in only:
                log('冒烟·桌面键盘 ...'); R['smoke_keyboard'] = smoke_keyboard(browser, args.html, args, outdir)
                log('冒烟·手机横屏触摸 ...'); R['smoke_landscape'] = smoke_touch(browser, args.html, args, outdir, portrait=False)
                log('冒烟·手机竖屏触摸 ...'); R['smoke_portrait'] = smoke_touch(browser, args.html, args, outdir, portrait=True)
            browser.close()
    except Exception:
        R['harness_error'] = traceback.format_exc()
        for pr in procs:
            try: pr.kill()
            except Exception: pass
    J = judge(R, args)
    if 'harness_error' in R: J.append(('脚本自身运行', False, R['harness_error'][-300:]))
    if 'matches' in R and R['matches'].get('missing'): J.append(('对局记录完整', False, f"缺 {R['matches']['missing'][:10]}"))
    R['judgement'] = [dict(item=a, ok=b, note=c) for a, b, c in J]
    R['all_pass'] = all(x[1] for x in J) and bool(J)
    R['elapsed_s'] = round(now() - t0, 1)
    (outdir / 'result.json').write_text(json.dumps(R, ensure_ascii=False, indent=1))
    print(print_summary(R, J, args))
    print(f'结果 JSON: {outdir / "result.json"}')
    if 'harness_error' in R: return 2
    return 0 if R['all_pass'] else 1

if __name__ == '__main__':
    sys.exit(main())
