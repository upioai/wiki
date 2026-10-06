# -*- coding: utf-8 -*-
"""douyin_dm_reply_inbox — 收件箱【就地回复】(不搜抖音号)。

与 douyin_dm_grounded(冷启动搜号发) 的区别：**不搜号、不开主页、不关注点赞**——客户已在收件箱里、
刚回过你,直接在私信会话列表点对应用户开会话再发。省掉搜索→主页→身份门(wrong_user 头号来源)。

新代码只做「点收件箱里的会话开聊天窗」这一段(UIA 精确定位会话行);**输入框定位 + 打字 + 点发送↑
全部复用 douyin_dm_grounded 的 proven 机制**(find_match send_arrow + AKKE_INPUT_OFFSET + type_text),
所以需该机已跑过 `py douyin_dm_grounded.py --capture` 校准(野荞冷启动发 DM 用的同一套)。

修过两个坑：① 打字前 focus_douyin() 重新置前(否则 SendInput 漏进 PowerShell 控制台)；
② 确认放在打字之前(避免漏字污染 input())。

用法：
  py douyin_dm_reply_inbox.py "<会话昵称>" "<要发的话>"          # 带确认(先验开对会话, 再发)
  py douyin_dm_reply_inbox.py "<会话昵称>" "<要发的话>" --yes     # 不确认直接发(自动化)
前置：抖音前台、私信面板停在会话列表 + 该机已校准(send_arrow.png + AKKE_INPUT_OFFSET)。
"""
from __future__ import annotations

import os
import re
import sys
import time

import pyautogui

import douyin_dm_grounded as _g
from douyin_dm_grounded import (
    focus_douyin,
    type_text,
    _verify_bubble,
    find_match,
    _input_offset,
)
from douyin_inbox_uia import find_douyin, find_im_panel, collect_nodes


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).strip()


def _center(rect):
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def find_row_rect(panel, target: str):
    """私信列表里找昵称==target 的会话行,返回名字节点 rect(供点击开会话)。"""
    nt = _norm(target)
    for ct, name, rect in collect_nodes(panel):
        if ct == "TextControl" and _norm(name) == nt and rect != (0, 0, 0, 0):
            return rect
    return None


def _scroll_find_row(win, target: str, max_scrolls: int):
    """私信面板只渲染首屏约 13 行(UIA 也只给已渲染的行)。客户不在首屏 → 滚着找。

    2026-10-06 深圳 B 端机：九牛网络在第 14 行开外，就地回复直接 no_row、已审批回复发不出去。
    滚轮落点取已渲染会话名的包围盒中心(必在列表区内)；每滚一次重新取面板再找。"""
    panel = find_im_panel(win) or win
    rects = [r for ct, _, r in collect_nodes(panel) if ct == "TextControl" and r != (0, 0, 0, 0)]
    if not rects:
        return None
    x = (min(r[0] for r in rects) + max(r[2] for r in rects)) // 2
    y = (min(r[1] for r in rects) + max(r[3] for r in rects)) // 2
    pyautogui.moveTo(x, y)
    for i in range(max_scrolls):
        pyautogui.scroll(-480)  # Windows 下是滚轮 delta，120=一格
        time.sleep(0.8)
        row = find_row_rect(find_im_panel(win) or win, target)
        if row:
            print(f"  [滚动找会话] 第 {i + 1} 次滚动后找到「{target}」")
            return row
    return None


def reply_in_inbox(target: str, message: str, confirm: bool = True, on_located=None) -> str:
    # ① 点收件箱里的会话行开聊天窗(新代码)
    focus_douyin()
    time.sleep(0.8)
    win = find_douyin()
    if win is None:
        print("[X] 没找到抖音窗口")
        return "no_window"
    row = find_row_rect(find_im_panel(win) or win, target)
    if not row:
        row = _scroll_find_row(win, target, int(os.environ.get("AKKE_REPLY_SCROLL_MAX", "15")))
    if not row:
        print(f"[X] 收件箱列表没找到会话「{target}」(停在会话列表了吗?)")
        return "no_row"
    cx, cy = _center(row)
    print(f"  [点会话] {target} @ ({cx},{cy})")
    pyautogui.click(cx, cy)
    time.sleep(1.5)

    # ② 输入框/发送↑定位——复用冷启动 proven 机制(模板匹配 send_arrow + 偏移)
    focus_douyin()  # 重新置前: 打字前必须确保抖音在前台, 否则 SendInput 漏进控制台
    time.sleep(0.4)
    _W, _H = pyautogui.size()
    _region = (int(_W * 0.6), 0, _W - int(_W * 0.6), _H)
    # 找图与坐标闸都跟 grounded(一触) 共用：面板渲染慢要多等几轮(_find_send_arrow)；
    # 闸的上下界按机器读 AKKE_SEND_GATE_XMIN/YMIN/YMAX。此前这里写死 y∈[0.28H,0.68H]，
    # 抖音 PC 8.5.1 停靠面板的发送↑在 y≈0.92H(深圳机)，一触早已放宽，这里没跟上 →
    # 已审批回复必判 send_arrow_bad_pos(2026-10-06)。getattr 兜底：机上 grounded 是旧版也能跑。
    _find = getattr(_g, "_find_send_arrow", None)
    sp = _find(region=_region) if _find else find_match("send_arrow.png", region=_region)
    if not sp:
        print("[X] 没匹配到 send_arrow.png —— 该机未校准? 先跑 py douyin_dm_grounded.py --capture")
        return "no_send_arrow"
    xmin = getattr(_g, "_SEND_GATE_XMIN", 0.85)
    ymin = getattr(_g, "_SEND_GATE_YMIN", 0.28)
    ymax = getattr(_g, "_SEND_GATE_YMAX", 0.68)
    if not (sp[0] >= xmin * _W and ymin * _H <= sp[1] <= ymax * _H):
        print(f"  [跳过] send_arrow 落异常位置 ({sp[0]},{sp[1]}) 不在 x≥{xmin * _W:.0f}, "
              f"y∈[{ymin * _H:.0f},{ymax * _H:.0f}] → 判假阳性, 不发")
        return "send_arrow_bad_pos"
    off = _input_offset()
    if not off:
        print("[X] 无 AKKE_INPUT_OFFSET —— 先跑 douyin_dm_grounded.py --capture 校准")
        return "no_input_offset"
    ipt = (sp[0] + off[0], sp[1] + off[1])

    # P5 boundary: target row is open and the proven input/send anchors are present.
    # The callback itself is best-effort in the orchestrator and must not block sending.
    if on_located:
        on_located()

    # ③ 确认(放打字之前, 避免漏字污染)
    if confirm:
        ans = input(f'  开对会话「{target}」, 即将发: "{message}"  → 回车发 / n 取消: ')
        if ans.strip().lower() == "n":
            print("  已取消(没打字没发)")
            return "cancelled"

    # ④ 点输入框 → 打字 → 点发送↑
    print(f"  input[锚定发送↑] -> ({ipt[0]},{ipt[1]})")
    pyautogui.click(ipt[0], ipt[1])
    time.sleep(1.0)
    type_text(message)
    print(f"  [待发] {message[:34]}")
    print(f"  click[发送↑] -> ({sp[0]},{sp[1]})")
    pyautogui.click(sp[0], sp[1])
    time.sleep(1.5)

    # ⑤ 验气泡
    ok = _verify_bubble(message)
    print(f"  [验气泡] {'✅ 见我方气泡 → sent' if ok else '⚠️ 未见气泡 → send_unverified(回池可重发)'}")
    return "sent" if ok else "send_unverified"


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        sys.exit('用法: py douyin_dm_reply_inbox.py "<会话昵称>" "<要发的话>" [--yes]')
    r = reply_in_inbox(args[0], args[1], confirm="--yes" not in sys.argv)
    print("结果:", r)
