# -*- coding: utf-8 -*-
"""douyin_dm_web_send_dom — 【DOM 发私信】(隐形 Edge + CDP, 零 pyautogui / 零坐标 / 零校准)。

读侧(douyin_dm_web_capture_dom)已上线; 本模块把【发送】也 DOM 化。覆盖两种:
  A 回复老客户 / B 首触新 lead —— 用 sec_uid【进对方主页→点「私信」开聊天框】这条【通用】路径
  (不管有没有老会话都能开); 没 sec_uid 时回退【会话列表按昵称点击】。

锚点(reference_douyin_web_dm_dom_anchors + 2026-07-03 关注/点赞实测):
  进会话(通用): goto /user/<sec_uid> → 点文本「私信」按钮 → 内嵌聊天框出现
  进会话(兜底): 点 [data-e2e="conversation-item"](按昵称)
  输入框      : [contenteditable](Draft.js), press_sequentially 逐字键入
  发送键      : [class*="e2e-send-msg-btn"](抖音自埋稳定标记, 红圆↑)
  确认发出    : [data-e2e="msg-item-content"] 最后几条气泡含本文案
  关注        : [data-e2e="user-info-follow-btn"](取可见项, 文本=「关注」才点, 已关注跳过)
  点赞        : 点第一个作品 a[href*="/video/"] → 弹层是 feed 滑列(前/当前/后各一个 digg),
                用 [data-e2e="feed-active-video"] 圈定当前视频的 [data-e2e="video-player-digg"];
                完事 Escape 关弹层([data-e2e="modal-video-container"] 消失为准)

首触前「关注+点赞第一个作品」(2026-07-03 补, 镜像 douyin_dm_web_grounded 2026-06-17 语义):
  批量首触(run_batch)默认开; best-effort 失败不阻断私信; AKKE_WEB_DO_FOLLOW=0 / AKKE_WEB_DO_LIKE=0 关。
  回复老客户路径【不】做(重复点=取关/取赞); 单发模式加 --engage 才做。

安全: 默认 dry-run(打开会话+打字+定位发送键但【不点】, 完事清空); 加 --send 才真发。
前置: 调试版 Edge :9222 已登录。
用法:
  py douyin_dm_web_send_dom.py --sec=<sec_uid> --msg="你好"            # B/A 通用, dry-run
  py douyin_dm_web_send_dom.py --sec=<sec_uid> --msg="你好" --send     # 真发
  py douyin_dm_web_send_dom.py --sec=<sec_uid> --msg="你好" --engage   # dry-run + 真关注/点赞(测试)
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

try:
    from dotenv import load_dotenv
    load_dotenv(WORK_DIR / ".env")   # 独立跑时也能读库(被 poll agent 调用时继承其 env, 双保险)
except ImportError:
    pass

DO_FOLLOW = os.environ.get("AKKE_WEB_DO_FOLLOW", "1") == "1"
DO_LIKE = os.environ.get("AKKE_WEB_DO_LIKE", "1") == "1"
# 批中让位(2026-07-03): 首触批发送间隙检测到新审批的自动回复 → 剩余行写 aborted 回池、终止本批,
# 让 poll agent 下一轮先发回复(自动回复绝对优先)。AKKE_DM_YIELD_TO_REPLY=0 关。
YIELD_TO_REPLY = os.environ.get("AKKE_DM_YIELD_TO_REPLY", "1") == "1"


def _pending_replies() -> int:
    """本号待发已审批自动回复条数(让位闸)。查询失败当 0——别让网络抖动腰斩正常批。"""
    import json as _json
    import urllib.request as _rq
    base = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    scoped = os.environ.get("SUPABASE_SCOPED_JWT")
    anon = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    service = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    account = os.environ.get("AKKE_ACCOUNT_ID")
    apikey, bearer = (anon, scoped) if (scoped and anon) else (service, service)
    if not (base and apikey and account):
        return 0
    try:
        req = _rq.Request(
            f"{base}/rest/v1/dm_reply_drafts?select=id&status=eq.approved&account_id=eq.{account}&limit=5",
            headers={"apikey": apikey, "Authorization": f"Bearer {bearer}"})
        with _rq.urlopen(req, timeout=6) as resp:
            return len(_json.loads(resp.read().decode() or "[]"))
    except Exception:
        return 0


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
    if not m:
        return False
    # 正向: 本文案整体出现在某气泡里(真发出的常态)。
    # 反向: 抖音偶把长文案拆成多段气泡, 只当"气泡是本文案的近乎完整片段"才算 —— 门槛从
    #   ≥4 收紧到 max(8, 60%*len), 堵住"任意 ≥4 字旧气泡恰是本文案子串"的假阳(2026-07-07 文哥号误报)。
    thr = max(8, int(len(m) * 0.6))
    return any((m in nt) or (nt in m and len(nt) >= thr) for nt in (_norm(t) for t in texts))


def _send_failed_marker(page) -> bool:
    """网页版把发送失败的消息标红字「发送失败」/「重新发送」(风控软丢/账号级限流最快可视信号,
    见 feedback_douyin_dm_status_code_silent_drop)。扫私信浮层里【整条文本恰为该词】的叶子节点,
    不扫 msg-item-content 正文, 免客户正文含该词误伤。命中即判发送失败。best-effort: 抖音若换渲染
    则不触发(无副作用), 主防线是发出后的气泡持久性复核。"""
    try:
        return bool(page.evaluate(
            "() => { const r = /^(发送失败|重新发送)$/;"
            " const root = document.querySelector('[data-e2e=\"im-dialog\"]') || document.body;"
            " return [...root.querySelectorAll('*')].some("
            "   e => e.children.length===0 && r.test((e.innerText||'').trim())); }"))
    except Exception:
        return False


def _follow_profile(page) -> str:
    """主页点「关注」([data-e2e="user-info-follow-btn"], 有隐藏副本, 只点可见那个)。
    文本非「关注/回关」(已关注/互相关注/请求中)一律跳过——再点会变成取关。best-effort 不抛。"""
    try:
        btns = page.locator('[data-e2e="user-info-follow-btn"]')
        for i in range(btns.count()):
            b = btns.nth(i)
            try:
                if not b.is_visible():
                    continue
                t = _norm(b.inner_text(timeout=1000))
                if t in ("关注", "回关"):
                    b.click(timeout=3000)
                    page.wait_for_timeout(900)
                    return "followed"
                return f"skip({t[:6]})"
            except Exception:
                continue
        return "no_btn"
    except Exception as e:
        return f"error({str(e)[:40]})"


def _like_first_work(page) -> str:
    """点开主页第一个作品弹层 → 点赞当前视频 → Escape 关弹层。
    弹层是 feed 滑列, 前/当前/后视频各有一个 video-player-digg → 必须用 feed-active-video 圈定,
    圈不到就退「在视口内的那个」。无已赞检测 → 只在首触路径跑一次, 别对同一人重复跑。"""
    modal = '[data-e2e="modal-video-container"]'
    status = "no_works"
    try:
        works = page.locator('a[href*="/video/"]:visible')
        if works.count() == 0:
            return "no_works"
        works.first.click(timeout=3000)
        page.wait_for_timeout(3500)
        cand = page.locator('[data-e2e="feed-active-video"] [data-e2e="video-player-digg"]')
        target = cand.first if cand.count() else None
        if target is None:
            vh = (page.viewport_size or {}).get("height", 900)
            all_d = page.locator('[data-e2e="video-player-digg"]')
            for i in range(all_d.count()):
                bb = all_d.nth(i).bounding_box()
                if bb and 0 <= bb["y"] <= vh:
                    target = all_d.nth(i)
                    break
        if target is None:
            status = "no_digg"
        else:
            target.click(timeout=3000)
            page.wait_for_timeout(900)
            status = "liked"
    except Exception as e:
        status = f"error({str(e)[:40]})"
    # 无论成败都要把弹层关掉, 不然挡住「私信」按钮
    try:
        for _ in range(3):
            if page.locator(modal).count() == 0:
                break
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
    except Exception:
        pass
    return status


def _engage_profile(page) -> None:
    """首触前关注+点赞(镜像 web_grounded 语义: 全 best-effort, 失败不阻断后面的私信)。"""
    f = _follow_profile(page) if DO_FOLLOW else "off"
    lk = _like_first_work(page) if DO_LIKE else "off"
    print(f"    [engage] follow={f} like={lk}")


def open_via_profile(page, sec_uid: str, engage: bool = False) -> bool:
    """B/A 通用: 进对方主页 → (首触: 关注+点赞) → 点「私信」开聊天框。返回输入框是否出现。
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
    if engage:
        page.wait_for_timeout(1500)   # 头部按钮/作品格再稳一拍
        _engage_profile(page)
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


def _leave_thread(page) -> None:
    """发完后离开当前会话 thread（导航回抖音首页），让对方后续消息重新产生未读红点。

    根因：send 把会话停在【打开态】→ 对方在已打开的 thread 里接着回 → 抖音判「已读」
    不标红点 → 红点闸的捕获（douyin_dm_web_capture_dom.py）漏读，多轮对话丢消息。
    发完主动离开会话，任何后续入站都落在非打开态会话 → 红点恢复可靠。
    气泡已 _verify_bubble 确认发出（服务端已接收），此时导航不影响送达。"""
    try:
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass  # 离开失败不致命：最坏退回原行为（该会话这一轮可能无红点），不影响已发出的消息


def send_dom(page, message: str, commit: bool, sec_uid: str = "", nick: str = "", engage: bool = False) -> str:
    """发 message。sec_uid 优先(通用), 否则 nick 列表兜底。commit=False 只打字不发。
    engage=True(首触批量)则进主页后先关注+点赞再开私信; 回复路径保持 False。
    返回: sent / unverified / dry_ok / no_conversation / no_input / no_send_btn / error:*"""
    if not message.strip():
        return "error:empty_message"
    opened = False
    if sec_uid:
        opened = open_via_profile(page, sec_uid, engage=engage)
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
    seen = False
    for _ in range(7):
        page.wait_for_timeout(1000)
        if _verify_bubble(page, message):
            seen = True
            break
    if not seen:
        _leave_thread(page)
        return "unverified"
    # 见气泡 ≠ 真送达: 网页版对被风控/spam 软丢的消息, 会先乐观渲染气泡再撤掉(或标红「发送失败」)。
    #   2026-07-07 文哥号实测: 日志判 sent 但会话窗口里那条根本不在 → 乐观气泡已被移除。
    #   停 ~2.5s 复核: ① 失败标记命中, 或 ② 气泡已消失 → 判 unverified(回池, 别当已发)。
    page.wait_for_timeout(2500)
    if _send_failed_marker(page) or not _verify_bubble(page, message):
        _leave_thread(page)
        return "unverified"
    _leave_thread(page)  # 发完离开 thread，别停在打开态（否则对方续聊无红点被漏读）
    return "sent"


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
            # 批中让位: 有新审批的自动回复 → 剩余行 aborted 回池(不计配额可重发), 终止本批。
            # 首行不检查(poll agent 进批前已做过让位闸), 从第 2 行起每行发前查一次。
            if YIELD_TO_REPLY and idx > 0 and _pending_replies() > 0:
                remain = rows[idx:]
                print(f"  !! 检测到待发自动回复 → 让位: 剩余 {len(remain)} 条 aborted 回池, 终止本批")
                for r2 in remain:
                    row2 = {k: (r2.get(k) or "") for k in _SENT_LOG_FIELDS[:7]}
                    row2.update(status="aborted", sent_at=datetime.now().isoformat(),
                                _ocr_confidence="", _ocr_seen="")
                    results.append(row2)
                break
            sec = (r.get("_sec_uid") or "").strip()
            msg = r.get("message") or ""
            nick = r.get("nickname") or ""
            if not sec:
                status = "no_secuid"
            else:
                # 首触批量: engage=True 先关注+点赞再发(镜像 PC/web_grounded 版流程)
                status = _map_status(send_dom(page, msg, True, sec_uid=sec, nick=nick, engage=True))
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
    engage = "--engage" in sys.argv
    if (not sec and not nick) or not msg:
        sys.exit('用法: py douyin_dm_web_send_dom.py --sec=<sec_uid>|--nick="昵称" --msg="内容" [--send] [--engage]')

    print(f"=== DOM 发送 {'(真发 --send)' if commit else '(DRY-RUN 不发)'}{' +关注/点赞(--engage)' if engage else ''} ===")
    print(f"  目标: {('sec='+sec[:24]+'…') if sec else ('nick='+nick)}\n  内容: {msg!r}")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        pages = [pg for c in browser.contexts for pg in c.pages]
        dy = [pg for pg in pages if "douyin.com" in (pg.url or "")]
        if not dy:
            sys.exit("✗ 没找到 douyin.com 页(调试版 Edge 开着吗?)")
        status = send_dom(dy[0], msg, commit, sec_uid=sec, nick=nick, engage=engage)
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
