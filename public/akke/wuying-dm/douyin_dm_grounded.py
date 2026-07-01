"""
抖音PC批量私信 — Qwen 视觉定位版 (grounded)。

不依赖固定坐标：每步截图 → Qwen VL 定位元素 → 点击，自适应任意分辨率
（解决无影 2286x1600 等非标准分辨率的校准难题）。身份核对(OCR)安全门保留：
搜到的主页昵称必须匹配目标 nickname，否则不发。

用法:  python douyin_dm_grounded.py contacts.csv      (wuying_poll_agent.py 调用)
       python douyin_dm_grounded.py --capture       (首次/换分辨率：采集模板参考图)
依赖:  pip install pyautogui pyperclip pillow opencv-python   (opencv 供模板匹配)

定位策略(2026-06-01 改):坐标漂移根治。导航(搜索框/用户tab/私信/发送↑)优先
  OpenCV 模板匹配(在抖音窗口内找参考图，像素级、抗分辨率漂移)；输入框无图标 →
  锚定到发送↑的固定像素偏移；模板缺失/未命中再回退旧 VL/固定坐标。VL 只保留
  身份核对(ocr_verify)。前提：无影分辨率锁死(关自适应)，模板只需采一次。
环境(.env 同目录): ANTHROPIC_API_KEY(放 OpenRouter key) / AKKE_OCR_MODEL(默认 qwen vl) /
                   AKKE_OCR_BASE_URL / AKKE_OCR_MIN_CONFIDENCE
点赞最新作品(2026-06-03): OCR 核身份通过后、点私信前,主页有作品就点进第一个作品点赞再
  Esc 退回主页(best-effort,失败不阻断私信)。需 AKKE_C_WORK_FIRST / AKKE_C_LIKE 两个固定
  归一化坐标(measure_nav.py 在无影上量);未配则整段跳过。有没有作品用 VL 实时判。
I/O 契约与 douyin_dm.py --auto 一致:
  contacts.csv 列: douyin_id(=昵称搜索词) nickname message has_works _comment_id _sec_uid _dispatch_id
  sent_log_YYYYMMDD.csv 列: 上述 + status follow_status like_status sent_at _ocr_confidence
          follow_status: followed / already / click_no_effect / not_found / failed / '' (身份门没过没走到关注)
          like_status:   liked / no_works / no_coords / click_no_effect / failed / ''
          —— 关注/点赞是 best-effort 不阻断私信，但状态落库便于事后诊断「关没关上」(2026-06-28)
  status: sent / unverified / rejected / wrong_user / cancelled / aborted / error:<msg>
          (rejected = 发后检测到对方拒收/无法送达系统提示，不计入成功)
          (unverified = 点了发送但对话区没出现含本文案的我方气泡 → 大概率没真发出
           [坐标漂/点空]，回池可重发，不计成功。注：7911 静默限频本地仍有气泡，此门挡不到)
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
    # override=True：本脚本常被 wuying_poll_agent.py 以 subprocess.run 拉起，会继承 agent
    # 启动时一次性 load 的旧 os.environ（如 AKKE_INPUT_OFFSET）。默认 override=False 时
    # 继承来的旧值会盖住 .env 的新值 → --capture 重采坐标后没重启 agent 就还用旧坐标点歪
    # （2026-06-21 饭粒自动 DM 近一个月真发≈0 的真因）。强制以 .env 为准，堵掉这个坑。
    load_dotenv(os.path.join(WORK_DIR, '.env'), override=True)
except Exception:
    pass

try:
    import wuying_window_lock as _wl  # 单窗口·DM优先串行锁(AKKE_WINDOW_LOCK=1 才生效，否则 no-op)
except ModuleNotFoundError:
    # 该依赖只在「同机串行跑 DM+route-B」的机器上需要(配 AKKE_WINDOW_LOCK=1)。
    # 单 DM 机器(如零星云电脑)没下这个文件也必须正常跑 → 缺失时退化成 no-op 桩，
    # 别让顶层 import 崩掉整脚本(2026-06-21 零星 12 连崩根因:host 了带此 import 的版本但机器无该文件)。
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
MIN_INTERVAL = int(os.environ.get('AKKE_MIN_INTERVAL', '30'))
MAX_INTERVAL = int(os.environ.get('AKKE_MAX_INTERVAL', '90'))
DAILY_LIMIT = int(os.environ.get('AKKE_DAILY_LIMIT', '30'))

pyautogui.FAILSAFE = False   # 用 clamp 防越界
pyautogui.PAUSE = 0.4

# 聊天面板固定坐标(归一化 0~1000, ×当前屏宽高自适应分辨率)。2026-05-30 截图实测:
# 私信聊天面板浮于屏幕右上~中部, 输入框/发送↑在 y≈0.50, 非底部。
# 量到偏就改这两个常量即可(用 AKKE_C_INPUT / AKKE_C_SEND 环境变量覆盖, 格式 "fx,fy")。
def _coord(envname, default):
    v = os.environ.get(envname)
    if v:
        try:
            a, b = v.split(',')
            return (int(a), int(b))
        except Exception:
            pass
    return default

C_INPUT = _coord('AKKE_C_INPUT', (840, 497))   # 消息输入框中心(px≈2145,712 @2554x1432)
C_SEND = _coord('AKKE_C_SEND', (979, 498))     # 红色↑发送按钮中心(px≈2500,713 @2554x1432)
# 顶部搜索框：VL 给的 y≈27 太靠顶边点不进输入区，实测 y≈40(归一化 25)才聚焦。
# 该 template 质量差(易误匹配)→ 搜索直接用固定坐标，不走模板。
C_SEARCH = _coord('AKKE_C_SEARCH', (432, 25))         # 搜索输入框中心(px≈1107,40 @2560x1620)
C_SEARCH_BTN = _coord('AKKE_C_SEARCH_BTN', (589, 14)) # 放大镜搜索按钮(px≈1507,23)；回车不触发搜索
# 左侧栏「推荐/首页」按钮(2026-06-12)。搜人前先点它【钉死回首页】——否则上一条留在视频页/
# 结果页时,顶部搜索框位置会变(首页在顶 y≈13、结果页挪到中间),固定坐标点搜索框就点空到视频上
# (2026-06-12「假发送」根因之一)。默认 None=不点(老行为);量了 AKKE_C_HOME 才启用 goto_home。
C_HOME = _coord('AKKE_C_HOME', None)                  # 左侧栏「推荐」按钮中心(px≈73,134 @2560x1600)
C_USERTAB = _coord('AKKE_C_USERTAB', (287, 45))       # 结果页「用户」tab(px≈735,73)
C_FIRST = _coord('AKKE_C_FIRST', (219, 103))          # 第一条结果头像(px≈561,167)
# 身份门多结果扫描(2026-06-14,根治「目标不在第一条→点错人→wrong_user」)：抖音「用户」结果是
# 【2列网格】(锁2560宽实测)——第1条左上、第2条右上(同高)、第3条左下…。有号(按号搜)时按
# 阅读顺序扫前 N 条、按号精确挑本人(扫到哪条都不会发错人)；无号不深扫(同名分不清、反增发错人风险)。
# N=1=现状(只点第一条/不扫，默认零影响)。COL_DX/ROW_DY/NCOLS 默认 = 锁 2560×1600 实测值，
# 所以锁了该分辨率的机器开启只需设 AKKE_SEARCH_SCAN_N=5；分辨率/布局不同的机器跑 measure-row-dy.py
# 量自己的值 + 设 AKKE_SEARCH_NCOLS 覆盖。
SEARCH_SCAN_N = int(os.environ.get('AKKE_SEARCH_SCAN_N', '1'))
RESULT_ROW_DY = int(os.environ.get('AKKE_C_RESULT_ROW_DY', '116'))  # 下一行头像 Y 间距(2560×1600 实测)
RESULT_COL_DX = int(os.environ.get('AKKE_C_RESULT_COL_DX', '237'))  # 右列头像 X 间距(2560×1600 实测)
SEARCH_NCOLS = max(1, int(os.environ.get('AKKE_SEARCH_NCOLS', '2')))  # 结果网格列数
# 非主页重试(2026-06-16)：点开结果落到「非主页」(残留/加载竞速/脏屏) 或 OCR 出错时，重试【同一条】
# 重新导航+加长等待，给页面第二次落稳机会，再用同一道严格门(is_profile+昵称/号exact)判——从不绕过身份门。
# 仅对「非主页/OCR错」重试：干净命中直接发、on_prof=True 的干净「别人」直接跳下一条深扫，都不重试(零延迟)。
# 治「号读到了/本人在第一条却判非主页」(模式 A/C)。0=关(旧行为)。
NONPROFILE_RETRY = int(os.environ.get('AKKE_NONPROFILE_RETRY', '2'))
C_CLOSE = _coord('AKKE_C_CLOSE', (976, 50))           # 聊天窗口右上角×关闭(px≈2499,81)
# 发私信前点赞最新作品用(2026-06-03)。默认 None=未采坐标→自动跳过点赞,不盲点。
# 在无影上跑 measure_nav.py 量出归一化坐标,写进 .env: AKKE_C_WORK_FIRST / AKKE_C_LIKE。
# 缩略图每人封面不同→只能固定坐标(宫格首格位置稳定),不能模板匹配。
C_WORK_FIRST = _coord('AKKE_C_WORK_FIRST', None)       # 主页作品宫格第一个(最新)作品封面中心
C_LIKE = _coord('AKKE_C_LIKE', None)                   # 作品播放页点赞♡(心形)按钮中心
# 发私信前关注用户用(2026-06-09;2026-06-24 改硬要求)。关注是首触 DM 必做步骤,不再因未采坐标静默跳过:
# 有坐标走坐标,未采坐标(None)则 follow_user() 用 VL 实时定位关注按钮兜底。坐标稳定时仍建议量准更快更稳。
# 主页头部「关注」按钮位置稳定→固定坐标。measure_nav.py 一并量,写进 .env: AKKE_C_FOLLOW。
C_FOLLOW = _coord('AKKE_C_FOLLOW', None)                # 主页头部「关注」按钮中心(可空→VL 兜底)
# 点赞后退回主页用(2026-06-09)。视频页左上角「返回」按钮——Esc 退不出视频层(吃焦点,
# 二触脚本实测),点返回键才稳。与 douyin_comment_grounded.py 共用同一 .env 的 AKKE_C_BACK;
# 二触配过就直接复用,没配则 None→VL 实时定位左上角返回键。
C_BACK = _coord('AKKE_C_BACK', None)                   # 视频页左上角「返回」按钮中心
# 「私信」按钮坐标(2026-06-09)。关注成功后主页头部 [关注]→[已关注] 会让「私信」左移,
# 原来未关注态量的 778,117 点空 → 聊天窗打不开。现在每次都先关注,故 AKKE_C_DM 应在
# 【已关注】态下量。dm_button.png 模板优先,未命中回退本坐标。默认沿用旧未关注态值兜底。
C_DM = _coord('AKKE_C_DM', (778, 117))                 # 主页头部「私信」按钮中心(已关注态)


def _vision(b64, prompt, mt=300):
    payload = {'model': MODEL, 'max_tokens': mt, 'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}},
        {'type': 'text', 'text': prompt}]}]}
    req = urllib.request.Request(BASE + '/chat/completions', data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())['choices'][0]['message']['content']


TEMPLATE_DIR = os.path.join(WORK_DIR, 'templates')


def _best_douyin_window():
    """抖音【主窗口】：标题含"抖音/douyin"、排除"菜单/menu"类弹窗、取面积最大者。
    坑(2026-05-31)：标题含"抖音"的不止主窗口，抓到菜单窗口会导致导航全落空。
    返回 pygetwindow window 对象或 None。"""
    try:
        import pygetwindow as gw
    except Exception:
        return None
    cands = []
    for w in gw.getAllWindows():
        t = (getattr(w, 'title', '') or '')
        if not ('抖音' in t or 'douyin' in t.lower()):
            continue
        if '菜单' in t or 'menu' in t.lower():
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
    """抖音窗口的屏幕矩形 (left, top, width, height)，供模板匹配限定搜索范围；
    拿不到或异常则退化为全屏。最大化时偶有 left=-8，夹到 0 防越界。"""
    w = _best_douyin_window()
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


def focus_douyin():
    """把抖音主窗口拉到最前台 + 最大化，确保截图/点击落在抖音而非 agent 控制台。"""
    try:
        w = _best_douyin_window()
        if w is None:
            print('  [focus] 未找到抖音主窗口（只有菜单/弹窗？）', file=sys.stderr)
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
        print('  [focus] 抖音主窗口已置前: %r' % ((getattr(w, 'title', '') or '')[:30],))
        return True
    except Exception as e:
        print('  [focus] 失败: %s（请手动确保抖音在前台）' % e, file=sys.stderr)
        return False


def goto_home(wait=2.0):
    """点左侧栏「推荐」钉死回首页,让顶部搜索框稳定在已知位置再点。每条搜人前调一次。
    没量 AKKE_C_HOME(C_HOME=None)→ 跳过(沿用老行为,只靠 reset/Esc)。2026-06-12 新增:
    根治「上一条停在视频页→点固定搜索框坐标点在视频上→搜索没执行→落随机人」的假发送链。"""
    if not C_HOME:
        return False
    click_norm(C_HOME[0], C_HOME[1], wait=wait, label='回首页(推荐)')
    return True


def click_user_tab(wait=2.0):
    """点搜索结果页「用户」分类tab,过滤成用户列表(否则停在「综合」,首条常是视频→点进落
    『(非主页)』)。锁分辨率下「用户」tab 恒在固定像素(默认 px≈734,73,与第一条结果 px≈561 不
    重叠)→ 固定坐标点,与搜索框/私信/发送同策略(见顶部定位策略:导航用固定坐标,VL 只做核身)。
    2026-06-15 回归固定坐标:#331 当时改 VL-first,是因云电脑 .env 残留坏值 AKKE_C_USERTAB=563,158
    与第一条结果重叠;源码默认 287,45=px734 一直正确。VL 每条飘 ±50px、偶发整个点空 tab→落非
    主页要手动救(2026-06-15 实测),不如校准过的固定坐标稳。返回是否点了「用户」tab。
    ⚠ 云电脑 .env 的 AKKE_C_USERTAB 必须是真·用户tab坐标——别再留 563,158 这种与首条重叠的旧
    值;不确定就删掉该行(用源码默认),或重跑 measure_nav.py 重采。"""
    click_norm(C_USERTAB[0], C_USERTAB[1], wait=wait, label='用户tab')
    return True


def find_match(name, confidence=0.82, scales=(1.0, 0.95, 1.05, 0.9, 1.1), region=None):
    """OpenCV 模板匹配：在抖音窗口(或指定 region)内找参考图 templates/<name> 的中心。
    锁分辨率后 scale≈1，多尺度只作冗余兜底。返回 (x,y) 屏幕绝对坐标或 None。
    region=(l,t,w,h) 屏幕像素，限定搜索范围(如发送↑只在右侧聊天区找，避开左侧会话列表
    的红色未读点误匹配)。需 opencv-python(confidence 依赖 cv2)；无参考图/缺 cv2 → None。"""
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
                print('  [match] 缺 opencv-python，模板匹配不可用 → pip install opencv-python', file=sys.stderr)
                return None
            pt = None   # pyscreeze 未命中也可能抛，当未找到处理
        if pt:
            print('  match[%s] -> (%d,%d) scale=%.2f' % (name, pt[0], pt[1], s))
            return (int(pt[0]), int(pt[1]))
    print('  match[%s] NOT FOUND (conf>=%.2f)' % (name, confidence))
    return None


def _shot(name):
    os.makedirs('screenshots', exist_ok=True)
    path = os.path.join('screenshots', name)
    pyautogui.screenshot(path)
    from PIL import Image
    with Image.open(path) as im:
        size = im.size
    return path, size


def _shot_crop(name, pad_x=180, pad_y=120):
    """截全屏后裁剪到【输入框+发送按钮】附近(锚定 C_INPUT/C_SEND 归一化坐标)。
    全屏小字 Qwen VL 易假阴性(2026-05-31 实测: 7 条里 5 条文字已进框却被判"没进"
    → 提前中止没点发送)。裁剪放大该区域后 VL 才看得清。"""
    os.makedirs('screenshots', exist_ok=True)
    path = os.path.join('screenshots', name)
    img = pyautogui.screenshot()
    W, H = img.size
    fxs = (C_INPUT[0], C_SEND[0])
    fys = (C_INPUT[1], C_SEND[1])
    x0 = max(0, int((min(fxs) - pad_x) / 1000 * W))
    x1 = min(W, int((max(fxs) + pad_x) / 1000 * W))
    y0 = max(0, int((min(fys) - pad_y) / 1000 * H))
    y1 = min(H, int((max(fys) + pad_y) / 1000 * H))
    img.crop((x0, y0, x1, y1)).save(path)
    return path, (x1 - x0, y1 - y0)


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
    """把 Qwen 可能返回的 list/[x]/[x,y] 压成单个数字。"""
    while isinstance(v, (list, tuple)) and v:
        v = v[0]
    return float(v)


def locate(desc, region=None):
    """region=(l,t,r,b) 比例(0~1)：先把截图裁到该区域再喂 Qwen，小目标(发送箭头等)
    在裁剪图里占比更大、定位更稳，坐标再映射回整屏。region=None 用整屏。"""
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
    p = ('抖音PC截图(可能是局部裁剪)。找到这个元素的点击中心:%s。'
         '用0到1000整数表示坐标(fx:0最左~1000最右,fy:0最顶~1000最底,相对这张图)。'
         '只回严格JSON:{"fx":整数,"fy":整数,"found":true或false}，fx/fy 必须是单个整数不要数组。'
         '看不到就found=false。' % desc)
    d = _pjson(_vision(b64, p))
    if not d.get('found', True):
        print('  locate[%s] NOT FOUND' % desc)
        return None
    fx, fy = d.get('fx'), d.get('fy')
    # 容错：fx 是 [x,y] 点 且 fy 缺失 → 拆开
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
            print('  retry locate in %.1fs...' % retry_wait)
            time.sleep(retry_wait)
    if not pt:
        raise RuntimeError('locate failed: ' + desc)
    pyautogui.click(pt[0], pt[1])
    time.sleep(wait)
    return pt


def click_norm(fx, fy, wait=1.8, label=''):
    """按固定归一化坐标(0~1000)点击,×当前屏幕宽高。用于 grounding 不稳的小按钮
    (私信/输入框/发送),坐标 2026-05-30 在 2286x1600 校准。"""
    W, H = pyautogui.size()
    x, y = int(fx / 1000.0 * W), int(fy / 1000.0 * H)
    print('  click_norm[%s] -> (%d,%d)' % (label, x, y))
    pyautogui.click(x, y)
    time.sleep(wait)
    return (x, y)


def _input_offset():
    """输入框中心相对发送↑中心的像素偏移 (dx,dy)，由 --capture 写入 .env。
    输入框是空框无图标无法模板匹配 → 锚定到有辨识度的发送↑再按偏移点。"""
    v = os.environ.get('AKKE_INPUT_OFFSET')   # "dx,dy"
    if v:
        try:
            a, b = v.split(',')
            return (int(a), int(b))
        except Exception:
            pass
    return None


def click_match_or_vl(name, vl_desc, wait=1.8, tries=3, region=None):
    """大元素(搜索框/用户tab)：模板匹配优先(稳)，未命中回退 VL grounding(旧逻辑)。"""
    pt = find_match(name)
    if pt:
        pyautogui.click(pt[0], pt[1])
        time.sleep(wait)
        return pt
    print('  [match→VL] 模板未命中，回退视觉定位: %s' % vl_desc)
    return click_el(vl_desc, wait=wait, tries=tries, region=region)


def click_match_or_norm(name, fx, fy, wait=1.8, label=''):
    """小元素(私信/发送↑)：模板匹配优先，未命中回退实测固定归一化坐标(不回退 VL,
    VL 对这些小密集元素本就不稳)。"""
    pt = find_match(name)
    if pt:
        pyautogui.click(pt[0], pt[1])
        time.sleep(wait)
        return pt
    return click_norm(fx, fy, wait=wait, label=label + '(固定坐标回退)')


def close_chat():
    """关闭右侧私信聊天窗口(点 ×)，让下一条从干净状态开始。不关的话上一条残留的聊天
    窗口会让下一条 send_arrow 误匹配到左侧会话列表(实测飘到 x≈655)→ 输入框锚错→不发。"""
    click_norm(C_CLOSE[0], C_CLOSE[1], wait=1.0, label='关闭聊天窗口×')


def reset_to_home():
    """每条 DM 开始前强制把界面退回干净首页，不依赖上一条 close_chat 是否点中 ×。
    2026-06-09 事故：发完一条(慧妈)聊天面板没关干净 → 后续 13 条「点搜索框」全部点在
    残留面板的空处 → 根本没搜成 → 每次核身份抓到的都是同一张上条用户的主页图 →
    齐刷刷 wrong_user(置信度全 0.95)。修法：每条开头兜底关聊天面板 + Esc 清浮层。"""
    # 1) 兜底关聊天面板(没开时点 × 位置为空处也无害)
    click_norm(C_CLOSE[0], C_CLOSE[1], wait=0.6, label='回首页:关聊天×')
    # 2) Esc 连按，关掉作品/动态详情浮层、搜索结果浮层、任何弹窗
    for _ in range(3):
        pyautogui.press('esc'); time.sleep(0.5)
    time.sleep(0.6)


def click_input(wait=1.0):
    """点消息输入框：先模板匹配发送↑拿锚点，按 AKKE_INPUT_OFFSET 偏移点输入框；
    缺锚点/偏移则回退固定 C_INPUT。"""
    sp = find_match('send_arrow.png')
    off = _input_offset()
    if sp and off:
        x, y = sp[0] + off[0], sp[1] + off[1]
        print('  input[锚定发送↑] -> (%d,%d)' % (x, y))
        pyautogui.click(x, y)
        time.sleep(wait)
        return (x, y)
    return click_norm(C_INPUT[0], C_INPUT[1], wait=wait, label='消息输入框(固定坐标回退)')


# ── Windows SendInput unicode 键入(绕开剪贴板)──────────────────────────
# 无影会话剪贴板重定向使 Ctrl+V 粘贴失效(实测：纯/显式 Ctrl+V 都进不了字，
# ASCII 键入能进、SendInput unicode 能进)。改用 KEYEVENTF_UNICODE 直接键入，
# 中文/emoji 都行、不依赖剪贴板、不过 IME。union 必须含 MouseInput(最大成员)否则
# sizeof(INPUT) 偏小、SendInput 校验不过(踩过：只放 ki → 框闪但无字)。
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
    """逐字符 SendInput 键入(含 emoji 代理对)。返回成功发出的事件数。"""
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
    """清空输入框后键入 text。优先 SendInput unicode(无影上唯一可靠的中文输入)，
    非 Windows / SendInput 不可用时回退剪贴板粘贴。调用前需先点击聚焦目标输入框。"""
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.15)
    pyautogui.press('delete')
    time.sleep(0.15)
    try:
        type_unicode(text)
    except Exception as e:
        print('  [warn] SendInput 键入失败(%s)，回退剪贴板粘贴' % e)
        pyperclip.copy(text)
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.6)


def ocr_verify(expected, expected_number=None):
    """核对当前页是不是用户「expected」的个人主页。
    返回 (match_and_on_profile, confidence, seen)。
    要求同时满足：① 是个人主页(有关注/私信按钮+粉丝数，不是视频播放页/其它)；② 昵称匹配。
    expected_number：按抖音号搜索时传入 —— 特殊字符昵称(We•͈ᴗ⁃͈ ✧i💫 类)VL 比对易误拒，
    此时再核主页显示的「抖音号:」，与搜索号【完全一致】= 确定本人(号唯一,ASCII 精确匹配比
    昵称 OCR 稳)，即使昵称比对没过也放行。
    """
    path, _ = _shot('_prof.png')
    b64 = base64.b64encode(open(path, 'rb').read()).decode()
    num_q = ('(c) 主页上「抖音号:」后面显示的号码是什么(原样抄,没看到就填空串)?'
             if expected_number else '')
    num_field = ',"actual_number":"抖音号原文或空串"' if expected_number else ''
    p = ('这是抖音PC客户端的一张截图。请判断:'
         '(a) 当前是不是某个用户的【个人主页】(有"关注"/"私信"按钮、粉丝/获赞数、作品宫格)? '
         '注意:视频播放页/搜索结果页/推荐页都【不是】个人主页。'
         '(b) 如果是个人主页,主页主人的昵称是否就是「%s」(允许emoji/标点/空格差异,核心字符一致即可)?'
         '%s'
         '只回严格JSON:{"is_profile":true/false,"match":true/false,"confidence":0~1,"actual_nickname":"看到的昵称"%s}'
         % (expected, num_q, num_field))
    try:
        d = _pjson(_vision(b64, p))
        on_prof = bool(d.get('is_profile', False))
        matched = bool(d.get('match', False))
        conf = float(d.get('confidence', 0.0))
        seen = str(d.get('actual_nickname', ''))
        seen_no = ''
        if expected_number:
            seen_no = str(d.get('actual_number', '')).strip().lstrip('抖音号').lstrip(':：').strip()
        if expected_number and on_prof and not (matched and conf >= 0.95):
            # 昵称比对没过/置信不足 → 用抖音号精确核身兜底。归一化后再比：去所有空白 + casefold
            #（VL 偶尔混入空格/大小写漂移，exact 比对会把本人误杀成 wrong_user）。
            if seen_no and _norm_no(seen_no) == _norm_no(expected_number):
                print('  [OCR] 昵称比对未过但抖音号一致(%s) → 判定本人' % seen_no)
                matched, conf = True, max(conf, 0.99)
        if not on_prof:
            seen = '(非主页)' + seen
        if seen_no:
            seen = '%s|号:%s' % (seen, seen_no)   # wrong_user 时留证据：分清改名误杀 vs 真错人
        return (on_prof and matched), conf, seen
    except Exception as e:
        return False, 0.0, 'ocr_error:%s:%s' % (type(e).__name__, e)


def _norm_no(x):
    """抖音号归一化：去所有空白字符 + casefold（号是 ASCII 唯一串，大小写/空格差异不该判错人）。"""
    return ''.join(str(x or '').split()).casefold()


def _has_works_on_profile():
    """VL 判定当前个人主页【作品】区是否至少有 1 个视频(宫格里有封面)。
    '暂无作品/TA还没有发布作品'等空态 → False。VL 失败默认 False(不点赞,安全)。"""
    path, _ = _shot('_works.png')
    b64 = base64.b64encode(open(path, 'rb').read()).decode()
    p = ('这是抖音PC某用户【个人主页】截图。判断「作品」区是否至少有1个视频作品缩略图'
         '(宫格里有视频封面)。若显示"暂无作品/TA还没有发布作品/还没有发布过作品"等空状态则没有。'
         '只回严格JSON:{"has_works":true/false}')
    try:
        d = _pjson(_vision(b64, p))
        return bool(d.get('has_works', False))
    except Exception as e:
        print('  [点赞] 作品检测失败(按无作品处理): %s' % e)
        return False


def _vl_bool(shot_name, prompt, key, region=None):
    """通用 VL 是非判定:截图问一个布尔题。返回 True/False/None(看不清/异常)。
    用于关注/点赞点完后复核真生效没——固定坐标盲点在无影会点空,必须回检。
    region=(l,t,r,b) 比例(0~1):先把全屏截图裁到该区域再喂模型。2026-06-12 必加——
    全屏 2560 宽缩放后「关注/已关注」小字读不清,VL 假阳性(实测把没关注的小米手机判成已关注)。
    裁到按钮所在角(头部/右侧栏)放大后才读得准。返回严格区分文字、不做诱导式定位。"""
    try:
        path, (W, H) = _shot(shot_name)
        if region:
            from PIL import Image
            l, t, r, b = region
            cpath = os.path.join('screenshots', '_vlb_crop.png')
            with Image.open(path) as im:
                im.crop((int(l * W), int(t * H), int(r * W), int(b * H))).save(cpath)
            path = cpath
        b64 = base64.b64encode(open(path, 'rb').read()).decode()
        d = _pjson(_vision(b64, prompt))
        v = d.get(key)
        return None if v is None else bool(v)
    except Exception as e:
        print('  [VL] %s 判定失败: %s' % (key, e))
        return None


def _is_followed():
    # 裁到主页头部右上(关注按钮所在),放大小字防误读。问"读出按钮文字"而非"找已关注按钮"(后者诱导)。
    return _vl_bool('_follow.png',
        '这是抖音PC个人主页【头部】截图。请只看头部那个跟关注有关的按钮,读出它现在的文字:'
        '是「关注」(=还没关注)还是「已关注 / 相互关注 / 已互关」(=已经关注了)? '
        '没看清就回 null。只回严格JSON:{"followed":true或false或null}',
        'followed', region=(0.30, 0.04, 1.0, 0.40))


def _is_liked():
    # 裁到视频页右侧竖排互动栏,放大心形防误读。
    # 2026-06-16 收紧 prompt:只认心形【图标本身】被填红,别把下方红色点赞数/角标/画面红当已赞;
    # 拿不准回 null(柴油假阳性根因:单帧把空心读成红→前置幂等检查误判已赞→跳过点击却记 liked)。
    return _vl_bool('_like.png',
        '这是抖音PC视频播放页【右侧竖排互动栏】截图。只看【最上方那个心形点赞图标本身】,'
        '判断它当前是【实心、整体被填充成红/粉红色】(=已点赞),还是【白色或灰色的空心描边】(=未点赞)。'
        '严禁把心形【下方的红色数字(点赞数)】、任何红色角标、或视频画面里出现的红色当作已点赞;'
        '只有心形图标【自身】被填成红色才回 true。心形被遮挡/模糊/拿不准一律回 null,不要猜。'
        '只回严格JSON:{"liked":true或false或null}',
        'liked', region=(0.82, 0.15, 1.0, 0.95))


def _is_liked_confirmed():
    """点赞【前置幂等检查】专用:连采两帧、两帧都判 True 才认「已点赞」;任一帧 False/None 都不认。
    单帧 VL 偶发假阳性(空心读成红 / 红色点赞数当心)会让前置检查误判已赞→跳过点击→真没点却记 liked
    (2026-06-15 柴油假阳性根因)。双采一致把这条假阳性压下去;目标号是陌生潜客、本就极少已赞,
    多一次 VL 调用代价可接受。返回 True(两帧都 True) / None(其余,交由后续定位空心爱心去点)。"""
    if _is_liked() is not True:
        return None
    time.sleep(0.6)
    return True if _is_liked() is True else None


def _back_to_profile(nick):
    """点视频页左上角「返回」按钮退回个人主页。Esc 退不出视频层(评论框/视频吃焦点,二触实测
    4 次 Esc 仍卡视频页)→ 点返回键才稳。优先固定坐标 AKKE_C_BACK;没采则 VL 定位左上角返回键。
    点完 ocr_verify 复核,没回到再点(最多 3 次),仍不行兜底 Esc。
    镜像 douyin_comment_grounded.reset_to_profile(不 import 避免循环依赖)。返回是否已回主页。"""
    for i in range(3):
        clicked = False
        if C_BACK:
            click_norm(C_BACK[0], C_BACK[1], wait=1.2, label='返回按钮(固定坐标)')
            clicked = True
        else:
            try:
                bp = locate('抖音视频播放页【左上角】的「返回」按钮(向左的箭头 / "返回" 文字,'
                            '点了退出当前视频回到用户个人主页)', region=(0.0, 0.0, 0.30, 0.22))
                if bp:
                    pyautogui.click(bp[0], bp[1]); time.sleep(1.2)
                    clicked = True
            except Exception as e:
                print('  [点赞] 返回键 VL 定位失败: %s' % e)
        if not clicked:
            pyautogui.press('esc'); time.sleep(1.0)   # 兜底
        try:
            on_profile, _, _ = ocr_verify(nick)
        except Exception:
            on_profile = False
        if on_profile:
            print('  [点赞] 第%d次点返回后已回主页' % (i + 1))
            return True
    print('  [点赞] ⚠️ 3次仍未确认回主页(可量 AKKE_C_BACK 固定坐标提稳)')
    return False


def like_latest_work(nick):
    """OCR 核身份通过后调用:主页有作品就点进第一个 → 暂停视频 → 点赞 → 点「返回」退回主页 → 复核。
    全程 best-effort:坐标未采 / 无作品 / 任何一步失败都尽量退回主页,绝不抛异常阻断私信。
    返回 'liked'/'no_works'/'no_coords'/'failed'。坐标: AKKE_C_WORK_FIRST / AKKE_C_LIKE(返回用 AKKE_C_BACK,可选)。"""
    if not (C_WORK_FIRST and C_LIKE):
        return 'no_coords'   # 无影未采坐标 → 整段跳过,直接私信(不盲点 0,0)
    try:
        if not _has_works_on_profile():
            return 'no_works'
        # 点进第一个作品(宫格首格固定坐标),等播放页加载
        click_norm(C_WORK_FIRST[0], C_WORK_FIRST[1], wait=2.5, label='第一个作品')
        # 暂停视频:抖音PC视频播完会自动跳下一条,不暂停的话点赞/返回期间可能跳到别的视频。
        # 空格切播放/暂停(刚打开是播放态→一次空格=暂停)。
        pyautogui.press('space'); time.sleep(0.8)
        # 点赞(心形固定坐标)。已赞会变取消赞——陌生潜客几乎不会已赞,可接受。
        click_norm(C_LIKE[0], C_LIKE[1], wait=1.2, label='点赞♡')
        # VL 复核心形是否点亮;只在【明确未点亮】时重点一次(无影单击偶尔不注册)。
        # 不在 None(看不清)时重点,避免把已点亮的又点成取消赞。
        if _is_liked() is False:
            print('  [点赞] 首点心形未点亮,重点一次')
            click_norm(C_LIKE[0], C_LIKE[1], wait=1.2, label='点赞♡(重点)')
        liked_ok = _is_liked()
        # 退回主页:点视频页左上角「返回」按钮(Esc 退不出视频层)。
        _back_to_profile(nick)
        return 'liked' if liked_ok is not False else 'click_no_effect'
    except Exception as e:
        print('  [点赞] 失败(%s) → 尝试退回主页,继续私信' % e)
        try:
            _back_to_profile(nick)
        except Exception:
            pass
        return 'failed'


def _click_follow_btn(label):
    """点一次「关注」按钮:有 C_FOLLOW 走固定坐标,否则 VL 实时定位(镜像 web 通道,缺坐标不再跳过)。
    返回 True=点了一次 / False=连 VL 都没定位到(此时不盲点)。"""
    if C_FOLLOW:
        click_norm(C_FOLLOW[0], C_FOLLOW[1], wait=1.5, label=label)
        return True
    pt = None
    try:
        pt = locate('抖音PC个人主页头部的【关注按钮】(在「私信」按钮附近、按钮上写着"关注"二字)。不要选「私信」',
                    region=(0.30, 0.04, 1.0, 0.40))
    except Exception:
        pt = None
    if not pt:
        return False
    pyautogui.click(pt[0], pt[1])
    time.sleep(1.5)
    return True


def follow_user():
    """OCR 核身份通过后、在个人主页上点「关注」按钮(在 like_latest_work 之前调,此时确定停在主页)。
    关注是首触 DM【必做】步骤(2026-06-24 改硬要求):未采坐标(AKKE_C_FOLLOW)改用 VL 实时定位兜底,不再静默跳过;
    仅当 VL 也定位不到 / 点击异常时才放过——best-effort 不抛异常、不阻断私信。
    返回 'followed'/'already'/'click_no_effect'/'not_found'/'failed'。
    注意:已关注的用户再点会【取消关注】——陌生潜客几乎不会已关注,可接受(与点赞同款权衡)。"""
    try:
        # 先看是不是已关注(避免再点成取消关注)
        if _is_followed() is True:
            print('  [关注] 已是关注态,跳过')
            return 'already'
        if not _click_follow_btn('关注'):
            print('  [关注] 无坐标且 VL 定位不到关注按钮 → 放过(不阻断私信)')
            return 'not_found'
        if _is_followed() is True:
            return 'followed'
        # 没生效:无影单击偶尔只唤醒窗口不注册 → 重点一次
        print('  [关注] 首点未生效,重点一次')
        if not _click_follow_btn('关注(重点)'):
            return 'click_no_effect'
        return 'followed' if _is_followed() is True else 'click_no_effect'
    except Exception as e:
        print('  [关注] 失败(%s) → 继续私信' % e)
        return 'failed'


def process(c):
    """返回 (status, ocr_conf_or_None)。"""
    nick = c['nickname']
    query = (c.get('douyin_id') or nick).strip() or nick
    msg = c['message']
    print('\n=== %s ===' % nick)

    # 身份门：搜 query → 扫前 N 条结果找本人(2026-06-14)。query!=nick ⇒ 搜索词是反查到的抖音号
    # → 有号才扫多条且按号精确核身(扫到哪条都不会发错人)；无号或没量行距 → 只验第一条(=现状,
    # 深扫同名认不出本人反增发错人风险)。导航全用实测固定坐标(VL 在本机对搜索框/结果定位飘)。
    has_num = (query != nick)
    scan_n = SEARCH_SCAN_N if (has_num and (RESULT_ROW_DY > 0 or RESULT_COL_DX > 0)) else 1
    m, conf, seen = False, 0.0, ''
    for i in range(scan_n):
        col, row = i % SEARCH_NCOLS, i // SEARCH_NCOLS   # 阅读顺序：左上→右上→左下→右下…
        if i > 0:
            print('  [扫描] 前%d条非本人 → 重搜点第%d条结果(列%d行%d)' % (i, i + 1, col, row))
        # 同一条最多 1+NONPROFILE_RETRY 次：仅「非主页/OCR错」(脏屏) 才重试，给页面落稳再用原严格门判。
        for attempt in range(1 + NONPROFILE_RETRY):
            if attempt > 0:
                print('  [非主页重试] 第%d条 第%d次重试(重新导航+加长等待，等页面落稳)' % (i + 1, attempt))
            focus_douyin()
            reset_to_home()  # 每条/每次开头强制回干净首页(关残留聊天面板+Esc清浮层)，防连环卡死(2026-06-09)
            # 2026-06-12 写的 goto_home 一直没接线(死代码)：只 reset_to_home(Esc+关聊天) 不回首页,
            # 上一条停在【搜索结果页】时顶部搜索框会挪位(见 C_SEARCH 注释)→ 固定坐标点空 → 打字/回车
            # 没执行 → 残留的上条结果还在 → 点第一条=残留人。实测零星 72h/70 条 wrong_user 里 50% 是
            # 搜A落B、且反复粘在 SHUI×10/小读知新×9 等少数页。goto_home 点左侧「推荐」把搜索框钉回已知
            # 位置根治此链。guarded：没量 AKKE_C_HOME 时 no-op，行为不变、零风险。
            goto_home()
            click_norm(C_SEARCH[0], C_SEARCH[1], wait=1.2, label='搜索框')
            type_text(query)
            # committed 中文 + Enter 即触发搜索。前置：云电脑切英文输入模式，否则中文 IME 抢首字 → 搜错人。
            pyautogui.press('enter')
            time.sleep(2.5 + attempt * 1.5)   # 重试时加长加载等待，给残留/慢加载落稳
            click_user_tab()  # VL 点中「用户」tab,过滤成用户列表
            # 第 i 条头像 = 第一条 X 往右挪 col×列距、Y 往下挪 row×行距(2列网格)。i=0 即左上第一条(现状)。
            click_norm(C_FIRST[0] + col * RESULT_COL_DX, C_FIRST[1] + row * RESULT_ROW_DY,
                       wait=2.0 + attempt * 1.0, label='第%d条结果头像' % (i + 1))
            m, conf, seen = ocr_verify(nick, expected_number=(query if has_num else None))
            print('  [OCR] 第%d条 match=%s conf=%.2f seen=%r threshold=%s'
                  % (i + 1, m, conf, seen, MIN_CONF))
            c.setdefault('_ocr_trace', []).append('第%d槽#%d:%s(conf%.2f)' % (i + 1, attempt + 1, seen, conf))
            c['_ocr_seen'] = ' || '.join(c['_ocr_trace'])   # 落 sent_log：累积每槽扫描轨迹，wrong_user 时事后能分清「改名误杀」vs「真错人」
            # 退出重试：干净命中(发) 或「不是非主页/OCR错」(=on_prof=True 的干净别人，跳下一条深扫)。
            # 只有「非主页/OCR错」(脏屏) 继续重试同一条——从不绕过身份门，重试后仍走 ocr_verify 严格判。
            if (m and conf >= MIN_CONF) or not (seen.startswith('(非主页)') or seen.startswith('ocr_error')):
                break
        if m and conf >= MIN_CONF:
            break
    if not m or conf < MIN_CONF:
        print('  [跳过] 前%d条都不是本人 → 不发' % scan_n)
        try:
            import shutil
            _tag = (str(c.get('_comment_id') or nick) or 'x').replace('/', '_')[:24]
            shutil.copy(os.path.join('screenshots', '_prof.png'),
                        os.path.join('screenshots', 'wronguser_%s.png' % _tag))
        except Exception:
            pass
        return 'wrong_user', conf

    # Pre-action 风控弹窗预检（任务 3 modal 检测 PR）：身份门通过 = 在 profile 上了，
    # 此时如果已经有 SMS/滑块/人脸弹窗就别再做关注/点赞/私信，否则会推高风控等级。
    # 一次 VL call 检测 3 类弹窗；命中立即 HALT 整条 lead（不计配额、回池）。
    _modal_type, _modal_text, _modal_sample = _check_pre_action_modal('_pre_modal_%s.png' % nick[:10])
    if _modal_type:
        c['_modal_text'] = _modal_text
        c['_captcha_sample'] = _modal_sample
        return 'blocked_%s' % _modal_type, conf

    # 发私信前先关注用户 + 点赞最新作品(都 best-effort:坐标未采/失败都不阻断私信)
    # 关注/点赞结果落进 c → 写进 sent_log（关注状态此前只 print 到控制台、捞不回，2026-06-28 持久化）
    c['follow_status'] = follow_user()
    print('  [关注] %s' % c['follow_status'])
    c['like_status'] = like_latest_work(nick)
    print('  [点赞] %s' % c['like_status'])

    # 私信按钮：模板匹配优先，未命中回退固定坐标 C_DM（已关注态量，打开右侧聊天面板）
    click_match_or_norm('dm_button.png', C_DM[0], C_DM[1], wait=4.0, label='私信按钮')
    # 发送↑只在【空输入框】下定位一次：打字后红色激活态会让模板误匹配到别处(实测打字后
    # re-match 飘到 y=671 点空没发出)。输入框锚定它、发送也点它 —— 全程复用这个干净坐标。
    # 限定右侧 40% 区域找：聊天输入区永远在右(x≈2486)，避开左侧会话列表红色未读点误匹配
    # (实测某些用户的会话列表红点让 send_arrow 飘到 x≈655 → 锚错 → 不发)。
    _W, _H = pyautogui.size()
    sp = find_match('send_arrow.png', region=(int(_W * 0.6), 0, _W - int(_W * 0.6), _H))
    # 坐标闸：真发送↑恒在聊天面板【右下角】(实测 x≈0.97W, y≈0.44H)。若模板匹配到了
    # 但落在预期区外(如误点进视频后匹配到视频UI里的红箭头，实测飘到 (1719,344)=x0.67/y0.21)，
    # 这是【假阳性定位】——继续锚定+打字+点发会把消息发到错地方却记 sent(VL 拒绝门测不到)。
    # 命中即判定位失败、不打字不发送、记 cancelled(走 skipped，不计配额、回池可重发)。
    if sp and not (sp[0] >= 0.85 * _W and 0.28 * _H <= sp[1] <= 0.68 * _H):
        print('  [跳过] send_arrow 匹配到异常位置 (%d,%d) 不在预期发送区(x≥%.0f, y∈[%.0f,%.0f]) '
              '→ 判假阳性定位，不发' % (sp[0], sp[1], 0.85 * _W, 0.28 * _H, 0.68 * _H))
        close_chat()
        return 'cancelled', conf
    off = _input_offset()
    if sp and off:
        ipt = (sp[0] + off[0], sp[1] + off[1])
        print('  input[锚定发送↑] -> (%d,%d)' % ipt)
        pyautogui.click(ipt[0], ipt[1]); time.sleep(1.0)
    else:
        click_norm(C_INPUT[0], C_INPUT[1], wait=1.0, label='消息输入框(固定坐标回退)')
    type_text(msg)
    print('  [待发] %s' % msg[:34])
    # 发送：点打字前定位的发送↑(干净坐标，不 re-match，避免红色激活态误匹配)。
    # 去掉了 VL _has_text/_is_sent 门——本机实测它们【假阴性】会误跳过真发出的条目
    # （文字明明进了框却被判"没进"→记 cancelled 漏发）。发送可靠性已由「OCR 身份门通过 +
    # 输入框锚定到 1.0 匹配的发送↑」保证；实发量以人工核对私信列表为准（skill Step 6）。
    if sp:
        print('  click[发送↑] -> (%d,%d)' % (sp[0], sp[1]))
        pyautogui.click(sp[0], sp[1]); time.sleep(1.5)
    else:
        click_norm(C_SEND[0], C_SEND[1], wait=1.5, label='发送按钮(↑)')
    print('  ✅ 已点发送')
    # 发送后【关聊天窗口前】两道检查(都需聊天面板还开着):
    #   ① 负向:有没有弹"对方拒收/无法送达"系统提示(明确命中才算)
    #   ② 正向:对话区有没有出现含本文案的我方气泡(没有=大概率没真发出)
    # _check_rejected 升级为同时识别「rejected + 风控 modal」三类（任务 3 modal 检测 PR）
    # 一次 VL call 出两个判断省 50% 调用成本。modal 命中优先级高于 rejected——
    # SMS/滑块/人脸是【账号被风控】信号，必须立即停号；rejected 只是对方拒收。
    rejected, post_modal_type, _modal_reason, _post_sample = _check_rejected()
    verified = False if (rejected or post_modal_type) else _verify_bubble(msg)
    close_chat()
    if post_modal_type:
        c['_modal_text'] = _modal_reason[:60]
        c['_captcha_sample'] = _post_sample
        print('  ⚠️  [post-send modal] type=%s → 记 blocked_%s(账号已风控,强制 cooling)'
              % (post_modal_type, post_modal_type))
        return 'blocked_%s' % post_modal_type, conf
    if rejected:
        print('  [拒绝] 检测到对方拒收/无法送达提示 → 记 rejected(不计入成功)')
        return 'rejected', conf
    if not verified:
        return 'unverified', conf
    print('  ✅ 已确认气泡发出')
    return 'sent', conf


def _has_text():
    """输入框里是否已有实际文字(非占位提示)。裁剪到输入区放大,避免全屏小字假阴性。"""
    p, _ = _shot_crop('_typed.png')
    try:
        d = _pjson(_vision(base64.b64encode(open(p, 'rb').read()).decode(),
            '这是抖音私信【消息输入框区域】的局部放大截图。框里现在是否已经有用户输入的实际文字内容'
            '(不是灰色的"发送消息"占位提示)?只回严格JSON:{"has_text":true/false}'))
        return bool(d.get('has_text', False))
    except Exception as e:
        print('  [warn] 输入核对失败(按已输入处理): %s' % e)
        return True


def _save_captcha_sample(modal_type, src_path):
    """命中验证码/风控弹窗时把截图复制到持久 captcha_samples/ 目录(攒样本)。

    任务2(简单字符验证码自动填写)卡点①「需要积累验证截图」的落地——没有真实样本
    没法判断该上 ddddocr 还是滑块/点选，所以先无脑攒。返回相对路径(写进 sent_log →
    wuying_poll_agent 上传 Supabase Storage，URL 进 captcha_alerts.metadata)。
    best-effort，失败回 ''，绝不拖垮发送主链路。"""
    try:
        import shutil
        os.makedirs('captcha_samples', exist_ok=True)
        # 文件名带微秒，配合 poll-agent 侧的账号 UUID 子目录，public bucket 下也难猜。
        name = '%s_%s.png' % (datetime.now().strftime('%Y%m%d_%H%M%S_%f'), modal_type)
        dst = os.path.join('captcha_samples', name)
        shutil.copy(src_path, dst)
        print('  [验证码样本] 已存 %s' % dst)
        return dst
    except Exception as e:
        print('  [warn] 验证码样本保存失败: %s' % e)
        return ''


def _check_rejected():
    """best-effort：发送后聊天区是否出现「消息没送达」的系统提示(对方好友验证/拒收陌生人/
    操作频繁/发送失败) **或** 抖音风控验证弹窗(SMS / 滑块 / 人脸活检 / 字符图片验证码)。

    返回 (rejected: bool, modal_type: str | None, reason: str)：
      rejected=True              → 对方拒收/系统提示，记 'rejected'（不算账号问题）
      modal_type='sms|slider|face' → 风控验证弹窗，记 'blocked_<type>'（**账号被风控**）
      都 None/False              → 按成功处理

    一次 VL call 同时判 rejected + modal —— 共享同一张 _after_send.png 截图，
    比拆两次省 50% VL 成本。明确命中才算；VL 失败/不确定 → 保守按成功处理，
    不误杀真发出的。注：静默限频(7911，无任何 UI 提示)这条路探测不到。"""
    try:
        p, _ = _shot('_after_send.png')
        d = _pjson(_vision(base64.b64encode(open(p, 'rb').read()).decode(),
            '这是抖音PC截图。请同时判断两件事:\n'
            '(a) 聊天区是否出现"消息没送达"系统提示？例如"对方开启了好友验证/需要先关注对方/'
            '对方拒绝接收陌生人消息/操作太频繁/发送失败"。普通气泡不算。\n'
            '(b) 屏幕上是否出现抖音风控验证弹窗？四类:\n'
            '    sms 短信验证：含"短信验证/请输入本人持有手机号/输入验证码/接收短信"\n'
            '    slider 滑块拼图：含"请完成下列验证后继续/按住左边按钮拖动/完成上方拼图"\n'
            '    face 人脸活检：含"请保持人脸/眨眼/转头/张嘴/扫码用手机完成认证"\n'
            '    char 字符图片验证码：含"请输入图中字符/看不清换一张/4-6位字母数字图片验证码"\n'
            '只回严格JSON: {"blocked":true/false, "reason":"<提示原文或空>",'
            '"modal_type":"sms"|"slider"|"face"|"char"|"none","modal_text":"<弹窗文字摘要≤30字>"}'))
        blocked = bool(d.get('blocked', False))
        mt = (d.get('modal_type') or 'none').strip().lower()
        modal_type = mt if mt in ('sms', 'slider', 'face', 'char') else None
        if blocked:
            print('  [拒绝原因] %s' % (d.get('reason') or '?'))
        sample = ''
        if modal_type:
            print('  ⚠️  [风控弹窗] type=%s · %s' % (modal_type, (d.get('modal_text') or '')[:60]))
            sample = _save_captcha_sample(modal_type, p)
        return blocked, modal_type, (d.get('reason') or ''), sample
    except Exception as e:
        print('  [warn] 拒绝/modal 核对失败(按成功处理): %s' % e)
        return False, None, '', ''


def _check_pre_action_modal(snap_name='_pre_modal.png'):
    """Pre-action 风控弹窗预检：在 OCR 身份门通过后、关注/点赞/私信之前
    用一次 VL call 看屏幕上是否已经有 SMS/滑块/人脸 弹窗。

    早期检测命中比"发完才发现"价值高得多——还没动作就停手，不会推高风控等级。
    复用 _check_rejected 的同款 modal prompt 部分，单独跑（这里没必要判 rejected）。

    返回 (modal_type: str | None, modal_text: str, sample_path: str)。命中即应 HALT 整轮。"""
    try:
        p, _ = _shot(snap_name)
        d = _pjson(_vision(base64.b64encode(open(p, 'rb').read()).decode(),
            '这是抖音PC截图。屏幕上是否出现抖音风控验证弹窗？四类:\n'
            'sms 短信验证：含"短信验证/请输入本人持有手机号/输入验证码/接收短信"\n'
            'slider 滑块拼图：含"请完成下列验证后继续/按住左边按钮拖动/完成上方拼图"\n'
            'face 人脸活检：含"请保持人脸/眨眼/转头/张嘴/扫码用手机完成认证"\n'
            'char 字符图片验证码：含"请输入图中字符/看不清换一张/4-6位字母数字图片验证码"\n'
            '只有明显弹窗才算（半遮罩 + 标题 + 按钮）；底部 banner 不算。\n'
            '只回严格JSON: {"modal_type":"sms"|"slider"|"face"|"char"|"none","modal_text":"<弹窗文字≤30字>"}'))
        mt = (d.get('modal_type') or 'none').strip().lower()
        if mt in ('sms', 'slider', 'face', 'char'):
            text = (d.get('modal_text') or '')[:60]
            print('  ⚠️  [pre-action 风控弹窗] type=%s · %s' % (mt, text))
            return mt, text, _save_captcha_sample(mt, p)
    except Exception as e:
        print('  [warn] pre-action modal 检测失败(按无弹窗处理): %s' % e)
    return None, '', ''


def _verify_bubble(msg):
    """正向送达验证：发送后对话区里【我方(右侧)最新气泡】是否包含刚发的文案。
    命中 → 有正向依据,记 sent；明确未命中 → 记 unverified(回池可重发,不计成功)。

    它挡的是【发送机制失败】——坐标漂/点空/文字没进框 → 根本没气泡(模板 NOT FOUND
    退固定坐标裸奔时的安全网,正是缺口①)。它【挡不住】抖音 7911 静默限频:那种本地
    照样显示气泡、只是对方收不到,GUI 路径看不到 status_code,物理上测不了。

    VL 异常/不确定 → 保守按【已发】(返回 True),不重蹈旧 _is_sent 的假阴性漏发
    (2026-05-31 实测查"输入框空没空"假阴性高;此处查"气泡里有没有我的字"是更强的正向
    信号,但仍只在【明确说没有】时才判 unverified)。

    2026-06-29：实测气泡渲染/VL 单次调用有时机竞态,同一条干净文案 ~半数被单次核对假阴性
    （气泡其实已在、第一次截图/读图没赶上）。改为【最多 3 次:miss 后等 1.5s 重新截图再核】,
    任一次命中即记 sent。retry 只补救"气泡在但没读到",全程无气泡仍判 unverified —— 不放松
    真失败判定、不增加假阳性漏判。"""
    snippet = (msg or '').strip()[:16]
    if not snippet:
        return True
    for attempt in range(3):
        try:
            p, _ = _shot('_bubble_check.png')
            d = _pjson(_vision(base64.b64encode(open(p, 'rb').read()).decode(),
                '这是抖音PC私信聊天窗口截图。对话区里【右侧、我自己发出的那一排消息气泡】中,'
                '最新一条是否包含这段文字：「%s」?只看右侧自己发的气泡,左侧对方发来的不算。'
                '只回严格JSON:{"bubble_found":true/false}' % snippet))
            if bool(d.get('bubble_found', False)):
                if attempt > 0:
                    print('  [验证] 第%d次核对命中气泡含「%s」→ sent' % (attempt + 1, snippet))
                return True
        except Exception as e:
            print('  [warn] 气泡验证异常(保守按已发,避免假阴性漏发): %s' % e)
            return True
        if attempt < 2:
            time.sleep(1.5)
    print('  [验证] 3次核对均未见我方气泡含「%s」→ 判 unverified(大概率没真发出,回池可重发)' % snippet)
    return False


def _is_sent():
    """输入框清空(只剩占位提示) = 文字已发出。裁剪到输入区放大,避免全屏小字误判。"""
    p, _ = _shot_crop('_after_send.png')
    try:
        d = _pjson(_vision(base64.b64encode(open(p, 'rb').read()).decode(),
            '这是抖音私信【消息输入框区域】的局部放大截图。框现在是不是空的(只剩"发送消息"灰色提示、'
            '说明刚才的文字已经作为消息发出去了)?只回严格JSON:{"sent":true/false}'))
        return bool(d.get('sent', False))
    except Exception as e:
        print('  [warn] 发送核对失败(按未发处理): %s' % e)
        return False


def main(contacts_csv):
    with open(contacts_csv, encoding='utf-8-sig') as f:
        contacts = list(csv.DictReader(f))
    if not contacts:
        print('名单为空, 退出')
        return
    if len(contacts) > DAILY_LIMIT:
        print('⚠️ 名单 %d > 上限 %d, 截断' % (len(contacts), DAILY_LIMIT))
        contacts = contacts[:DAILY_LIMIT]

    print('=== douyin_dm_grounded  model=%s  conf>=%s ===' % (MODEL, MIN_CONF))
    print('共 %d 条; 前置: ①抖音PC前台+最大化 ②分辨率锁定 ③输入法切【英文模式】' % len(contacts))
    print('   ⚠️ 输入法必须英文模式：中文 IME 会抢搜索词首字 → 搜错人。')

    log = 'sent_log_%s.csv' % datetime.now().strftime('%Y%m%d')
    fields = ['douyin_id', 'nickname', 'message', 'has_works',
              '_comment_id', '_sec_uid', '_dispatch_id',
              'status', 'follow_status', 'like_status',
              'sent_at', '_ocr_confidence', '_ocr_seen', '_captcha_sample']
    first_write = not os.path.exists(log)
    with open(log, 'a', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        if first_write:
            w.writeheader()
        with _wl.dm_batch():            # DM 批期间置 .dm-want → route-B 让位(DM 优先)
            for i, c in enumerate(contacts, 1):
                print('\n========== [%d/%d] ==========' % (i, len(contacts)))
                _wl.dm_keepalive()      # 刷新 .dm-want mtime 防长批被误判 stale
                ocr_conf = None
                try:
                    with _wl.window_turn('dm'):   # 抢单抖音窗口操作权(DM 直接抢)
                        status, ocr_conf = process(c)
                except Exception as e:
                    print('  ❌ 异常: %s' % e)
                    status = 'error:%s' % e
                w.writerow({**c, 'status': status,
                            'sent_at': datetime.now().isoformat(),
                            '_ocr_confidence': '' if ocr_conf is None else '%.3f' % ocr_conf})
                f.flush()
                if i < len(contacts):
                    time.sleep(random.randint(MIN_INTERVAL, MAX_INTERVAL))
    print('\n✅ 完成. 日志: %s' % log)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _write_env_kv(updates):
    """把 updates 合并进同目录 .env：已有键替换，没有则追加，其它行不动。"""
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
    """先等回车(给时间导航到对应界面+移鼠标)，再短倒计时后抓光标位置。"""
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


def capture():
    """模板采集：依次把鼠标悬停到各元素上，自动抓取光标周围裁剪存 templates/。
    前提：抖音PC登录、最大化，并【已手动打开任意一个私信聊天窗口】(能看到发送↑+输入框)。
    锁定无影分辨率后只需采集一次；换分辨率才需重采。比每次实时定位稳得多。"""
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    W, H = pyautogui.size()
    print('=== 模板采集模式  分辨率 %dx%d ===' % (W, H))
    print('抖音已登录最大化。每个元素【先导航到对应界面】，再按回车抓取(不限时)。')
    print('建议顺序：随便搜一个人→停在搜索结果页(采搜索框+用户tab)→进TA主页(采私信)')
    print('→点私信开聊天窗口(采发送↑+输入框)。\n')
    # (文件名, 提示[含所在界面], 裁剪宽, 裁剪高)
    items = [
        ('search_box.png', '顶部搜索输入框中心(放大镜旁，任意界面都有)', 90, 32),
        ('tab_user.png', '搜索结果页顶部的「用户」分类标签(需先搜一个词)', 64, 34),
        ('dm_button.png', '某用户个人主页头部的「私信」按钮', 80, 38),
        ('send_arrow.png', '私信聊天窗口右下角红色↑发送按钮', 48, 48),
    ]
    send_pos = None
    for fname, label, cw, ch in items:
        x, y = _countdown(label)
        if fname == 'send_arrow.png':
            send_pos = (x, y)
        l, t = max(0, x - cw // 2), max(0, y - ch // 2)
        pyautogui.screenshot(region=(l, t, cw, ch)).save(os.path.join(TEMPLATE_DIR, fname))
        print('   ✓ 存 %s（光标@%d,%d 裁 %dx%d）\n' % (fname, x, y, cw, ch))
    # 输入框无图标 → 记录它相对发送↑的像素偏移
    ix, iy = _countdown('【消息输入框中心】(灰色"发送消息"提示处)')
    if send_pos:
        dx, dy = ix - send_pos[0], iy - send_pos[1]
        _write_env_kv({'AKKE_INPUT_OFFSET': '%d,%d' % (dx, dy)})
        print('   ✓ 输入框@(%d,%d) 相对发送↑偏移=%d,%d → 写入 .env AKKE_INPUT_OFFSET\n' % (ix, iy, dx, dy))
    else:
        print('   ⚠️ 未采到发送↑位置，AKKE_INPUT_OFFSET 未写入，输入框将回退固定坐标\n')
    print('✅ 采集完成。参考图在 %s' % TEMPLATE_DIR)
    print('   抽查：用图片查看器打开 templates/*.png，确认每张只框住目标元素(没多框背景/文字)。')
    print('   不满意就重跑 --capture。之后直接跑发送，定位自动走模板匹配。')
    return 0


def calibrate():
    """坐标校准：自动量出聊天面板【输入框 + 发送键】坐标，写进 .env。
    前提：抖音PC已登录最大化，且【已手动打开任意一个私信聊天窗口】。
    多次采样取中位数 + 生成标注图供肉眼复核，比每次发送实时定位稳。"""
    print('=== 坐标校准模式 ===')
    print('前提：抖音PC已登录、最大化，并且已经【打开任意一个私信聊天窗口】')
    print('（右侧能看到聊天面板，底部有横向输入框 + 右下角发送按钮）')
    if not KEY:
        print('缺 OPENROUTER_API_KEY / ANTHROPIC_API_KEY，无法视觉定位', file=sys.stderr)
        return 2
    focus_douyin()
    time.sleep(1.0)
    W, H = pyautogui.size()
    print('屏幕分辨率: %dx%d' % (W, H))
    region = (0.45, 0.55, 1.0, 1.0)   # 右半屏下半=聊天输入区，小目标在裁剪图里占比更大
    in_xs = []; in_ys = []; send_xs = []; send_ys = []
    n = 5
    for i in range(n):
        print('  采样 %d/%d ...' % (i + 1, n))
        pin = locate('抖音私信聊天面板最底部的横向消息输入框，里面有"发送消息"灰色提示文字', region=region)
        if pin:
            in_xs.append(pin[0]); in_ys.append(pin[1])
        ps = locate('抖音私信输入框最右侧的圆形发送按钮，里面是一个向上的箭头↑（红色/粉色/灰色都可能）', region=region)
        if ps:
            send_xs.append(ps[0]); send_ys.append(ps[1])
        time.sleep(0.4)
    if not in_xs or not send_xs:
        print('校准失败：定位不到输入框或发送键。请确认私信窗口真的打开了，再重跑。', file=sys.stderr)
        return 1
    ix = int(_median(in_xs)); iy = int(_median(in_ys))
    sx = int(_median(send_xs)); sy = int(_median(send_ys))
    ci = (round(ix / W * 1000), round(iy / H * 1000))
    cs = (round(sx / W * 1000), round(sy / H * 1000))
    print('  输入框 像素=(%d,%d) 归一化=%d,%d' % (ix, iy, ci[0], ci[1]))
    print('  发送键 像素=(%d,%d) 归一化=%d,%d' % (sx, sy, cs[0], cs[1]))
    if len(send_xs) >= 3 and (max(send_xs) - min(send_xs)) > W * 0.05:
        print('  ⚠️ 发送键定位抖动较大，请务必看下面的标注图确认绿圈位置；不对就重跑或发群求助')
    try:
        from PIL import Image, ImageDraw
        shot, _ = _shot('_calib_raw.png')
        im = Image.open(shot).convert('RGB')
        d = ImageDraw.Draw(im)
        r = max(14, W // 110)
        d.ellipse([ix - r, iy - r, ix + r, iy + r], outline=(255, 0, 0), width=5)
        d.ellipse([sx - r, sy - r, sx + r, sy + r], outline=(0, 255, 0), width=5)
        cp = os.path.join('screenshots', '_calib_marked.png')
        im.save(cp)
        print('  已存标注图: %s' % cp)
        print('  红圈=输入框、绿圈=发送键，请打开这张图确认两个圈都落在对的位置上')
    except Exception as e:
        print('  [warn] 标注图生成失败: %s' % e)
    _write_env_kv({'AKKE_C_INPUT': '%d,%d' % ci, 'AKKE_C_SEND': '%d,%d' % cs})
    print('  ✅ 已写入 .env：AKKE_C_INPUT=%d,%d  AKKE_C_SEND=%d,%d' % (ci[0], ci[1], cs[0], cs[1]))
    print('  下次发送自动用这组坐标。若标注图位置不对，重跑本校准即可。')
    return 0


if __name__ == '__main__':
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--capture' in flags:
        sys.exit(capture())
    if '--calibrate' in flags:
        sys.exit(calibrate())
    if not args:
        print('用法: python douyin_dm_grounded.py contacts.csv', file=sys.stderr)
        print('      python douyin_dm_grounded.py --capture     # 首次/换分辨率：采集模板参考图(推荐)', file=sys.stderr)
        print('      python douyin_dm_grounded.py --calibrate   # 旧：仅校准聊天面板固定坐标', file=sys.stderr)
        sys.exit(2)
    if not KEY:
        print('❌ 缺 ANTHROPIC_API_KEY / OPENROUTER_API_KEY', file=sys.stderr)
        sys.exit(2)
    main(args[0])
