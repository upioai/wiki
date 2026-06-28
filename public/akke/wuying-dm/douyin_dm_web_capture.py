# -*- coding: utf-8 -*-
"""douyin_dm_web_capture — Edge 网页版 DM 自动回复·捕获(VL 视觉读私信列表)。

PC 客户端版(douyin_inbox_uia)用 UIA 读会话列表；但 Edge 网页版 UIA 树完全不同
(无 imSaasContainerId、私信浮层易失、未读徽标数字混入页面其它数字) → 改用 Qwen VL
截图读：截私信列表 → VL 读出每个会话 [昵称, 预览, 红点] → 匹配本账号已发记录(by_name)
→ 未读+匹配+相关 的调 record_dm_inbound 写回客户回复(role=customer)。

列表长、未读可能在下方(运营实测) → 滚动多屏覆盖，按昵称去重，连续一屏无新会话即停。

被 douyin_dm_autoreply.capture() 在 web 通道(AKKE_DM_SCRIPT 含 'web')时调用。
v1：要求 Edge 已停在【私信会话列表】(自动点开入口 open_inbox 见下，可 AKKE_WEB_DM_AUTOOPEN=1 开)。
前提：Edge 登录该号、最大化、输入法英文；.env 同 grounded(ANTHROPIC/OPENROUTER key)。
"""
from __future__ import annotations

import base64
import os
import re
import time

import pyautogui

# 复用 grounded 的视觉原语(截图/VL/JSON 解析/置前)与 inbox 的匹配/写库。
from douyin_dm_grounded import _shot, _vision, _pjson, focus_douyin, locate
from douyin_inbox_uia import load_sent_dms, match_name, SYS_NOTICE, _is_noise_preview

# 相关性过滤(同 douyin_dm_autoreply)：互粉/涨粉 spam 剔除，其余真回复一律放行。
SPAM_KW = ("有效粉", "互粉", "小火花", "互发", "掉粉", "千粉不如", "长按", "转发", "关注后")
DECOR_KW = (
    "装修", "全屋", "定制", "柜", "户型", "几室", "房", "平", "㎡", "报价", "多少钱",
    "材料", "板材", "墙", "改", "水电", "保温", "毛坯", "精装", "厨", "卫", "阳台",
    "好的", "谢谢", "可以", "行", "嗯", "要", "想",
)

MAX_SCROLLS = int(os.environ.get("AKKE_WEB_DM_MAX_SCROLLS", "5"))
AUTOOPEN = os.environ.get("AKKE_WEB_DM_AUTOOPEN", "0").lower() in ("1", "true", "yes")
# 顶部【私信图标】固定坐标(归一化 0~1 "x,y")。量法:鼠标移到图标上跑 measure one-liner。
# 设了它,AUTOOPEN 就点这个固定坐标开私信浮层(比 VL 定位稳)。
_C_DM_ICON = os.environ.get("AKKE_WEB_C_DM_ICON", "").strip()
# 滚动会话列表时鼠标悬停位置(归一化 0~1 of 屏宽高)。默认左中(列表通常在左)；
# 若第一版滚不动(列表在别处)，.env 设 AKKE_WEB_DM_LIST_X/Y 调，不用重传脚本。
# 私信会话列表面板在屏幕的【右侧】(实测约 0.74~0.94 宽，左边是背景个人主页、最右是打开的聊天框)。
# 红点扫描 + 读行 + 滚动都只锁这块，避开背景主页的红色 UI。不同布局/分辨率可 env 调。
_PANEL_L = float(os.environ.get("AKKE_WEB_DM_PANEL_L", "0.74"))
# 右界默认 0.91：会话列表的未读红点在列表行右端(~0.90)，再往右(~0.92+)就是【打开的聊天窗口】。
# 收窄到 0.91 把聊天窗排除在扫描外，否则会把聊天窗里的红色/内容当成列表(治"什么材料的板材"串聊天窗)。
_PANEL_R = float(os.environ.get("AKKE_WEB_DM_PANEL_R", "0.91"))
_LX = float(os.environ.get("AKKE_WEB_DM_LIST_X", str(round((_PANEL_L + _PANEL_R) / 2, 3))))
_LY = float(os.environ.get("AKKE_WEB_DM_LIST_Y", "0.5"))

# 红点像素检测参数。抖音未读徽标 ~ #FE2C55 (R254 G44 B85)。只认【小而集中】的红色圆点/数字徽标，
# 排除红字/红封面/红按钮。阈值与尺寸 env 可微调(不同缩放/主题)。
_RED_R = int(os.environ.get("AKKE_WEB_DM_RED_R", "190"))
_RED_G = int(os.environ.get("AKKE_WEB_DM_RED_G", "100"))
_RED_B = int(os.environ.get("AKKE_WEB_DM_RED_B", "125"))
_BADGE_MIN = int(os.environ.get("AKKE_WEB_DM_BADGE_MIN", "8"))    # 红点最少采样像素(去噪)
_BADGE_MAXW = int(os.environ.get("AKKE_WEB_DM_BADGE_MAXW", "95"))  # 红点最大 x 宽(超过=红字/红图非圆点)
# 红点最大 y 高：真未读圆点/数字徽标矮(~12-18px)；本号红底头像「有」高(~40px)会被当假红点。
# 治 2026-06-27 有大有小「红底头像被当未读徽标」假阳。不同缩放可调。
_BADGE_MAXH = int(os.environ.get("AKKE_WEB_DM_BADGE_MAXH", "30"))
# 本号自己的展示名(如「有大有小」)。VL 偶尔把会话行/聊天窗里本号头像字读成对方昵称 → 防御性
# 跳过，不污染去重(否则一个错读塌缩整轮)。不设=不启用该防御。
_SELF_NAME = re.sub(r"\s+", "", os.environ.get("AKKE_WEB_DM_SELF_NAME", "")).strip()


def _rpc(name: str, payload: dict):
    import json
    import urllib.request

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    scoped = os.environ.get("SUPABASE_SCOPED_JWT")
    anon = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    service = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    apikey, bearer = (anon, scoped) if (scoped and anon) else (service, service)
    req = urllib.request.Request(
        f"{url}/rest/v1/rpc/{name}",
        data=json.dumps(payload).encode(),
        headers={"apikey": apikey, "Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else None


def is_relevant(text: str) -> bool:
    return not (any(k in text for k in SPAM_KW) and not any(k in text for k in DECOR_KW))


# VL 有时把会话行的时间戳并进 preview(如「你是哪个 · 昨天」)。写库前剥掉尾部「· 时间」，
# 否则当成客户消息喂给 chatReply 会带噪声。只剥【确为时间】的尾巴，不误伤正文里的「·」。
_TS_TAIL = re.compile(
    r"\s*[·•・]\s*(\d{1,2}:\d{2}|昨天|前天|刚刚|今天|\d+\s*(分钟|小时|天)前|周[一二三四五六日天]|"
    r"\d{1,2}[-/月]\d{1,2}日?)\s*$"
)


def _strip_ts(s: str) -> str:
    return _TS_TAIL.sub("", s or "").strip()


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").strip()


def we_sent_last(preview: str, sent: str) -> bool:
    """预览是不是【我们最后发的那条】(开场白/上一条回复) → 是则该会话我们发在最后、对方没回。
    我们发的内容较长 → 要求归一化预览 >=6 字才按前缀判，避免客户短回复(如'你好'恰是开场白前缀)
    被误当成我们发的。治本次「青山不归客/童(预览=我方内容)被 VL 误标未读」的假阳性。
    会话列表预览常被抖音截断带尾巴省略号(…/...) → 先剥掉再前缀判，否则省略号破坏 startswith：
    治 2026-06-27「等不到天亮等时光」我方 opener 被读成"你好，等不到天…"漏拦的首回假阳。"""
    p = re.sub(r"[…．.]+$", "", _norm(preview))
    s = _norm(sent)
    if len(p) < 6 or not s:
        return False
    return s.startswith(p) or p.startswith(s)


def open_inbox():
    """点顶部【私信图标】打开右侧会话列表浮层。AKKE_WEB_DM_AUTOOPEN=1 才跑。
    优先用固定坐标 AKKE_WEB_C_DM_ICON(稳)，没配才回退 VL 定位。全程程序自己 focus，
    不失焦 → 浮层不收起。best-effort,失败不抛。"""
    if not AUTOOPEN:
        return
    focus_douyin()
    time.sleep(0.8)
    if _C_DM_ICON:
        try:
            xs, ys = _C_DM_ICON.split(",")
            W, H = pyautogui.size()
            pyautogui.click(int(float(xs) * W), int(float(ys) * H))
            time.sleep(2.5)
            print(f"  [open_inbox] 固定坐标点开私信 {_C_DM_ICON}")
            return
        except Exception as e:  # noqa: BLE001
            print(f"  [open_inbox] 固定坐标点击失败({e}) → 试 VL 定位")
    try:
        pt = locate(
            "抖音网页版顶部导航栏的【私信/消息图标】(对话气泡图标,点开显示私信会话列表)",
            region=(0.5, 0.0, 1.0, 0.12),
        )
    except Exception as e:  # noqa: BLE001
        print(f"  [open_inbox] 视觉定位异常: {e}")
        pt = None
    if pt:
        pyautogui.click(pt[0], pt[1])
        time.sleep(2.5)
    else:
        print("  [open_inbox] 未定位到私信图标 → 假设已在私信列表")


def _find_red_badges(path):
    """像素检测私信列表的【红色未读圆点】→ [(y_center, x_center)]（整图像素坐标）。
    抖音红 #FE2C55。按 y 聚类成行，过滤噪点/红字/红图(太宽或太大)，只留小而集中的红点。
    这是'只读红点'的核心：没红点的行根本不会进入后续读取。"""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    iw, ih = im.size
    px = im.load()
    xlo, xhi = int(iw * _PANEL_L), int(iw * _PANEL_R)  # 只扫右侧私信面板区
    reds = []
    for y in range(0, ih, 2):
        for x in range(xlo, xhi, 2):
            r, g, b = px[x, y]
            if r > _RED_R and g < _RED_G and b < _RED_B and (r - g) > 110 and (r - b) > 80:
                reds.append((x, y))
    if not reds:
        return []
    reds.sort(key=lambda p: p[1])
    bands, cur = [], [reds[0]]
    for p in reds[1:]:
        if p[1] - cur[-1][1] <= 14:
            cur.append(p)
        else:
            bands.append(cur)
            cur = [p]
    bands.append(cur)
    badges = []
    for band in bands:
        if len(band) < _BADGE_MIN or len(band) > 2000:
            continue  # 太小=噪点；太大=红头像/红封面
        xs = [p[0] for p in band]
        if max(xs) - min(xs) > _BADGE_MAXW:
            continue  # x 太宽=红字/红按钮，不是圆点
        ys = [p[1] for p in band]
        if max(ys) - min(ys) > _BADGE_MAXH:
            continue  # y 太高=红底头像(如本号「有」)/红封面，不是小圆点
        badges.append((sum(ys) // len(ys), sum(xs) // len(xs)))
    badges.sort()
    merged = []  # 合并 y 很近的(同一徽标被切两段)
    for yc, xc in badges:
        if merged and yc - merged[-1][0] < 30:
            continue
        merged.append((yc, xc))
    return merged


def _open_and_read(badge, inbox_path):
    """读红点会话：先从【会话列表那一行】裁条读 对方昵称+预览，再点开会话标记已读。
    行内只有【对方头像+对方昵称+预览+时间】，绝不含本号红底头像「有」→ 不会把本号头像字
    误读成昵称。旧版从【聊天窗右侧】读，那里全是我方蓝气泡+本号「有」头像 → 昵称恒读成
    「有」→ match 全失败 + 昵称去重塌缩、真回复被丢(2026-06-27 有大有小事件根因)。
    点开只为标记已读(红点消、下轮不重抓)；发送仍由 send 走 sec_uid 直达主页发。"""
    import io

    from PIL import Image
    yc, xc = badge
    W, H = pyautogui.size()
    nick = preview = ""
    # 1) 从收件箱截图里裁出红点那一行(列表整列宽 × 红点上下半行高)，VL 读昵称+预览。
    try:
        with Image.open(inbox_path) as im:
            iw, ih = im.size
            half = int(ih * float(os.environ.get("AKKE_WEB_DM_ROW_HALF", "0.026")))
            crop = im.crop((int(iw * _PANEL_L), max(0, yc - half), int(iw * _PANEL_R), min(ih, yc + half)))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
        p = (
            "这是抖音网页版私信【会话列表里的一行】(对方头像 + 对方昵称 + 最后一条消息预览 + 时间)。"
            "读出：(a)对方昵称(头像右边那个名字,可能纯数字/字母/中文,照原样,别读成预览或时间)；"
            "(b)最后一条消息预览文字。"
            "返回严格 JSON {\"nickname\":\"对方昵称\",\"preview\":\"消息预览\"}。只返回 JSON。"
        )
        d = _pjson(_vision(b64, p))
        if isinstance(d, list):
            d = d[0] if d else {}
        if isinstance(d, dict):
            nick = str(d.get("nickname") or "").strip()
            preview = str(d.get("preview") or "").strip()
    except Exception as e:  # noqa: BLE001
        print(f"  [VL] 读会话行异常: {e}")
    # 2) 点会话行(列表列中间、红点那一行的 y)打开会话 = 标记已读，红点消失、下轮不再重抓。
    click_x = int(W * (_PANEL_L + _PANEL_R) / 2)
    try:
        pyautogui.click(click_x, yc)
        time.sleep(1.2)
    except Exception as e:  # noqa: BLE001
        print(f"  [open] 点会话(标已读)失败: {e}")
    return {"nickname": nick, "preview": preview}


def _on_inbox_page() -> bool:
    """护栏：只看【右侧私信面板区】问 VL 这块是不是会话列表(私信常是右侧浮层、背景是主页，
    所以裁到面板区判才准)。不是就别读。VL 异常时放行(不阻断)。"""
    import io

    from PIL import Image
    path, (W, H) = _shot("_dm_guard.png")
    try:
        with Image.open(path) as im:
            iw, ih = im.size
            crop = im.crop((int(iw * _PANEL_L), 0, int(iw * _PANEL_R), ih))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        b64 = base64.b64encode(open(path, "rb").read()).decode()
    p = ("这是抖音网页的一块截图。里面是不是一个【私信会话列表】(一竖列会话，每行=头像+对方昵称"
         "+最后一条消息预览)？只回严格 JSON {\"is_inbox\":true/false}。")
    try:
        d = _pjson(_vision(b64, p))
        return bool(d.get("is_inbox"))
    except Exception:
        return True  # VL 异常不阻断


def capture():
    open_inbox()
    # 只有自动开私信(AUTOOPEN)时才 focus_douyin——它会把 Edge 切到"抖音"主窗口，可能抢到
    # 别的标签页(个人主页)、截错屏。手动模式假设运营已把私信列表显示在前，直接截当前画面。
    if AUTOOPEN:
        try:
            focus_douyin()
        except Exception:
            pass
    # 不再用 VL 护栏(它会把明明是私信列表的画面误判'不是'、误杀)。安全闸=只扫右侧面板区红点：
    # 没开浮层/在主页时,该区域没有抖音红的小圆点徽标 → 自然找到 0 个红点、no-op。
    by_name = load_sent_dms()
    print(f"=== web capture[红点像素检测]: by_name {len(by_name)} 人, 最多滚 {MAX_SCROLLS} 屏 ===")
    if not by_name:
        print("  [warn] by_name 为空 → 检查 AKKE_ACCOUNT_ID / DB 鉴权")
        return

    W, H = pyautogui.size()
    mx, my = int(W * _LX), int(H * _LY)
    seen = set()
    inbound = 0
    empty = 0
    for s in range(MAX_SCROLLS):
        path, _ = _shot("_dm_inbox.png")
        badges = _find_red_badges(path)
        new_this_screen = 0
        for badge in badges:
            row = _open_and_read(badge, path)
            if not row:
                continue
            nick = str(row.get("nickname") or "").strip()
            print(f"  [红点@y={badge[0]},x={badge[1]}] 读到 nick={nick!r} preview={str(row.get('preview') or '')[:18]!r}")
            preview = _strip_ts(str(row.get("preview") or ""))
            if not nick or nick in seen:
                continue
            # 防御：昵称读成本号自己的名(本号头像字被误读) → 跳过且不污染去重(否则错读塌缩整轮)。
            if _SELF_NAME and (_norm(nick) == _SELF_NAME or _SELF_NAME.startswith(_norm(nick)) or _norm(nick).startswith(_SELF_NAME)):
                print(f"  [skip] {nick}: 疑似本号自己的名(头像误读) → 忽略，不计入去重")
                continue
            seen.add(nick)
            new_this_screen += 1
            hit = match_name(nick, by_name)
            if not hit:
                print(f"  [skip] {nick}(红点): 不在本账号已发记录 → 非我们 DM 的人")
                continue
            conv = hit.get("conversation_id")
            if not conv:
                print(f"  [skip] {nick}: 无 conversation_id")
                continue
            # 二道兜底：红点行但 VL 把预览读成了我们发的内容(极少) → 跳过。短客户回复不受影响。
            if we_sent_last(preview, hit.get("sent", "")):
                print(f"  [skip] {nick}: 预览疑似我们发的(误读) → {preview[:18]}")
                continue
            if any(t in preview for t in SYS_NOTICE):
                print(f"  [no_reply] {nick}: 系统提示 → {preview[:20]}")
                continue
            # 纯时间戳/状态行(如「09:45」「6-18」「在线」「关闭会话」) → 非客户回复。
            # 治本次「只关注没回复，VL 把聊天窗里的时间戳 09:45 误读成最后一条消息」的首次回复误报。
            # 复用 UIA 路径同款 _is_noise_preview(整条 fullmatch，不误伤「明天9:00行吗」这类真回复)。
            if _is_noise_preview(preview):
                print(f"  [no_reply] {nick}: 纯时间戳/状态行 → {preview[:20]}")
                continue
            if preview.startswith("[") and preview.endswith("]"):
                print(f"  [skip] {nick}: 纯表情")
                continue
            if not is_relevant(preview):
                print(f"  [noise] {nick}: spam → {preview[:20]}")
                continue
            try:
                _rpc("record_dm_inbound", {"p_conversation_id": conv, "p_content": preview})
                inbound += 1
                print(f"  [inbound] {nick}: {preview[:30]}")
            except Exception as e:  # noqa: BLE001
                print(f"  [err] record_dm_inbound {nick}: {e}")
        print(f"  -- 第{s + 1}屏: 红点 {len(badges)} 个 / 新未读 {new_this_screen} / 累计 {len(seen)}")
        empty = empty + 1 if new_this_screen == 0 else 0
        if empty >= 2:
            print("  -- 连续2屏无新红点 → 停止滚动")
            break
        pyautogui.moveTo(mx, my)
        pyautogui.scroll(-700)
        time.sleep(1.2)
    print(f"=== web capture 完成: 写回 {inbound} 条客户回复 ===")


if __name__ == "__main__":
    capture()
