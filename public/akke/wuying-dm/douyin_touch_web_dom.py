# -*- coding: utf-8 -*-
"""douyin_touch_web_dom — route-B 三连(点赞+评论1+评论2)【浏览器 DOM 版】(隐形 Edge + CDP)。

替代 douyin_comment_grounded.py(抖音 PC 客户端 GUI 版)作 _realtime_touch_consume_wuying
的执行器: 消费端加 --comment-script=douyin_touch_web_dom.py 即切换, CSV/comment_log/
--result-out 契约完全对齐, consume 零改动。

相对 PC 版的关键差异:
  ① 【不搜昵称】: CSV 透传的 _aweme_id 直接拼 https://www.douyin.com/video/<id> 打开,
     URL 唯一锁定目标视频 → 没有搜索撞名、没有身份门误杀(PC 版 no_row/wrong_user 根除)。
  ② 零坐标零截图 AI: 全 DOM 定位(CDP 接管调试版 Edge :9222, 同 send_dom)。
  ③ 与 DM 串行: 每条包 wuying_window_lock.window_turn('rb') —— poll agent 发送批
     (自动回复/一触)置 .dm-want 时本执行器等待 → 优先级 自动回复 > 一触 > route-B。
     (锁在 AKKE_WINDOW_LOCK=1 时生效; 浏览器机上锁住的是「CDP 页面导航权」——
      DM send_dom 与本脚本共用同一个 Edge 标签页, 并发导航会互相打断, 必须串行。)

锚点(评论区候选写在 CANDS, 首跑用 --probe 在真机钉死后可收敛):
  点赞   : [data-e2e="video-player-digg"](视口内第一个可见, 同 send_dom 点赞锚)
  评论框 : [data-e2e="comment-input"] 内 contenteditable(候选链兜底)
  发送   : [data-e2e="comment-publish"] → 按钮文本「发送」→ Enter 兜底
  验证   : 评论列表文本含评论前缀

用法:
  py douyin_touch_web_dom.py contacts.csv --result-out=r.json     # consume 生产调用(真发)
  py douyin_touch_web_dom.py --probe --url=https://www.douyin.com/video/7xxx   # 探锚点(只读)
  py douyin_touch_web_dom.py contacts.csv --dry                   # 全流程走位, 不点赞不发评论
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("✗ 缺 playwright")

import wuying_window_lock as _wl  # DM 优先串行锁(AKKE_WINDOW_LOCK=1 生效, 否则 no-op)

CDP = "http://127.0.0.1:9222"
WORK_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(WORK_DIR / ".env")
except ImportError:
    pass

# 评论区锚点(2026-07-08 文哥机 probe3 实测钉死):
#   输入框【初始不在 DOM】——页面只有「留下你的精彩评论吧」占位(hash class 不可靠, 按文本找
#   叶子可见元素 JS 强点); 点击后挂载 Draft.js 编辑器 .public-DraftEditor-content(与私信
#   输入框同款; 视频页全页唯一 Draft 编辑器, 无歧义)。评论列表锚 [data-e2e="comment-list"]。
CANDS_INPUT = [
    'div.public-DraftEditor-content',
    '[contenteditable="true"]',
]
PLACEHOLDER_TEXT = "留下你的精彩评论吧"
JS_CLICK_PLACEHOLDER = """() => {
  const els = [...document.querySelectorAll('*')].filter(e =>
    e.children.length === 0 && (e.innerText || '') === '留下你的精彩评论吧');
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) { el.click(); return true; }
  }
  return false;
}"""
CANDS_PUBLISH = [
    '[data-e2e="comment-publish"]',
    'button:has-text("发送")',
]
# 发送红圆↑(2026-07-08 文哥机 probe6 实测): 无 class 无 data-e2e 无文本(SPAN>svg>path),
# 只能结构定位——输入框同排(y 中心 ±45px)的小图标组(@/表情/↑), 最右那个=发送。
# probe6 点击后评论即时上墙(SENT)。
JS_ROW_ICONS = """(e, cy) => { let c = e; for (let i=0;i<7;i++) c = c.parentElement || c;
  return [...c.querySelectorAll('*')].filter(x => {
    const r = x.getBoundingClientRect();
    return r.width>4 && r.width<70 && r.height>4 && r.height<70
      && Math.abs((r.y+r.height/2)-cy)<45 && x.children.length<=2;
  }).map(x => { const r=x.getBoundingClientRect(); return {x:Math.round(r.x), w:Math.round(r.width)}; })
  .sort((a,b)=>a.x-b.x); }"""


def _click_send_icon(page, box) -> bool:
    """点输入框同排最右小图标(红圆↑)。probe6 实测路径。"""
    try:
        bb = box.bounding_box()
        if not bb:
            return False
        cy = bb["y"] + bb["height"] / 2
        icons = box.evaluate(JS_ROW_ICONS, cy)
        if not icons:
            return False
        tgt = icons[-1]
        page.mouse.click(tgt["x"] + tgt["w"] / 2, cy)
        return True
    except Exception:
        return False
CANDS_ITEM = [
    '[data-e2e="comment-list"]',
]
DIGG = '[data-e2e="video-player-digg"]'

_LOG_FIELDS = ['douyin_id', 'nickname', 'message', 'message2', 'has_works',
               '_comment_id', '_sec_uid', '_dispatch_id',
               'status', 'liked', 'status2', 'sent_at', '_ocr_confidence',
               'like_at', 'comment1_at', 'comment2_at',
               '_aweme_id', '_create_time', '_detected_at']


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _now() -> str:
    return datetime.now().strftime('%H:%M:%S')


def _connect(p):
    browser = p.chromium.connect_over_cdp(CDP)
    page = next((pg for ctx in browser.contexts for pg in ctx.pages
                 if "douyin.com" in (pg.url or "")), None)
    if page is None:
        pages = [pg for ctx in browser.contexts for pg in ctx.pages]
        page = pages[0] if pages else None
    return page


def _first_visible(page, selector):
    loc = page.locator(selector)
    for i in range(min(loc.count(), 6)):
        try:
            if loc.nth(i).is_visible():
                return loc.nth(i)
        except Exception:
            continue
    return None


def _find_input(page):
    for sel in CANDS_INPUT:
        el = _first_visible(page, sel)
        if el is not None:
            return el, sel
    # 输入框未挂载 → JS 强点「留下你的精彩评论吧」占位(playwright click 会被可见性检查卡住)
    try:
        if page.evaluate(JS_CLICK_PLACEHOLDER):
            page.wait_for_timeout(1500)
            for sel in CANDS_INPUT:
                el = _first_visible(page, sel)
                if el is not None:
                    return el, sel
    except Exception:
        pass
    return None, None


def _like(page) -> str:
    try:
        page.wait_for_selector(DIGG, timeout=8000)   # 页面慢时等 digg 挂载(no_digg 治此)
    except Exception:
        pass
    el = _first_visible(page, DIGG)
    if el is None:
        return "no_digg"
    try:
        el.click(timeout=3000)
        page.wait_for_timeout(800)
        return "1"
    except Exception as e:
        return f"err:{str(e)[:30]}"


def _post_comment(page, text: str, commit: bool) -> str:
    """发一条评论。返回 sent / dry_ok / no_input / no_send / unverified / err:*"""
    box, sel = _find_input(page)
    if box is None:
        return "no_input"
    try:
        box.click(timeout=3000)
        page.wait_for_timeout(400)
        box.press_sequentially(text, delay=40)
        page.wait_for_timeout(500)
    except Exception as e:
        return f"err:type/{str(e)[:30]}"
    if not commit:
        # dry: 打印发送键候选命中数(真发前观察锚点), 然后清空退出
        for psel in CANDS_PUBLISH:
            try:
                print(f"      [dry] publish [{psel}] -> {page.locator(psel).count()}")
            except Exception:
                pass
        try:
            page.keyboard.press("Control+A"); page.keyboard.press("Delete")
        except Exception:
            pass
        return "dry_ok"
    sent_via = None
    for psel in CANDS_PUBLISH:
        btn = _first_visible(page, psel)
        if btn is not None:
            try:
                btn.click(timeout=3000)
                sent_via = psel
                break
            except Exception:
                continue
    if sent_via is None:
        # 主路径(probe6 实测): 红圆↑无任何稳定属性 → 结构定位同排最右图标
        if _click_send_icon(page, box):
            sent_via = "row-icon"
        else:
            return "no_send"
    # 验证: 评论区出现本文案前缀(截 12 字, 防长文案被折叠截断)
    # 列表显示可能延迟(probe4 实测: 发成功但立刻查是"暂无评论") → 窗口放宽 10s;
    # 仍查不到返回 unverified —— DB 端(#788)对 unverified 不自动重试、转人工核对, 不会重发轰炸。
    frag = _norm(text)[:12]
    for _ in range(10):
        page.wait_for_timeout(1000)
        for isel in CANDS_ITEM:
            try:
                body = page.locator(isel).all_inner_texts()
            except Exception:
                continue
            if any(frag and frag in _norm(t) for t in body):
                return "sent"
    return "unverified"


def probe(url: str) -> int:
    """只读探锚点: 打开视频页, 打印各候选命中数, 不点赞不评论。真机首跑用。"""
    with sync_playwright() as p:
        page = _connect(p)
        if page is None:
            print("✗ 连不上 Edge / 无标签页"); return 1
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        print(f"=== probe {url}")
        print(f"  digg [{DIGG}] -> {page.locator(DIGG).count()}")
        for sel in CANDS_INPUT:
            print(f"  input [{sel}] -> {page.locator(sel).count()}")
        for sel in CANDS_PUBLISH:
            try:
                print(f"  publish [{sel}] -> {page.locator(sel).count()}")
            except Exception as e:
                print(f"  publish [{sel}] -> err {e}")
        for sel in CANDS_ITEM:
            print(f"  item [{sel}] -> {page.locator(sel).count()}")
        return 0


def run(csv_path: str, result_out: str | None, commit: bool) -> int:
    with open(csv_path, encoding="utf-8-sig") as f:
        contacts = list(csv.DictReader(f))
    if not contacts:
        print("CSV 空"); return 1
    log_path = WORK_DIR / f"comment_log_{datetime.now():%Y%m%d}.csv"
    results = {}
    first = not log_path.exists()
    rc_all = 0
    with sync_playwright() as p, open(log_path, "a", encoding="utf-8-sig", newline="") as lf:
        w = csv.DictWriter(lf, fieldnames=_LOG_FIELDS, extrasaction="ignore")
        if first:
            w.writeheader()
        page = _connect(p)
        if page is None:
            print("✗ 连不上调试版 Edge —— 全批不发(consume 下轮重试)"); return 1
        for c in contacts:
            aweme = (c.get("_aweme_id") or "").strip()
            nick = c.get("nickname") or "?"
            row = dict(c)
            row.update({"status": "", "liked": "", "status2": "", "sent_at": "",
                        "_ocr_confidence": "", "like_at": "", "comment1_at": "", "comment2_at": ""})
            if not aweme:
                row["status"] = "failed:no_aweme_id"
                print(f"[{_now()}] ✗ {nick}: 缺 _aweme_id, 无法定位视频")
                w.writerow(row); results[c.get("_sec_uid") or ""] = row; rc_all = 1
                continue
            url = f"https://www.douyin.com/video/{aweme}"
            # DM 优先: .dm-want 在时此处等待; 抢到窗口(=CDP 导航权)才动
            with _wl.window_turn('rb'):
                print(f"[{_now()}] ★ {nick} {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    page.wait_for_timeout(1200)
                except Exception as e:
                    row["status"] = f"failed:goto/{str(e)[:30]}"
                    w.writerow(row); results[c.get("_sec_uid") or ""] = row; rc_all = 1
                    continue
                # ① 点赞(best-effort, 失败不阻断评论)
                if commit:
                    row["liked"] = _like(page)
                    row["like_at"] = datetime.now().isoformat(timespec="seconds")
                else:
                    row["liked"] = "dry"
                print(f"    like={row['liked']}")
                # ② 评论1
                m1 = (c.get("message") or "").strip()
                row["status"] = _post_comment(page, m1, commit) if m1 else "failed:empty_message"
                row["comment1_at"] = datetime.now().isoformat(timespec="seconds")
                print(f"    评论1={row['status']} {m1[:24]}")
                # ③ 评论2(可空; 评论1 没发出去就不追发)
                m2 = (c.get("message2") or "").strip()
                if m2 and row["status"] in ("sent", "dry_ok"):
                    page.wait_for_timeout(2500)
                    row["status2"] = _post_comment(page, m2, commit)
                    row["comment2_at"] = datetime.now().isoformat(timespec="seconds")
                    print(f"    评论2={row['status2']} {m2[:18]}")
                row["sent_at"] = datetime.now().isoformat(timespec="seconds")
                # 离开视频页回首页: 不停留在目标页(降机器味), 也把页面还给 DM 干净态
                try:
                    page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
            ok = row["status"] in ("sent", "dry_ok")
            if not ok:
                rc_all = 1
            w.writerow(row)
            results[c.get("_sec_uid") or ""] = row
    if result_out:
        with open(result_out, "w", encoding="utf-8") as rf:
            json.dump(results, rf, ensure_ascii=False, indent=1)
    return rc_all


if __name__ == "__main__":
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--probe" in flags:
        url = next((f.split("=", 1)[1] for f in flags if f.startswith("--url=")), "")
        if not url:
            sys.exit("--probe 需要 --url=https://www.douyin.com/video/xxx")
        sys.exit(probe(url))
    if not args:
        print(__doc__); sys.exit(2)
    result_out = next((f.split("=", 1)[1] for f in flags if f.startswith("--result-out=")), None)
    commit = "--dry" not in flags
    sys.exit(run(args[0], result_out, commit))
