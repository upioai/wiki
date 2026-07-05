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
VERSION = "v2026-07-02.11"

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

MAX_CONSEC_FAILURES = 3


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


# ── VL ──
def _vision(b64: str, prompt: str, mt: int = 600) -> str:
    payload = {"model": MODEL, "max_tokens": mt, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
        {"type": "text", "text": prompt}]}]}
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"]


def _pjson(txt: str):
    t = txt.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.lower().startswith("json"):
            t = t[4:]
    return json.loads(t.strip())


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
    path = _shot("_wecom_bottom.png")
    try:
        from PIL import Image

        im = Image.open(path)
        w, h = im.size
        im.crop((int(w * CROP_RATIO), int(h * 0.55), w, int(h * 0.93))).save(path)
        return _pjson(_vision(_b64(path), p, mt=200))
    except Exception as e:  # noqa: BLE001
        log("  [read] 底部带二读失败:", e)
        return None


def _bottom_line_pending(b: dict | None, name: str, is_ours=None, led=None) -> str | None:
    """底部带二读结果是否构成待回：left + 非空 + 非我方近发文案/非样板/本会话没回过。
    纯函数可单测。"""
    bt = ((b or {}).get("text") or "").strip()
    if (b or {}).get("side") != "left" or not bt:
        return None
    check_ours = is_ours if is_ours is not None else _is_our_recent_message
    if check_ours(bt) or _BOILERPLATE_RE.search(bt) or _is_already_replied(name, bt, led=led):
        return None
    return bt


def _read_title_fullshot() -> str:
    """整屏截图只问会话标题。会话列表分隔条可拖，列表变宽时裁剪会切进标题，
    VL 只看到「@微信」徽标 → 同轮重读同一张裁剪图也救不回（系统性而非随机）。
    整屏里标题永远完整，名字读空时用这个兜底，消息仍用裁剪图结果。"""
    p = ('这是企业微信截图。只回严格JSON：{"customer_name":"聊天区顶部的会话标题'
         '(对方昵称，右侧的@微信徽标若有则一并带上)"}。读不到就给空串。')
    try:
        d = _pjson(_vision(_b64(_shot("_wecom_title.png")), p, mt=200))
        return ((d or {}).get("customer_name") or "").strip()
    except Exception as e:  # noqa: BLE001
        log("  [read] 整屏读标题失败:", e)
        return ""


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
    with urllib.request.urlopen(req, timeout=60) as r:
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
    return {"date": _today(), "sent": 0, "hour": datetime.now().hour, "sent_hour": 0, "per_customer": {}}


def _save_counter(c: dict):
    try:
        _atomic_json_dump(c, COUNTER_FILE)
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠️ [state] 计数器写入失败({e})")


def _bump_counter(c: dict, customer: str):
    c["sent"] += 1
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
def read_open_conversation() -> dict | None:
    """读当前打开会话。★不再让 VL 判"哪条最新"(它会被大气泡/空白带偏)，改让它把聊天区
    每条消息【从上到下按顺序全列出来】(带左/右)——VL 只做"照顺序抄"(擅长)，"谁最新"由代码取
    列表最后一条(确定)。小夏那种消息少、顶部对齐、下面一片空白的会话也不受影响。"""
    p = ("这是企业微信【已打开的某个会话】截图。只回严格JSON："
         '{"customer_name":"顶部会话标题(对方昵称)","is_external":true或false,'
         '"is_new_friend":true或false,'
         '"messages":[{"text":"消息文字","side":"left或right"}]}。'
         "★messages：只列出聊天区里【最靠下的最多 6 条】消息气泡，【严格按从上到下的先后"
         "顺序】依次列出(最下面/最新的排最后一个)。更早的历史不要列。忽略居中的时间戳和"
         "系统提示(如「你已添加了XX」「现在可以开始聊天了」)、忽略空白区域。"
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
    try:
        d = _pjson(_vision(_b64(_shot_chat_area("_wecom_read.png")), p, mt=1400))
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


def _deliver(counter: dict, name: str, reply: str, conv_id, started: float,
             single_bubble: bool = False, poster_urls=None, poster_ids=None) -> bool:
    """把 reply 逐段 GUI 打字发出 →（若有）逐张发官方海报 → confirm 落库 → 计数。返回是否真发。
    single_bubble=True（首触）：确定性只发【一条】，且不配图（首触保持纯暖场）。
    poster_urls/poster_ids：端点命中的官方海报，文本发完后逐张内联发；0 张则退化为纯文本。"""
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
    _bump_counter(counter, name)
    log(f"  已发送并落库(文本 + {len(sent_poster_ids)} 图)。今日 {counter['sent']}/{DAILY_LIMIT}")
    return True


# loop 黑窗日志被 VL 当聊天读回来的特征：行首时间戳「[HH:MM:SS]」/「小艳回复：」/
# 「客户[」。命中任一 → 整次读取作废（2026-07-02：黑窗盖在企微上，4 条"客户消息"
# 全是我方日志行，基于垃圾读取真发出了一条回复）。
_CONSOLE_ARTIFACT_RE = re.compile(r"^\s*(\[\d{2}:\d{2}:\d{2}\]|小艳回复\s*[:：]|客户\[)")


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
_BOILERPLATE_RE = re.compile(r"联系人验证请求|现在(我们)?可以开始聊天|你已添加了")


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


def _process_conversation(d: dict | None, counter: dict) -> bool:
    """对一个【已读出】的会话做决策与回复（首触/代回/三护栏）。返回是否真发了一条。
    读(在哪个会话、怎么截图)与决策分离——本函数不碰 GUI 读取，便于单测与双通道复用。"""
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
        # 首触钉死一条，发完等客户回复才由代回继续（幂等 alreadyGreeted 也已挡重复打招呼）
        return _deliver(counter, name, reply, conv_id, started, single_bubble=True)

    # 分流②：有客户新消息 → 代回。
    # 取【最后一条我方消息之后的所有连续客户消息】合并 → 客户连发多条时一次覆盖回复、不漏。
    # 末尾是我方消息(right) → burst 为 None = 没有待回的客户新消息，跳过（也天然防自回）。
    text = _pending_customer_burst(d)
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
_LAST_CHAT_HASH = {"h": None}


def _chat_area_hash() -> str | None:
    """右侧聊天区截图哈希（裁掉输入框/顶栏/左列表——光标会闪、列表时间会跳）。
    与上一轮相同 = 没有新内容 → 跳过 VL 调用（省钱 + 少一次误读机会）。"""
    try:
        import hashlib

        import pyautogui

        W, H = _screen()
        region = (int(0.28 * W), int(0.08 * H), int(0.68 * W), int(0.70 * H))  # x,y,w,h
        im = pyautogui.screenshot(region=region)
        return hashlib.md5(im.tobytes()).hexdigest()
    except Exception:  # noqa: BLE001
        return None


def _screen_looks_dark() -> bool:
    """全屏近黑 = 锁屏/黑屏/远程断流。别硬跑（VL 全失败白烧钱），记日志跳过本轮。"""
    try:
        import pyautogui

        im = pyautogui.screenshot().convert("L").resize((32, 32))
        px = list(im.getdata())
        return sum(px) / len(px) < 12
    except Exception:  # noqa: BLE001
        return False


def detect_unread_rows() -> list:
    """VL 只看左侧会话列表窄条，返回每个【带红色未读数字圆点】行的中心 y（0-1000 归一，
    相对全屏高——裁剪条高度=全屏高度，归一值直接可用于 click_norm）。"""
    try:
        import pyautogui

        W, H = _screen()
        crop_w = int(0.24 * W)
        os.makedirs("screenshots", exist_ok=True)
        path = os.path.join("screenshots", "_wecom_list.png")
        pyautogui.screenshot(path, region=(0, 0, crop_w, H))
        p = ("这是企业微信左侧会话列表的截图。找出所有【带红色未读数字小圆点】的会话行，"
             '只回严格JSON：{"rows":[{"y":该行中心的纵向位置，按 0-1000 归一化(相对整张图高度)}]}。'
             '没有红点回 {"rows":[]}。只认红色数字圆点；灰色免打扰小点、绿色图标都不算。')
        d = _pjson(_vision(_b64(path), p, mt=200))
        out = []
        for r in (d.get("rows") or []):
            y = r.get("y")
            if isinstance(y, (int, float)) and 0 <= y <= 1000:
                out.append(int(y))
        return out[:5]
    except Exception as e:  # noqa: BLE001
        log("  [unread-rows] VL 失败:", e)
        return []


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

    # ── 通道①：当前打开的会话（不点击、直接读）──
    h = _chat_area_hash()
    if h is not None and h == _LAST_CHAT_HASH.get("h"):
        pass  # 聊天区与上轮一模一样 → 没新消息，跳过 VL
    else:
        scroll_chat_bottom()
        time.sleep(0.3)
        d = read_open_conversation()
        _LAST_CHAT_HASH["h"] = _chat_area_hash()
        if d and _process_conversation(d, counter):
            sent_any = True
            _LAST_CHAT_HASH["h"] = None  # 发了消息界面已变，下轮强制重读

    # ── 通道②：红点会话逐个处理（上限 3/轮；点开读完红点自清）──
    for _ in range(3):
        rows = detect_unread_rows()
        if not rows:
            break
        y = rows[0]
        click_norm(C_SESSION1[0], y, label=f"未读会话(y={y})")
        time.sleep(1.2)
        scroll_chat_bottom()
        time.sleep(0.4)
        d = read_open_conversation()
        _LAST_CHAT_HASH["h"] = None  # 切了会话，通道① 的 hash 作废
        if d and _process_conversation(d, counter):
            sent_any = True
        time.sleep(random.uniform(2, 5))  # 会话间随机小间隔，拟人

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
    log(f"DRY_RUN={DRY_RUN}  日限={DAILY_LIMIT} 时限={HOURLY_LIMIT} 客户连发={PER_CUSTOMER_DAILY}")
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
        if counter["sent"] >= DAILY_LIMIT:
            log(f"今日已达日限 {DAILY_LIMIT}，停发"); time.sleep(random.uniform(600, 1200)); continue
        if counter.get("hour") == datetime.now().hour and counter.get("sent_hour", 0) >= HOURLY_LIMIT:
            log(f"本小时已达时限 {HOURLY_LIMIT}，等下个钟头"); time.sleep(random.uniform(300, 600)); continue

        try:
            sent = handle_one(counter)
            consec_fail = 0
            _heartbeat("sent" if sent else "idle", counter)
        except Exception as e:  # noqa: BLE001
            consec_fail += 1
            log(f"  tick 异常({consec_fail}/{MAX_CONSEC_FAILURES}):", e)
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
