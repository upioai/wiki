"""
企业微信(WeCom)按手机号/微信号自动加好友 — Qwen 视觉定位版 (grounded)。

镜像 douyin_dm_grounded.py 的范式：每步截图 → Qwen VL 定位元素 → 点击，自适应分辨率；
身份门(OCR)保留：搜到的卡片必须匹配目标姓名/号，否则不加，防加错人。

企业微信按号加好友真实流程(2026-06-29 实测)：
  搜索框打手机号/微信号 → 点下拉里「网络查找手机号/邮箱」→ 弹出人物卡片 →
  点「添加到通讯录」→「发送添加申请」弹框填验证语 → 发送。
  漏掉「网络查找」这步会一直 not_found。

用法:
  python wecom_add_contact_grounded.py _wecom_add_xxx.csv   # 批量加好友
  python wecom_add_contact_grounded.py --capture            # 首次/换分辨率：采集搜索框等模板
  python wecom_add_contact_grounded.py --calibrate          # VL 量「搜索框」固定坐标写 .env(可选)

依赖:  pip install pyautogui pyperclip pillow opencv-python pygetwindow

I/O 契约:
  输入 CSV 列: search_key(手机号或微信号) name province _source_id [note] [verify_msg]
               —— claim-enterprise-leads.ts 产出，search_key 已是微信号优先否则手机号。
               verify_msg 为该行个性化验证语(含自称占位符 {me})；缺列/为空则回退全局 AKKE_WECOM_VERIFY_MSG。
  输出 sent_log_wecom_YYYYMMDD.csv 列: 上述 + status sent_at _ocr_confidence _ocr_seen _captcha_sample
  status: 已发请求 / already(已是好友) / not_found(搜不到) / 加不上(找到但加不了) /
          wrong_user(身份门没过) / cancelled / aborted / error:<msg>
  —— 回本机后 `pnpm tsx scripts/writeback-enterprise-lead.ts --from-sent-log=<本文件>` 灌回 DB。

⚠️ 前置(企业微信端,运营在云电脑手动确认):
  ①企业微信PC已登录、最大化、前台 ②分辨率锁定 ③输入法切【英文模式】(中文IME会抢搜索词首字)
  ④首次必须先 --capture 标坐标(没见过你这台企业微信的UI,坐标要现场采)。

环境(.env 同目录):
  ANTHROPIC_API_KEY 或 OPENROUTER_API_KEY  — VL 身份门/定位用
  AKKE_OCR_MODEL(默认 qwen vl) / AKKE_OCR_BASE_URL / AKKE_OCR_MIN_CONFIDENCE
  AKKE_WECOM_VERIFY_MSG  — 加好友验证语全局默认(CSV 没带 verify_msg 时用)
  AKKE_WECOM_SELF_NAME   — 本账号显示名(如 全屋定制小夏)，验证语里 {me} 占位符填它。
                           不用手设：发送时未设会自动 VL 识别一次并写回 .env 缓存；
                           想固定/识别不准时再手设或跑 --whoami。
  AKKE_WECOM_C_SEARCH 等固定坐标(可选,--capture/--calibrate 写入)
  AKKE_DAILY_LIMIT(默认 20,加好友比 DM 更敏感,起步小) / AKKE_MIN_INTERVAL / AKKE_MAX_INTERVAL
"""
from __future__ import annotations

import base64
import csv
import ctypes
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

import pyautogui
import pyperclip

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(WORK_DIR)
try:
    from dotenv import load_dotenv
    # override=True：与 douyin_dm_grounded 同理，被 subprocess 拉起时以 .env 新值为准，
    # 别让继承的旧 os.environ 盖住刚 --capture 重采的坐标。
    load_dotenv(os.path.join(WORK_DIR, '.env'), override=True)
except Exception:
    pass

try:
    import wuying_window_lock as _wl   # 同机串行锁(AKKE_WINDOW_LOCK=1 才生效)，缺失则 no-op
except ModuleNotFoundError:
    import contextlib as _ctxlib

    class _WLStub:
        @_ctxlib.contextmanager
        def dm_batch(self):
            yield

        @_ctxlib.contextmanager
        def window_turn(self, *a, **k):
            yield

        def dm_keepalive(self):
            pass

    _wl = _WLStub()

KEY = os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('OPENROUTER_API_KEY')
BASE = os.environ.get('AKKE_OCR_BASE_URL', 'https://openrouter.ai/api/v1')
MODEL = os.environ.get('AKKE_OCR_MODEL', 'qwen/qwen3-vl-30b-a3b-instruct')
MIN_CONF = float(os.environ.get('AKKE_OCR_MIN_CONFIDENCE', '0.95'))
MIN_INTERVAL = int(os.environ.get('AKKE_MIN_INTERVAL', '40'))
MAX_INTERVAL = int(os.environ.get('AKKE_MAX_INTERVAL', '120'))
# 加好友比陌生 DM 更敏感(频繁加易被风控/封)，默认日限保守 20。
DAILY_LIMIT = int(os.environ.get('AKKE_DAILY_LIMIT', '20'))
VERIFY_MSG = os.environ.get(
    'AKKE_WECOM_VERIFY_MSG',
    '您好，之前在平台上联系过您，方便加个微信详细沟通一下哈~',
)
# 验证语里自称占位符 {me} 替换成本机登录的企微账号名（如「全屋定制小夏」）。
# 来源：env AKKE_WECOM_SELF_NAME（一次性设最稳）→ 没设则 --whoami VL 自动识别一次写回 .env。
SELF_NAME = (os.environ.get('AKKE_WECOM_SELF_NAME', '') or '').strip()
_SELF_TOKENS = ('{me}', '[您的姓名]', '[你的姓名]', '[你名字]', '[名字]')


def fill_self_name(msg: str) -> str:
    """把验证语里的自称占位符换成本账号名；账号名常自带「全屋定制/兔宝宝」，去掉相邻重复；
    没配账号名时优雅收掉残留标点（不留「兔宝宝的，」这种）。"""
    if not msg:
        return msg
    import re
    name = SELF_NAME
    for tok in _SELF_TOKENS:
        msg = msg.replace(tok, name)
    if name:
        # 账号名含品牌词时去重相邻重复：兔宝宝的兔宝宝小艳→兔宝宝小艳；全屋定制全屋定制→全屋定制
        msg = re.sub(r'兔宝宝的?(兔宝宝)', r'\1', msg)
        msg = re.sub(r'(全屋定制)\1', r'\1', msg)
    else:
        # 没账号名：「我是兔宝宝的，给您…」→「我是兔宝宝，给您…」
        msg = msg.replace('兔宝宝的，', '兔宝宝，')
    return msg

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.4

TEMPLATE_DIR = os.path.join(WORK_DIR, 'templates-wecom')


def _coord(envname, default):
    v = os.environ.get(envname)
    if v:
        try:
            a, b = v.split(',')
            return (int(a), int(b))
        except Exception:
            pass
    return default


# 企业微信加好友面板归一化坐标(0~1000)；首次 --capture/--calibrate 现场采。
# 留 None 的元素 → 实时 VL grounding 兜底(grounded 哲学，未标坐标也能跑)。
C_SEARCH = _coord('AKKE_WECOM_C_SEARCH', None)        # 顶部「搜索」框中心
C_RESULT = _coord('AKKE_WECOM_C_RESULT', None)        # 搜索下拉/结果里第一条匹配卡片
C_ADD = _coord('AKKE_WECOM_C_ADD', None)              # 「添加到通讯录」/「添加」按钮
C_VERIFY_INPUT = _coord('AKKE_WECOM_C_VERIFY_INPUT', None)  # 验证语输入框
C_SEND = _coord('AKKE_WECOM_C_SEND', None)            # 「发送申请」按钮


def _vision(b64, prompt, mt=300):
    payload = {'model': MODEL, 'max_tokens': mt, 'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}},
        {'type': 'text', 'text': prompt}]}]}
    req = urllib.request.Request(BASE + '/chat/completions', data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())['choices'][0]['message']['content']


def _best_wecom_window():
    """企业微信主窗口：标题/类名含 企业微信 / WXWork / Work Weixin / wework，取面积最大者。"""
    try:
        import pygetwindow as gw
    except Exception:
        return None
    cands = []
    for w in gw.getAllWindows():
        t = (getattr(w, 'title', '') or '')
        tl = t.lower()
        if not ('企业微信' in t or 'wxwork' in tl or 'work weixin' in tl
                or 'wework' in tl or 'wecom' in tl):
            continue
        if '菜单' in t or 'menu' in tl:
            continue
        try:
            area = int(getattr(w, 'width', 0) or 0) * int(getattr(w, 'height', 0) or 0)
        except Exception:
            area = 0
        cands.append((area, w))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0], reverse=True)
    return cands[0][1]


def _win_region():
    w = _best_wecom_window()
    if w is not None:
        try:
            l, t = max(0, int(w.left)), max(0, int(w.top))
            ww, hh = int(w.width), int(w.height)
            if ww > 0 and hh > 0:
                return (l, t, ww, hh)
        except Exception:
            pass
    W, H = pyautogui.size()
    return (0, 0, W, H)


def focus_wecom():
    """把企业微信主窗口拉到最前台 + 最大化。"""
    try:
        w = _best_wecom_window()
        if w is None:
            print('  [focus] 未找到企业微信主窗口（登录了吗？）', file=sys.stderr)
            return False
        try:
            if getattr(w, 'isMinimized', False):
                w.restore()
            w.activate()
        except Exception:
            pass
        try:
            w.maximize()
        except Exception:
            pass
        time.sleep(1.2)
        print('  [focus] 企业微信主窗口已置前: %r' % ((getattr(w, 'title', '') or '')[:30],))
        return True
    except Exception as e:
        print('  [focus] 失败: %s（请手动确保企业微信在前台）' % e, file=sys.stderr)
        return False


# ── 视觉定位(grounded)──────────────────────────────────────────────
def _shot(name):
    os.makedirs('screenshots', exist_ok=True)
    path = os.path.join('screenshots', name)
    pyautogui.screenshot(path)
    from PIL import Image
    with Image.open(path) as im:
        size = im.size
    return path, size


def _pjson(txt):
    t = txt.strip()
    if t.startswith('```'):
        t = t.split('```')[1]
        if t.lower().startswith('json'):
            t = t[4:]
    return json.loads(t.strip())


def _to_px(v, dim):
    v = float(v)
    if v <= 1.0:
        px = v * dim
    elif v <= 1000.0:
        px = v / 1000.0 * dim
    else:
        px = v
    return max(2, min(int(px), dim - 2))


def _scalar(v):
    while isinstance(v, (list, tuple)) and v:
        v = v[0]
    return float(v)


def find_match(name, confidence=0.82, scales=(1.0, 0.95, 1.05, 0.9, 1.1), region=None):
    """OpenCV 模板匹配：在企业微信窗口内找 templates-wecom/<name> 的中心。无图/缺 cv2 → None。"""
    tpl = os.path.join(TEMPLATE_DIR, name)
    if not os.path.exists(tpl):
        return None
    if region is None:
        region = _win_region()
    try:
        from PIL import Image
        base = Image.open(tpl).convert('RGB')
    except Exception as e:
        print('  [match] 读参考图失败 %s: %s' % (name, e), file=sys.stderr)
        return None
    for s in scales:
        try:
            needle = base if abs(s - 1.0) < 1e-6 else base.resize(
                (max(8, int(base.width * s)), max(8, int(base.height * s))))
            pt = pyautogui.locateCenterOnScreen(needle, region=region, confidence=confidence)
        except Exception as e:
            msg = str(e).lower()
            if 'opencv' in msg or 'cv2' in msg or 'confidence' in msg:
                print('  [match] 缺 opencv-python，模板匹配不可用', file=sys.stderr)
                return None
            pt = None
        if pt:
            print('  match[%s] -> (%d,%d) scale=%.2f' % (name, pt[0], pt[1], s))
            return (int(pt[0]), int(pt[1]))
    print('  match[%s] NOT FOUND (conf>=%.2f)' % (name, confidence))
    return None


def locate(desc, region=None):
    """Qwen VL 定位：region=(l,t,r,b) 比例先裁剪再喂模型，坐标映射回整屏。"""
    path, (W, H) = _shot('_loc.png')
    if region:
        from PIL import Image
        l, t, r, b = region
        x0, y0, x1, y1 = int(l * W), int(t * H), int(r * W), int(b * H)
        cpath = os.path.join('screenshots', '_loc_crop.png')
        with Image.open(path) as im:
            crop = im.crop((x0, y0, x1, y1))
            crop.save(cpath)
            cw, ch = crop.size
        b64 = base64.b64encode(open(cpath, 'rb').read()).decode()
    else:
        x0 = y0 = 0
        cw, ch = W, H
        b64 = base64.b64encode(open(path, 'rb').read()).decode()
    p = ('企业微信PC客户端截图(可能是局部裁剪)。找到这个元素的点击中心:%s。'
         '用0到1000整数表示坐标(fx:0最左~1000最右,fy:0最顶~1000最底,相对这张图)。'
         '只回严格JSON:{"fx":整数,"fy":整数,"found":true或false}，fx/fy 必须是单个整数不要数组。'
         '看不到就found=false。' % desc)
    d = _pjson(_vision(b64, p))
    if not d.get('found', True):
        print('  locate[%s] NOT FOUND' % desc)
        return None
    fx, fy = d.get('fx'), d.get('fy')
    if isinstance(fx, (list, tuple)) and len(fx) == 2 and fy is None:
        fx, fy = fx[0], fx[1]
    try:
        x = x0 + _to_px(_scalar(fx), cw)
        y = y0 + _to_px(_scalar(fy), ch)
        x = max(2, min(x, W - 2))
        y = max(2, min(y, H - 2))
    except Exception as e:
        print('  locate[%s] 解析坐标失败: %s raw=%r' % (desc, e, d))
        return None
    print('  locate[%s] -> (%d,%d)%s' % (desc, x, y, ' [crop]' if region else ''))
    return (x, y)


def click_el(desc, wait=1.8, tries=3, retry_wait=1.5, region=None):
    pt = None
    for k in range(tries):
        pt = locate(desc, region=region)
        if pt:
            break
        if k < tries - 1:
            time.sleep(retry_wait)
    if not pt:
        raise RuntimeError('locate failed: ' + desc)
    pyautogui.click(pt[0], pt[1])
    time.sleep(wait)
    return pt


def click_norm(fx, fy, wait=1.8, label=''):
    W, H = pyautogui.size()
    x, y = int(fx / 1000.0 * W), int(fy / 1000.0 * H)
    print('  click_norm[%s] -> (%d,%d)' % (label, x, y))
    pyautogui.click(x, y)
    time.sleep(wait)
    return (x, y)


def click_fixed_or_vl(coord, vl_desc, wait=1.8, label='', region=None):
    """有固定坐标先用固定坐标(稳)，否则回退实时 VL grounding。"""
    if coord:
        return click_norm(coord[0], coord[1], wait=wait, label=label)
    return click_el(vl_desc, wait=wait, region=region)


# ── Windows SendInput unicode 键入(绕剪贴板/IME，无影必须)──────────────
_PUL = ctypes.POINTER(ctypes.c_ulong)


class _KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", _PUL)]


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", _PUL)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short), ("wParamH", ctypes.c_ushort)]


class _InputI(ctypes.Union):
    _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _InputI)]


_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002


def _emit_unicode(scan, up):
    flags = _KEYEVENTF_UNICODE | (_KEYEVENTF_KEYUP if up else 0)
    ii = _InputI()
    ii.ki = _KeyBdInput(0, scan, flags, 0, ctypes.pointer(ctypes.c_ulong(0)))
    inp = _Input(1, ii)
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def type_unicode(text):
    sent = 0
    for ch in text:
        cp = ord(ch)
        units = [cp] if cp <= 0xFFFF else [
            0xD800 + ((cp - 0x10000) >> 10), 0xDC00 + ((cp - 0x10000) & 0x3FF)]
        for u in units:
            sent += _emit_unicode(u, False)
            sent += _emit_unicode(u, True)
        time.sleep(0.02)
    return sent


def type_text(text):
    """清空输入框后键入。优先 SendInput unicode，失败回退剪贴板。调用前先点聚焦目标框。"""
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.15)
    pyautogui.press('delete')
    time.sleep(0.15)
    try:
        type_unicode(text)
    except Exception as e:
        print('  [warn] SendInput 键入失败(%s)，回退剪贴板' % e)
        pyperclip.copy(text)
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.6)


# ── 搜索结果状态判定 + 身份门 ─────────────────────────────────────────
def classify_search_result(name, search_key):
    """搜完一个号后截图，VL 判断结果属于哪种状态，并核身份。
    返回 (state, confidence, seen):
      state ∈ found(可加,身份匹配) / not_found(搜不到) / already(已是好友/已在通讯录)
            / cannot_add(找到但无添加入口) / wrong_user(搜到但不是本人)
    """
    path, _ = _shot('_wecom_result.png')
    b64 = base64.b64encode(open(path, 'rb').read()).decode()
    p = ('这是企业微信PC客户端搜索「%s」后的截图。请判断当前搜索结果状态，只回严格JSON:'
         '{"state":"found|not_found|already|cannot_add",'
         ' "name_match":true/false,"confidence":0~1,"seen":"看到的姓名/微信昵称或提示文字"}'
         '。说明:'
         ' found=搜到一个可添加的用户(有“添加到通讯录/添加”入口);'
         ' already=该用户已经是好友/已在通讯录(显示“发消息”而非“添加”);'
         ' not_found=提示“无法找到该用户/该用户不存在/搜索结果为空”;'
         ' cannot_add=搜到了但没有任何添加入口。'
         ' name_match=结果显示的姓名/昵称是否可能就是「%s」(允许昵称与真名不同，拿不准填false)。'
         % (search_key, name or '(未知)'))
    try:
        d = _pjson(_vision(b64, p))
        state = str(d.get('state', 'cannot_add'))
        nm = bool(d.get('name_match', False))
        conf = float(d.get('confidence', 0.0))
        seen = str(d.get('seen', ''))
        if state == 'found' and not nm:
            # 找到但姓名对不上：微信昵称常≠真名，按号搜到唯一结果仍可加，但标记低置信。
            # 不直接判 wrong_user(否则真名≠昵称的全被误杀)；交由 conf 体现，人工可复核 sent_log。
            print('  [身份门] 搜到但姓名未必匹配(昵称≠真名常见)，仍按 found 处理，conf=%.2f' % conf)
        return state, conf, seen
    except Exception as e:
        return 'cannot_add', 0.0, 'ocr_error:%s:%s' % (type(e).__name__, e)


def _save_result_sample(state, src_path):
    """把异常结果(not_found/cannot_add)截图另存留证。"""
    try:
        os.makedirs('screenshots', exist_ok=True)
        dst = os.path.join('screenshots', 'wecom_%s_%s.png' % (
            state, datetime.now().strftime('%Y%m%d_%H%M%S')))
        from PIL import Image
        Image.open(src_path).save(dst)
        return dst
    except Exception:
        return ''


# ── 单条加好友流程 ───────────────────────────────────────────────────
def process(c):
    """对单条线索执行：搜索 → 判定 → 身份门 → 加到通讯录 → 填验证语 → 发送申请。
    返回 (status, ocr_conf, seen, sample_path)。"""
    search_key = (c.get('search_key') or '').strip()
    name = (c.get('name') or '').strip()
    if not search_key:
        return '加不上', None, 'no_search_key', ''

    focus_wecom()
    # 1) 点搜索框 → Esc 清旧 → 键入号
    click_fixed_or_vl(C_SEARCH, '企业微信顶部的搜索输入框', wait=1.0, label='搜索框',
                      region=(0.0, 0.0, 0.6, 0.12))
    pyautogui.press('esc')
    time.sleep(0.2)
    click_fixed_or_vl(C_SEARCH, '企业微信顶部的搜索输入框', wait=0.8, label='搜索框',
                      region=(0.0, 0.0, 0.6, 0.12))
    type_text(search_key)
    time.sleep(1.5)

    # 2) 点搜索框下方「网络查找手机号/邮箱」那一条 —— 企业微信按号加好友的必经一步，
    #    点了才会发起网络查找、弹出人物卡片(有"添加到通讯录")。漏这步会一直 not_found。
    try:
        click_fixed_or_vl(
            C_RESULT,
            '搜索框正下方下拉列表里"网络查找手机号/邮箱"或"搜索:%s"那一条' % search_key,
            wait=1.2, label='网络查找入口', region=(0.0, 0.04, 0.55, 0.7))
    except RuntimeError:
        print('  [网络查找] 没找到"网络查找"入口，可能已直接出结果或该号搜不到')
    time.sleep(3.0)   # 网络查找有往返，等卡片加载出来

    # 3) 判定结果状态 + 身份门（此时人物卡片/添加按钮应已出现）
    state, conf, seen = classify_search_result(name, search_key)
    print('  [result] state=%s conf=%.2f seen=%r' % (state, conf, seen))
    if state == 'already':
        return 'already', conf, seen, ''
    if state == 'not_found':
        path, _ = _shot('_wecom_result.png')
        return 'not_found', conf, seen, _save_result_sample('not_found', path)
    if state == 'cannot_add':
        path, _ = _shot('_wecom_result.png')
        return '加不上', conf, seen, _save_result_sample('cannot_add', path)

    # 4) 点「添加到通讯录 / 添加」
    try:
        click_fixed_or_vl(C_ADD, '“添加到通讯录”或“添加”按钮(蓝色按钮)', wait=1.8, label='添加按钮')
    except RuntimeError:
        path, _ = _shot('_wecom_noadd.png')
        return '加不上', conf, seen, _save_result_sample('cannot_add', path)

    # 5) 填验证语(若弹出验证框)，发送申请
    #    优先用该行 verify_msg(claim 脚本按备注个性化生成、含 {me} 占位符)，没有才回退全局默认。
    msg = (c.get('verify_msg') or '').strip() or VERIFY_MSG
    msg = fill_self_name(msg)
    try:
        click_fixed_or_vl(C_VERIFY_INPUT, '“发送添加申请”弹框里的验证消息输入框', wait=1.0,
                          label='验证语输入框')
        type_text(msg)
    except RuntimeError:
        print('  [verify] 没找到验证语输入框(可能无需验证)，直接尝试发送')
    try:
        click_fixed_or_vl(C_SEND, '“发送”或“确定”申请按钮(蓝色)', wait=1.8, label='发送申请')
    except RuntimeError:
        path, _ = _shot('_wecom_nosend.png')
        return '加不上', conf, seen, _save_result_sample('cannot_add', path)

    time.sleep(1.2)
    return '已发请求', conf, seen, ''


# ── 批量运行 ─────────────────────────────────────────────────────────
def main(contacts_csv):
    with open(contacts_csv, encoding='utf-8-sig') as f:
        contacts = list(csv.DictReader(f))
    if not contacts:
        print('名单为空, 退出')
        return
    if len(contacts) > DAILY_LIMIT:
        print('⚠️ 名单 %d > 上限 %d, 截断' % (len(contacts), DAILY_LIMIT))
        contacts = contacts[:DAILY_LIMIT]

    print('=== wecom_add_contact_grounded  model=%s  conf>=%s ===' % (MODEL, MIN_CONF))
    print('共 %d 条; 前置: ①企业微信PC前台+最大化 ②分辨率锁定 ③输入法切【英文模式】' % len(contacts))
    print('   ⚠️ 输入法必须英文模式：中文 IME 会抢搜索词首字 → 搜错人。')
    print('   ⚠️ 加好友敏感，日限 %d，间隔 %d-%ds；先看着跑前几条确认 UI 流程对。' % (
        DAILY_LIMIT, MIN_INTERVAL, MAX_INTERVAL))

    # 验证语 {me} 自称：env 没设就自动识别一次（写回 .env 缓存），无需运营手动配。
    ensure_self_name()

    log = 'sent_log_wecom_%s.csv' % datetime.now().strftime('%Y%m%d')
    fields = ['search_key', 'name', 'province', '_source_id',
              'status', 'sent_at', '_ocr_confidence', '_ocr_seen', '_captcha_sample']
    first_write = not os.path.exists(log)
    with open(log, 'a', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        if first_write:
            w.writeheader()
        with _wl.dm_batch():
            for i, c in enumerate(contacts, 1):
                print('\n========== [%d/%d] %s ==========' % (i, len(contacts), c.get('name', '')))
                _wl.dm_keepalive()
                ocr_conf = None
                seen = ''
                sample = ''
                try:
                    with _wl.window_turn('dm'):
                        status, ocr_conf, seen, sample = process(c)
                except Exception as e:
                    print('  ❌ 异常: %s' % e)
                    status = 'error:%s' % e
                print('  → %s' % status)
                w.writerow({**c, 'status': status,
                            'sent_at': datetime.now().isoformat(),
                            '_ocr_confidence': '' if ocr_conf is None else '%.3f' % ocr_conf,
                            '_ocr_seen': seen, '_captcha_sample': sample})
                f.flush()
                if i < len(contacts):
                    time.sleep(random.randint(MIN_INTERVAL, MAX_INTERVAL))
    print('\n✅ 完成. 日志: %s' % log)
    print('   回本机后: pnpm tsx scripts/writeback-enterprise-lead.ts --from-sent-log=%s' % log)


# ── 标坐标(交互) ─────────────────────────────────────────────────────
def _write_env_kv(updates):
    p = os.path.join(WORK_DIR, '.env')
    lines = []
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            lines = f.read().splitlines()
    seen = set()
    out = []
    for ln in lines:
        kv = ln.split('=', 1)
        k = kv[0].strip() if len(kv) == 2 else None
        if k in updates:
            out.append('%s=%s' % (k, updates[k]))
            seen.add(k)
        else:
            out.append(ln)
    for k, v in updates.items():
        if k not in seen:
            out.append('%s=%s' % (k, v))
    with open(p, 'w', encoding='utf-8') as f:
        f.write(chr(10).join(out) + chr(10))


def _countdown(label, secs=3):
    try:
        input('\n▶ 导航到对应界面，把鼠标移到【%s】上，准备好按【回车】开始...' % label)
    except EOFError:
        pass
    print('  保持鼠标不动，倒计时：', end='', flush=True)
    for s in range(secs, 0, -1):
        print(' %d' % s, end='', flush=True)
        time.sleep(1)
    print(' → 抓取')
    return pyautogui.position()


def calibrate():
    """逐元素 hover→回车，把企业微信加好友面板各按钮的归一化坐标写进 .env。
    前提：企业微信已登录最大化。建议先手动搜一个能加的人、把“添加申请”弹框开出来，
    这样 5 个元素能在同一界面一次采全。"""
    print('=== 企业微信加好友 坐标校准 ===')
    print('建议：先手动搜一个能加的用户、点“添加”、把“发送添加申请”弹框开出来，再开始采。')
    W, H = pyautogui.size()
    print('屏幕分辨率: %dx%d' % (W, H))
    items = [
        ('AKKE_WECOM_C_SEARCH', '顶部「搜索」输入框中心'),
        ('AKKE_WECOM_C_RESULT', '搜索框下方「网络查找手机号/邮箱」那一条(打完号才出现)'),
        ('AKKE_WECOM_C_ADD', '「添加到通讯录」/「添加」按钮'),
        ('AKKE_WECOM_C_VERIFY_INPUT', '“发送添加申请”弹框里的验证语输入框'),
        ('AKKE_WECOM_C_SEND', '“发送/确定”申请按钮'),
    ]
    updates = {}
    for env_key, label in items:
        x, y = _countdown(label)
        cx, cy = round(x / W * 1000), round(y / H * 1000)
        updates[env_key] = '%d,%d' % (cx, cy)
        print('   ✓ %s = %d,%d (px %d,%d)' % (env_key, cx, cy, x, y))
    _write_env_kv(updates)
    print('\n✅ 已写入 .env。重跑发送即用这组坐标(VL 仅作未标元素兜底)。不准就重跑本校准。')
    return 0


def _detect_self_name():
    """VL 识别当前登录企微账号显示名，返回 name 或 ''（不写 env、不打印结论，供自动/手动复用）。"""
    if not KEY:
        return ''
    focus_wecom()
    time.sleep(1.0)
    # 点左下角「我/头像」区域把个人信息露出来，再截图识别（不同版本位置略有差异，VL 兜底）
    try:
        click_el('企业微信左下角“我”或当前登录用户的头像', wait=1.5,
                 region=(0.0, 0.82, 0.18, 1.0))
    except Exception:
        pass
    path, _ = _shot('_wecom_whoami.png')
    b64 = base64.b64encode(open(path, 'rb').read()).decode()
    p = ('这是企业微信PC客户端截图。请识别【当前登录账号本人】的显示名/昵称'
         '(通常在个人信息卡片或左下角，不是聊天对象的名字)。'
         '只回严格JSON:{"name":"显示名","found":true或false}。看不到填found=false。')
    try:
        d = _pjson(_vision(b64, p))
        if not d.get('found', False):
            return ''
        name = str(d.get('name', '')).strip()
    except Exception:
        return ''
    try:
        pyautogui.press('esc')
    except Exception:
        pass
    return name


def ensure_self_name():
    """发送前确保拿到本账号名：env 已设直接用；没设就自动 VL 识别一次并写回 .env 缓存。
    识别不到也不报错——验证语退化成不带自称的版本（仍可发）。"""
    global SELF_NAME
    if SELF_NAME:
        print('  自称账号名：%s（来自 AKKE_WECOM_SELF_NAME）' % SELF_NAME)
        return
    print('  未设 AKKE_WECOM_SELF_NAME，自动识别本账号名…')
    nm = _detect_self_name()
    if nm:
        SELF_NAME = nm
        _write_env_kv({'AKKE_WECOM_SELF_NAME': nm})  # 缓存，下次免识别
        print('  ✓ 自动识别账号名「%s」，验证语 {me} 将填它（已写 .env 缓存，下次直接用）。' % nm)
    else:
        print('  ⚠️ 没自动识别到账号名，验证语将不带自称；要带就在 .env 写 AKKE_WECOM_SELF_NAME=你的企微名。')


def whoami():
    """手动跑一次 VL 识别并写回 .env（可选；正常发送时会自动识别，无需先跑这个）。
    一次性：python wecom_add_contact_grounded.py --whoami"""
    if not KEY:
        print('❌ 缺 ANTHROPIC_API_KEY / OPENROUTER_API_KEY，无法 VL 识别', file=sys.stderr)
        return 2
    name = _detect_self_name()
    if not name:
        print('❌ 没识别到账号名，请手动在 .env 写 AKKE_WECOM_SELF_NAME=<你的企微名>', file=sys.stderr)
        return 1
    _write_env_kv({'AKKE_WECOM_SELF_NAME': name})
    print('✅ 识别到账号名「%s」，已写入 .env 的 AKKE_WECOM_SELF_NAME。' % name)
    print('   下次加好友时验证语里的 {me} 会自动填成它。识别不准就手动改这行。')
    return 0


def capture():
    """模板采集：把鼠标悬停到“搜索框/添加按钮/发送申请”上，抓裁剪图存 templates-wecom/。
    模板匹配比 VL 稳，锁分辨率后只采一次。"""
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    W, H = pyautogui.size()
    print('=== 模板采集  分辨率 %dx%d ===' % (W, H))
    items = [
        ('wecom_search.png', '顶部「搜索」输入框', 100, 34),
        ('wecom_add.png', '「添加到通讯录」/「添加」按钮', 96, 38),
        ('wecom_send.png', '“发送添加申请”按钮', 96, 38),
    ]
    for fname, label, cw, ch in items:
        x, y = _countdown(label)
        l, t = max(0, x - cw // 2), max(0, y - ch // 2)
        pyautogui.screenshot(region=(l, t, cw, ch)).save(os.path.join(TEMPLATE_DIR, fname))
        print('   ✓ 存 %s（光标@%d,%d 裁 %dx%d）' % (fname, x, y, cw, ch))
    print('✅ 采集完成。参考图在 %s（抽查每张只框住目标）。' % TEMPLATE_DIR)
    return 0


if __name__ == '__main__':
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--whoami' in flags:
        sys.exit(whoami())
    if '--capture' in flags:
        sys.exit(capture())
    if '--calibrate' in flags:
        sys.exit(calibrate())
    if not args:
        print('用法: python wecom_add_contact_grounded.py _wecom_add_xxx.csv', file=sys.stderr)
        print('      python wecom_add_contact_grounded.py --calibrate  # 首次：标 5 个按钮坐标', file=sys.stderr)
        print('      python wecom_add_contact_grounded.py --capture    # 可选：采模板图', file=sys.stderr)
        print('      python wecom_add_contact_grounded.py --whoami     # 可选：VL 识别本账号名写 .env', file=sys.stderr)
        sys.exit(2)
    if not KEY:
        print('❌ 缺 ANTHROPIC_API_KEY / OPENROUTER_API_KEY（身份门/定位需要）', file=sys.stderr)
        sys.exit(2)
    main(args[0])
