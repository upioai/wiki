# -*- coding: utf-8 -*-
"""douyin_dm_web_capture_dom — 抖音网页版私信【DOM 版】捕获(读侧, 阶段1)。

替掉 douyin_dm_web_capture.py 的 VL 截图 + 红点像素 + 点击标已读那一层, 改用 Playwright
connect_over_cdp 接管【调试版 Edge】直接读 DOM:
  [data-e2e="conversation-item"] → [昵称, 预览, 未读] (锚点见 reference_douyin_web_dm_dom_anchors)
判回复【过滤链原样复用】VL 版(match_name → we_sent_last → SYS_NOTICE/噪声 → is_relevant),
命中 → record_dm_inbound 写回客户回复。

相对 VL 版的三个关键差异:
  ① 精确昵称/预览(DOM innerText, 非 OCR) → 根治"误读本号头像/昵称塌缩整轮"(2026-06-27 事件)。
  ② 【不点任何会话】(点会话=标已读=毁未读信号; 也避免 VL 版那条 click 把列表搅乱)。
     不标已读会让同一条回复每轮重读 → 但 record_dm_inbound 有"1小时同内容 no-op"去重 +
     DB 层 dm_inbound_is_noise 中央闸兜底, 不会重复写。
  ③ VL 只该留作【风控弹窗检测】(滑块/人脸/字符码, DOM 接不了); 本读侧脚本不碰发送、不碰 modal。

安全闸: conversation-item=0(DOM 没读到会话/面板没开) → 【弃权 + 告警】, 绝不当"全员没回复"。

前置(无影): 调试版 Edge 9222(start-edge-debug 等价命令)已登录、点开私信会话列表;
  .env(同目录)有 SUPABASE_URL + 鉴权(SCOPED_JWT+anon 或 SERVICE_ROLE_KEY) + AKKE_ACCOUNT_ID。
依赖: py -m pip install playwright (attach 现成 Edge, 不需 playwright install)。
用法:
  py douyin_dm_web_capture_dom.py            # 默认 dry-run: 只打印判定, 不写库
  py douyin_dm_web_capture_dom.py --commit   # 真写回 record_dm_inbound
"""
from __future__ import annotations

import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("✗ 缺 playwright。先跑: py -m pip install playwright")

# 判回复过滤链 + 写回 RPC + 本号名防御, 全部复用 VL 版/UIA 版已验证逻辑(DRY, 不重写)。
from douyin_inbox_uia import load_sent_dms, match_name, SYS_NOTICE, _is_noise_preview, _http, alert_ambiguous_nickname, alert_backfill_suspect
from douyin_dm_web_capture import is_relevant, we_sent_last, _strip_ts, _norm, _rpc, _SELF_NAME

CDP = "http://127.0.0.1:9222"
# 写库开关: 命令行 --commit(独立跑) 或 env AKKE_WEB_DM_DOM_COMMIT=1(被 douyin_dm_autoreply 编排调用时,
# 编排路没命令行参数 → 走 env)。默认 dry-run, 两者都不设就只打印不写。
import os as _os
COMMIT = ("--commit" in sys.argv) or _os.environ.get("AKKE_WEB_DM_DOM_COMMIT", "").lower() in ("1", "true", "yes")
# 验证用: --test-nick=<昵称> 把该测试号【强制当成已 DM 客户】(合成 hit), 好在不真给它发 opener
# 的情况下验完 match 之后的整条判回复链。合成 conv 写 TEST → 即便 --commit 也不落库。
TEST_NICK = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--test-nick=")), "")


# 事件触发起草: record_dm_inbound 写完后立刻打 Fly 中转 → Vercel /api/dm-reply/draft-one,
# 去掉等 */5 cron 那段 0-5min 调度延迟(端到端 ~3-7min → ~15-40s)。
# 云电脑(国内 IP)连不上 Vercel(大陆不可达) → AKKE_DM_DRAFT_ENDPOINT 指 Fly 中转(fly.dev)。
# 幂等: 同 trigger_message_id 已起草 → 端点回 not-awaiting no-op; 故每轮对老红点重复触发也安全。
# env 未配则静默跳过 → 退回 */5 cron 起草(不破坏既有部署)。
def _trigger_draft(conversation_id: str) -> None:
    endpoint = _os.environ.get("AKKE_DM_DRAFT_ENDPOINT")
    secret = _os.environ.get("AKKE_DM_DRAFT_SECRET")
    if not endpoint or not secret:
        return
    import json
    import urllib.request

    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps({"conversation_id": conversation_id}).encode(),
            headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"       ↳ [draft] 触发起草 HTTP {resp.status}")
    except Exception as e:
        # 触发失败不致命: inbound 已写库不丢, */5 cron 会兜底补起草。
        print(f"       ↳ [draft] 触发失败({type(e).__name__}, cron 兜底): {e}")

# 每个会话项一次 evaluate 取全: 行文本 + 未读红点 badge。
# 红点检测【不押 hash class】: 先试 class 锚, 再兜底【按计算样式找抖音红(~#FE2C55)的小元素】——
# 这是 VL 版"红点像素检测"的 DOM 等价(更准, 不用截图)。运营确认: 新回复才有红点 → 红点是权威闸。
_EXTRACT = r"""
el => {
  const lines = (el.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  let badge = !!el.querySelector('[class*="badge" i],[class*="unread" i],[class*="dot" i],[data-e2e*="badge" i]');
  let badgeText = '';
  if (!badge) {
    for (const n of el.querySelectorAll('*')) {
      const bg = getComputedStyle(n).backgroundColor || '';
      const m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
      if (!m) continue;
      const r = +m[1], g = +m[2], b = +m[3];
      // 抖音未读红 ~ rgb(254,44,85): 红高、绿低、蓝中。排红字红图只认【小圆点/数字徽标】。
      if (r > 200 && g < 95 && b > 40 && b < 125) {
        const rc = n.getBoundingClientRect();
        if (rc.width > 0 && rc.width < 42 && rc.height > 0 && rc.height < 30) {
          badge = true; badgeText = (n.innerText || '').trim().slice(0, 4); break;
        }
      }
    }
  }
  const img = el.querySelector('img');
  return { lines, badge, badgeText, avatar: img ? (img.getAttribute('src')||'').slice(0,60) : '' };
}
"""

# ===== 不依赖红点的兜底对账(7/7-7/10 核查 P2: 红点被运营点掉即永久漏检, 窗口内 4 例) =====
# 每 AKKE_DM_FULLSCAN_INTERVAL_S(默认 3600s) 一轮, 对【无红点】的会话行也跑同一条判回复
# 过滤链; 通过后再加两道 DB 守卫(红点权威性没了, 必须补): ① 预览匹配该会话近 5 条 messages
# 任意一条 → 已录过/是我方消息, 跳过; ② 预览匹配该会话近 3 条草稿 draft → 是我方刚发、DB
# 回写滞后(complete_dm_reply 晚几分钟)的窗口期, 跳过。
# 都不匹配 = 疑似漏检 → 【只推卡人工确认, 不自动补录】: 列表预览分不清方向, 运营 GUI
# 手动回的那条不进 DB、守卫看不见, 自动补录会把我方手打内容错当客户消息 → 管线自动回
# 一条(自言自语误发)。红点只客户消息才有、全量扫失去该保证 → 检测自动/确认人工。
# 时间戳台账在本地(_fullscan.stamp), 只在 COMMIT 时盖。
_FULLSCAN_ON = _os.environ.get("AKKE_DM_FULLSCAN", "1").lower() in ("1", "true", "yes")
_FULLSCAN_INTERVAL_S = int(_os.environ.get("AKKE_DM_FULLSCAN_INTERVAL_S", "3600"))
_FULLSCAN_STAMP = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "_fullscan.stamp")


def _fullscan_due() -> bool:
    if not _FULLSCAN_ON:
        return False
    try:
        with open(_FULLSCAN_STAMP, encoding="utf-8") as f:
            last = float(f.read().strip() or 0)
    except Exception:
        last = 0.0
    import time as _time
    return _time.time() - last >= _FULLSCAN_INTERVAL_S


def _fullscan_stamp() -> None:
    import time as _time
    try:
        with open(_FULLSCAN_STAMP, "w", encoding="utf-8") as f:
            f.write(str(_time.time()))
    except Exception as e:
        print(f"  [warn] fullscan 盖章失败: {e}")


def _pv_match(a: str, b: str) -> bool:
    """预览与库内文本是否同一条消息: 剥尾部省略号+归一化后, 相等 或 短方≥6字且互为包含
    (抖音列表预览 ~60 字截断; 短回复如「好的」只认全等, 防被长文误包含)。"""
    import re as _re
    a = _re.sub(r"[….．.]+$", "", _norm(a))
    b = _re.sub(r"[….．.]+$", "", _norm(b))
    if not a or not b:
        return False
    if a == b:
        return True
    return min(len(a), len(b)) >= 6 and (a in b or b in a)


def _fullscan_known(conv: str, preview: str) -> bool:
    """兜底支线的 DB 守卫。读失败一律按「已知」处理(宁漏一轮下小时重试, 不错录)。"""
    try:
        msgs = _http("GET", "messages", {
            "select": "content", "conversation_id": f"eq.{conv}",
            "order": "created_at.desc", "limit": "5",
        }) or []
        if any(_pv_match(preview, m.get("content") or "") for m in msgs):
            return True
        drafts = _http("GET", "dm_reply_drafts", {
            "select": "draft", "conversation_id": f"eq.{conv}",
            "order": "created_at.desc", "limit": "3",
        }) or []
        return any(_pv_match(preview, d.get("draft") or "") for d in drafts)
    except Exception as e:
        print(f"       ↳ [兜底] DB 守卫读失败({type(e).__name__}) → 本轮按已知跳过: {e}")
        return True


_TIME_TOKENS = ("昨天", "前天", "刚刚", "今天")


def _is_time_line(s: str) -> bool:
    import re
    s = s.strip().lstrip("·•・ ").strip()
    if any(t in s for t in _TIME_TOKENS):
        return True
    return bool(re.fullmatch(r"(\d{1,2}:\d{2}|\d+\s*(分钟|小时|天)前|周[一二三四五六日天]|\d{1,2}[-/月]\d{1,2}日?)", s))


def _parse_item(d: dict) -> tuple[str, str, bool]:
    """[行文本, badge] → (昵称, 预览, 未读)。昵称=首行; 预览=去昵称+时间后最长一行。"""
    lines = d.get("lines") or []
    nick = lines[0] if lines else ""
    body = [ln for ln in lines[1:] if not _is_time_line(ln)]
    preview = max(body, key=len) if body else ""
    return nick, preview, bool(d.get("badge"))


def _find_page(browser):
    pages = [pg for c in browser.contexts for pg in c.pages]
    dy = [pg for pg in pages if "douyin.com" in (pg.url or "")]
    return dy[0] if dy else None


def _ensure_panel(page) -> int:
    """返回 conversation-item 数。为 0 时尝试 DOM 点私信入口开浮层再数一次。"""
    n = page.locator('[data-e2e="conversation-item"]').count()
    if n == 0:
        try:
            entry = page.locator('[data-e2e="im-entry"]')
            if entry.count():
                entry.first.click(timeout=3000)
                page.wait_for_timeout(2500)
                n = page.locator('[data-e2e="conversation-item"]').count()
        except Exception as e:
            print(f"  [warn] 尝试开私信浮层失败: {e}")
    return n


def capture_dom():
    print(f"=== web capture[DOM]{'  (DRY-RUN, 不写库)' if not COMMIT else '  (COMMIT 写回)'} ===")
    by_name = load_sent_dms()
    if not by_name:
        print("  [warn] by_name 为空 → 检查 AKKE_ACCOUNT_ID / DB 鉴权; 本轮不读")
        return
    print(f"  by_name {len(by_name)} 人")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP)
        except Exception as e:
            print(f"  ❌ 连不上调试版 Edge {CDP}: {e} —— 弃权(不当全员没回复)")
            return
        page = _find_page(browser)
        if not page:
            print("  ❌ 没找到 douyin.com 标签页 —— 弃权")
            return

        n = _ensure_panel(page)
        # 安全闸: 读不到会话项 → 弃权 + 告警, 绝不静默当"全员没回复"。
        if n == 0:
            print("  ❌ conversation-item=0(面板没开/DOM 变了) —— 弃权 + 告警, 不写任何库")
            return
        print(f"  conversation-item {n} 个")

        fullscan = _fullscan_due()
        if fullscan:
            print(f"  [兜底对账] 本轮含全量扫: 无红点行也对账(间隔 {_FULLSCAN_INTERVAL_S}s), 治红点被点掉永久漏")

        items = page.locator('[data-e2e="conversation-item"]')
        seen, inbound, unread_total, would_preview, backfilled = set(), 0, 0, 0, 0
        for i in range(n):
            try:
                d = items.nth(i).evaluate(_EXTRACT)
            except Exception as e:
                print(f"  [warn] #{i} 提取失败: {e}")
                continue
            nick, preview, unread = _parse_item(d)
            preview = _strip_ts(preview)
            if not nick or nick in seen:
                continue
            seen.add(nick)

            # ★ 红点闸(权威信号, 运营确认: 新回复才有红点)。无红点 = 无新回复 → 跳过, 不论预览。
            #   根治"我方第二条消息(opener 之外)被 we_sent_last 漏判成客户回复"的假阳(小艳子🍭)。
            #   例外: 兜底对账轮(fullscan)放无红点行过闸, 走下方兜底支线(同过滤链 + 两道 DB 守卫)。
            if not unread:
                if not fullscan:
                    # dry-run 透明: 统计"纯预览逻辑下本会被误报"的条数, 好看出红点闸拦下多少假阳。
                    if not COMMIT:
                        h = match_name(nick, by_name)
                        if h and not we_sent_last(preview, h.get("sent", "")) and not any(t in preview for t in SYS_NOTICE) and not _is_noise_preview(preview):
                            would_preview += 1
                    continue
            else:
                unread_total += 1
                if not COMMIT:
                    print(f"  [红点] {nick}: {preview[:26]}")  # dry-run: 每个红点会话都现身, 看它被怎么处置

            # 本号自己的名(头像字误读, DOM 下基本不会但保留防御) → 不计。
            if _SELF_NAME and (_norm(nick) == _SELF_NAME or _SELF_NAME.startswith(_norm(nick)) or _norm(nick).startswith(_SELF_NAME)):
                continue

            hit = match_name(nick, by_name)
            if not hit and TEST_NICK and _norm(nick) == _norm(TEST_NICK):
                print(f"       ↳ [test] 强制把 {nick!r} 当已 DM 客户, 验后续链(合成 hit, 不落库)")
                hit = {"name": nick, "sent": "", "conversation_id": "TEST"}
            if not hit:
                if not COMMIT and unread:
                    print(f"       ↳ 跳过: 不在已发记录(群/陌生人, 非我们 DM 的客户)")
                continue  # 不在本账号已发记录 = 非我们 DM 的人(群/陌生)
            # 同昵称歧义(7/9 蛟河平姐挂错山西平姐会话): 不自动挂靠, 推 Lark 转人工。
            if hit.get("ambiguous"):
                print(f"  [同昵称歧义] {nick}: 本号下 {hit.get('conv_count', 2)} 个同名会话 → 不自动挂靠, 转人工")
                if COMMIT:
                    alert_ambiguous_nickname(nick, hit.get("conv_count", 2))
                continue
            conv = hit.get("conversation_id")
            if not conv:
                continue
            # 兜底对账支线(无红点行, 仅 fullscan 轮到这): 同链静默判定 + 两道 DB 守卫,
            # 都过 = 疑似漏检 → 推卡人工确认(不自动补录, 防把运营手打回复错当客户消息误发)。
            if not unread:
                if we_sent_last(preview, hit.get("sent", "")) or any(t in preview for t in SYS_NOTICE) \
                        or _is_noise_preview(preview) or (preview.startswith("[") and preview.endswith("]")) \
                        or not is_relevant(preview):
                    continue
                if conv == "TEST" or _fullscan_known(conv, preview):
                    continue
                print(f"  [兜底疑似{'·DRY' if not COMMIT else ''}] {nick}: {preview[:34]} (不在系统里 → 推卡人工确认, 不自动补录)")
                if COMMIT:
                    alert_backfill_suspect(nick, preview)
                backfilled += 1
                continue
            # 判回复过滤链(与 VL 版同序): 我方回声 → 系统提示 → 噪声行 → 纯表情 → spam
            if we_sent_last(preview, hit.get("sent", "")):
                print(f"  [no_reply] {nick}: 预览=我方 opener")
                continue
            if any(t in preview for t in SYS_NOTICE):
                print(f"  [no_reply] {nick}: 系统提示")
                continue
            if _is_noise_preview(preview):
                print(f"  [no_reply] {nick}: 纯时间戳/状态行")
                continue
            if preview.startswith("[") and preview.endswith("]"):
                continue
            if not is_relevant(preview):
                print(f"  [noise] {nick}: spam → {preview[:18]}")
                continue
            # 命中真回复
            print(f"  [inbound{'·DRY' if not COMMIT else ''}] {nick}: {preview[:34]}")
            if not COMMIT:
                # 诊断: 打印实际比对的 opener + 匹配到的库内昵称, 判断假阳性根因
                # (match_name 匹配错人 vs opener≠最后一条我方消息)。
                print(f"       ↳ 诊断: match到库内name={hit.get('name','?')!r} | 比对的opener={str(hit.get('sent',''))[:42]!r}")
            if COMMIT and conv == "TEST":
                print(f"       ↳ [test] 合成会话, 跳过真写库(链路验证用)")
                inbound += 1
            elif COMMIT:
                try:
                    _rpc("record_dm_inbound", {"p_conversation_id": conv, "p_content": preview})
                    inbound += 1
                    _trigger_draft(conv)  # 事件触发起草(去掉等 */5 cron 的 0-5min 调度延迟)
                except Exception as e:
                    print(f"  [err] record_dm_inbound {nick}: {e}")
            else:
                inbound += 1
        if fullscan and COMMIT:
            _fullscan_stamp()
        print(f"=== 完成: {'(DRY)' if not COMMIT else ''} 命中客户回复 {inbound} 条 / "
              f"有红点 {unread_total} 个 / 扫 {len(seen)} 人"
              f"{f' / 兜底疑似 {backfilled} 条(已推卡待人工确认)' if fullscan else ''} ===")
        if not COMMIT and would_preview:
            print(f"  (红点闸拦下 {would_preview} 条'纯预览逻辑会误报'的——多半是我方多发的消息, 如小艳子🍭)")
        if not COMMIT and unread_total == 0:
            print("  (本轮 0 红点 → 0 回复, 与'新回复才有红点'一致。"
                  "等真有红点回复时再跑一次, 验证红点检测能抓到、过滤链判得对)")


if __name__ == "__main__":
    capture_dom()
