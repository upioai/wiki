# -*- coding: utf-8 -*-
"""douyin_dm_autoreply — 无影端 DM 自动回复编排（复用现成脚本，无新 GUI）。

方案：docs/requirements/2026-06-18-dm-auto-reply.md

两段（默认都跑）：
  capture: douyin_inbox_uia 扫私信【会话列表】→ 三道过滤定位"客户回了的会话"
           → 用列表预览(短回复预览=全文) rpc record_dm_inbound 写回 messages(role=customer)。
           ※ 纯读 UIA，不开会话；长回复预览会被截断(~60字)，v1 先用截断版，够短回复用。
  send:    rpc claim_dm_replies 拉本账号已审批(approved)回复 → douyin_dm_reply_inbox
           就地回复(在私信会话列表点对应用户开会话→打字→发送↑→验气泡，**不搜抖音号**)
           → rpc complete_dm_reply 回写 sent/failed(+messages role=ai)。

无新 GUI：读用 inbox_uia(只读)、发用 douyin_dm_reply_inbox(就地回复，复用 grounded 的
send_arrow 定位+type_text)。挂进 wuying_poll_agent 作第 4 条串行支线即可。

前置：抖音 PC 前台最大化 + 停在私信会话列表；输入法英文模式(发送子进程要求)。
env(同 inbox_uia/grounded)：AKKE_ACCOUNT_ID + SUPABASE_URL + 鉴权 + ANTHROPIC/OPENROUTER_API_KEY。
用法：py douyin_dm_autoreply.py            # capture + send
      py douyin_dm_autoreply.py capture    # 只捕获(联调读取这半最稳)
      py douyin_dm_autoreply.py send       # 只发
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

# 复用列表检测(只读 UIA)；load_sent_dms 已带 conversation_id
from douyin_inbox_uia import (  # noqa: F401
    SYS_NOTICE,
    classify,
    collect_nodes,
    collect_texts,
    find_douyin,
    find_im_panel,
    load_sent_dms,
    match_name,
    parse_rows,
    parse_rows_unread,
)

HERE = os.path.dirname(os.path.abspath(__file__))

# 可选：自导航到私信收件箱（自动跑时窗口不在列表）。没量 AKKE_C_DM_INBOX 就跳过 →
# 手动模式（抖音已停在私信列表）行为不变。复用 douyin_dm_grounded 的点击/聚焦原语。
try:
    import time as _time
    from douyin_dm_grounded import focus_douyin as _focus, click_norm as _click, _coord as _co
    _C_DM_INBOX = _co("AKKE_C_DM_INBOX", None)
    # 抖音点开私信只是个空框，必须再点一个会话才会渲染出左侧会话列表(否则 UIA 读到 0 行)。
    # 量了 AKKE_C_DM_FIRST(第一个会话条目位置) 才点这第二下；没量=保持旧行为(只点私信)。
    _C_DM_FIRST = _co("AKKE_C_DM_FIRST", None)
except Exception:
    _focus = _click = None
    _C_DM_INBOX = None
    _C_DM_FIRST = None


def _goto_dm_inbox():
    """点开全局私信收件箱（让左侧会话列表可读）。无坐标=跳过（手动模式）。
    抖音私信面板点开后是空框，再点第一个会话(AKKE_C_DM_FIRST)才撑开会话列表。"""
    if not _C_DM_INBOX or not _focus:
        return
    try:
        _focus()
        _time.sleep(0.8)
        _click(_C_DM_INBOX[0], _C_DM_INBOX[1], wait=1.5, label="DM收件箱")
        # 第二下：点第一个会话撑开列表。只读捕获——点开会话仅标已读、不输入不发送。
        if _C_DM_FIRST:
            _click(_C_DM_FIRST[0], _C_DM_FIRST[1], wait=1.2, label="DM首会话")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 自导航私信收件箱失败（按手动模式继续）: {e}")


ACCOUNT_ID = os.environ.get("AKKE_ACCOUNT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
_SCOPED_JWT = os.environ.get("SUPABASE_SCOPED_JWT")
_ANON = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_ANON_KEY")
_SERVICE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if _SCOPED_JWT and _ANON:
    _APIKEY, _BEARER = _ANON, _SCOPED_JWT
else:
    _APIKEY = _BEARER = _SERVICE

# 第二道：互粉/涨粉 spam（不固定，词集中）；第三道：相关性(含装修词/短确认才算真回复)。
SPAM_KW = ("有效粉", "互粉", "小火花", "互发", "掉粉", "千粉不如", "长按", "转发", "关注后")
DECOR_KW = (
    "装修", "全屋", "定制", "柜", "户型", "几室", "房", "平", "㎡", "报价", "多少钱",
    "材料", "板材", "墙", "改", "水电", "保温", "毛坯", "精装", "厨", "卫", "阳台",
    "好的", "谢谢", "可以", "行", "嗯", "要", "想",  # 短确认/意向词也放行
)


def _rpc(name: str, payload: dict):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/rpc/{name}",
        data=json.dumps(payload).encode(),
        headers={
            "apikey": _APIKEY,
            "Authorization": f"Bearer {_BEARER}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else None


def is_spam(text: str) -> bool:
    return any(k in text for k in SPAM_KW) and not any(k in text for k in DECOR_KW)


def is_relevant(text: str) -> bool:
    # 真客户回复(suspect 且非 spam)一律放行——不强求含装修词：短回复如「能发一下设计图吗」
    # 「四个房间」没关键词但确是回复。无关只靠 spam 词 + classify 的系统消息白名单挡。
    return not is_spam(text)


def capture():
    # web(Edge 网页版)通道：UIA 树与 PC 客户端完全不同(无 imSaasContainerId/浮层易失/徽标混淆)
    # → 改用 VL 视觉读私信列表(douyin_dm_web_capture)。同 send() 的 _dm_script_is_web 惯例。
    if "web" in os.environ.get("AKKE_DM_SCRIPT", "").lower():
        # AKKE_WEB_DM_USE_DOM=1 → DOM 版(connect_over_cdp 读 conversation-item，红点闸，无 VL/OCR/截图)。
        # 默认关=维持 VL 版，零行为变化。DOM 版前置：调试版 Edge(9222)、AKKE_WEB_DM_DOM_COMMIT=1 才写库。
        if os.environ.get("AKKE_WEB_DM_USE_DOM", "").lower() in ("1", "true", "yes"):
            from douyin_dm_web_capture_dom import capture_dom
            return capture_dom()
        from douyin_dm_web_capture import capture as web_capture
        return web_capture()
    _goto_dm_inbox()  # 量了 AKKE_C_DM_INBOX 才点开收件箱；没量=假设已停在列表
    win = find_douyin()
    if win is None:
        print("[X] 没找到抖音窗口（前台最大化、停在私信列表了吗？）")
        return
    by_name = load_sent_dms()
    # 红点门控（默认开）：只抓【有未读徽标】的会话行 = 客户真发了新消息。
    # 状态行/时间戳/"在线"/"关闭会话"天然没未读徽标 → 结构性排除（治 6/21 噪声刷库）。
    # rect 不可靠的机器自动回退老 classify 门（显式失败、别静默漏回复）。可 AKKE_DM_REDDOT_GATE=0 强制老门。
    reddot = os.environ.get("AKKE_DM_REDDOT_GATE", "1").lower() in ("1", "true", "yes")
    nodes = collect_nodes(find_im_panel(win) or win) if reddot else []
    rects_ok = any(rect != (0, 0, 0, 0) for (_ct, _n, rect) in nodes)
    use_reddot = reddot and rects_ok
    if reddot and not rects_ok:
        print("[warn] 红点门控: UIA rect 全不可靠(全0) → 回退老 classify 门，防静默漏回复")
    if use_reddot:
        rows3 = parse_rows_unread(nodes)
        rows = [(nm, pv) for (nm, pv, u) in rows3 if u]
        print(f"=== capture[红点门控]: {len(rows3)} 行 / 未读 {len(rows)} / 已发记录 {len(by_name)} 人 ===")
    else:
        rows = parse_rows(collect_texts(win))
        print(f"=== capture[classify门]: {len(rows)} 行, 已发记录 {len(by_name)} 人 ===")
    for name, preview in rows:
        hit = match_name(name, by_name)
        if not hit:
            continue
        # classify 做噪声过滤：时长气泡(09:45/36:22) / 未读徽标数 / 我方末条气泡指纹 /
        # 系统提示(SYS_NOTICE 含撤回) / 状态行。两种门控都必须跑——红点只证明"有未读徽标"，
        # 不证明 VL/UIA 读到的 preview 是真客户文本：follow-only 会话也有红点，但读出来是
        # 时间戳；我方末条气泡也会被误读成客户回复(2026-06-26 Yara 时间戳 / QQL 我方气泡假阳
        # 根因 = 红点模式当时跳过了 classify)。
        if classify(preview, hit) != "suspect":
            continue  # no_reply：噪声 / 我方末条 / 系统消息
        # 兜底：预览=我们的开场白(你好…刷到你…) → 非客户回复（指纹偶尔对不上时漏判）。
        # "刷到你"是开场白复述客户评论的签名，客户不会这么说；不会误伤客户"你好"开头的真回复。
        if preview.startswith("你好") and "刷到你" in preview:
            print(f"[skip] {name}: 像我们的开场白(你好…刷到你)，非客户回复 → {preview[:20]}")
            continue
        # 纯表情/贴纸(如 [龙舟载福])非文字回复，不抓不回。
        _p = preview.strip()
        if _p.startswith("[") and _p.endswith("]"):
            print(f"[skip] {name}: 纯表情贴纸，非文字回复 → {preview[:20]}")
            continue
        conv = hit.get("conversation_id")
        if not conv:
            print(f"[skip] {name}: 无 conversation_id（load_sent_dms 没带到）")
            continue
        if not is_relevant(preview):
            print(f"[noise] {name}: 非装修相关/spam → {preview[:24]}")
            continue
        try:
            _rpc("record_dm_inbound", {"p_conversation_id": conv, "p_content": preview})
            print(f"[inbound] {name}: {preview[:30]}")
        except Exception as e:  # noqa: BLE001
            print(f"[err] record_dm_inbound {name}: {e}")


def send():
    try:
        claimed = _rpc("claim_dm_replies", {"p_account_id": ACCOUNT_ID, "p_limit": 5}) or []
    except Exception as e:  # noqa: BLE001
        print(f"[err] claim_dm_replies: {e}")
        return
    if not claimed:
        print("[send] 无 approved 待发")
        return
    # 通道分流（同 wuying_poll_agent 的 _dm_script_is_web 惯例）：
    #   web(Edge 网页版) → sec_uid 地址栏直达主页发(claim_dm_replies 返回的 douyin_user_id 即 sec_uid)；
    #   PC 客户端       → 在私信会话列表点开会话就地回复(不搜号)。
    is_web = "web" in os.environ.get("AKKE_DM_SCRIPT", "").lower()
    if is_web:
        from douyin_dm_web_reply import reply_in_web
        print(f"=== send: claim 到 {len(claimed)} 条（web·sec_uid 直达主页发）===")
    else:
        from douyin_dm_reply_inbox import reply_in_inbox
        print(f"=== send: claim 到 {len(claimed)} 条（就地回复·不搜号）===")

    # 逐条发、逐条回写。
    for d in claimed:
        nm = (d.get("customer_name") or "").strip()
        draft = (d.get("draft") or "").strip()
        if not nm or not draft:
            _rpc("complete_dm_reply", {"p_id": d["id"], "p_status": "failed", "p_error": "缺 name/draft"})
            print(f"[reply] (跳过) 缺 name/draft: {d['id']}")
            continue
        try:
            if is_web:
                sec = (d.get("douyin_user_id") or "").strip()
                res = reply_in_web(nm, sec, draft, confirm=False)  # 自动化：不确认
            else:
                res = reply_in_inbox(nm, draft, confirm=False)  # 自动化：不确认
        except Exception as e:  # noqa: BLE001
            res = f"exc:{e}"
        ok = res == "sent"
        try:
            _rpc("complete_dm_reply", {
                "p_id": d["id"],
                "p_status": "sent" if ok else "failed",
                "p_error": None if ok else str(res),
            })
            print(f"[reply] {nm}: {'已发 ✓' if ok else 'failed(%s)' % res}")
        except Exception as e:  # noqa: BLE001
            print(f"[err] complete_dm_reply {d['id']}: {e}")


def main():
    for k, v in [("AKKE_ACCOUNT_ID", ACCOUNT_ID), ("SUPABASE_URL", SUPABASE_URL), ("DB鉴权", _BEARER)]:
        if not v:
            sys.exit(f"[X] env missing: {k}")
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    if mode in ("capture", "both"):
        capture()
    if mode in ("send", "both"):
        send()


if __name__ == "__main__":
    main()
