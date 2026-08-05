# -*- coding: utf-8 -*-
"""douyin_dm_bubble_capture — 点进会话【读消息气泡】的私信捕获（治「UI 标签挤掉短回复」）。

## 为什么要这个脚本（2026-07-28 一筑事故）

现有 `douyin_dm_web_capture_dom.py` 从**会话列表行**取文本：

    preview = max(body, key=len)      # 去昵称/时间行后，取最长的一行

这个「最长行」启发式在列表行里同时存在 UI 标签时会取错。实测漏抓（captcha_alerts 台账）：
「黍艾」最新一条读成『置顶』、另有『· 星期五』『对方已确认聊天，可继续发送消息』
『回复较慢，建议联系商家客服』『』(空)。而真实入站消息**中位只有 5 字、62% ≤5 字**
（feedback_dm_backtest_wash_data_and_customers_say_5_chars）——客户回「新房」两个字，
被同一行里的「置顶」「对方已确认聊天，可继续发送消息」直接挤掉。

**红点在场也漏**：一筑那 5 位客户红点亮了 6 小时，快路径每轮都过闸，读到的却始终是 UI 标签
→ 过滤链判「不是客户回复」→ 跳过。inbound 永不入库 → dm-auto-reply 候选条件
「最后一条是客户」永不成立 → 自动回复零触发。这不是红点被点没（运营是**发现 6h 没回复
才点进去截图的**，点开发生在漏抓之后），也不是进程死了（poll agent 心跳一直在跳）。

**根因是「猜」**：列表行预览是渲染给人看的摘要，不是消息内容的可靠表示。
本脚本不猜——**点进会话直接读消息气泡** `[data-e2e="msg-item-content"]`（发送侧
`douyin_dm_web_send_dom.py` 早就在用它做发出确认，锚点已实测稳定）。

## 与红点快路径的关系：时间分层，不是并存打架

点开会话 = 标已读 = 清红点，会毁掉快路径赖以判「有没有新回复」的信号。所以本脚本
**只接手快路径已经错过的会话**：距最后一条我方 sent ≥ `--min-age-min`(默认 30) 分钟
仍没有 inbound 的。0-30min 内归红点快路径，超时未果才点进去——那时红点对我们已无价值。

判据也换了：不再依赖任何易失的 UI 信号，改成**跟库对账**——
「库里这条会话最后一条是我方 ai」+「气泡里最后一条是客户方」+「该文本不等于库里最新
customer 消息」→ 写入。幂等、可重入、重跑无副作用（`record_dm_inbound` 另有 1h 同内容
no-op + DB 中央噪声闸兜底）。

## 方向判定（我方气泡 vs 客户气泡）——三重回退

DOM 锚点清单里没记录方向标记，故实现三层，任一给出结论即采纳，并在日志里标出用了哪层：
  ① DOM 属性/class 含 self|right|mine|out|sender 等方向词（若抖音真有，最可靠）
  ② 几何：气泡中心 x > 消息区中心 → 我方（IM 通用惯例：己方靠右）
  ③ 文本：与本号已发消息（load_sent_dms）互为子串 → 我方
三层全无结论 → 弃权（不写库、打印待人工），**绝不猜**。

首次上线请先跑 dry-run（默认），把 `[方向]` 那几行发回来确认①②哪层可用，再加 --commit。

用法:
  py douyin_dm_bubble_capture.py                     # dry-run，只打印
  py douyin_dm_bubble_capture.py --commit            # 真写 record_dm_inbound
  py douyin_dm_bubble_capture.py --max=5             # 本轮最多处理 5 个会话（默认 8）
  py douyin_dm_bubble_capture.py --min-age-min=15    # 放宽到 15 分钟没回就接手

前置：同 capture_dom —— 调试版 Edge 9222 已登录并点开过私信；.env 有 SUPABASE_URL +
鉴权 + AKKE_ACCOUNT_ID。
"""
from __future__ import annotations

import os
import random
import sys
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("✗ 缺 playwright。先跑: py -m pip install playwright")

from douyin_inbox_uia import _http, _norm, SYS_NOTICE, is_media_preview, _is_noise_preview
from douyin_dm_web_capture import _rpc, _strip_ts

CDP = "http://127.0.0.1:9222"
ACCOUNT_ID = os.environ.get("AKKE_ACCOUNT_ID")

COMMIT = "--commit" in sys.argv
_arg = lambda k, d: next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith(f"--{k}=")), d)
MAX_CONVS = _arg("max", 8)
MIN_AGE_MIN = _arg("min-age-min", 30)
MAX_AGE_H = _arg("max-age-h", 48)
BUBBLE_TAIL = 8          # 每个会话只看最后 N 条气泡（更早的与"有没有新回复"无关）

BUBBLE = '[data-e2e="msg-item-content"]'
CONV_ITEM = '[data-e2e="conversation-item"]'

# 方向词（①层）。抖音若在气泡或其祖先埋了方向语义，这些词最可能出现。
_SELF_HINT = ("self", "mine", "right", "out", "send", "own")
_PEER_HINT = ("other", "left", "in", "recv", "receive", "peer")


def _now_ms() -> float:
    return time.time() * 1000


def _iso_ms(s: str) -> float:
    """PostgREST 时间戳 → epoch ms。容错到秒级即可。"""
    from datetime import datetime

    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp() * 1000
    except Exception:
        return 0.0


def load_awaiting_convs() -> list[dict]:
    """本号「已发出、等回复、快路径已错过」的会话。

    条件（全部在 py 侧算，避免 PostgREST 表达不了「最后一条是谁」）：
      · 会话属本号
      · 最后一条消息 role='ai' 且已 sent（我方在等回复）
      · 距那条 sent 在 [MIN_AGE_MIN 分钟, MAX_AGE_H 小时] 之间
        —— 下界让红点快路径先跑；上界不翻老对话（necro-bump 由 dm-auto-reply 另有 24h 门）
    """
    rows = _http("GET", "messages", {
        "select": "role,content,created_at,sent_at,"
                  "conversations!inner(id,customer_name,douyin_user_id,messaging_account_id)",
        "conversations.messaging_account_id": f"eq.{ACCOUNT_ID}",
        "order": "created_at.desc",
        "limit": "3000",
    }) or []

    last: dict[str, dict] = {}
    last_cust: dict[str, str] = {}   # conv_id → 库里最新一条 customer 的原文
    for r in rows:  # 已按 created_at 降序 → 每个会话首见即最后一条
        conv = r.get("conversations") or {}
        cid = conv.get("id")
        if not cid:
            continue
        if r.get("role") == "customer" and cid not in last_cust:
            last_cust[cid] = r.get("content") or ""
        if cid in last:
            continue
        last[cid] = {
            "conv_id": cid,
            "name": conv.get("customer_name") or "",
            "uid": conv.get("douyin_user_id") or "",
            "role": r.get("role"),
            "content": r.get("content") or "",
            "at": _iso_ms(r.get("sent_at") or r.get("created_at") or ""),
        }
    for cid, v in last.items():
        v["last_customer"] = last_cust.get(cid, "")

    now = _now_ms()
    lo, hi = MIN_AGE_MIN * 60_000, MAX_AGE_H * 3_600_000
    out = [v for v in last.values() if v["role"] == "ai" and lo <= (now - v["at"]) <= hi and v["name"]]
    out.sort(key=lambda v: v["at"])  # 等最久的先处理
    return out


def read_bubbles(page) -> list[dict]:
    """当前打开的会话里，最后 BUBBLE_TAIL 条气泡：文本 + 几何 + 方向线索。"""
    return page.evaluate(
        """(sel) => {
        const els = Array.from(document.querySelectorAll(sel));
        const tail = els.slice(-%d);
        const out = [];
        for (const el of tail) {
          const r = el.getBoundingClientRect();
          // 方向线索：自身 + 最多 4 层祖先的 class/data-* 拼起来，供 py 侧找方向词
          let hint = '', n = el, depth = 0;
          while (n && depth < 5) {
            hint += ' ' + (n.className && n.className.baseVal !== undefined ? n.className.baseVal : (n.className || ''));
            for (const a of (n.attributes || [])) if (a.name.startsWith('data-')) hint += ' ' + a.name + '=' + a.value;
            n = n.parentElement; depth++;
          }
          out.push({ text: (el.innerText || '').trim(), cx: r.left + r.width / 2, w: r.width, hint: hint.toLowerCase().slice(0, 400) });
        }
        // 消息区中心：取所有气泡的最左最右算包络中心，比 window 宽度稳（浮层不占满屏）
        const xs = els.map(e => e.getBoundingClientRect());
        const lo = Math.min(...xs.map(r => r.left)), hi = Math.max(...xs.map(r => r.right));
        return { bubbles: out, mid: (lo + hi) / 2 };
        }"""
        % BUBBLE_TAIL,
        BUBBLE,
    )


def judge_side(b: dict, mid: float, own_texts: list[str]) -> tuple[str, str]:
    """→ ('self'|'peer'|'unknown', 用了哪层)。三层全无结论就 unknown，绝不猜。"""
    h = b.get("hint") or ""
    self_hit = any(w in h for w in _SELF_HINT)
    peer_hit = any(w in h for w in _PEER_HINT)
    if self_hit != peer_hit:  # 只有单侧命中才算数（两边都命中=噪声词，弃用①）
        return ("self" if self_hit else "peer", "dom-hint")

    cx = b.get("cx")
    if cx is not None and mid and abs(cx - mid) > max(8.0, (b.get("w") or 0) * 0.05):
        return ("self" if cx > mid else "peer", "geometry")

    t = _norm(b.get("text") or "")
    if len(t) >= 8:
        for o in own_texts:
            n = _norm(o)
            if len(n) >= 8 and (n in t or t in n):
                return ("self", "text-match")

    return ("unknown", "none")


def is_capturable(text: str) -> tuple[bool, str]:
    """能不能作为客户 inbound 入库。媒体占位放行（#840 白名单），系统提示/噪声不放行。"""
    t = (text or "").strip()
    if not t:
        return False, "空"
    if is_media_preview(t):
        return True, "媒体占位(白名单放行)"
    if any(k in t for k in SYS_NOTICE):
        return False, "系统提示"
    if _is_noise_preview(t):
        return False, "噪声(时间戳/在线行)"
    return True, "ok"


def open_conv(page, nick: str) -> bool:
    """按昵称在收件箱列表里点开会话。点开=标已读，调用前请确认这条已被快路径错过。"""
    if page.locator(CONV_ITEM).count() == 0:
        try:
            page.locator('[data-e2e="im-entry"]').first.click(timeout=3000)
            page.wait_for_timeout(2500)
        except Exception:
            return False
    items = page.locator(CONV_ITEM)
    target = _norm(nick)
    for i in range(min(items.count(), 60)):
        try:
            lines = (items.nth(i).inner_text() or "").split("\n")
            if lines and _norm(lines[0]) == target:
                items.nth(i).click()
                page.wait_for_timeout(2200)
                return page.locator(BUBBLE).count() > 0
        except Exception:
            continue
    return False


def main() -> None:
    if not ACCOUNT_ID:
        sys.exit("✗ .env 缺 AKKE_ACCOUNT_ID")
    convs = load_awaiting_convs()
    print(f"=== 气泡捕获 {'(COMMIT 写库)' if COMMIT else '(dry-run 不写库)'} · "
          f"等回复 {MIN_AGE_MIN}min~{MAX_AGE_H}h 的会话 {len(convs)} 个，本轮处理 {min(len(convs), MAX_CONVS)} 个 ===")
    if not convs:
        return

    own_texts = [c["content"] for c in convs][:200]
    done = fresh = 0

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        pages = [pg for c in browser.contexts for pg in c.pages]
        page = next((pg for pg in pages if "douyin.com" in (pg.url or "")), None)
        if page is None:
            sys.exit("✗ 没找到 douyin 页 —— Edge 没起或没开抖音")

        for c in convs[:MAX_CONVS]:
            done += 1
            waited_h = (_now_ms() - c["at"]) / 3_600_000
            print(f"\n[{done}] 「{c['name']}」 已等 {waited_h:.1f}h  conv={c['conv_id'][:8]}")
            if not open_conv(page, c["name"]):
                print("     ✗ 没点开会话（昵称在列表里找不到 / 气泡未渲染）→ 跳过")
                continue

            data = read_bubbles(page)
            bubbles, mid = data.get("bubbles") or [], data.get("mid") or 0
            if not bubbles:
                print("     ✗ 读到 0 条气泡 → 跳过")
                continue

            last_peer = None
            for b in bubbles:
                side, how = judge_side(b, mid, own_texts)
                print(f"     [方向] {side:<7} via {how:<10} x={b.get('cx'):.0f} mid={mid:.0f}  {b.get('text','')[:40]!r}")
                if side == "peer":
                    last_peer = b
            if last_peer is None:
                print("     ⚠ 最后 %d 条气泡里没有一条判定为客户方 → 弃权（不写库）" % len(bubbles))
                continue

            text = _strip_ts((last_peer.get("text") or "").strip())
            ok, why = is_capturable(text)
            if not ok:
                print(f"     ⊘ 最后一条客户气泡不可入库（{why}）: {text[:40]!r}")
                continue
            # 去重闸(2026-08-05 补)：docstring 一直宣称有「文本≠库里最新 customer 消息」
            # 这道守卫，实际从没实现过。缺了它会重复入库旧消息——
            # 「客户回过(已入库) → 我方又发了一条 → 客户没再说话」的会话完全满足
            # load_awaiting_convs 的条件(库里最后一条是 ai)，气泡尾部那条 peer 正是
            # 那句已入库的老回复，于是每轮都重记一次。RPC record_dm_inbound 也拦不住:
            # 它的幂等分支要求「会话最后一条是 customer 且内容相同」，而这里最后一条是
            # ai → 直接 INSERT。运营在别处手动回复(不进库)会让这个场景更常见。
            if _norm(text) and _norm(text) == _norm(c.get("last_customer") or ""):
                print(f"     ⊘ 与库里最新客户消息相同，已入库过 → 跳过: {text[:30]!r}")
                continue

            fresh += 1
            print(f"     ✅ 客户最新: {text[:60]!r}  ({why})")
            if COMMIT:
                try:
                    mid_ = _rpc("record_dm_inbound", {"p_conversation_id": c["conv_id"], "p_content": text})
                    print(f"        ↳ record_dm_inbound → {mid_}")
                except Exception as e:
                    print(f"        ↳ ✗ 写库失败({type(e).__name__}): {e}")
            else:
                print("        ↳ dry-run，未写库（加 --commit 才写）")

            time.sleep(random.uniform(1.5, 3.5))

    print(f"\n=== 完成: 处理 {done} 个会话，捞到 {fresh} 条客户最新消息 "
          f"{'(已写库)' if COMMIT else '(dry-run)'} ===")


if __name__ == "__main__":
    main()
