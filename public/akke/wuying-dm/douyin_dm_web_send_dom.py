# -*- coding: utf-8 -*-
"""douyin_dm_web_send_dom — 【DOM 发私信】(隐形 Edge + CDP, 零 pyautogui / 零坐标 / 零校准)。

读侧(douyin_dm_web_capture_dom)已上线; 本模块把【发送】也 DOM 化。覆盖两种:
  A 回复老客户 / B 首触新 lead —— 用 sec_uid【进对方主页→点「私信」开聊天框】这条【通用】路径
  (不管有没有老会话都能开); 没 sec_uid 时回退【会话列表按昵称点击】。

锚点(reference_douyin_web_dm_dom_anchors):
  进会话(通用): goto /user/<sec_uid> → 点文本「私信」按钮 → 内嵌聊天框出现
  进会话(兜底): 点 [data-e2e="conversation-item"](按昵称)
  输入框      : [contenteditable](Draft.js), press_sequentially 逐字键入
  发送键      : [class*="e2e-send-msg-btn"](抖音自埋稳定标记, 红圆↑)
  确认发出    : [data-e2e="msg-item-content"] 最后几条气泡含本文案

安全: 默认 dry-run(打开会话+打字+定位发送键但【不点】, 完事清空); 加 --send 才真发。
前置: 隐形 Edge :9222(headless)已登录。
用法:
  py douyin_dm_web_send_dom.py --sec=<sec_uid> --msg="你好"            # B/A 通用, dry-run
  py douyin_dm_web_send_dom.py --sec=<sec_uid> --msg="你好" --send     # 真发
  py douyin_dm_web_send_dom.py --nick="昵称" --msg="你好"              # 兜底: 列表点击(回复老客户)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("✗ 缺 playwright")

CDP = "http://127.0.0.1:9222"
SEND_BTN = '[class*="e2e-send-msg-btn"]'
WORK_DIR = Path(__file__).resolve().parent


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _find_input(page):
    ce = page.locator('[data-e2e="msg-input"] [contenteditable="true"]')
    if ce.count() == 0:
        ce = page.locator('[contenteditable="true"]')
    return ce.first if ce.count() else None


def _clear(page, box):
    try:
        box.click(); page.keyboard.press("Control+A"); page.keyboard.press("Delete"); page.wait_for_timeout(300)
    except Exception:
        pass


def _verify_bubble(page, message: str) -> bool:
    try:
        texts = page.eval_on_selector_all(
            '[data-e2e="msg-item-content"]', "els => els.slice(-8).map(e => (e.innerText||''))")
    except Exception:
        return False
    m = _norm(message)
    return any(m and (m in _norm(t) or (_norm(t) in m and len(_norm(t)) >= 4)) for t in texts)


def open_via_profile(page, sec_uid: str) -> bool:
    """B/A 通用: 进对方主页 → 点「私信」开聊天框。返回输入框是否出现。
    关键(实测): headless 下主页加载慢, 必须【等 networkidle + 多等】再点, 点早了不触发;
    点不出输入框就再等再点(最多 3 次)。"""
    try:
        page.goto(f"https://www.douyin.com/user/{sec_uid}", wait_until="domcontentloaded", timeout=20000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    if _find_input(page):
        return True
    # 等「私信」按钮真出现, 再点; 点完用 wait_for(可见) 等输入框冒出来(别死等固定秒), 重试 6 次。
    try:
        page.get_by_role("button", name="私信").first.wait_for(state="visible", timeout=10000)
    except Exception:
        pass
    for _ in range(6):
        btn = page.get_by_role("button", name="私信")
        clicked = False
        for i in range(btn.count()):
            try:
                el = btn.nth(i)
                if el.is_visible():
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.click(timeout=3000)
                    clicked = True
                    break
            except Exception:
                continue
        if clicked:
            try:
                page.locator('[contenteditable="true"]').first.wait_for(state="visible", timeout=4000)
                return True
            except Exception:
                pass
        page.wait_for_timeout(1500)
    return _find_input(page) is not None


def open_conversation(page, nick: str) -> bool:
    """兜底: 会话列表按昵称点开。"""
    if page.locator('[data-e2e="conversation-item"]').count() == 0:
        try:
            page.locator('[data-e2e="im-entry"]').first.click(timeout=3000); page.wait_for_timeout(2500)
        except Exception:
            pass
    items = page.locator('[data-e2e="conversation-item"]')
    for i in range(items.count()):
        try:
            if _norm(nick) in _norm(items.nth(i).inner_text(timeout=600) or ""):
                items.nth(i).click(); page.wait_for_timeout(2200)
                return _find_input(page) is not None
        except Exception:
            continue
    return False


def send_dom(page, message: str, commit: bool, sec_uid: str = "", nick: str = "") -> str:
    """发 message。sec_uid 优先(通用), 否则 nick 列表兜底。commit=False 只打字不发。
    返回: sent / unverified / dry_ok / no_conversation / no_input / no_send_btn / error:*"""
    if not message.strip():
        return "error:empty_message"
    opened = False
    if sec_uid:
        opened = open_via_profile(page, sec_uid)
        if not opened and nick:
            opened = open_conversation(page, nick)
    elif nick:
        opened = open_conversation(page, nick)
    else:
        return "error:no_target"
    if not opened:
        return "no_conversation"

    box = _find_input(page)
    if box is None:
        return "no_input"
    try:
        box.click(); box.press_sequentially(message, delay=50); page.wait_for_timeout(700)
    except Exception as e:
        return f"error:type/{e}"

    nbtn = page.locator(SEND_BTN).count()
    if not commit:
        _clear(page, box)
        return f"dry_ok(send_btn={nbtn})"
    if nbtn == 0:
        _clear(page, box)
        return "no_send_btn"
    try:
        page.locator(SEND_BTN).first.click(timeout=3000)
    except Exception as e:
        return f"error:click_send/{e}"
    # 发出后气泡渲染有延迟 → 轮询等它出现(最多 ~7s), 别只等一次。
    for _ in range(7):
        page.wait_for_timeout(1000)
        if _verify_bubble(page, message):
            return "sent"
    return "unverified"


# ── 批量契约(被 wuying_poll_agent 当 AKKE_DM_SCRIPT 调用): contacts.csv → sent_log_YYYYMMDD.csv ──
# I/O 与 douyin_dm_web_grounded.py 一致, 故 poll agent 的派单/回读/map_status 全不用改。
_SENT_LOG_FIELDS = ["douyin_id", "nickname", "message", "has_works", "_comment_id",
                    "_sec_uid", "_dispatch_id", "status", "sent_at", "_ocr_confidence", "_ocr_seen"]


def _map_status(raw: str) -> str:
    """send_dom 返回值 → sent_log.status(对齐现有 web 枚举, 详见 wuying_poll_agent.map_status)。"""
    if raw == "sent":
        return "sent"
    if raw == "unverified":
        return "unverified"               # 发了没确认到气泡 → 回池可重发(不计成功)
    if raw in ("no_conversation", "no_input", "no_send_btn"):
        return "dm_panel_failed"          # 开会话/定位失败 → 累计 3 次永久 skip, 不罚账号
    if raw.startswith("error:no_target") or raw == "no_secuid":
        return "no_secuid"
    return raw                             # error:*


def run_batch(csv_path: str) -> int:
    import csv as _csv
    import random
    import time
    from datetime import datetime
    min_iv = int(os.environ.get("AKKE_WEB_DM_MIN_INTERVAL", "20"))
    max_iv = int(os.environ.get("AKKE_WEB_DM_MAX_INTERVAL", "45"))
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(_csv.DictReader(f))
    print(f"=== DOM 批量发送: {len(rows)} 条 (间隔 {min_iv}-{max_iv}s) ===")
    out = WORK_DIR / f"sent_log_{datetime.now():%Y%m%d}.csv"
    results = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP)
        except Exception as e:
            print(f"✗ 连不上隐形 Edge {CDP}: {e} —— 全批不发(不写 sent_log, poll agent 下轮重试)")
            return 1
        pages = [pg for c in browser.contexts for pg in c.pages]
        dy = [pg for pg in pages if "douyin.com" in (pg.url or "")]
        if not dy:
            print("✗ 没找到 douyin.com 页(隐形 Edge 开着吗?) —— 全批不发")
            return 1
        page = dy[0]
        for idx, r in enumerate(rows):
            sec = (r.get("_sec_uid") or "").strip()
            msg = r.get("message") or ""
            nick = r.get("nickname") or ""
            if not sec:
                status = "no_secuid"
            else:
                status = _map_status(send_dom(page, msg, True, sec_uid=sec, nick=nick))
            row = {k: (r.get(k) or "") for k in _SENT_LOG_FIELDS[:7]}
            row.update(status=status, sent_at=datetime.now().isoformat(), _ocr_confidence="", _ocr_seen="")
            results.append(row)
            print(f"  [{status}] {nick}: {msg[:24]}")
            if idx < len(rows) - 1:
                time.sleep(random.randint(min_iv, max_iv))   # 节奏门: 别零延迟连发
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=_SENT_LOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    sent = sum(1 for r in results if r["status"] == "sent")
    print(f"=== 完成: sent {sent}/{len(results)} → {out.name} ===")
    return 0


def main():
    # 批量模式: 传了 contacts.csv(位置参数) → 被 poll agent 当发送脚本调用。
    csv_arg = next((a for a in sys.argv[1:] if a.endswith(".csv") and not a.startswith("--")), "")
    if csv_arg:
        sys.exit(run_batch(csv_arg))

    # 单发模式(测试用)
    sec = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--sec=")), "")
    nick = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--nick=")), "")
    msg = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--msg=")), "")
    commit = "--send" in sys.argv
    if (not sec and not nick) or not msg:
        sys.exit('用法: py douyin_dm_web_send_dom.py --sec=<sec_uid>|--nick="昵称" --msg="内容" [--send]')

    print(f"=== DOM 发送 {'(真发 --send)' if commit else '(DRY-RUN 不发)'} ===")
    print(f"  目标: {('sec='+sec[:24]+'…') if sec else ('nick='+nick)}\n  内容: {msg!r}")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        pages = [pg for c in browser.contexts for pg in c.pages]
        dy = [pg for pg in pages if "douyin.com" in (pg.url or "")]
        if not dy:
            sys.exit("✗ 没找到 douyin.com 页(隐形 Edge 开着吗?)")
        status = send_dom(dy[0], msg, commit, sec_uid=sec, nick=nick)
    print(f"\n→ 结果: {status}")
    if status.startswith("dry_ok"):
        print("  ✅ 开会话+打字+发送键定位都成(没发)。加 --send 真发。")
    elif status == "sent":
        print("  ✅ 已发送且气泡确认。")
    elif status == "unverified":
        print("  ⚠ 点了发送但气泡没读到本文案——去会话肉眼核对是否发出。")
    elif status == "no_conversation":
        print("  ✗ 没打开会话——sec_uid 主页「私信」没点开输入框 / 或 nick 不在列表。")
    else:
        print(f"  ✗ {status}")


if __name__ == "__main__":
    main()
