# -*- coding: utf-8 -*-
"""douyin_inbox_uia — 精确版二触回复检测·只读报告（无影端，UIA 读会话列表，无 VL/OCR/截图）。

抖音 PC = CEF(Chrome_WidgetWin_1)，UIA 暴露私信【会话列表】精确文本。实测每个会话行 =
四件套同级节点：头像(ListItem>Image) → 名字(Text) → 时间(Text) → 预览(Text, 最后一条消息,
抖音截断~60字)。本脚本读左侧列表（按坐标过滤，排除右侧打开的会话面板），按【指纹】判：

  预览仍是「我们发给 TA 的那条 DM」的前缀  → no_reply（确认没回）
  预览=已知系统提示（只能发送一条/达人电商通知等）→ no_reply（确认没回）
  其它（预览变了、又不是已知系统消息）        → 可疑 → 跳过（不标、不动）

保守策略（用户定）：**只确认 no_reply，可疑一律跳过，不自动标 replied、不写任何库**——
彻底杜绝「小蒙蒙」那种误杀（系统/电商消息被当成回复）。谁真回了由人工另行处理。

前置：抖音 PC 前台最大化 + 打开【私信】停在会话列表（别点进具体会话）。
依赖：py -m pip install uiautomation
env(同 .env)：SUPABASE_URL + 鉴权(SCOPED_JWT+anon 或 SERVICE_ROLE_KEY) + AKKE_ACCOUNT_ID。
用法：py douyin_inbox_uia.py        # 只读，打印 no_reply / 可疑(跳过)，不写库
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

try:
    import uiautomation as auto
except ImportError:
    sys.exit("[X] 缺依赖：先跑  py -m pip install uiautomation  再重试")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

ACCOUNT_ID = os.environ.get("AKKE_ACCOUNT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
_SCOPED_JWT = os.environ.get("SUPABASE_SCOPED_JWT")
_ANON = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_ANON_KEY")
_SERVICE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if _SCOPED_JWT and _ANON:
    _APIKEY, _BEARER = _ANON, _SCOPED_JWT
else:
    _APIKEY = _BEARER = _SERVICE

MAX_NODES = 4000
FP_LEN = 14               # 指纹比较取前 N 个归一化字符
_TIME_RE = re.compile(r"(\d{1,2}:\d{2}|\d{1,2}[-/月]\d{1,2}|分钟前|小时前|天前|前天|昨天|刚刚|周[一二三四五六日]|\d+秒)")
# 未读数徽标 = 纯数字(最多3位, 可带+)。实测 douyin PC: 未读会话行预览右缘挂 TextControl '1'，
# 已读行没有 → 未读信号在 UIA 树里、可免费读（spike 2026-06-22 坐实）。
_BADGE_RE = re.compile(r"^\s*\d{1,3}\+?\s*$")
# 已知「非回复」系统/电商提示 → 仍判 no_reply（对方确实没回）。可按需扩。
# 2026-06-18 扩：隐私拦截/发送失败 + 不可展示系统消息（DM 自动回复实测样本）。
SYS_NOTICE = (
    "对方回复或关注你之前", "只能发送一条", "达人侧购买", "商品和订单存在疑问",
    "由于对方的隐私设置", "你无法发送消息", "该消息类型暂不能展示",
    # 撤回提示("你撤回了一条消息"/"对方撤回了一条消息")是系统行，非客户回复。
    # 2026-06-26 QQL 会话把"你撤回了一条消息"误写成客户消息 → 首回假阳。
    "撤回了一条消息",
    # 抖音搜索页"相关搜索"推荐栏 UI 文字，非客户回复。
    # 2026-06-26 饭粒「喵小懒」会话把"相关搜索"误写成客户消息 → 首回假阳。
    "相关搜索",
    # 服务评价邀请，抖音系统下发，非客户回复。2026-08-05 一筑实测被当成客户回复写库
    # (DB 噪声闸兜住了 → check=rejected，但不该让它进到这一步)。
    "诚邀您评价本次服务",
)


def _http(method, path, params=None, body=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "apikey": _APIKEY, "Authorization": f"Bearer {_BEARER}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw else None


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).strip()


# 状态行/纯时间戳整条被错当客户预览（仅老 classify 门 / --write-messages 路径会漏；
# 红点门控靠未读徽标已结构性排除）。整条 fullmatch 才判噪声——不用「包含」，避免误杀
# 「你在线吗」「明天9:00行吗」这类真回复；相对词(昨天/刚刚/周三)也不算，可能是真回复。
_NOISE_PREVIEW_RE = re.compile(r"关闭会话|(刚刚|\d+(分钟|小时|天)前)?在线|\d{1,2}:\d{2}|\d{1,2}[-/月]\d{1,2}")


def _is_noise_preview(prev: str) -> bool:
    return bool(_NOISE_PREVIEW_RE.fullmatch(_norm(prev)))


# 媒体占位预览（[图片]/[视频]/[语音]/[文件]/[位置]）：与纯表情贴纸（[龙舟载福] 等，名字
# 不可枚举）同为括号占位，但语义完全不同——发图的常是决策期客户（户型图/现场照/报价截图），
# 必须占位入库→转人工，不能与贴纸一起静默丢（2026-07-10 微笑早晨户型图漏抓案）。
# 白名单与 DB 闸 dm_inbound_is_noise v5 规则 7 保持一致，改一处必改另一处。
_MEDIA_PREVIEW_RE = re.compile(r"\[(图片|视频|语音|文件|位置)\]")


def is_media_preview(prev: str) -> bool:
    p = _norm(prev)
    return bool(re.fullmatch(r"(\[[^\]]+\])+", p)) and bool(_MEDIA_PREVIEW_RE.search(p))


def find_douyin():
    for w in auto.GetRootControl().GetChildren():
        nm, cn = (w.Name or ""), (w.ClassName or "")
        if "抖音" in nm or "douyin" in nm.lower() or "douyin" in cn.lower():
            return w
    return None


def collect_texts(root):
    """文档顺序(DFS 前序)收集全树非空 TextControl 文本。不靠坐标（CEF 节点
    BoundingRectangle 多不可靠）。右侧面板漏入的噪声由 parse_rows 护栏 + 保守判别兜。"""
    out, stack, n = [], [(root, 0)], 0
    while stack:
        ctrl, depth = stack.pop(0)
        n += 1
        if n > MAX_NODES:
            break
        if ctrl.ControlTypeName == "TextControl":
            t = (ctrl.Name or "").strip()
            if t:
                out.append(t)
        try:
            kids = ctrl.GetChildren()
        except Exception:
            kids = []
        for ch in reversed(kids):
            stack.insert(0, (ch, depth + 1))
    return out


def parse_rows(texts):
    """扁平 TextControl 序列 → 会话行 (name, preview)。每行=名字+时间+预览三连，
    中间必是时间行 → 据此锚定，跳过表头/搜索框。护栏：名字异常长(>20)或以「你好」
    开头 = 其实是右侧面板的预览被错当名字 → 丢弃。
    预览阈值=2（2026-06-19：原 12 把短客户回复「能发一下设计图吗」「四个房间」全丢了；
    时间行才是强锚点，预览只要非空即可，下游 match/classify/relevance 兜误判）。"""
    rows, i = [], 0
    while i + 2 < len(texts):
        name, mid, prev = texts[i], texts[i + 1], texts[i + 2]
        if (_TIME_RE.search(mid) and len(prev) >= 2 and not _TIME_RE.search(name)
                and len(name) <= 20 and not name.startswith("你好")):
            rows.append((name, prev))
            i += 3
        else:
            i += 1
    return rows


def collect_nodes(root):
    """文档顺序 DFS 收集【所有】控件 (ControlTypeName, Name, rect)——带 rect 供红点门控。
    与 collect_texts 区别：不只 TextControl、且带 BoundingRectangle。"""
    out, stack, n = [], [(root, 0)], 0
    while stack:
        ctrl, _ = stack.pop(0)
        n += 1
        if n > MAX_NODES:
            break
        try:
            name = (ctrl.Name or "").strip()
        except Exception:
            name = ""
        try:
            r = ctrl.BoundingRectangle
            rect = (r.left, r.top, r.right, r.bottom)
        except Exception:
            rect = (0, 0, 0, 0)
        out.append((ctrl.ControlTypeName, name, rect))
        try:
            kids = ctrl.GetChildren()
        except Exception:
            kids = []
        for ch in reversed(kids):
            stack.insert(0, (ch, 0))
    return out


def find_im_panel(win):
    """找私信面板 (AutomationId=imSaasContainerId)——把扫描范围收到会话列表面板，
    避开个人主页作品数/顶部全局未读徽标等无关数字。找不到返回 None（调用方回退全窗口）。"""
    stack, n = [win], 0
    while stack:
        c = stack.pop(0)
        n += 1
        if n > MAX_NODES:
            break
        try:
            if (c.AutomationId or "") == "imSaasContainerId":
                return c
        except Exception:
            pass
        try:
            kids = c.GetChildren()
        except Exception:
            kids = []
        for ch in reversed(kids):
            stack.insert(0, ch)
    return None


def parse_rows_unread(nodes):
    """nodes=collect_nodes() → [(name, preview, unread)]。红点门控核心。
    锚定会话行同 parse_rows（name+time+preview 三连）；unread = 该行 y 带内、列表列右缘
    有【纯数字未读徽标】TextControl（非本行 name/time/preview 节点）。
    rect 全不可靠(全0)的机器：badges 取不到 → unread 恒 False，调用方据此回退老门（别静默漏回复）。"""
    texts = [(name, rect) for (ct, name, rect) in nodes if ct == "TextControl" and name]
    rows, i = [], 0
    while i + 2 < len(texts):
        (nm, nr), (mid, mr), (pv, pr) = texts[i], texts[i + 1], texts[i + 2]
        if (_TIME_RE.search(mid) and len(pv) >= 2 and not _TIME_RE.search(nm)
                and len(nm) <= 20 and not nm.startswith("你好")):
            rows.append((nm, pv, nr, pr, {nr, mr, pr}))
            i += 3
        else:
            i += 1
    consumed = set()
    for r in rows:
        consumed |= r[4]
    # 列表列右缘 = 各行 name/time/preview 节点右边界最大值（徽标在时间那一侧、比短预览更靠右，
    # 不能只看预览右缘）+ 余量，挡开打开的聊天区气泡数字/头部全局徽标。
    list_right = max((rect[2] for rect in consumed), default=0)
    badges = [
        rect for (name, rect) in texts
        if _BADGE_RE.match(name) and rect not in consumed and rect != (0, 0, 0, 0)
        and (list_right == 0 or rect[0] < list_right + 40)
    ]
    out = []
    for k, (nm, pv, nr, _pr, _c) in enumerate(rows):
        top = nr[1]
        bottom = rows[k + 1][2][1] if k + 1 < len(rows) else top + 80
        unread = any(top <= ((b[1] + b[3]) // 2) < bottom for b in badges)
        out.append((nm, pv, unread))
    return out


def load_sent_dms():
    """本账号已发 DM：{normalized_name: {name, sent, conversation_id}}（每人最近一条 sent）。
    conversation_id 供 DM 自动回复(douyin_dm_autoreply)调 record_dm_inbound 用；
    二触只读 name/sent，多带一个字段不影响。"""
    rows = _http("GET", "messages", {
        "select": "content,sent_at,conversations!inner(id,douyin_user_id,customer_name,messaging_account_id)",
        "role": "eq.ai", "status": "eq.sent",
        "conversations.messaging_account_id": f"eq.{ACCOUNT_ID}",
        "order": "sent_at.desc", "limit": "500",
    }) or []
    by_name, seen = {}, set()
    for r in rows:
        conv = r.get("conversations") or {}
        uid, nm = conv.get("douyin_user_id"), conv.get("customer_name") or ""
        if not uid or uid in seen:
            continue
        seen.add(uid)
        key = _norm(nm)
        if key in by_name:
            # 同昵称撞车：本号下 ≥2 个不同 uid 的同名会话——昵称 join 必然挂错
            # (2026-07-09 蛟河平姐消息挂进山西平姐会话、自动回复发错人实锤)。
            # 标 ambiguous，调用方【禁止自动挂靠】、推 Lark 转人工。保留首个(最近发过的)
            # conversation_id 不动，未升级的调用方行为不变。
            by_name[key]["ambiguous"] = True
            by_name[key]["conv_count"] = by_name[key].get("conv_count", 1) + 1
            continue
        by_name[key] = {
            "name": nm,
            "sent": r.get("content") or "",
            "conversation_id": conv.get("id"),
        }
    return by_name


# 同昵称歧义/兜底疑似 告警：本地台账【永久】去重(2026-07-11 用户定"提醒一次就好，之后
# 再也不提醒")——键 = 昵称+预览前缀，报过的键永不再报；该客户发了【新内容】(预览变了)
# 才算新键再报一次。台账在本地非 DB(同 wecom 惯例)，经 emit_captcha_alert →
# captcha-monitor cron 推云电脑监控群。歧义会话的客户回复不入库(防挂错人)，
# 不告警运营就永远不知道有人在等——显式失败。
# 代价(接受)：同人隔久重发一模一样的话不会二次提醒；删台账 json 可重置。
_AMBIG_LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ambig_alerts.json")

_BACKFILL_LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backfill_alerts.json")

# 昵称静音名单(2026-07-19 饭粒定)：确认过【不是真客户】的同名会话(抖音活动号/薅红包号/
# 内部号)永不推歧义卡。按昵称去重(上面那层)只保证"一辈子报一次"，静音名单是"一次都不报"。
# 每行一个昵称，# 开头为注释；每次告警重读文件 → 加人不用重启脚本。
# 代价：名单里若混进真客户，他的等待就没人提醒(歧义会话客户回复本来就不入库)——只放确认过的。
# 文件缺失/读失败 = 不静音(fail-open，宁可多报一张卡也不静默吞掉真客户)。
# 不带 `_` 前缀：.gitignore 的 `worker/scripts/wuying-dm/_*` 会挡掉，那样进不了镜像分发。
_AMBIG_MUTE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ambig-mute.txt")


def _ambig_muted(nick: str) -> bool:
    try:
        with open(_AMBIG_MUTE, encoding="utf-8") as f:
            names = {_norm(ln) for ln in f if ln.strip() and not ln.lstrip().startswith("#")}
    except Exception:
        return False
    return _norm(nick) in names


def _preview_kind(preview: str) -> str:
    """把"我读到的是什么"翻译成运营看得懂的一句话。

    2026-07-28 起卡片正文直接贴 preview，于是运营收到的是「最新一条『置顶』」
    「最新一条『[早上好]』」——**看了根本不知道发生了什么**，等于没推（那次复盘
    里标了"未做"）。preview 是渲染给人看的列表摘要，取到 UI 标签/贴纸时它压根
    不是客户说的话，卡片必须说清这一点，否则运营会以为客户真的发了「置顶」。
    """
    p = (preview or "").strip()
    if not p:
        return "读到的是空白（客户消息渲染不出来）"
    if is_media_preview(p):
        return "客户发的是图片/视频/文件一类，内容读不到"
    if p.startswith("[") and p.endswith("]"):
        return f"读到的是表情贴纸『{p[:16]}』，不是客户说的话——真话可能被这张贴纸盖在下面"
    if any(t in p for t in ("撤回了一条消息", "该消息类型暂不能展示")):
        return f"读到的是系统提示『{p[:16]}』，客户的真实内容读不到"
    if p in ("置顶",) or any(t in p for t in ("对方已确认聊天", "回复较慢", "星期")):
        return f"读到的是界面标签『{p[:16]}』，不是消息内容——真话被这个标签挤掉了"
    return f"读到的内容是『{p[:30]}』"


def alert_backfill_suspect(nick: str, preview: str) -> None:
    """全量对账发现疑似漏检回复：【不自动补录】只推卡人工确认。
    原因：会话列表预览分不清方向——运营在 GUI 手动回过的那条不进 DB，两道 DB 守卫
    看不见它，自动补录会把我方手打内容错当客户消息 → 管线自动回一条(自言自语误发)。
    红点路径没这问题(红点只客户消息才有)，全量扫失去该保证 → 检测自动化、确认留给人。
    去重：同一条(昵称+预览)只报一次、永不重复(台账协议见 _AMBIG_LEDGER 上方注释)。"""
    key = _norm(nick) + "|" + _norm(preview)[:40]
    now = time.time()
    led = {}
    try:
        with open(_BACKFILL_LEDGER, encoding="utf-8") as f:
            led = json.load(f)
    except Exception:
        pass
    if key in led:
        return
    try:
        _http("POST", "rpc/emit_captcha_alert", body={
            "p_account_id": ACCOUNT_ID,
            "p_level": "p1_orange",
            "p_type": "dm_backfill_suspect",
            "p_message": (
                f"疑似漏检回复：「{nick}」最新一条不在系统里。{_preview_kind(preview)}。"
                "请点进这个会话看真实内容——是客户发的→把原文发给 Claude 补录接管；"
                "是运营手动回的→忽略本卡"
            ),
            "p_metadata": {"nickname": nick, "preview": preview[:200], "source": "dm_fullscan"},
        })
        led[key] = now
        with open(_BACKFILL_LEDGER, "w", encoding="utf-8") as f:
            json.dump(led, f)
        print(f"  [alert] 兜底疑似漏检已推 Lark: {nick}")
    except Exception as e:
        print(f"  [warn] 兜底疑似告警失败(不致命): {e}")


def alert_ambiguous_nickname(nick: str, n_convs: int, preview: str = "") -> None:
    """同昵称歧义转人工告警。去重：同一歧义昵称在本号下【只报一次、永不重复】——
    2026-07-19 用户指令收紧为按【昵称】去重(原按 昵称+预览, 该客户每发新内容都再报,
    对结构性歧义是噪声)。代价(接受)：报过后该昵称即便再发新内容也不再提醒——歧义是
    结构问题(本号下 N 个同名会话), 靠人工消歧/合并会话根治, 一次提醒足够; 要重置删台账
    json。台账协议见 _AMBIG_LEDGER 上方注释。
    另：静音名单 _ambig_mute.txt 里的昵称【一次都不报】(见 _AMBIG_MUTE 注释)。"""
    if _ambig_muted(nick):
        print(f"  [skip] 同昵称歧义已静音(名单 ambig-mute.txt): {nick}")
        return
    key = _norm(nick)
    now = time.time()
    led = {}
    try:
        with open(_AMBIG_LEDGER, encoding="utf-8") as f:
            led = json.load(f)
    except Exception:
        pass
    if key in led:
        return
    try:
        _http("POST", "rpc/emit_captcha_alert", body={
            "p_account_id": ACCOUNT_ID,
            "p_level": "p1_orange",
            "p_type": "dm_nickname_ambiguous",
            "p_message": (
                f"同昵称歧义：「{nick}」在本号下有 {n_convs} 个同名会话，"
                "新回复未自动挂靠(防发错人)。请上云电脑人工回复该客户，并让 Claude 补录到正确会话"
            ),
            "p_metadata": {"nickname": nick, "conv_count": n_convs, "source": "dm_capture"},
        })
        led[key] = now
        with open(_AMBIG_LEDGER, "w", encoding="utf-8") as f:
            json.dump(led, f)
        print(f"  [alert] 同昵称歧义已推 Lark: {nick}")
    except Exception as e:
        print(f"  [warn] 同昵称歧义告警失败(不致命): {e}")


def match_name(row_name, by_name):
    rn = _norm(row_name)
    if rn in by_name:
        return by_name[rn]
    hits = [v for k, v in by_name.items() if k and (k in rn or rn in k)]
    return hits[0] if len(hits) == 1 else None


def classify(preview, hit):
    # UI 噪声护栏：列表 preview 偶尔抓到的是抖音 PC 的 UI 元素 (语音/视频时长气泡
    # "00:15"、未读数徽标 "1"/"292"、纯数字标签) 而非真实消息文本。这些落到
    # suspect 分支会被 --write-messages 当客户回复入库 → dm-notify-watch 推首回
    # 假阳卡。2026-06-25 饭粒账号 "00:15" / "292" 假阳事件根因。
    p = (preview or "").strip()
    if not p:
        return "no_reply"
    if _TIME_RE.fullmatch(p):       # 时长气泡: 00:15 / 26:00
        return "no_reply"
    if _BADGE_RE.match(p):          # 未读徽标: 1 / 99 / 292
        return "no_reply"
    if p.isdigit():                 # 兜底其它纯数字 UI 标签
        return "no_reply"

    sent_fp = _norm(hit["sent"])[:FP_LEN]
    prev_fp = _norm(p)[:FP_LEN]
    if sent_fp and prev_fp and (sent_fp.startswith(prev_fp) or prev_fp.startswith(sent_fp)):
        return "no_reply"
    if any(s in p for s in SYS_NOTICE):
        return "no_reply"
    if _is_noise_preview(preview):
        return "no_reply"  # 状态行/纯时间戳(在线/关闭会话/09:58/6-18) → 非客户回复
    return "suspect"


# ─────────────────────────────────────────────────────────────
# --write-messages 模式 (PR #453 ish, 2026-06-22 起):
# suspect 行 → 找对应 conversations → 写一条 role='customer' message →
# 推进 stage ice_break → nurture (照搬 worker/dm_ios_wda_inbox_poll.py pattern).
# 配套触发 /api/cron/dm-notify-watch 即时推 Lark @ 群.
# ─────────────────────────────────────────────────────────────

def find_active_conversation(customer_name, account_id):
    """按 (messaging_account_id, customer_name) 拿最近 1 条 conversation. 同昵称多 conv
       时取 created_at 最新. 没找到返 None."""
    rows = _http("GET", "conversations", {
        "select": "id,customer_name,stage,douyin_user_id,org_id,created_at",
        "messaging_account_id": f"eq.{account_id}",
        "customer_name": f"eq.{customer_name}",
        "order": "created_at.desc",
        "limit": "1",
    }) or []
    return rows[0] if rows else None


def last_customer_content(conv_id):
    """拉 conversation 最后一条 role='customer' message content. 防重复入库."""
    rows = _http("GET", "messages", {
        "select": "id,content",
        "conversation_id": f"eq.{conv_id}",
        "role": "eq.customer",
        "order": "created_at.desc",
        "limit": "1",
    }) or []
    return rows[0] if rows else None


def write_customer_message(conv, preview, dry_run):
    """写一条 customer message + 推进 stage (照 worker/dm_ios_wda_inbox_poll.py).
       messages CHECK 约束: role IN ('ai','customer') / status IN ('draft',
       'pending_approval','approved','sending','sent','failed'). 客户回复用
       role='customer' + status='sent' + sent_at=now (与 web-chat 入站路径一致)."""
    import datetime as _dt
    now_iso = _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    msg_body = {
        "conversation_id": conv["id"],
        "role": "customer",
        "content": preview,
        "status": "sent",
        "sent_at": now_iso,
        "org_id": conv.get("org_id"),
    }
    if dry_run:
        print(f"  [DRY] INSERT messages role=customer conv={conv['id'][:8]} content={preview[:30]!r}")
        if conv.get("stage") == "ice_break":
            print(f"  [DRY] UPDATE conversations SET stage='nurture' WHERE id={conv['id'][:8]}")
        return True
    try:
        _http("POST", "messages", body=msg_body)
        if conv.get("stage") == "ice_break":
            _http("PATCH", "conversations",
                  params={"id": f"eq.{conv['id']}"},
                  body={"stage": "nurture"})
        stage_after = "nurture" if conv.get("stage") == "ice_break" else conv.get("stage")
        print(f"  ✓ {(conv.get('customer_name') or '')[:16]:<16} | inserted (stage→{stage_after})")
        return True
    except Exception as e:
        print(f"  ✗ {(conv.get('customer_name') or '')[:16]:<16} | {e}")
        return False


def main():
    # CLI flags. --write-messages 默认关闭, 保留只读语义兼容 second-touch 老用例.
    # --dry-run 在 --write-messages 之上不真写, 仅 print 将写的 sql.
    write_messages = "--write-messages" in sys.argv
    dry_run = "--dry-run" in sys.argv

    miss = [k for k, v in [("AKKE_ACCOUNT_ID", ACCOUNT_ID), ("SUPABASE_URL", SUPABASE_URL),
                           ("DB 鉴权", _BEARER)] if not v]
    if miss:
        sys.exit(f"[X] env missing: {', '.join(miss)}")

    win = find_douyin()
    if win is None:
        sys.exit("[X] 没找到抖音窗口（前台最大化了吗？）")
    mode_tag = "WRITE-DRY" if (write_messages and dry_run) else ("WRITE" if write_messages else "READ-ONLY")
    print(f"=== inbox_uia account={ACCOUNT_ID[:8]} mode={mode_tag} ===")

    rows = parse_rows(collect_texts(win))
    print(f"读到 {len(rows)} 个会话行")
    if not rows:
        sys.exit("[X] 没解析到会话行 —— 确认停在【私信会话列表】（不是某人主页/某个会话内）")

    by_name = load_sent_dms()
    print(f"本账号发送记录: {len(by_name)} 人\n")

    no_reply, suspect, outside = 0, [], 0
    for name, preview in rows:
        hit = match_name(name, by_name)
        if not hit:
            outside += 1
            continue
        if hit.get("ambiguous"):
            print(f"  · {name[:16]:<16} | 同昵称歧义({hit.get('conv_count', 2)} 会话) → 不自动挂靠, 转人工")
            alert_ambiguous_nickname(name, hit.get("conv_count", 2), preview)
            continue
        if classify(preview, hit) == "no_reply":
            no_reply += 1
            print(f"  · {name[:16]:<16} | no_reply")
        else:
            # suspect 行带上 matched conversation 的 customer_name (来自 hit, 已 norm 过)
            # 供 write 路径精确 lookup conversations 行.
            suspect.append((name, preview, hit["name"]))

    print(f"\n汇总：no_reply={no_reply}  可疑={len(suspect)}  不在发送记录={outside}")

    if not write_messages:
        # 原只读模式：可疑跳过、不写库。second-touch 调用方依赖此语义。
        if suspect:
            print("可疑（预览变了但非已知系统消息 → 跳过不处理，留作参考）:")
            for name, preview, _ in suspect:
                print(f"  ? {name[:16]:<16} | 预览='{preview[:28]}'")
        print("\n本工具只读：不标 replied、不写库。谁真回了由人工另行处理。")
        return

    # --write-messages 模式：suspect → 找 conv → 写 customer message → 推 stage.
    print(f"\n=== {mode_tag} 处理 {len(suspect)} 条可疑 ===")
    written, skipped_dup, skipped_no_conv, errors = 0, 0, 0, 0
    for name, preview, matched_name in suspect:
        conv = find_active_conversation(matched_name, ACCOUNT_ID)
        if not conv:
            print(f"  ⚠ {name[:16]:<16} | 找不到 conversation (customer_name={matched_name!r}), 跳过")
            skipped_no_conv += 1
            continue
        last = last_customer_content(conv["id"])
        if last and _norm(last["content"]) == _norm(preview):
            print(f"  · {name[:16]:<16} | 重复 (匹配 msg {last['id'][:8]}…), 跳过")
            skipped_dup += 1
            continue
        ok = write_customer_message(conv, preview, dry_run)
        if ok:
            written += 1
        else:
            errors += 1
    print(f"\n写入={written}  重复跳过={skipped_dup}  没找到conv跳过={skipped_no_conv}  失败={errors}")
    if dry_run:
        print("\n⚠ DRY-RUN: 上述全为预演,实际未写库. 去掉 --dry-run 真写.")


if __name__ == "__main__":
    main()
