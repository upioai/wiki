"""企微好友单聊 AI 自动代回 主循环（方案B·架构①，2026-06-29）。

云电脑 Python 端只做 GUI I/O：截屏 VL 读客户消息 → 调 TS 端点拿小艳回复 → GUI 逐字打字发送。
大脑/DB/Langfuse/成本全在 TS（/api/wecom/chat/reply）。详见 docs/superpowers/specs/2026-06-11-wecom-chat-ai-1on1-design.md。

每 tick：
  风控门(工作时间+日/时上限) → VL 看有无未读外部会话 → 点开最上未读会话 → VL 读会话状态 →
    · 新好友刚通过、还没真人对话 → POST opener 拿破冰招呼（首触，服务端幂等去重）
    · 有客户新消息 → 去重 → POST generate 拿回复（代回）
  → 拟人延迟 → 点输入框+逐字 SendInput+发送键 → POST confirm 落库。

⚠️ AKKE_WECOM_DRY_RUN 默认 =1（只读+生成+打印，不真发）。验证读取/回复/坐标 OK 后改 0 才真发。
⚠️ 主号无隔离 → 灰度从严：日限默认 20、时限 8，确认稳了再调高。

env（.env 同目录）：
  ANTHROPIC_API_KEY=<OpenRouter key>           VL 用
  AKKE_OCR_MODEL=qwen/qwen3-vl-30b-a3b-instruct
  WECOM_CHAT_ENDPOINT=https://<域名>/api/wecom/chat/reply
  WECOM_CHAT_SECRET=<与 Vercel 同值>
  AKKE_WECOM_C_INPUT=145,913     AKKE_WECOM_C_SEND=919,952     AKKE_WECOM_C_SESSION1=52,56
  AKKE_WECOM_SEND_KEY=enter|ctrl+enter (默认 enter)
  AKKE_WECOM_TYPE_MODE=unicode|paste (默认 unicode 逐字)
  AKKE_WECOM_PERSONA=xiaoxia|xiaofan|xiaowen|xiaowu  (本机绑定的销售身份壳；留空=默认小艳)
  AKKE_WECOM_DRY_RUN=1   AKKE_WECOM_DAILY_LIMIT=20   AKKE_WECOM_HOURLY_LIMIT=8
  AKKE_WECOM_PER_CUSTOMER_DAILY=6   AKKE_WECOM_HOURS=9-21
  AKKE_WECOM_MIN_INTERVAL=25  AKKE_WECOM_MAX_INTERVAL=70
  # 7 天召回·主动发起腿（Block B，默认关）：
  AKKE_WECOM_RECALL_ENABLED=0        开则每 tick 经端点 claim_recall 拉待发召回、GUI 主动搜人发
  AKKE_ACCOUNT_ID=<企微销售号 accounts.id>   派单/回执按它（=派单 cron 的目标号）
  AKKE_WECOM_C_SEARCH=60,30          顶部搜索框坐标（measure_wecom_coords.py 量）
  AKKE_WECOM_RECALL_CLAIM_LIMIT=2    每 tick 最多领几条召回
  AKKE_WECOM_RECALL_DAILY_LIMIT=5    召回【独立】日限,跟被动 DAILY_LIMIT 分开算、互不占额度
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime

# 版本号：每次改动递增。启动行打印 + 启动自检，杜绝"云电脑跑的到底哪版"对不上。
VERSION = "v2026-07-23.nonext-suppress"  # detect_unread_rows 连会话名返回 + 非外部会话名 300s 抑制缓存：治「橙红头像被 VL 误判成未读红点(旺德福)→ 每轮 churn 挤掉真客户→不够灵敏」。外部客户永不进抑制=零漏接。含 test-22 全部

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(WORK_DIR, "wecom_reply_status.json")
COUNTER_FILE = os.path.join(WORK_DIR, "wecom_reply_counter.json")
STOP_FLAG = os.path.join(WORK_DIR, "wecom_reply_STOP")  # 存在即停（一键停发）
SENT_TEXTS_FILE = os.path.join(WORK_DIR, "wecom_sent_texts.json")  # 我方近发文案(防自问自答)
REPLIED_FILE = os.path.join(WORK_DIR, "wecom_replied_ledger.json")  # 每会话已回过的客户消息(防重复回)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(WORK_DIR, ".env"), override=True)
except Exception:  # noqa: BLE001
    pass

KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
BASE = os.environ.get("AKKE_OCR_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.environ.get("AKKE_OCR_MODEL", "qwen/qwen3-vl-30b-a3b-instruct")
ENDPOINT = os.environ.get("WECOM_CHAT_ENDPOINT", "")
SECRET = os.environ.get("WECOM_CHAT_SECRET", "")

DRY_RUN = os.environ.get("AKKE_WECOM_DRY_RUN", "1") != "0"
DAILY_LIMIT = int(os.environ.get("AKKE_WECOM_DAILY_LIMIT", "20"))
# 读会话截屏裁掉左侧导航+会话列表的比例。列表分隔条可拖，拖宽后标题被切时调大/调小此值
CROP_RATIO = float(os.environ.get("AKKE_WECOM_CROP_RATIO", "0.12"))
HOURLY_LIMIT = int(os.environ.get("AKKE_WECOM_HOURLY_LIMIT", "8"))
PER_CUSTOMER_DAILY = int(os.environ.get("AKKE_WECOM_PER_CUSTOMER_DAILY", "6"))
MIN_INTERVAL = int(os.environ.get("AKKE_WECOM_MIN_INTERVAL", "25"))
MAX_INTERVAL = int(os.environ.get("AKKE_WECOM_MAX_INTERVAL", "70"))
SEND_KEY = os.environ.get("AKKE_WECOM_SEND_KEY", "enter").lower()
TYPE_MODE = os.environ.get("AKKE_WECOM_TYPE_MODE", "unicode").lower()
# 销售身份壳：本机绑定哪个销售号就设哪个（xiaoxia/xiaofan/xiaowen）。
# 留空 = 端点回退默认人设（小艳）。每台云电脑一个销售号 → 一个 persona。
PERSONA = os.environ.get("AKKE_WECOM_PERSONA", "").strip()
_hrs = os.environ.get("AKKE_WECOM_HOURS", "9-21").split("-")
HOUR_START, HOUR_END = int(_hrs[0]), int(_hrs[1])

# 7 天召回·主动发起腿（Block B，默认关）。开则每 tick 在被动代回之后经端点 claim_recall
# 拉「给某个沉默好友发的当天召回话术」→ GUI 主动搜到人 → 身份门 → 点开 → 复用 _deliver 发。
RECALL_ENABLED = os.environ.get("AKKE_WECOM_RECALL_ENABLED", "0") == "1"
# 本机企微销售号在 accounts 表的 id（=派单 cron 的目标号）。召回 claim/complete 按它。
ACCOUNT_ID = os.environ.get("AKKE_ACCOUNT_ID", "").strip()
RECALL_CLAIM_LIMIT = int(os.environ.get("AKKE_WECOM_RECALL_CLAIM_LIMIT", "2"))
# 召回【独立】日限，跟被动代回的 DAILY_LIMIT 完全分开、各算各的（不共用计数）。
# 召回发送计入 counter['recall_sent']，被动计入 counter['sent']，互不占额度。
RECALL_DAILY_LIMIT = int(os.environ.get("AKKE_WECOM_RECALL_DAILY_LIMIT", "5"))

MAX_CONSEC_FAILURES = 3

# 红蓝对抗测试白名单：这些会话名即使没带@微信徽标（红方测试号是企微号，VL 会把
# is_external 判成 false）也按外部客户放行代回。逗号分隔，归一化包含匹配。
# ⚠️ 只在红蓝对抗测试期间设置；测完必须从 .env 删掉并重启，否则同名内部同事会被 AI 代回。
TEST_ALLOW = [s.strip() for s in os.environ.get("AKKE_WECOM_TEST_ALLOW", "").split(",") if s.strip()]
# 红蓝测试专注模式：只轮询【当前打开的会话】（通道①），跳过红点遍历（通道②）和召回（通道③）
# ——loop 不再切走会话，响应最快、测试确定性最高。⚠️ 开着期间不服务其他客户，测完必须删。
TEST_FOCUS_ONLY = os.environ.get("AKKE_WECOM_TEST_FOCUS_ONLY", "0") == "1"


def log(*a):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]", *a, flush=True)


def _coord(name: str, default):
    v = os.environ.get(name)
    if not v:
        return default
    try:
        fx, fy = v.split(",")
        return (int(fx), int(fy))
    except Exception:  # noqa: BLE001
        return default


C_INPUT = _coord("AKKE_WECOM_C_INPUT", (145, 913))
C_SEND = _coord("AKKE_WECOM_C_SEND", (919, 952))
C_SESSION1 = _coord("AKKE_WECOM_C_SESSION1", (52, 56))
# 顶部搜索框（召回主动发起腿：搜目标客户名）。首次部署用 measure_wecom_coords.py 量准。
C_SEARCH = _coord("AKKE_WECOM_C_SEARCH", (60, 30))


# ── VL ──
def _vision(b64: str, prompt: str, mt: int = 600) -> str:
    payload = {"model": MODEL, "max_tokens": mt, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
        {"type": "text", "text": prompt}]}]}
    last = None
    for attempt in range(3):  # 无影→OpenRouter 链路偶发断连(Remote end closed/SSL EOF)，重试消化
        try:
            req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}, method="POST")
            # 40s：qwen-vl 正常 <15s 回。90s×3重试时网络坏天气单次读屏最坏卡 4.5min，
            # 把整个 tick 拖死、检测时延爆表（2026-07-19 13:26-13:29 无日志空洞实例）。
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise last


def _salvage_objects(t: str) -> list:
    """从(可能被 max_tokens 截断的)文本里抢救所有【已闭合】的气泡对象，逐个 loads。
    气泡是扁平结构({"text":..,"side":..,"y":..})。外层 {"messages":[...]} 被截断时永不闭合，
    所以遇到不闭合的 { 要【右移一位继续找里层】，而不是放弃。截断处那个残缺对象自然丢弃。"""
    out, i, n = [], 0, len(t)
    while i < n:
        if t[i] != "{":
            i += 1
            continue
        depth, j, in_str, esc, closed = 0, i, False, False, False
        while j < n:
            c = t[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    closed = True
                    break
            j += 1
        if closed:
            try:
                obj = json.loads(t[i:j + 1])
                if isinstance(obj, dict) and ("text" in obj or "messages" in obj):
                    out.append(obj)
            except Exception:  # noqa: BLE001
                pass
            i = j + 1  # 跳过整个已闭合对象
        else:
            i += 1  # 这个 { 到截断处没闭合(多半是外层 wrapper) → 右移找里层完整气泡
    return out


def _pjson(txt: str):
    t = txt.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.lower().startswith("json"):
            t = t[4:]
    t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # 截断兜底：VL 输出被 max_tokens 截断(Unterminated string/Expecting value)时，
        # 抢救已完整的气泡对象重组成 {"messages":[...]}，别让一次截断吞掉整轮新消息。
        objs = _salvage_objects(t)
        if objs:
            log(f"  [pjson] JSON 截断，抢救出 {len(objs)} 个完整对象")
            return {"messages": objs}
        raise


# ── 窗口 / 截图 / 点击 ──
def _screen():
    import pyautogui

    return pyautogui.size()


def _find_wework_window():
    try:
        import pygetwindow as gw
    except Exception:  # noqa: BLE001
        return None
    cands = []
    for w in gw.getAllWindows():
        t = (getattr(w, "title", "") or "")
        if "企业微信" not in t and "WeWork" not in t:
            continue
        if "菜单" in t or "menu" in t.lower():
            continue
        try:
            area = int(getattr(w, "width", 0) or 0) * int(getattr(w, "height", 0) or 0)
        except Exception:  # noqa: BLE001
            area = 0
        cands.append((area, w))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0], reverse=True)
    return cands[0][1]


def _force_foreground_maximize(hwnd) -> None:
    """ctypes 硬置前+最大化：比 pygetwindow 的 activate/maximize 在无影上稳
    （后者常只 activate 没真最大化 → 窗口没铺满屏 → 按屏幕比例算的坐标落偏）。
    alt 键技巧绕开 Windows 前台锁（前台进程之外 SetForegroundWindow 会被拒）。"""
    import ctypes

    user32 = ctypes.windll.user32
    SW_RESTORE, SW_MAXIMIZE, VK_MENU, KEYUP = 9, 3, 0x12, 0x0002
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    try:
        user32.keybd_event(VK_MENU, 0, 0, 0)          # ALT 按下（骗过前台锁）
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(VK_MENU, 0, KEYUP, 0)      # ALT 抬起
    except Exception:  # noqa: BLE001
        user32.SetForegroundWindow(hwnd)


def _is_maximized_fullscreen() -> bool:
    """校验企微前台窗口是否真铺满屏（宽高 ≥ 屏幕 90%）。没铺满 = 抢窗口没成，
    这轮坐标会落偏 → 调用方跳过本轮，宁可不发也不往没最大化的窗口瞎点。"""
    w = _find_wework_window()
    if w is None:
        return False
    try:
        import pyautogui

        sw, sh = pyautogui.size()
        return (getattr(w, "width", 0) or 0) >= sw * 0.9 and (getattr(w, "height", 0) or 0) >= sh * 0.9
    except Exception:  # noqa: BLE001
        return False


def focus_wecom() -> bool:
    w = _find_wework_window()
    if w is None:
        log("  [focus] 未找到企微窗口")
        return False
    hwnd = getattr(w, "_hWnd", None)
    if hwnd:
        try:
            _force_foreground_maximize(hwnd)
        except Exception:  # noqa: BLE001
            try:
                w.activate(); w.maximize()  # 兜底走 pygetwindow
            except Exception:  # noqa: BLE001
                pass
    else:
        try:
            if getattr(w, "isMinimized", False):
                w.restore()
            w.activate()
            w.maximize()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(1.0)
    # 校验真最大化：没铺满屏就判抢窗口失败（坐标会落偏），本轮不动作
    if not _is_maximized_fullscreen():
        log("  [focus] 企微未铺满屏(抢窗口失败)，跳过本轮避免点空")
        return False
    return True


def _wecom_is_foreground() -> bool:
    """校验当前前台窗口是不是企微。SendInput 打字是全局的——打到一半企微失焦（弹窗/
    人为切窗），剩下的话术会打进别的应用甚至发错人。每段打字前、按发送键前都要过这道。"""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 255)
        t = buf.value or ""
        return ("企业微信" in t) or ("WeWork" in t)
    except Exception:  # noqa: BLE001
        return False


def _shot(name: str = "_wecom.png"):
    import pyautogui

    os.makedirs("screenshots", exist_ok=True)
    path = os.path.join("screenshots", name)
    pyautogui.screenshot(path)
    return path


def _b64(path: str) -> str:
    return base64.b64encode(open(path, "rb").read()).decode()


def _shot_chat_area(name: str) -> str:
    """整屏截图后裁掉左侧导航条+会话列表，只留聊天区喂 VL。
    稀疏会话(消息少、大片空白)整屏喂 VL 常漏读最靠下的气泡——2026-07-02 橘枳
    「衣柜怎么算」被漏读、误判 sender=self 跳过。裁掉列表还避免把列表里的
    消息预览文字混进气泡。
    比例 0.12：2560 宽下导航条+列表右缘 ~308px(12%)、最左气泡起点 ~332px(13%)，
    0.15 会切进气泡把「我通过了…」截成半句(文本变了→服务端指纹去重失效→重复回)。
    裁剪失败回退整屏(宁可退化不中断)。"""
    path = _shot(name)
    try:
        from PIL import Image

        im = Image.open(path)
        w, h = im.size
        im.crop((int(w * CROP_RATIO), 0, w, h)).save(path)
    except Exception as e:  # noqa: BLE001
        log("  [crop] 裁剪失败(退回整屏):", e)
    return path


def _read_bottom_line() -> dict | None:
    """底部窄带二读：主读判「末尾是我方/无待回」时的复核。客户紧贴我方长回复
    下面发的小气泡（『可以多色搭配吧』）VL 主读常漏看——裁出聊天区最底部一条
    横带（高度 55%~93%，物理排除上方大海报/长历史干扰），只问最底一条的文字+左右。"""
    p = ('这是企业微信会话窗口的底部区域截图。只回严格JSON：'
         '{"text":"最靠下的那条消息气泡的文字","side":"left或right"}。'
         '气泡整体靠左=left(客户发的)，靠右=right(自己发的)。'
         '没有任何气泡就回 {"text":"","side":""}。图片/表情等非文字气泡 text 写'
         '「[图片]」「[表情]」。忽略输入框、工具栏图标和居中的时间戳/系统提示。')
    # 前台校验：主读有此闸、二读一直裸奔——console 盖在企微上时二读会把日志当聊天
    # （2026-07-17 'click[未读会话...]' 被当客户消息真调了 generate）。
    if not _wecom_is_foreground():
        log("  [read] 企微不在前台，跳过底部带二读")
        return None
    path = _shot("_wecom_bottom.png")
    try:
        from PIL import Image

        im = Image.open(path)
        w, h = im.size
        # 带 0.35~0.84：带顶 0.55→0.35 罩住偏上的气泡；带底 0.93→0.84 物理排除
        # 输入框工具栏（0.93 会把工具栏图标罩进来，VL 读成幽灵「[图片]」气泡，
        # 2026-07-17 红蓝测试实例）。
        im.crop((int(w * CROP_RATIO), int(h * 0.35), w, int(h * 0.84))).save(path)
        d = _pjson(_vision(_b64(path), p, mt=200))
        # 只有带内读到【left 气泡】才直接采信。带内最底是 right/读空都走整幅回退——
        # 长会话里最新的客户气泡可能在带缘之外（0.84 下缘正好切掉 21:05「价格要是合适」
        # 实例），带内的 right 气泡不能证明「无待回」。
        if ((d or {}).get("text") or "").strip() and (d or {}).get("side") == "left":
            return d
    except Exception as e:  # noqa: BLE001
        log("  [read] 底部带二读(横带)失败:", e)
    # 整幅聊天区问「最靠下的一条气泡」，位置无关：稀疏会话(气泡全在顶部)和
    # 长会话(最新气泡贴着输入框上缘)都能兜住。
    try:
        return _pjson(_vision(_b64(_shot_chat_area("_wecom_bottom_full.png")), p, mt=200))
    except Exception as e:  # noqa: BLE001
        log("  [read] 底部带二读(整幅回退)失败:", e)
        return None


def _bottom_line_pending(b: dict | None, name: str, is_ours=None, led=None) -> str | None:
    """底部带二读结果是否构成待回：left + 非空 + 非我方近发文案/非样板/本会话没回过。
    纯函数可单测。"""
    bt = ((b or {}).get("text") or "").strip()
    if (b or {}).get("side") != "left" or not bt:
        return None
    check_ours = is_ours if is_ours is not None else _is_our_recent_message
    if check_ours(bt) or _BOILERPLATE_RE.search(bt) or _TIMESTAMP_RE.match(bt) \
            or _CONSOLE_ARTIFACT_RE.search(bt) or _is_already_replied(name, bt, led=led):
        return None
    return bt


# 标题缓存：标题在同一会话内不变，却每次现读——网络抖一下就成单点故障，把成功的
# 气泡读取整个拖下水（2026-07-19 12:57 SSL EOF → 退整幅 → 漏「不是颗粒板」实例）。
# 读成功即缓存；失败/读空用缓存顶上；只在【切会话】时作废（_reset_title_cache）。
_TITLE_CACHE = {"t": None}


def _reset_title_cache():
    _TITLE_CACHE["t"] = None


def _read_title_fullshot() -> str:
    """整屏截图只问会话标题。会话列表分隔条可拖，列表变宽时裁剪会切进标题，
    VL 只看到「@微信」徽标 → 同轮重读同一张裁剪图也救不回（系统性而非随机）。
    整屏里标题永远完整，名字读空时用这个兜底，消息仍用裁剪图结果。
    缓存优先：标题在同一会话内不变，缓存命中直接返回，省掉稳态每 tick 的全屏截图+VL；
    缓存只在切会话时作废（_reset_title_cache，含召回身份门——那里先 reset 强制现读）。"""
    if _TITLE_CACHE["t"]:
        return _TITLE_CACHE["t"]
    p = ('这是企业微信截图。只回严格JSON：{"customer_name":"聊天区顶部的会话标题'
         '(对方昵称，右侧的@微信徽标若有则一并带上)"}。读不到就给空串。')
    title = ""
    try:
        d = _pjson(_vision(_b64(_shot("_wecom_title.png")), p, mt=200))
        title = ((d or {}).get("customer_name") or "").strip()
    except Exception as e:  # noqa: BLE001
        log("  [read] 整屏读标题失败:", e)
    if not _name_is_blank(title):
        _TITLE_CACHE["t"] = title
    return title


def click_norm(fx: int, fy: int, label: str = ""):
    import pyautogui

    W, H = _screen()
    x, y = int(fx / 1000 * W), int(fy / 1000 * H)
    log(f"  click[{label}] -> px({x},{y})")
    pyautogui.click(x, y)


def move_norm(fx: int, fy: int):
    import pyautogui

    W, H = _screen()
    pyautogui.moveTo(int(fx / 1000 * W), int(fy / 1000 * H), duration=0.4)


def scroll_chat_bottom():
    """打开会话后【狠滚到最底】：确保 VL 读到的是最新一条，而不是可视区里的旧消息。
    长会话(聊了很多轮/发过图)光滚几次到不了真底 → 客户最新那条在下面没进截图 → 漏读。
    故大幅加量：移到聊天区多次大步长下滚 + End 键兜底，滚到不能再滚。"""
    import pyautogui

    W, H = _screen()
    pyautogui.moveTo(int(0.55 * W), int(0.5 * H))  # 移到右侧聊天区中部(可滚区)
    for _ in range(14):
        pyautogui.scroll(-3000)
        time.sleep(0.08)
    try:
        pyautogui.press("end")  # 部分场景 End 直达底部，兜底
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.2)


# ── 逐字 Unicode 输入（ctypes SendInput，绕 IME）──
def _type_unicode(text: str):
    import ctypes
    from ctypes import wintypes

    KEYEVENTF_UNICODE, KEYEVENTF_KEYUP, INPUT_KEYBOARD = 0x0004, 0x0002, 1

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    send = ctypes.windll.user32.SendInput
    # 按 UTF-16 code unit 发（不是按 Python 字符）：emoji 等增补平面字符(>0xFFFF)的码点
    # 塞进 16 位 wScan 会截断成乱码；UTF-16 把它拆成一对 surrogate、逐 unit 发才正确。
    units = text.encode("utf-16-le")
    for i in range(0, len(units), 2):
        code = int.from_bytes(units[i:i + 2], "little")
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(0, code, flags, 0, None)))
            send(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        if not (0xD800 <= code <= 0xDBFF):  # 高位代理后不停顿，保证代理对连续送达
            time.sleep(random.uniform(0.04, 0.16))  # 拟人字间延迟


def _type_paste(text: str):
    import pyautogui
    import pyperclip

    pyperclip.copy(text)
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "v")


def type_reply(text: str):
    if TYPE_MODE == "paste":
        _type_paste(text)
    else:
        _type_unicode(text)


def press_send():
    import pyautogui

    if SEND_KEY in ("ctrl+enter", "ctrlenter"):
        pyautogui.hotkey("ctrl", "enter")
    else:
        pyautogui.press("enter")


# ── 发图（内联图片·剪贴板粘贴）──
# 官方产品海报由端点按触发词命中后回 posterUrls；这里下载→放进 Windows 剪贴板→Ctrl+V 粘进
# 企微输入框→发送键。pyperclip 只能放文本，图片必须走 win32clipboard 的 CF_DIB。
def _download_image(url: str) -> str:
    os.makedirs("screenshots", exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1].lower() or ".jpg"
    path = os.path.join("screenshots", f"_poster_{abs(hash(url)) % 10**8}{ext}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    with open(path, "wb") as f:
        f.write(data)
    return path


def _copy_image_to_clipboard(img_path: str):
    """PIL 转 BMP → 去掉 14 字节 BMP 文件头得到 DIB → 塞进剪贴板 CF_DIB。"""
    import io

    import win32clipboard  # pywin32
    from PIL import Image

    img = Image.open(img_path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "BMP")
    dib = buf.getvalue()[14:]  # BMP 文件头 14 字节之后即 DIB
    buf.close()
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def send_image(url: str) -> bool:
    """下载 → 拷进剪贴板 → 点输入框 Ctrl+V → 等企微渲染预览 → 发送。返回是否真发。"""
    import pyautogui

    try:
        path = _download_image(url)
        _copy_image_to_clipboard(path)
    except Exception as e:  # noqa: BLE001
        log("  [send_image] 下载/剪贴板失败，跳过这张图:", e)
        return False
    click_norm(*C_INPUT, label="输入框(图)")
    time.sleep(0.4)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.2)  # 等企微把图片渲染成待发预览
    press_send()
    time.sleep(1.0)
    return True


# ── 端点 ──
def _post(body: dict) -> dict:
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + SECRET, "Content-Type": "application/json"}, method="POST")
    # 120s：长历史+画像合并的 generate 偶尔 >60s，客户端先放弃会造成「服务端已落库、
    # 回复无人收、重试被判老话」的吞噬（2026-07-17 21:19「价格要是合适」实例）。
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


# ── 防自问自答：记我方近发文案，代回前比对（VL 偶把我方消息误判成客户消息 → 会回复自己）──
def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", s or "").strip()


def _atomic_json_dump(obj, path: str):
    """临时文件+原子替换：防半写入损坏（状态文件坏=护栏裸奔，比不写还危险）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _load_sent_texts() -> list:
    if not os.path.exists(SENT_TEXTS_FILE):
        return []
    try:
        return json.load(open(SENT_TEXTS_FILE, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        # 文件存在但读不出 = 损坏。护栏裸奔风险，必须大声喊，不能静默当空。
        log(f"  ⚠️ [state] 发送缓冲文件损坏({e})，防自回护栏降级！建议停发检查 {SENT_TEXTS_FILE}")
        return []


def _record_sent_texts(texts: list):
    try:
        arr = _load_sent_texts()
        for t in texts:
            n = _norm_text(t)
            if n:
                arr.append(n)
        _atomic_json_dump(arr[-120:], SENT_TEXTS_FILE)  # 环形缓冲(补录原始段落后条数增多,放宽到 120)
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠️ [state] 发送缓冲写入失败({e})")


def _is_our_recent_message(text: str, sent_list: list | None = None) -> bool:
    # 模糊匹配：VL 偶把我方消息读串一两个字(如"咱们家房子"→"咱们房子")，精确匹配会漏、
    # 就又回复自己。改成"高相似即判我方消息"，容忍 VL OCR 抖动。真实客户回复不可能跟
    # 我方话术 ≥88% 相似，误伤概率极低。sent_list 可注入(单测用)，默认读缓冲文件。
    from difflib import SequenceMatcher

    n = _norm_text(text)
    if not n:
        return False
    for s in (sent_list if sent_list is not None else _load_sent_texts()):
        if not s:
            continue
        if n == s:
            return True
        if len(n) >= 8 and SequenceMatcher(None, n, s).ratio() >= 0.88:
            return True
    return False


# ── 护栏③ 每会话「回复台账」：同一会话的同一条客户消息只回一次，永不重复 ──
# 不管 VL 怎么误判发送方，只要这条消息在本会话已回过(模糊比对，容忍读串字) → 不再回。
# 直接钉死「用户没发新消息就不重复发」，与 VL 判左右准不准无关。按会话名分桶避免误伤别的客户。
def _load_replied() -> dict:
    if not os.path.exists(REPLIED_FILE):
        return {}
    try:
        d = json.load(open(REPLIED_FILE, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠️ [state] 回复台账文件损坏({e})，防重复护栏降级！建议停发检查 {REPLIED_FILE}")
        return {}


def _ledger_buckets_for(name: str, led: dict) -> list:
    """取该客户的台账桶：精确 key 优先；再模糊匹配 key（VL 常把同一昵称读成多个变体，
    如 巭孬嫑勥烎 → 奚舜翼舜炎/奚舜董舜贤…，名字读飘会让台账查空 → 重复回）。"""
    from difflib import SequenceMatcher

    key = _norm_text(name)
    out = []
    if key in led:
        out.append(led[key])
    for k, v in led.items():
        if k != key and len(key) >= 3 and SequenceMatcher(None, key, k).ratio() >= 0.75:
            out.append(v)
    return out


def _is_already_replied(name: str, text: str, led: dict | None = None) -> bool:
    from difflib import SequenceMatcher

    n = _norm_text(text)
    if not n:
        return False
    for bucket in _ledger_buckets_for(name, led if led is not None else _load_replied()):
        for prev in bucket:
            if n == prev:
                return True
            if len(n) >= 6 and SequenceMatcher(None, n, prev).ratio() >= 0.90:
                return True
    return False


def _drop_replied_lines(name: str, text: str, led: dict | None = None) -> str | None:
    """burst 逐行过台账：答过的行剔掉，只留没答过的。VL 漏读右侧我方回复气泡时，
    burst 会把已答过的旧客户消息和新消息收成一段（2026-07-02 橘枳
    '衣柜怎么算\\n全包吗\\n用什么板材？'——前两条已答过又被整段重答）。
    整段模糊比对对"旧+新"组合永远不命中，去重粒度必须到行。全剔空 → None。"""
    led = led if led is not None else _load_replied()
    kept = [l for l in text.split("\n")
            if l.strip() and not _is_already_replied(name, l, led=led)]
    return "\n".join(kept) if kept else None


def _record_replied(name: str, text: str):
    try:
        led = _load_replied()
        key = _norm_text(name)
        n = _norm_text(text)
        if not n:
            return
        bucket = led.get(key, [])
        bucket.append(n)
        led[key] = bucket[-10:]  # 每会话留近 10 条
        # 会话桶封顶 80，超出丢最早的键
        if len(led) > 80:
            for k in list(led.keys())[: len(led) - 80]:
                led.pop(k, None)
        _atomic_json_dump(led, REPLIED_FILE)
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠️ [state] 回复台账写入失败({e})")


def _is_test_allowed(name: str) -> bool:
    """会话名是否命中红蓝对抗测试白名单（归一化包含匹配，容忍 VL 读名带徽标/空白）。"""
    n = _norm_text(re.sub(r"\s*@\s*[一-鿿\w]+\s*$", "", name or ""))
    if not n:
        return False
    for a in TEST_ALLOW:
        an = _norm_text(a)
        if an and (an in n or n in an):
            return True
    return False


# ── 风控状态 ──
def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_counter() -> dict:
    try:
        d = json.load(open(COUNTER_FILE, encoding="utf-8"))
        if d.get("date") == _today():
            return d
    except Exception:  # noqa: BLE001
        pass
    return {"date": _today(), "sent": 0, "recall_sent": 0, "hour": datetime.now().hour, "sent_hour": 0, "per_customer": {}}


def _save_counter(c: dict):
    try:
        _atomic_json_dump(c, COUNTER_FILE)
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠️ [state] 计数器写入失败({e})")


def _bump_counter(c: dict, customer: str, key: str = "sent"):
    # key='sent' = 被动代回；key='recall_sent' = 召回。两者各自独立计数、互不占额度。
    c[key] = c.get(key, 0) + 1
    if key == "sent":
        # 时限/客户连发是被动代回专用护栏，只对 sent 生效；召回不碰这两个。
        if c.get("hour") != datetime.now().hour:
            c["hour"], c["sent_hour"] = datetime.now().hour, 0
        c["sent_hour"] += 1
        c["per_customer"][customer] = c["per_customer"].get(customer, 0) + 1
    _save_counter(c)


def _heartbeat(action: str, c: dict):
    try:
        _atomic_json_dump({"ts": datetime.now().isoformat(), "version": VERSION, "date": c["date"],
                           "sent_today": c["sent"], "dry_run": DRY_RUN, "last_action": action},
                          STATUS_FILE)
    except Exception:  # noqa: BLE001
        pass


def _in_work_hours() -> bool:
    h = datetime.now().hour
    return HOUR_START <= h < HOUR_END


# ── 读 ──
# （旧 detect_unread 已废：红点检测由 detect_unread_rows 返回坐标，供双通道②点击）
# 小图气泡列举提示词（diff 定位读 / 底部裁剪读共用）：小图少泡，VL 只做抄字+判左右
_SMALL_BUBBLES_PROMPT = (
    "这是企业微信聊天区的一小段截图。只回严格JSON："
    '{"messages":[{"text":"消息文字","side":"left或right",'
    '"x":气泡中心距图片左边的相对位置(0到1000的整数),"y":气泡中心距图片顶部的相对位置(0到1000的整数)}]}。'
    "列出图中所有消息气泡，每条必须带 x 和 y。被【上边缘】切到一半的气泡不要列(那是旧消息)；"
    "但【贴着下边缘】的气泡必须列出(那是最新消息，即使被切到一点也要列、text 写读得到的部分)。"
    "side：气泡靠左=left(对方发的)，靠右=right(自己发的)，按位置不按颜色。"
    "忽略居中的时间戳/系统提示。图片/表情/语音等非文字气泡 text 写「[图片]」"
    "「[表情]」「[语音]」。带引用块的气泡只取正文。一个都没有回空数组。")


def _geom_normalize(msgs: list) -> list:
    """几何归代码（小图读取版）：顺序由 y 排、左右由 x 定（x<500=left）。VL 的列举
    顺序不可信——2026-07-19 21:00 诊断实锤：六泡条带 VL 按「先左列后右列」分栏列举，
    右气泡全排最后 → 取尾即我方 → 永远 sender=self。x/y 缺失时回退 VL 原值。"""
    out = []
    for m in msgs:
        m = dict(m)
        try:
            x = float(m.get("x"))
            if 0 <= x <= 1000:
                m["side"] = "left" if x < 500 else "right"
        except (TypeError, ValueError):
            pass
        out.append(m)
    try:
        ys = [float(m.get("y")) for m in out]
        if out and all(0 <= y <= 1000 for y in ys):
            out = [m for _, m in sorted(zip(ys, out), key=lambda t: t[0])]
    except (TypeError, ValueError):
        pass
    return out


def read_open_conversation() -> dict | None:
    """读当前打开会话。★不再让 VL 判"哪条最新"(它会被大气泡/空白带偏)，改让它把聊天区
    每条消息【从上到下按顺序全列出来】(带左/右)——VL 只做"照顺序抄"(擅长)，"谁最新"由代码取
    列表最后一条(确定)。小夏那种消息少、顶部对齐、下面一片空白的会话也不受影响。"""
    p = ("这是企业微信【已打开的某个会话】截图。只回严格JSON："
         '{"customer_name":"顶部会话标题(对方昵称)","is_external":true或false,'
         '"is_new_friend":true或false,'
         '"messages":[{"text":"消息文字","side":"left或right","y":气泡中心距图片顶部的相对位置(0到1000的整数)}]}。'
         "★messages：只列出聊天区里【最靠下的最多 8 条】消息气泡。★★先找到画面里【最底下】"
         "的那条气泡，从它开始向上数，超过 8 条就舍弃最上面的——【绝对不能漏掉最底下的气泡】。"
         "输出按从上到下顺序排列(最下面/最新的排最后一个)。忽略居中的时间戳和"
         "系统提示(如「你已添加了XX」「现在可以开始聊天了」)、忽略空白区域和最底部的输入框/工具栏。"
         "★y：每条气泡必须带 y——气泡中心到整张图【顶部】的距离，按图片总高度归一成 0~1000 的整数"
         "(顶部=0，底部=1000)。"
         "side：气泡整体【靠窗口左边】=left(对方/客户发的)；【靠右边】=right(自己发的)。"
         "**按气泡靠左还是靠右判断，不要只看颜色或内容**。一条都读不到就给空数组 []。"
         "若某条气泡里带【引用块】(引用之前某条消息的灰色小字部分)，text 只取该气泡的"
         "**正文**，引用块内容一律忽略不要带出来。"
         "客户发的图片/表情包/语音/视频/名片等非文字消息，text 统一写成「[图片]」「[表情]」"
         "「[语音]」这类标记，不要描述图片内容。"
         "is_new_friend：会话里【没有任何左右气泡、只有居中的「已添加/可以开始聊天了/通过了朋友」系统提示】=true；"
         "只要有过任意一条左/右气泡就=false。"
         "is_external：对方是外部微信客户(带@微信标记)=true，内部同事=false。")
    # 截屏前校验企微在前台——发送侧早有此闸，读取侧一直裸奔：黑窗/别的窗口盖在上面时
    # 截到的是日志文本，VL 会把它编成"聊天"（2026-07-02 把 loop 自己的日志当客户消息回了）。
    if not _wecom_is_foreground():
        log("  [read] 企微不在前台(会拍到别的窗口)，本轮跳过")
        return None
    # ── 结构性读法（test-14）：气泡列举永远发生在【小图】上 ──────────────────
    # 「多泡大图里别漏最底一条」是 VL 系统性失败的任务（横带/y坐标/提示词锚定均救不回，
    # 2026-07-17~19 反复实锤）。底部条带必须按【固定像素高】裁而不是按比例——比例裁剪
    # 的泡数随会话密度缩放（55% 在密集会话里裁出 12 泡照样漏「甲醛」，物证 2026-07-19
    # 20:25），固定 385px ≈ 3~5 泡 = diff 小图的成功尺寸。下缘再收 45px 剔工具栏行
    # （防幽灵[图片]）。读空（稀疏会话泡在上部/空会话——本来就少泡）才退整幅。
    try:
        im = _chat_region_shot()
        os.makedirs("screenshots", exist_ok=True)
        bpath = os.path.join("screenshots", "_wecom_read_bottom.png")
        bot = im.height - 45
        im.crop((0, max(0, bot - 385), im.width, bot)).save(bpath)
        d0 = _pjson(_vision(_b64(bpath), _SMALL_BUBBLES_PROMPT, mt=2000))
        msgs = d0 if isinstance(d0, list) else (d0 or {}).get("messages") or []
        msgs = _geom_normalize([m for m in msgs if isinstance(m, dict)])
        # 诊断日志：条带读到什么必须可见——黑盒每次都要靠传图取证（2026-07-19 教训）
        _tail_dbg = "、".join(
            f"{(m.get('side') or '?')}:{str(m.get('text') or '')[:12]}" for m in msgs[-3:])
        log(f"  [read] 底部条带 {len(msgs)} 泡" + (f"，末3: {_tail_dbg}" if msgs else "（空→退整幅）"))
        if msgs:
            title = _read_title_fullshot()
            if not _name_is_blank(title):
                return {"customer_name": title,
                        "is_external": bool(re.search(r"@\s*微信", title)),
                        "is_new_friend": False, "messages": msgs}
            log("  [read] 底部裁剪读到气泡但标题读空，退整幅")
    except Exception as e:  # noqa: BLE001
        log("  [read] 底部裁剪读失败(退整幅):", e)
    try:
        d = _pjson(_vision(_b64(_shot_chat_area("_wecom_read.png")), p, mt=1400))
        # 「谁最新」=几何问题：按 y 排序由代码定，不信 VL 列举顺序（整幅无 x，_geom_normalize 只走 y 排）
        if d and d.get("messages"):
            d["messages"] = _geom_normalize(d["messages"])
        # 会话名读空(只剩@微信徽标)：多为裁剪切进标题(列表分隔条被拖宽)的系统性失败，
        # 重读同一张裁剪图救不回——改用整屏只问标题兜底，消息仍用裁剪图结果。
        if d and _name_is_blank((d.get("customer_name") or "")):
            title = _read_title_fullshot()
            if not _name_is_blank(title):
                log(f"  [read] 裁剪图名字读空，整屏兜底读到标题: {title!r}")
                d["customer_name"] = title
        return d
    except Exception as e:  # noqa: BLE001
        log("  [read] VL 失败:", e)
        return None


def humanize_delay(reply: str, elapsed_ms: float):
    target = min(20.0, max(5.0, len(reply) * 0.25))
    wait = max(0.0, target - elapsed_ms / 1000.0)
    if wait > 0:
        time.sleep(wait)


# 发送中止信号：_deliver 因【失去前台】中途中止时置 True。主循环据此把"已并入基线却没发成"
# 的这条【重置基线、下轮重新检出重试】(护栏③保幂等)，区别于护栏正常跳过(那种要吸收进基线、
# 不重试，否则死循环+空转烧 VL)。每次 _process_conversation 入口清零，只反映最近一次处理。
_ABORTED = {"v": False}

# test-22：像素 diff 说"变了"但 VL 把变化区域抄空(矛盾——多半 VL 漏抄了短气泡/贴底气泡) →
# 不信 VL 的"没有"、走一次全读复核(就是重启走的路)防漏抄；连续 EMPTY_DIFF_MAX 次全读都确认
# 无待回，才判噪音(已读回执/动画)、放行推进基线，防持续噪音每轮全读失控。
_EMPTY_DIFF = {"n": 0}
EMPTY_DIFF_MAX = int(os.environ.get("AKKE_WECOM_EMPTY_DIFF_MAX", "2"))


def _deliver(counter: dict, name: str, reply: str, conv_id, started: float,
             single_bubble: bool = False, poster_urls=None, poster_ids=None,
             count_key: str = "sent") -> bool:
    """把 reply 逐段 GUI 打字发出 →（若有）逐张发图 → confirm 落库 → 计数。返回是否真发。
    single_bubble=True（首触）：【文本】确定性只发一条；配图不受影响，照发。
      （2026-07-21 起首触也带资料：端点按抖音画像选好的配置清单版本。）
    poster_urls/poster_ids：端点回的图（官方海报 / 配置清单 / 布局图 / 效果图），
      文本发完后逐张内联发；0 张则退化为纯文本。"""
    poster_urls = poster_urls or []
    poster_ids = poster_ids or []
    if DRY_RUN:
        move_norm(*C_INPUT)
        _n_seg = len([s for s in re.split(r"\n+", reply) if s.strip()]) or 1
        log(f"  [DRY_RUN] 不真发。将发 文本 {_n_seg} 段 + 图片 {len(poster_urls)} 张。"
            "鼠标已移到输入框预测点，确认坐标对不对。")
        return False

    # 原始自然段（合并前）——必须一并记进防自回缓冲：随机合并后气泡里存的是"整块"，
    # 但 VL 读回时常按【单个自然段】读，拿单段比整块相似度不够会漏判 → 又自回。记原始段修此。
    # 「[图片:<id>]」是服务端海报去重记账标记，LLM 学舌带出来时绝不能当文字打给
    # 客户（2026-07-02 '[图片:config-568]' 字面量事故）。服务端已双剥，这里兜底。
    paras = [s for s in re.split(r"\n+", reply)
             if s.strip() and not re.fullmatch(r"\[图片[:：][^\]]*\]", s.strip())]
    if not paras:
        if reply.strip() and re.fullmatch(r"(\s*\[图片[:：][^\]]*\]\s*)+", reply):
            log("  回复只剩海报标记行，无可发文本，跳过")
            return False
        paras = [reply.strip()]
    if single_bubble:
        # 首触钉死一条：把换行/多空白并成单空格 → 只发 1 条，等客户回复后才由代回继续。
        segs = [re.sub(r"\s*\n+\s*", " ", reply).strip()]
    else:
        # 拟人化：不固定发 3 条，随机发 1~3 条气泡（把自然段随机分组合并，组内用空格连，
        # 避免再触发换行发送）。段落越多上限越高，但都不会超过 3。
        target = random.randint(1, min(3, len(paras)))
        if target >= len(paras):
            segs = paras
        else:
            per = -(-len(paras) // target)  # ceil：每组段数
            segs = [" ".join(paras[i:i + per]) for i in range(0, len(paras), per)]

    # 拟人延迟（按整条文案算一次） → 逐段 点输入框→清残留→打字→(前台校验)→发送
    humanize_delay(reply, (time.time() - started) * 1000)
    import pyautogui

    sent_segs = []  # 已真正发出的段：中途中止也要记进缓冲，护栏才认得它们
    for seg in segs:
        # 打字前校验企微在前台：失焦时 SendInput 会把话术打进别的窗口甚至发错人（显式失败）
        if not _wecom_is_foreground():
            log("  [deliver] 企微失去前台(弹窗/被切窗)，中止本条发送避免打错窗口")
            _record_sent_texts(sent_segs)
            _ABORTED["v"] = True  # 中止≠已处理：主循环据此重置基线、下轮重新检出重试
            return False
        click_norm(*C_INPUT, label="输入框")
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "a")  # 清掉输入框可能的残留(上次失败/人手打了半句)，防拼接错发
        time.sleep(0.15)
        pyautogui.press("delete")
        time.sleep(0.2)
        type_reply(seg)
        time.sleep(0.4)
        if not _wecom_is_foreground():
            log("  [deliver] 打字后企微失去前台，中止发送（输入框残留下轮会被清掉）")
            _record_sent_texts(sent_segs)
            _ABORTED["v"] = True  # 同上：中止要重试，不能让消息被基线吞掉
            return False
        press_send()
        sent_segs.append(seg)
        time.sleep(0.8)

    # 记下我方刚发出的：原始段落 + 合并后气泡 + 整条 → 无论 VL 把它读回成整条/合并块/单段，
    # 缓冲都能命中、不会被当成客户消息（治"随机合并后自然段漏判致自回"）。
    _record_sent_texts(paras + segs + [reply])

    # 文本发完 → 逐张发官方海报（首触 single_bubble 不带 poster，这里自然为空）
    sent_poster_ids = []
    for idx, url in enumerate(poster_urls):
        if send_image(url):
            sent_poster_ids.append(poster_ids[idx] if idx < len(poster_ids) else "")
            time.sleep(random.uniform(0.6, 1.4))  # 图间拟人间隔
        else:
            log(f"  海报发送失败，跳过: {url}")

    # 发成功 → 回调落 AI 消息（带已发海报 id，端点追加 [图片:<id>] 标记供下轮会话去重）
    # conv_id 为 None（本地兜底话术，如非文字消息固定回复）时不 confirm，只本地记账。
    if conv_id:
        try:
            _post({"action": "confirm", "conversationId": conv_id, "content": reply,
                   "posterIds": [pid for pid in sent_poster_ids if pid]})
        except Exception as e:  # noqa: BLE001
            log("  [endpoint confirm] 失败（消息已发出但未落库，需人工核对）:", e)
    _bump_counter(counter, name, count_key)
    _lim = RECALL_DAILY_LIMIT if count_key == "recall_sent" else DAILY_LIMIT
    _tag = "召回" if count_key == "recall_sent" else "被动"
    log(f"  已发送并落库(文本 + {len(sent_poster_ids)} 图)。今日{_tag} {counter.get(count_key, 0)}/{_lim}")
    return True


# loop 黑窗日志被 VL 当聊天读回来的特征：行首时间戳「[HH:MM:SS]」/「小艳回复：」/
# 「客户[」。命中任一 → 整次读取作废（2026-07-02：黑窗盖在企微上，4 条"客户消息"
# 全是我方日志行，基于垃圾读取真发出了一条回复）。
_CONSOLE_ARTIFACT_RE = re.compile(
    r"^\s*(\[\d{2}:\d{2}:\d{2}\]|小艳回复\s*[:：]|客户\[)"
    # loop 日志行特征扩充（2026-07-17）：PowerShell 盖在企微上时，底部二读把
    # 'click[未读会话(y=50)] -> px(227,80)' 当客户消息真调了 generate。
    r"|click\[|->\s*px\(|\[DRY_RUN\]|\[endpoint\s|\[read\]|\[recall\]|待回客户消息|命中测试白名单")


def _looks_like_console_read(d: dict | None) -> bool:
    """任一消息行长得像 loop 自己的日志 → 这次读取拍到的不是聊天窗，作废。纯函数可单测。"""
    for m in (d or {}).get("messages") or []:
        if _CONSOLE_ARTIFACT_RE.search(((m or {}).get("text") or "")):
            return True
    return False


def _name_is_blank(name: str) -> bool:
    """会话名剥掉「@微信」徽标后是否为空。VL 偶发漏读标题只抄回徽标（客户[@微信]），
    这种名字打到端点会被归一成空串 → 400 还烧 tick 异常计数——预检跳过（2026-07-02）。"""
    return not re.sub(r"\s*@\s*微信\s*$", "", name or "").strip()


def _latest_msg(d: dict | None) -> dict:
    """从 read_open_conversation 的 messages 数组取【最后一条】= 最新消息。VL 只按顺序抄，
    取最后一条由代码定(确定性)，不再让 VL 判"哪条最新"。side→sender 映射。"""
    msgs = (d or {}).get("messages") or []
    if not msgs:
        return {"ok": False, "text": "", "sender": None}
    last = msgs[-1] or {}
    text = (last.get("text") or "").strip()
    side = last.get("side")
    sender = "customer" if side == "left" else "self" if side == "right" else "system"
    return {"ok": bool(text), "text": text, "sender": sender}


# 好友验证样板（"我通过了你的联系人验证请求，现在我们可以开始聊天了"）：企微系统
# 自动文案，渲染成左气泡、VL 会当客户消息读出来——但它不是客户开口，永远不该被代回
# （2026-07-02 橘枳被回了两遍，其中一遍还是裁剪切半句后指纹去重失效）。服务端同规则双侧防御。
# 变体：「我通过了你的联系人验证请求，现在我们可以开始聊天了」（对方通过）
#       「你已添加了XX，现在可以开始聊天了。」（我方添加，居中系统条，无"我们"二字——
#        2026-07-02 第一版正则漏了它，被当客户消息又回了一遍报价）
#       「你撤回了一条消息」/「"XX"撤回了一条消息」（撤回提示也是居中系统条，VL 读成
#        左气泡 → 被当客户消息代回，2026-07-17 云电脑实发一条）
_BOILERPLATE_RE = re.compile(r"联系人验证请求|现在(我们)?可以开始聊天|你已添加了|撤回了一条消息")

# 聊天区居中时间戳（「09:56」「昨天 09:56」「星期二 09:56」「2026年7月10日 09:56」）偶被
# VL 读成左气泡 → 被当客户消息代回（2026-07-17 A杨琳会话把 '09:56' 当消息真发了问候）。
# VL prompt 里"忽略时间戳"挡不住，必须确定性过滤：纯时间/日期样式一律不是人话。
_TIMESTAMP_RE = re.compile(
    r"^(?:今天|昨天|前天|星期[一二三四五六日天]|周[一二三四五六日天]|\d{4}年\d{1,2}月\d{1,2}日)?"
    r"\s*(?:[01]?\d|2[0-3]):[0-5]\d$")


def _pending_customer_burst(d: dict | None, is_ours=None) -> str | None:
    """取【我方最后一条消息之后的所有连续客户消息】合并成一段。客户连发多条("定金交给谁"
    "什么材料的板材")→ 一次覆盖回复、不漏。
    从末尾往前收，只收【side==left(客户) 且不是我方发过的文案】的；遇到边界就停——
    边界 = side==right(我方气泡：程序发的 或 你从销售号手动敲的) 或 命中我方近发缓冲。
    这样：①程序发的不回(缓冲) ②你手动从销售号发的也不回(right 气泡) ③客户的照常回。
    末尾是我方消息(right)→tail 空→None(无待回，天然防自回/防抢答手动消息)。"""
    msgs = (d or {}).get("messages") or []
    if not msgs:
        return None
    check_ours = is_ours if is_ours is not None else _is_our_recent_message
    tail = []
    for m in reversed(msgs):
        side = (m or {}).get("side")
        t = ((m or {}).get("text") or "").strip()
        if not t:
            continue
        if _BOILERPLATE_RE.search(t):
            continue  # 好友验证样板 → 透明跳过（不待回、也不当边界）
        if _TIMESTAMP_RE.match(t):
            continue  # 时间戳被误读成气泡 → 透明跳过
        if side == "left" and not check_ours(t):
            tail.append(t)  # 客户消息 → 待回
        else:
            break  # 我方气泡(right,程序发/手动发)/我方文案/系统 → 边界，停
    if not tail:
        return None
    tail.reverse()
    return "\n".join(tail)  # 客户连发多条 → 合并（\n 仅作分隔喂给端点，不影响发送）


def _split_media(text: str) -> tuple:
    """把合并后的待回文本拆成 (文字行, 媒体标记行)。媒体标记是 VL 按约定输出的
    「[图片]」「[语音]」等。决策：纯表情不回；含图/语音回固定兜底；混合只答文字。纯函数可单测。"""
    media_re = re.compile(r"^\[(图片|表情|语音|视频|文件|名片|位置|链接|小程序)\]$")
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    text_lines = [l for l in lines if not media_re.match(l)]
    media_lines = [l for l in lines if media_re.match(l)]
    return text_lines, media_lines


def _process_conversation(d: dict | None, counter: dict, pending_override: str | None = None) -> bool:
    """对一个【已读出】的会话做决策与回复（首触/代回/三护栏）。返回是否真发了一条。
    读(在哪个会话、怎么截图)与决策分离——本函数不碰 GUI 读取，便于单测与双通道复用。
    pending_override：像素 diff 定位读（通道①根治路径）已确定的待回文本——跳过
    burst/二读的 VL 赌位置判定，但所有确定性护栏（白名单/我方文案/台账/媒体/限额）照跑。"""
    _ABORTED["v"] = False  # 本轮处理开始：清中止信号，只有 _deliver 真中止才会重新置位
    if not d:
        return False
    if _looks_like_console_read(d):
        log("  读取疑似拍到黑窗日志('小艳回复'/时间戳/日志格式)，作废本轮")
        return False
    latest = _latest_msg(d)
    name = (d.get("customer_name") or "").strip()
    if _name_is_blank(name):
        log(f"  会话名读空/只剩@微信徽标(VL 漏读标题: {name!r})，跳过本轮(下轮重读)")
        return False
    if not d.get("is_external"):
        if _is_test_allowed(name):
            log(f"  [redblue] {name} 命中测试白名单(AKKE_WECOM_TEST_ALLOW)，按外部客户放行")
        else:
            log(f"  非外部客户({name})，跳过")
            return False

    started = time.time()

    # 分流①：新好友刚通过、还没真人对话 → 主动首触（服务端幂等去重：已打过招呼回 alreadyGreeted）
    if d.get("is_new_friend") and latest.get("sender") != "customer":
        log(f"  新好友[{name}] 待首触，主动打招呼")
        try:
            resp = _post({"action": "opener", "customerName": name, "persona": PERSONA})
        except Exception as e:  # noqa: BLE001
            log("  [endpoint opener] 失败:", e)
            raise
        if resp.get("alreadyGreeted"):
            log("  端点判定已打过招呼，跳过")
            return False
        reply = (resp.get("reply") or "").strip()
        conv_id = resp.get("conversationId")
        if not reply:
            log("  端点无首触文案（可能生成空/异常），跳过")
            return False
        log(f"  小艳首触: {reply!r}")
        # 首触带的资料（2026-07-21）：端点按抖音画像里的户型选好清单版本回过来。
        # 拿不到抖音画像时是通用版，字段缺失/端点旧版本则为空列表 → 退化成纯文本首触。
        opener_urls = resp.get("posterUrls") or []
        opener_ids = resp.get("posterIds") or []
        if opener_urls:
            log(f"  首触随带资料 {len(opener_urls)} 张: {opener_ids}")
        # 首触【文本】钉死一条，发完等客户回复才由代回继续（幂等 alreadyGreeted 也已挡重复打招呼）。
        # single_bubble 只管文本分段，不影响配图 —— 图在文本后逐张发。
        return _deliver(counter, name, reply, conv_id, started, single_bubble=True,
                        poster_urls=opener_urls, poster_ids=opener_ids)

    # 分流②：有客户新消息 → 代回。
    # 取【最后一条我方消息之后的所有连续客户消息】合并 → 客户连发多条时一次覆盖回复、不漏。
    # 末尾是我方消息(right) → burst 为 None = 没有待回的客户新消息，跳过（也天然防自回）。
    text = pending_override if pending_override is not None else _pending_customer_burst(d)
    if not text:
        # 底部窄带二读复核：主读常漏看紧贴我方回复下面的客户小气泡（判成末尾=self）
        _bl = _bottom_line_pending(_read_bottom_line(), name)
        if _bl:
            log(f"  主读判无待回，底部带二读发现客户末条: {_bl!r} → 按待回处理")
            text = _bl
        else:
            _lastsender = latest.get("sender")
            log(f"  末尾是我方消息/无待回客户消息(最后一条 sender={_lastsender})，跳过")
            return False
    _nlines = text.count("\n") + 1
    log(f"  [待回客户消息] {_nlines} 条合并 · {text!r}")

    # 确定性护栏（不靠 VL 左右判断）：
    #   ① 是我方近发文案(模糊) → 跳（防自问自答）
    #   ② 等于会话名/联系人名 → VL 误读联系人/空会话，跳
    #   ③ 本会话已回过(模糊) → 不重复回
    if _is_our_recent_message(text):
        log(f"  读到的是我方近发文案，跳过避免自问自答: {text!r}")
        return False

    if _norm_text(text) == _norm_text(name):
        log(f"  读到的'消息'与会话名相同(VL 误读联系人名/空会话，非真实消息)，跳过: {text!r}")
        return False

    if _is_already_replied(name, text):
        log(f"  本会话已回过这条(护栏③防重复回)，跳过: {text!r}")
        return False

    # 护栏③逐行版：整段没命中也要逐行筛——VL 漏读我方右气泡时 burst 会把
    # 已答过的旧客户消息连带新消息收成一段，只回没答过的行。
    _remaining = _drop_replied_lines(name, text)
    if _remaining is None:
        log(f"  burst 各行均已回过(护栏③逐行)，跳过: {text!r}")
        return False
    if _remaining != text:
        log(f"  burst 含已答过的旧行，剔除后只回: {_remaining!r}")
        text = _remaining

    # 非文字消息确定性处理（不让 LLM 猜图）：纯表情 → 不回；含图/语音/视频等 →
    # 固定兜底话术请客户打字；文字+媒体混合 → 剔掉媒体标记、只答文字部分。
    _text_lines, _media_lines = _split_media(text)
    if not _text_lines:
        if all(l == "[表情]" for l in _media_lines):
            _record_replied(name, text)  # 纯表情=互动气氛，不回但记账，避免每轮重复处理
            log("  客户只发了表情，不回（已记台账）")
            return False
        fallback = "收到～图片/语音我这边看不太方便，您方便打字说一下吗？"
        log(f"  客户发的是非文字消息({_media_lines})，回固定兜底话术")
        sent = _deliver(counter, name, fallback, None, started, single_bubble=True)
        if sent:
            _record_replied(name, text)
        return sent
    if _media_lines:
        text = "\n".join(_text_lines)  # 混合：只把文字部分喂给端点

    log(f"  客户[{name}] 新消息: {text!r}")

    if counter["per_customer"].get(name, 0) >= PER_CUSTOMER_DAILY:
        log(f"  客户[{name}] 今日已达连发上限 {PER_CUSTOMER_DAILY}，跳过")
        return False

    # 调端点拿回复（指纹幂等：重复读到同一条 → duplicate → 不重复回）
    try:
        resp = _post({"action": "generate", "customerName": name, "message": text, "persona": PERSONA})
    except Exception as e:  # noqa: BLE001
        log("  [endpoint generate] 失败:", e)
        raise
    if resp.get("duplicate"):
        log("  端点判定重复消息，跳过")
        return False
    reply = (resp.get("reply") or "").strip()
    conv_id = resp.get("conversationId")
    if not reply:
        log("  端点无回复（可能 hard-gate 空/异常），跳过")
        return False
    poster_urls = resp.get("posterUrls") or []
    poster_ids = resp.get("posterIds") or []
    if poster_urls:
        log(f"  命中官方海报 {len(poster_urls)} 张，随文本一并发")
    log(f"  小艳回复: {reply!r}")
    sent = _deliver(counter, name, reply, conv_id, started,
                    poster_urls=poster_urls, poster_ids=poster_ids)
    if sent:
        # 逐行记账：下次 burst 不管怎么组合，行级比对都能命中(护栏③逐行)
        for _l in text.split("\n"):
            _record_replied(name, _l)
    return sent


# ── 双通道 tick ──────────────────────────────────────────────────────────────
# ── 像素 diff 定位读（通道①根治路径，2026-07-17 test-11）────────────────────
# 「屏幕哪里出现了新内容」是几何问题：当前聊天区截图与上一轮做像素差分，代码算出
# 变化区域 bbox，只裁那一小块（1~3 个气泡）喂 VL 做 OCR。VL 不再承担「在整屏十几个
# 气泡里别漏最底一条」（横带/y坐标/提示词锚定均反复失败的任务），新气泡物理上必在
# diff 区域内、不可能被漏。prev 为 None（启动/切会话/发完消息后）时走一次旧全读建基线。
_PREV_CHAT = {"im": None}


def _reset_chat_baseline():
    _PREV_CHAT["im"] = None


def _chat_region_shot():
    """右侧聊天区截图（排除左列表/顶栏/输入框光标区）。区域常量对应固定 UI 框架
    （列表分隔线/输入框位置），不是对消息位置的猜测。
    ⚠️ 左边界必须用 CROP_RATIO（与读屏裁剪同源）：客户气泡起点紧贴会话列表右缘
    （本机屏宽 ~19%），老哈希区写死 0.28W 把短的左气泡整个排除在监控区外——
    「画面变了、监控区没变」→ 判无新消息 → 漏检总根因（2026-07-17 22:32
    「你们能上门量尺吗」实锤）。"""
    import pyautogui

    W, H = _screen()
    x0 = int((CROP_RATIO + 0.005) * W)  # 会话列表右缘起，含全部左气泡
    # 下边界 0.87→0.92 屏高：对齐真实 UI 边界（聊天区/输入框分界线 ~0.93H）。
    # 0.87 不是任何 UI 边界，最新气泡(~86-88%)会骑在截图边缘被切半、再被
    # 「切半不列」规则丢掉（2026-07-19 12:06「量尺收费吗」实例）。光标在输入框
    # (~0.95H+)仍在区域外，不会引起 diff 噪声。
    region = (x0, int(0.06 * H), int(0.96 * W) - x0, int(0.86 * H))  # 覆盖 6%~92% 屏高
    return pyautogui.screenshot(region=region)


def _diff_new_bbox():
    """返回 ("FULL", None, im) 首轮建基线 / ("DIFF", bbox, im) 有变化 / ("NONE", None, im)。
    纯几何，无 VL。⚠️ 本函数【不】前移基线——基线只在调用方确认读取成功后 commit：
    VL 失败时基线原地不动，下轮 diff 重报同一区域自动重试。否则一次读取失败就把
    变化永久消费掉（2026-07-19 11:48「量尺收费吗」JSON 截断卡死实例）。"""
    from PIL import ImageChops

    im = _chat_region_shot()
    prev = _PREV_CHAT["im"]
    if prev is None or prev.size != im.size:
        return ("FULL", None, im)
    diff = ImageChops.difference(im.convert("L"), prev.convert("L")).point(
        lambda p: 255 if p > 28 else 0)
    bbox = diff.getbbox()
    if bbox is None:
        return ("NONE", None, im)
    return ("DIFF", bbox, im)


def _read_diff_bubbles(im, bbox) -> list:
    """裁 diff 区域(上扩 240px 带上紧邻的上一气泡做 burst 边界，下扩 60px)喂 VL 读气泡。
    小图少泡，VL 只做它从不失败的事：抄字 + 判左右。"""
    x0, y0, x1, y1 = bbox
    top = max(0, y0 - 240)
    bot = min(im.height, y1 + 60)
    crop = im.crop((0, top, im.width, bot))
    os.makedirs("screenshots", exist_ok=True)
    path = os.path.join("screenshots", "_wecom_diff.png")
    crop.save(path)
    try:
        # mt=2000：长增项气泡多条时 1000 仍截断 JSON（Unterminated string char~1400，2026-07-20 实例）；
        # 即便再截断，_pjson 也会抢救已闭合的气泡对象兜底，不会整轮报废。
        d = _pjson(_vision(_b64(path), _SMALL_BUBBLES_PROMPT, mt=2000))
        msgs = d if isinstance(d, list) else (d or {}).get("messages") or []
        return _geom_normalize([m for m in msgs if isinstance(m, dict)])
    except Exception as e:  # noqa: BLE001
        log("  [diff-read] VL 失败(基线不前移，下轮重试):", e)
        return None  # None=读取失败（区别于真空数组）→ 调用方不 commit 基线


def _screen_looks_dark() -> bool:
    """全屏近黑 = 锁屏/黑屏/远程断流。别硬跑（VL 全失败白烧钱），记日志跳过本轮。"""
    try:
        import pyautogui

        im = pyautogui.screenshot().convert("L").resize((32, 32))
        px = list(im.getdata())
        return sum(px) / len(px) < 12
    except Exception:  # noqa: BLE001
        return False


# 非外部会话短时抑制缓存（治「橙红头像被 VL 误判成未读红点」的 churn，2026-07-23）：
# 读到「非外部」的会话名 → 缓存 TTL 秒，期内 detect 到同名不再点开，避免每轮 churn 在
# 内部会话（旺德福那种彩色头像）上、把真客户挤后面。外部客户(@微信)永不进此缓存 → 零漏接。
# key = _recall_norm_name(会话名/标题)，value = 过期时间戳(time.time())。
_NONEXT_SUPPRESS: dict = {}
_NONEXT_SUPPRESS_TTL = 300.0


def detect_unread_rows() -> list:
    """VL 只看左侧会话列表窄条，返回每个【带红色未读数字圆点】行的 {y, name}：
    y=行中心纵向位置(0-1000 归一，相对全屏高，直接可用于 click_norm)；name=会话名(供抑制缓存匹配)。"""
    try:
        import pyautogui

        W, H = _screen()
        crop_w = int(0.24 * W)
        os.makedirs("screenshots", exist_ok=True)
        path = os.path.join("screenshots", "_wecom_list.png")
        pyautogui.screenshot(path, region=(0, 0, crop_w, H))
        p = ("这是企业微信左侧会话列表的截图。找出所有【会话名右侧/右上角带红色未读数字小圆点】的会话行，"
             '只回严格JSON：{"rows":[{"y":该行中心纵向位置(0-1000归一,相对整张图高度),'
             '"name":"该行会话名(每行顶部那行黑色粗体文字,不含时间和下方灰色预览)"}]}。'
             '没有红点回 {"rows":[]}。只认【会话名右侧那个红色数字圆点】；'
             '灰色免打扰小点、绿色图标、以及【左侧彩色/橙红色头像本身】都不算红点(头像不是红点)。')
        d = _pjson(_vision(_b64(path), p, mt=300))
        out = []
        for r in (d.get("rows") or []):
            y = r.get("y")
            if isinstance(y, (int, float)) and 0 <= y <= 1000:
                out.append({"y": int(y), "name": str(r.get("name") or "")})
        return out[:5]
    except Exception as e:  # noqa: BLE001
        log("  [unread-rows] VL 失败:", e)
        return []


# ── 7 天召回·主动发起腿（Block B，2026-07-16）───────────────────────────────
# 与被动代回的区别：被动是「点开来红点的会话回未读」；召回是「主动搜到一个沉默好友、
# 发一条当天的召回话术」。派单大脑在 Vercel cron（/api/cron/wecom-recall-dispatch），
# 这里只做 GUI I/O：claim_recall 拿话术 → 搜人 → 身份门 → 点开 → 复用 _deliver 发 →
# VL 探红点感叹号(被删) → complete_recall 回执。默认关，串在被动腿之后不抢窗口。
def _recall_norm_name(s: str) -> str:
    s = re.sub(r"\s*@\s*微信\s*$", "", s or "")
    return re.sub(r"\s+", "", s).strip()


def _recall_search_and_open(name: str):
    """点搜索框输入 name → 回车打开最匹配会话 → 整屏读标题做身份门。
    返回 (ok, detail)。ok=True 才算打开对了人（标题与 name 归一化匹配）；
    不匹配/读不到标题一律 False（绝不发，防重名发错人——身份门是唯一防线，无 DOM）。"""
    _reset_title_cache()  # 召回要切走会话，标题缓存作废（身份门必须现读，绝不能吃缓存）
    import pyautogui

    if not C_SEARCH:
        return (False, "no_search_coord")
    click_norm(*C_SEARCH, label="搜索框")
    time.sleep(0.5)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.2)
    if not _wecom_is_foreground():
        return (False, "lost_foreground")
    type_reply(name)
    time.sleep(1.5)  # 等搜索结果浮出
    if not _wecom_is_foreground():
        return (False, "lost_foreground")
    pyautogui.press("enter")  # 打开最匹配的第一个结果
    time.sleep(1.3)
    title = _read_title_fullshot()
    tn, nn = _recall_norm_name(title), _recall_norm_name(name)
    if not tn:
        return (False, "title_unread")  # 读不到标题 → 不确定打开了谁 → 不发
    # 归一化后完全相等，或（名字≥2字）互为子串 → 算同一人。短名不宽松（防误配）。
    if tn == nn or (len(nn) >= 2 and (nn in tn or tn in nn)):
        return (True, title)
    log(f"  [recall] 身份门不匹配：目标『{name}』打开的是『{title}』→ 不发")
    return (False, f"identity_mismatch:{title}")


def _recall_detect_deleted() -> bool:
    """发完后截图问 VL：最后发出的消息旁有没有红色感叹号 + 提示是不是『非好友/关系变更』。
    真删=True（永久出库、不再推剩余天数）；仅『发送中/网络失败』不算删=False（可下轮重试）。"""
    p = ('这是企业微信聊天窗口截图，我刚发出一条消息。只回严格JSON：'
         '{"deleted":true或false,"hint":"感叹号旁的提示文字，没有就空串"}。'
         'deleted=true 仅当：消息右侧有红色感叹号/发送失败，且提示明确是'
         '「对方不是你的好友/好友关系已变更/需要重新发送好友申请/开启了好友验证」这类被删信号。'
         '仅仅"发送中/网络异常/正在重试"不算 deleted。一切正常发出就 deleted=false。')
    try:
        d = _pjson(_vision(_b64(_shot_chat_area("_wecom_recall_check.png")), p, mt=200))
        return bool((d or {}).get("deleted"))
    except Exception as e:  # noqa: BLE001
        log("  [recall] 红点探测失败(当未删):", e)
        return False


def recall_send_one(item: dict, counter: dict):
    """处理一条召回：搜人→身份门→点开→_deliver 发→探红点。返回 (outcome, sent, deleted)。"""
    name = (item.get("customer_name") or item.get("customerName") or "").strip()
    message = (item.get("message") or "").strip()
    conv_id = item.get("conversation_id") or item.get("conversationId")
    if not name or not message:
        return ("skipped_bad_item", False, False)
    if not focus_wecom():
        return ("failed_focus", False, False)
    ok, detail = _recall_search_and_open(name)
    if not ok:
        return (f"skipped_{detail}", False, False)
    scroll_chat_bottom()
    time.sleep(0.3)
    # 复用 _deliver：逐段打字发 + 前台校验 + confirm 落 AI 消息（=本次召回进会话历史，
    # 客户回了就自清场）+ 计数【记 recall_sent、独立于被动 sent】。DRY_RUN 下 _deliver 只预览不发。
    sent = _deliver(counter, name, message, conv_id, time.time(), count_key="recall_sent")
    deleted = _recall_detect_deleted() if sent else False
    if deleted:
        log(f"  [recall] {name} 发后检出红点感叹号=已删 → 出库")
    return ("sent" if sent else "not_sent", sent, deleted)


def handle_recall(counter: dict) -> bool:
    """召回支线：经端点领一小批待发召回，逐条主动搜人发出并回执。默认关。"""
    if not (RECALL_ENABLED and ACCOUNT_ID and ENDPOINT):
        return False
    try:
        resp = _post({"action": "claim_recall", "accountId": ACCOUNT_ID, "limit": RECALL_CLAIM_LIMIT})
    except Exception as e:  # noqa: BLE001
        log("  [recall] claim_recall 失败:", e)
        return False
    items = (resp or {}).get("claimed") or []
    if not items:
        return False
    log(f"  [recall] claim 到 {len(items)} 条待召回")
    sent_any = False
    for it in items:
        disp_id = it.get("id")
        # 每条前查【召回独立日限】RECALL_DAILY_LIMIT（不占被动 DAILY_LIMIT）：满了把剩余 skip 回执释放。
        if counter.get("recall_sent", 0) >= RECALL_DAILY_LIMIT:
            log(f"  [recall] 已达召回日限 {RECALL_DAILY_LIMIT}，剩余召回 skip 回执释放")
            try:
                _post({"action": "complete_recall", "dispatchId": disp_id,
                       "status": "skipped", "errorMessage": "daily_cap"})
            except Exception:  # noqa: BLE001
                pass
            continue
        outcome, sent, deleted = recall_send_one(it, counter)
        # 回执映射：DRY_RUN→aborted(dry_run 留痕)；真发→sent；身份门/坐标未标等→skipped；其它→failed。
        if DRY_RUN:
            status, err = "aborted", f"dry_run:{outcome}"
        elif sent:
            status, err = "sent", None
        elif outcome.startswith("skipped"):
            status, err = "skipped", outcome
        else:
            status, err = "failed", outcome
        try:
            _post({"action": "complete_recall", "dispatchId": disp_id,
                   "status": status, "deleted": bool(deleted), "errorMessage": err})
        except Exception as e:  # noqa: BLE001
            log("  [recall] complete_recall 回执失败(消息可能已发，需人工核对):", e)
        if sent:
            sent_any = True
        time.sleep(random.uniform(3, 7))  # 召回条间拟人间隔
    return sent_any


def handle_one(counter: dict) -> bool:
    """双通道 tick：
    ①【轮询当前打开的会话】——正在聊的会话来新消息【不会出红点】(消息直接进聊天框)，
      只能靠轮询盯；聊天区像素与上轮相同就跳过 VL(省钱)。
    ②【遍历左侧红点会话】——其他客户来消息才有红点，逐个点开处理(每轮上限 3 个)。
    不变式：任何时刻只有一个会话是打开的(①盯)，其余全靠红点(②接) → 多客户不漏接。"""
    if _screen_looks_dark():
        log("  ⚠️ 屏幕近黑(锁屏/断流?)，跳过本轮")
        return False
    if not focus_wecom():
        return False

    sent_any = False
    _ABORTED["v"] = False  # 每轮开始清零：只反映本 tick 的发送中止，防短路时残留上轮值

    # 被动代回是否还有额度（日限 DAILY_LIMIT + 时限 HOURLY_LIMIT）。召回【不受此影响】、
    # 有自己独立的日限（RECALL_DAILY_LIMIT）→ 被动到限了，本轮照样能跑召回。
    passive_ok = (counter["sent"] < DAILY_LIMIT
                  and not (counter.get("hour") == datetime.now().hour
                           and counter.get("sent_hour", 0) >= HOURLY_LIMIT))
    if not passive_ok:
        log(f"  被动已到限（日 {counter['sent']}/{DAILY_LIMIT} 或本小时时限），本轮只跑召回")

    if passive_ok:
        # ── 通道①：当前打开的会话——像素 diff 定位读（test-11 根治路径）──
        # 首轮(基线为空)走旧全读建基线；之后每轮 diff：无变化=确定无新消息(几何结论)，
        # 有变化=只把变化小块喂 VL。发消息/切会话后重置基线。
        kind, bbox, im = _diff_new_bbox()
        if kind == "NONE":
            _EMPTY_DIFF["n"] = 0  # 无变化 → 复核 streak 清零（基线不动）
        elif kind == "FULL":
            _EMPTY_DIFF["n"] = 0
            scroll_chat_bottom()
            time.sleep(0.3)
            # ⚠️ 基线必须在【读取之前】拍（TOCTOU）：曾在读取后补拍基线，读取截图与
            # 基线截图之间到达的消息被拍进基线 → diff 永远视而不见 → 无声吞掉
            # （2026-07-19 13:41「甲醛」实例）。基线偏早只多触发一次空读，偏晚就是吞消息。
            baseline_im = _chat_region_shot()
            d = read_open_conversation()
            _PREV_CHAT["im"] = baseline_im
            if d and _process_conversation(d, counter):
                sent_any = True
                _reset_chat_baseline()  # 发了消息界面已变，下轮重建基线
            elif _ABORTED["v"]:
                # 检出待回但 _deliver 失前台中止 → 重置基线，下轮重新检出重试，
                # 不让已并入 baseline_im 的这条被无声吞掉（护栏正常跳过时 _ABORTED=False，不进这支）。
                _reset_chat_baseline()
        else:  # DIFF
            msgs = _read_diff_bubbles(im, bbox)
            if msgs is None:
                pass  # VL 读取失败 → 基线不前移，下轮 diff 重报同一区域自动重试
            else:
                tail = _pending_customer_burst({"messages": msgs})
                if tail:
                    _EMPTY_DIFF["n"] = 0
                    _PREV_CHAT["im"] = im  # 抄到待回才 commit 基线（消费与处理成事务）
                    title = _read_title_fullshot()
                    if _name_is_blank(title):
                        log("  [diff] 读到待回但标题读空，重置基线走下轮 FULL 兜底")
                        _reset_chat_baseline()
                    else:
                        ext = bool(re.search(r"@\s*微信", title))
                        d = {"customer_name": title, "is_external": ext,
                             "is_new_friend": False, "messages": msgs}
                        log(f"  [diff] 变化区域检出待回: {tail!r}")
                        if _process_conversation(d, counter, pending_override=tail):
                            sent_any = True
                            _reset_chat_baseline()
                        elif _ABORTED["v"]:
                            # 发送失前台中止 → 重置基线重试，不让这条被基线吞掉。
                            _reset_chat_baseline()
                else:
                    # ★test-22：像素说"变了"但 bbox 抄空(矛盾——多半 VL 漏抄了短气泡) → 不信 VL 的
                    # "没有"、【绝不急着推基线】，走一次【全读】复核(全读比 bbox 局部读更全、正是重启
                    # 走的路，实测能捞回漏抄的那条)。这就是把"重启能救回来"做成每轮自动、并加上限防失控。
                    _EMPTY_DIFF["n"] += 1
                    log(f"  [diff] 变化区抄空(第 {_EMPTY_DIFF['n']}/{EMPTY_DIFF_MAX} 次)，全读复核防漏抄…")
                    scroll_chat_bottom()
                    time.sleep(0.3)
                    baseline_im = _chat_region_shot()  # 读取前拍(TOCTOU 安全)
                    d = read_open_conversation()
                    if d and _process_conversation(d, counter):
                        sent_any = True  # 全读抄到了 bbox 漏的那条 → 处理掉
                        _EMPTY_DIFF["n"] = 0
                        _reset_chat_baseline()
                    elif _ABORTED["v"]:
                        _reset_chat_baseline()
                    elif _EMPTY_DIFF["n"] >= EMPTY_DIFF_MAX:
                        # 连续 N 次全读都确认无待回 → 判噪音(已读回执/动画)，放行推进基线、防每轮全读失控
                        log(f"  [diff] 连续 {EMPTY_DIFF_MAX} 次全读均无待回，判噪音、推进基线")
                        _PREV_CHAT["im"] = baseline_im
                        _EMPTY_DIFF["n"] = 0
                    # else：没到上限也没抄到 → 【基线不推进】，下轮 diff 再触发再给一次读的机会
                    #（真气泡一直在→迟早抄到；瞬时噪音→下轮消失、diff=NONE 自然停）

        # ── 通道②：红点会话逐个处理（上限 3/轮；点开读完红点自清）──
        # 测试专注模式：跳过——不点红点、不切会话，只盯当前打开的测试会话
        for _ in range(0 if TEST_FOCUS_ONLY else 3):
            rows = detect_unread_rows()
            if not rows:
                break
            now = time.time()
            # 抑制近期已判「非外部」的会话（治橙红头像被误判成红点 → 每轮 churn 挤掉真客户）：
            # 外部客户永不进 _NONEXT_SUPPRESS，故绝不会误抑制真客户。
            fresh = [r for r in rows
                     if _NONEXT_SUPPRESS.get(_recall_norm_name(r.get("name", "")), 0) < now]
            if not fresh:
                log(f"  [unread] {len(rows)} 个红点均为近期已判内部会话，跳过不重复点开"
                    f"（治头像误判 churn）")
                break
            r0 = fresh[0]
            y = r0["y"]
            click_norm(C_SESSION1[0], y, label=f"未读会话(y={y} {r0.get('name', '')[:8]})")
            time.sleep(1.2)
            scroll_chat_bottom()
            time.sleep(0.4)
            d = read_open_conversation()
            _reset_chat_baseline()  # 切了会话，通道① 的基线作废
            _reset_title_cache()  # 换了会话，标题缓存作废
            if d:
                if not d.get("is_external"):
                    # 缓存会话名(detect 列表名 + 读到的标题双 key)抑制 TTL 秒，防橙红头像每轮重点开
                    for nm in {_recall_norm_name(d.get("customer_name", "")),
                               _recall_norm_name(r0.get("name", ""))}:
                        if nm:
                            _NONEXT_SUPPRESS[nm] = now + _NONEXT_SUPPRESS_TTL
                if _process_conversation(d, counter):
                    sent_any = True
            time.sleep(random.uniform(2, 5))  # 会话间随机小间隔，拟人

    # ── 通道③：7 天召回·主动发起（默认关；串在被动腿之后，subprocess 级串行不抢窗口）──
    # 独立日限 RECALL_DAILY_LIMIT，不占被动额度；被动到限也照跑。测试专注模式下跳过（会搜人切走会话）。
    if RECALL_ENABLED and not TEST_FOCUS_ONLY:
        try:
            if handle_recall(counter):
                sent_any = True
        except Exception as e:  # noqa: BLE001
            log("  [recall] 支线异常(不影响被动代回):", e)

    if not sent_any:
        log("  本轮无新消息/无待回")
    return sent_any


def _startup_selfcheck() -> tuple:
    """启动自检：关键 env 必须齐、persona 建议设、记录初始分辨率(之后每 tick 校验——
    远程桌面换设备接入会改分辨率 → 归一化坐标全偏)。不合格直接拒启，别带病上岗。"""
    problems = []
    if not KEY:
        problems.append("缺 ANTHROPIC_API_KEY/OPENROUTER_API_KEY(.env)")
    if not ENDPOINT or not SECRET:
        problems.append("缺 WECOM_CHAT_ENDPOINT / WECOM_CHAT_SECRET(.env)")
    if "fly.dev" not in (ENDPOINT or "") and "localhost" not in (ENDPOINT or ""):
        problems.append(f"ENDPOINT 疑似不是 Fly 中转({ENDPOINT})——云电脑连不上 vercel.app")
    if not PERSONA:
        log("  ⚠️ 未设 AKKE_WECOM_PERSONA，将用默认人设(小艳)——销售号请设置对应身份")
    if TEST_ALLOW:
        log(f"  🔴 红蓝对抗测试白名单生效: {TEST_ALLOW} —— 测试结束务必从 .env 删掉 AKKE_WECOM_TEST_ALLOW 并重启")
    if TEST_FOCUS_ONLY:
        log("  🔴 测试专注模式：只轮询当前打开会话（跳过红点遍历+召回），期间不服务其他客户 —— 测完删 AKKE_WECOM_TEST_FOCUS_ONLY")
    if RECALL_ENABLED and not ACCOUNT_ID:
        problems.append("AKKE_WECOM_RECALL_ENABLED=1 但缺 AKKE_ACCOUNT_ID(召回按它 claim/回执，缺了不发)")
    if RECALL_ENABLED:
        log(f"  召回主动发起腿：开 · account={ACCOUNT_ID[:8] if ACCOUNT_ID else '缺'} · "
            f"搜索框坐标={C_SEARCH} · 每tick最多{RECALL_CLAIM_LIMIT}条")
    try:
        import pyautogui

        pyautogui.FAILSAFE = True  # 鼠标甩左上角紧急停
        w, h = pyautogui.size()
    except Exception as e:  # noqa: BLE001
        problems.append(f"pyautogui 不可用: {e}")
        w, h = 0, 0
    if problems:
        for p in problems:
            log("  ❌ 自检不过:", p)
        sys.exit(1)
    log(f"  自检通过 · 分辨率 {w}x{h} · persona={PERSONA or '(默认小艳)'} · 端点={ENDPOINT}")
    return w, h


def main():
    log(f"=== 企微自动代回 主循环启动 {VERSION} ===")
    log(f"DRY_RUN={DRY_RUN}  被动日限={DAILY_LIMIT} 时限={HOURLY_LIMIT} 客户连发={PER_CUSTOMER_DAILY}  召回独立日限={RECALL_DAILY_LIMIT}")
    log(f"工作时间 {HOUR_START}-{HOUR_END}  发送键={SEND_KEY}  打字模式={TYPE_MODE}")
    base_w, base_h = _startup_selfcheck()

    consec_fail = 0
    while True:
        if os.path.exists(STOP_FLAG):
            log("检测到 STOP 标志，停发退出。删除 wecom_reply_STOP 后重启恢复。")
            break
        counter = _load_counter()
        _heartbeat("tick", counter)

        # 分辨率校验：远程桌面换设备接入会改分辨率 → 坐标全偏 → 宁可跳过本轮
        try:
            import pyautogui

            _w, _h = pyautogui.size()
            if (_w, _h) != (base_w, base_h):
                log(f"  ⚠️ 分辨率变了 {base_w}x{base_h} → {_w}x{_h}(远程桌面换设备?)，跳过本轮避免坐标偏移")
                time.sleep(random.uniform(60, 120)); continue
        except Exception:  # noqa: BLE001
            pass

        if not _in_work_hours():
            log(f"非工作时间({datetime.now().hour}点)，休眠"); time.sleep(random.uniform(300, 600)); continue
        # 被动 & 召回各自独立判限：两者【都】到限才整轮休眠；否则进 handle_one 内部各自门控
        # （被动到限只跳过被动通道①②，召回仍按自己的 RECALL_DAILY_LIMIT 跑，不共用额度）。
        _passive_maxed = (counter["sent"] >= DAILY_LIMIT
                          or (counter.get("hour") == datetime.now().hour
                              and counter.get("sent_hour", 0) >= HOURLY_LIMIT))
        _recall_maxed = (not RECALL_ENABLED) or counter.get("recall_sent", 0) >= RECALL_DAILY_LIMIT
        if _passive_maxed and _recall_maxed:
            log(f"被动({counter['sent']}/{DAILY_LIMIT}) + 召回({counter.get('recall_sent', 0)}/{RECALL_DAILY_LIMIT}) 都到限，休眠")
            time.sleep(random.uniform(600, 1200)); continue

        try:
            sent = handle_one(counter)
            consec_fail = 0
            _heartbeat("sent" if sent else "idle", counter)
        except Exception as e:  # noqa: BLE001
            consec_fail += 1
            log(f"  tick 异常({consec_fail}/{MAX_CONSEC_FAILURES}):", e)
            _reset_chat_baseline()  # diff 基线可能已消费而处理未完成 → 重置，下轮全读兜底
            _heartbeat(f"error:{e}", counter)
            if consec_fail >= MAX_CONSEC_FAILURES:
                # 风控信号/连续异常 → 停发不硬重试（显式失败）。写 STOP 标志，
                # 让启动器的崩溃自重启锁住不再拉起，直到人工排查后删 STOP。
                log("连续异常达上限，停发退出。排查后删 wecom_reply_STOP 重启。建议先 DRY_RUN 复跑。")
                try:
                    open(STOP_FLAG, "w", encoding="utf-8").write(
                        f"{datetime.now().isoformat()} consec_fail>={MAX_CONSEC_FAILURES}: {e}\n")
                except Exception:  # noqa: BLE001
                    pass
                break

        time.sleep(random.uniform(MIN_INTERVAL, MAX_INTERVAL))  # 随机间隔，不固定周期


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("手动中止")
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        sys.exit(1)
