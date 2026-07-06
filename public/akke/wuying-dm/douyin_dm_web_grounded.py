"""
抖音【网页版 / Edge 浏览器】批量私信 — Qwen 视觉定位版 (grounded, web)。

与 douyin_dm_grounded.py(抖音 PC 客户端版)的关系:
  低层机器(Qwen VL 调用 / 截图 / SendInput 中文键入 / 身份 OCR / 气泡核对 /
  CSV 契约 / 窗口管理)【直接 import 复用】,本文件只重写三段【网页特有】流程:
    ① 导航     —— 不搜索, Ctrl+L 地址栏直达 douyin.com/user/<sec_uid>(sec_uid 唯一,
                  天然定位到对的人, 省掉 PC 版的搜索 + 双因子撞名一大段)。
    ② 点「私信」—— 主页头部灰色「私信」按钮(紧挨红「关注」,视觉锁字防误点),
                  打开【页内右侧浮层】聊天面板(不跳页/不开客户端)。
    ③ 面板身份门 —— 网页全局私信箱可能没切干净 → 发送前 OCR 面板顶昵称 vs 目标,
                  不一致不发(PC 版没有这道闸,网页版必须加)。

I/O 契约与 douyin_dm_grounded.py 一致(故 wuying_poll_agent.py 可平替调用):
  contacts.csv 列: douyin_id nickname message has_works _comment_id _sec_uid _dispatch_id
  ⚠️ web 版强依赖 _sec_uid(直达 URL)—— 缺 sec_uid 的行记 no_secuid 跳过。
  sent_log_YYYYMMDD.csv 列: 上述 + status sent_at _ocr_confidence _ocr_seen
  status: sent / unverified / rejected / wrong_user / wrong_chat / dm_panel_failed
          / no_secuid / cancelled / error:<msg>
          (wrong_chat = 私信面板对话对象≠目标, 防发错人, 回池可重发)
          (unverified = 点了发送但对话区没出现含本文案的我方气泡, 回池可重发, 不计成功)

每条流程(2026-06-17 加「关注+点赞」, 镜像 PC 版): 直达主页 → 主页身份门 → 关注 →
  (有作品则)点进第一个作品点赞再退回主页 → 点私信 → 面板身份门 → 发 → 气泡核对。
  关注/点赞全 best-effort(失败不阻断私信); 关掉: AKKE_WEB_DO_FOLLOW=0 / AKKE_WEB_DO_LIKE=0。

用法:
  python douyin_dm_web_grounded.py contacts.csv              # 正式跑
  python douyin_dm_web_grounded.py contacts.csv --max=2 --confirm   # spike: 只跑前 2 条 + 每条发送前人工确认
  python douyin_dm_web_grounded.py --calibrate               # 校准网页私信面板【输入框/发送↑】固定坐标
                                                             # (先在 Edge 里手动打开任意一个私信聊天浮层再跑)
  python douyin_dm_web_grounded.py --measure                 # 量【第一个作品/点赞♡】坐标启用点赞
                                                             # (关注走视觉、后退走浏览器快捷键, 不用量)

前提(云电脑 Edge):
  ① Edge 登录小文/有大有小(shawsnk)抖音网页版、窗口最大化、缩放建议但不强制 100%
  ② 输入法切【英文】(中文 IME 会抢 URL 首字/键入)
  ③ .env(同目录)同 PC 版: ANTHROPIC_API_KEY(放 OpenRouter key)/ AKKE_OCR_* / AKKE_C_INPUT / AKKE_C_SEND
  ④ 125% 等非 100% 缩放: 默认开 DPI-aware(AKKE_DPI_AWARE=1)让截图清晰 + 坐标自洽;
     若坐标整体偏 1.25x 或异常, 设 AKKE_DPI_AWARE=0 回退后重新 --calibrate。
依赖: 同 douyin_dm_grounded.py(pyautogui pyperclip pillow; 视觉走 OpenRouter)。
"""
from __future__ import annotations

import csv
import ctypes
import os
import random
import sys
import time
from datetime import datetime

import pyautogui

# 低层 helper 全部复用 PC 客户端版(同目录同进程)。import 即跑其模块级 .env 加载 + pyautogui 配置。
from douyin_dm_grounded import (  # noqa: E402
    KEY, MODEL, MIN_CONF, MIN_INTERVAL, MAX_INTERVAL, DAILY_LIMIT,
    _coord, _vision, _shot, _pjson,
    locate, click_norm, type_text, type_unicode, ocr_verify,
    _verify_bubble, _check_rejected, focus_douyin, calibrate,
    _has_works_on_profile, _is_liked, _is_followed, _countdown, _write_env_kv,
)

# 网页私信面板【输入框 / 发送↑】固定坐标(归一化 0~1000, ×当前屏宽高)。
# 复用 PC 版同名 env 键(本机只跑网页版, 不冲突);默认值=两张实测截图估算(图1):
#   输入框 ~0.83W,0.59H → 830,590 ; 发送↑ ~0.965W,0.59H → 965,590。
# 上机用 --calibrate 量准后写进 .env 覆盖。
WEB_C_INPUT = _coord('AKKE_C_INPUT', (830, 590))
WEB_C_SEND = _coord('AKKE_C_SEND', (965, 590))
# 主页「私信」按钮固定坐标兜底(视觉定位失败才用)。图2 估算 ~0.75W,0.175H → 751,175。
WEB_C_DM = _coord('AKKE_WEB_C_DM', (751, 175))
# 发私信前「关注 + 点赞最新作品」(镜像 PC 版 follow_user/like_latest_work)。全 best-effort,失败不阻断私信。
# 关注:红「关注」按钮在「私信」左边(图2 ~0.70W,0.175H),视觉优先 + 固定坐标兜底。
WEB_C_FOLLOW = _coord('AKKE_WEB_C_FOLLOW', (704, 175))
# 作品宫格第一个 / 视频页点赞♡ / 视频弹层关闭X:小元素,必须 --measure 量准(默认 None=未量)。
# WEB_C_WORK_FIRST/WEB_C_LIKE 未量→跳过点赞,不盲点。
WEB_C_WORK_FIRST = _coord('AKKE_WEB_C_WORK_FIRST', None)
WEB_C_LIKE = _coord('AKKE_WEB_C_LIKE', None)
# 视频关闭X(2026-06-17):点作品在 web 是【弹层 modal】覆盖主页,Alt+左走浏览器历史会退到【上一个用户】
# (spike 实测)→ 点赞后必须点弹层右上角【关闭X】关掉弹层、回到当前主页。量准它才退得对。
WEB_C_VIDEO_CLOSE = _coord('AKKE_WEB_C_VIDEO_CLOSE', None)
DO_FOLLOW = os.environ.get('AKKE_WEB_DO_FOLLOW', '1') == '1'   # 关掉关注: AKKE_WEB_DO_FOLLOW=0
DO_LIKE = os.environ.get('AKKE_WEB_DO_LIKE', '1') == '1'       # 关掉点赞: AKKE_WEB_DO_LIKE=0
# 退回主页兜底方式(WEB_C_VIDEO_CLOSE 未量时才用):back=Alt+Left / close=Ctrl+W。
BACK_MODE = os.environ.get('AKKE_WEB_BACK', 'back')

URL_TMPL = os.environ.get('AKKE_WEB_PROFILE_URL', 'https://www.douyin.com/user/%s')
DPI_AWARE = os.environ.get('AKKE_DPI_AWARE', '1') == '1'
# URL 通道默认信任 sec_uid:落到个人主页即发,不强求昵称精确匹配(改名/派单旧昵称容错)。
# =0 退回严格昵称匹配(抖音号搜索通道老行为)。详见 process_web 身份门。
TRUST_SECUID = os.environ.get('AKKE_WEB_TRUST_SECUID', '1') == '1'
# 面板身份门也信任 sec_uid：sec_uid 地址栏直达已唯一锁人，面板再用 VL 读昵称二次校验
# 遇花体/emoji/长名会读错 → 假阴性 wrong_chat 拒发(2026-07-05 Yᗜangঞᩚ→YOang)。
# 与主页门 TRUST_SECUID 对称。=0 退回严格面板拦截。
TRUST_SECUID_ON_PANEL = os.environ.get('AKKE_WEB_TRUST_SECUID_ON_PANEL', '1') == '1'


def _set_dpi_aware():
    """125% 等缩放下让进程 DPI-aware: 截图=物理像素(清晰, VL 读得准) + pyautogui 坐标
    与截图同在物理像素空间(归一化×屏宽高自洽)。不设的话 DPI-unaware 进程截图被降采样
    模糊、且物理/逻辑像素混用易错位。两态都【自洽】, 关键是 calibrate 与发送用【同一态】
    —— 本脚本恒在启动时统一设置, 故 calibrate 量的坐标与发送点的坐标必然同空间。"""
    if not DPI_AWARE:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def goto_profile(sec_uid, wait=4.5):
    """Edge 地址栏直达对方主页。Ctrl+L 聚焦地址栏 → 全选清空 → 键入 URL → Enter。
    sec_uid 唯一 → 落到的就是对的人(身份天然保证), 不需要搜索 + 撞名消歧。
    地址栏导航是【整页重载】, 顺带把上一条残留的私信浮层清掉, 每条从干净页开始。"""
    focus_douyin()   # 把 Edge(标题含"抖音")窗口置前 + 最大化
    url = URL_TMPL % sec_uid
    pyautogui.hotkey('ctrl', 'l'); time.sleep(0.8)
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.2)
    pyautogui.press('delete'); time.sleep(0.2)
    # 关键(2026-06-17): 用 SendInput unicode 注入 URL【绕开中文 IME】。
    # pyautogui.typewrite 会被中文输入法拦/吞字 → 残缺 URL 被 Edge 当搜索词丢给搜狗
    # (spike 首跑 '(非主页)搜狗搜索' 根因)。type_unicode 直接发 unicode 事件, 不过 IME。
    type_unicode(url); time.sleep(0.4)
    pyautogui.press('enter')
    time.sleep(wait)


def open_dm_panel(nick, wait=3.5):
    """主页头部点「私信」灰按钮 → 打开右侧聊天浮层。
    视觉锁「私信」二字(它紧挨红「关注」, 盲点坐标易点成关注)→ 裁到头部右侧放大定位;
    视觉失败才回退固定坐标 WEB_C_DM。"""
    # 页面可能自动下滚 → 头部「私信」按钮离开定位 region / 固定坐标点空 → dm_panel_failed
    # (2026-07-06 云电脑现场实测)。点私信前先滚回顶部: scroll 在鼠标位置生效, 不点击, 不依赖焦点。
    _W, _H = pyautogui.size()
    pyautogui.moveTo(int(_W * 0.5), int(_H * 0.5))
    pyautogui.scroll(3000)   # 正数=上滚, 一次滚到顶部, 复位头部
    time.sleep(0.6)
    pt = None
    try:
        pt = locate(
            '抖音网页版个人主页头部的【私信】按钮(浅灰色底, 在红色"关注"按钮的右边, 按钮上写着"私信"两个字)。'
            '注意不要选红色的"关注"按钮',
            region=(0.55, 0.10, 0.95, 0.25))
    except Exception as e:
        print('  [私信] 视觉定位异常: %s' % e)
    if pt:
        pyautogui.click(pt[0], pt[1])
        time.sleep(wait)
    else:
        print('  [私信] 视觉未定位到, 回退固定坐标 WEB_C_DM')
        click_norm(WEB_C_DM[0], WEB_C_DM[1], wait=wait, label='私信(固定坐标回退)')


def verify_chat_panel(expected):
    """私信面板身份门(网页特有)。OCR 右侧浮层判定: (a)面板开没开 (b)顶部对话对象是不是目标。
    返回 (panel_open, match, confidence, seen)。VL 异常 → (False,False,0,'ocr_error')。
    防的是【全局私信箱没切到目标会话 → 把话发给列表里上一个人】(图1 暴露的风险)。"""
    path, _ = _shot('_chatpanel.png')
    import base64
    b64 = base64.b64encode(open(path, 'rb').read()).decode()
    p = ('这是抖音网页版的一张截图, 屏幕右侧可能有一个【私信聊天浮层面板】。请判断:'
         '(a) 右侧私信聊天面板是不是打开了(顶部显示对话对象昵称, 底部有"发送消息"输入框)? '
         '(b) 如果打开了, 面板顶部当前对话对象的昵称是不是「%s」'
         '(允许 emoji/标点/空格差异, 核心字符一致即可)? '
         '只回严格JSON:{"panel_open":true/false,"match":true/false,"confidence":0~1,"seen":"看到的昵称"}'
         % expected)
    try:
        d = _pjson(_vision(b64, p))
        return (bool(d.get('panel_open', False)), bool(d.get('match', False)),
                float(d.get('confidence', 0.0)), str(d.get('seen', '')))
    except Exception as e:
        return False, False, 0.0, 'ocr_error:%s' % type(e).__name__


def follow_user_web():
    """主页上点红「关注」按钮(发私信前)。已关注则跳过(避免再点成取消关注)。
    视觉锁红「关注」(在「私信」左边)→ 失败回退固定坐标。best-effort,不抛异常、不阻断私信。
    返回 followed/already/off/click_no_effect/failed。复用 PC 版 _is_followed 复核(裁头部右侧读按钮文字)。"""
    if not DO_FOLLOW:
        return 'off'
    try:
        if _is_followed() is True:
            return 'already'
        pt = None
        try:
            pt = locate('抖音网页版个人主页头部的【红色"关注"按钮】(在灰色"私信"按钮的左边)。不要选"私信"',
                        region=(0.55, 0.10, 0.95, 0.25))
        except Exception:
            pt = None
        if pt:
            pyautogui.click(pt[0], pt[1]); time.sleep(1.3)
        else:
            click_norm(WEB_C_FOLLOW[0], WEB_C_FOLLOW[1], wait=1.3, label='关注(固定坐标回退)')
        if _is_followed() is True:
            return 'followed'
        # 没生效:单击偶尔只唤醒窗口不注册 → 固定坐标重点一次
        click_norm(WEB_C_FOLLOW[0], WEB_C_FOLLOW[1], wait=1.3, label='关注(重点)')
        return 'followed' if _is_followed() is True else 'click_no_effect'
    except Exception as e:
        print('  [关注] 失败(%s) → 继续私信' % e)
        return 'failed'


def _back_to_profile_web(nick):
    """作品【视频弹层】退回个人主页。web 点作品是 modal 覆盖主页:
    ① 量了 WEB_C_VIDEO_CLOSE → 点弹层右上角【关闭X】关弹层(正解,不动浏览器历史);
    ② 没量 → 兜底 BACK_MODE(back=Alt+Left / close=Ctrl+W) —— 注意 Alt+左在 modal 下会退到
       【上一个用户】(2026-06-17 spike 实测),所以强烈建议量 X。
    每次点完 ocr_verify 复核在【当前本人主页】(nick),最多 3 次,仍不行兜底 Esc。返回是否已回主页。"""
    for i in range(3):
        if WEB_C_VIDEO_CLOSE:
            click_norm(WEB_C_VIDEO_CLOSE[0], WEB_C_VIDEO_CLOSE[1], wait=1.5, label='视频关闭X')
        elif BACK_MODE == 'close':
            pyautogui.hotkey('ctrl', 'w'); time.sleep(1.5)
        else:
            pyautogui.hotkey('alt', 'left'); time.sleep(1.5)
        try:
            on_prof, _, _ = ocr_verify(nick)
        except Exception:
            on_prof = False
        if on_prof:
            print('  [点赞] 第%d次关弹层后已回【%s】主页' % (i + 1, nick))
            return True
        pyautogui.press('esc'); time.sleep(0.8)   # 兜底:Esc 也常能关 modal
    print('  [点赞] ⚠️ 3次仍未确认回主页(没量 X 就靠 Alt+左易退错人,请 --measure 量 X)')
    return False


def like_first_work_web(nick):
    """有作品就点进第一个 → 点赞 → 退回主页(发私信前)。
    全 best-effort:未量坐标/无作品/任何失败都尽量退回主页,绝不抛异常阻断私信。
    返回 liked/no_works/no_coords/off/click_no_effect/failed。坐标: AKKE_WEB_C_WORK_FIRST / AKKE_WEB_C_LIKE。"""
    if not DO_LIKE:
        return 'off'
    if not (WEB_C_WORK_FIRST and WEB_C_LIKE):
        return 'no_coords'   # 未 --measure → 跳过点赞,不盲点 0,0
    try:
        if not _has_works_on_profile():
            return 'no_works'
        click_norm(WEB_C_WORK_FIRST[0], WEB_C_WORK_FIRST[1], wait=2.8, label='第一个作品')
        # 不按空格暂停:web 页面空格可能滚动而非暂停;视频页一般不自动跳下个用户的视频,直接点赞。
        click_norm(WEB_C_LIKE[0], WEB_C_LIKE[1], wait=1.2, label='点赞♡')
        # v1 单击不复核重点:web 视频页点赞♡的 _is_liked 复核区域还没按真实布局标定(默认沿用 PC 版
        # 右侧栏区域,web 可能不同)→ 若区域错会把"已点成功"误判未点→重点→反而取消赞。拿到视频页
        # 截图标定区域后再开"未点亮重点"。现在单击一次(陌生潜客几乎不会已赞,单击通常生效)。
        liked = _is_liked()   # 仅记录, 不据此重点
        _back_to_profile_web(nick)
        return 'liked' if liked is not False else 'click_no_effect'
    except Exception as e:
        print('  [点赞] 失败(%s) → 尝试退回主页,继续私信' % e)
        try:
            _back_to_profile_web(nick)
        except Exception:
            pass
        return 'failed'


def process_web(c, confirm=False):
    """单条网页私信流程。返回 (status, ocr_conf_or_None)。"""
    nick = c['nickname']
    sec = (c.get('_sec_uid') or '').strip()
    msg = c['message']
    print('\n=== %s (sec_uid=%s…) ===' % (nick, sec[:20]))
    if not sec:
        print('  [跳过] 缺 _sec_uid —— web 版靠 URL 直达, 没 sec_uid 不发')
        return 'no_secuid', None

    # ①+② 地址栏直达主页 + 主页身份门。
    # 【URL 通道核心差异(2026-06-17)】:sec_uid 直达 URL 已【唯一锁定是谁】→ 不需要像
    # 抖音号搜索那样靠昵称从一堆人里认本人。派单带的是抓评论时的【旧昵称】,用户改名后会
    # 昵称失配 → 误判 wrong_user(自动派单首批 wrong_user 根因)。故默认 AKKE_WEB_TRUST_SECUID=1:
    # 只要 OCR 确认【落在某个个人主页】(不是搜索页/登录墙/脏屏)就发,不强求昵称精确匹配。
    # 设 AKKE_WEB_TRUST_SECUID=0 退回严格昵称匹配(抖音号通道老行为)。
    num = (c.get('douyin_id') or '').strip()
    expnum = num if (num and num != nick and any(ch.isdigit() for ch in num)) else None
    m, conf, seen, on_prof = False, 0.0, '', False
    for attempt in range(3):
        goto_profile(sec, wait=4.5 + attempt * 2.0)
        m, conf, seen = ocr_verify(nick, expected_number=expnum)
        on_prof = not (seen.startswith('(非主页)') or seen.startswith('ocr_error'))
        print('  [OCR主页] 第%d次 match=%s conf=%.2f on_prof=%s seen=%r' % (attempt + 1, m, conf, on_prof, seen))
        c['_ocr_seen'] = seen
        if (m and conf >= MIN_CONF) or on_prof:
            break  # 昵称匹配上 / 已落到某主页(sec_uid 保证是对的人)→ 不再重试
        if attempt < 2:
            print('  [非主页重试] 页面没落成主页 → 重新导航 + 加长等待')
    if m and conf >= MIN_CONF:
        pass  # 昵称也精确匹配,最稳
    elif TRUST_SECUID and on_prof:
        print('  [信任sec_uid] 昵称没精确匹配但已落在个人主页 → URL=sec_uid 唯一锁定本人,照发(改名/旧昵称容错)')
    else:
        print('  [跳过] OCR 没确认落在个人主页(搜索页/登录墙/脏屏)→ wrong_user 不发, 回池重发')
        return 'wrong_user', conf

    # ②.5 发私信前: 关注 + (有作品则)点赞最新作品。best-effort, 失败不阻断私信(镜像 PC 版)。
    print('  [关注] %s' % follow_user_web())
    like_result = like_first_work_web(nick)
    print('  [点赞] %s' % like_result)

    # ②.6 点赞会点进视频弹层, web 上关闭弹层(关闭X/Alt+左)极不稳 → 不论关没关掉, 只要真点过作品,
    # 发私信前都用地址栏【重新直达主页】拿一张干净页(sec_uid 唯一锁人, reload 不改关注/点赞状态)。
    # 这样「关弹层失败」不再连累私信定位(dm_panel_failed 根因, 2026-06-22 城城城)。
    # off/no_coords/no_works = 没点过作品、没开弹层 → 不必重导航。
    if like_result not in ('off', 'no_coords', 'no_works'):
        print('  [重导航] 点赞后回干净主页(规避关弹层不稳)')
        goto_profile(sec, wait=4.0)

    # ③ 点私信开面板 + 面板身份门(失败重试一次)。重导航后页面是干净主页, 私信按钮位置正常。
    open_dm_panel(nick)
    panel_open, hmatch, hconf, hseen = verify_chat_panel(nick)
    if not panel_open:
        # 首次 verify 常假阴性(面板还在渲染)。先多等再复核,【别急着再点私信】——
        # 面板已开时再点「私信」在部分主页会 toggle 关掉, 弄巧成拙(2026-06-17 spike 暴露)。
        time.sleep(2.5)
        panel_open, hmatch, hconf, hseen = verify_chat_panel(nick)
    if not panel_open:
        print('  [私信] 复核仍未开 → 再点一次私信')
        open_dm_panel(nick)
        panel_open, hmatch, hconf, hseen = verify_chat_panel(nick)
    if not panel_open:
        print('  [跳过] 私信面板没打开 → dm_panel_failed(回池可重发)')
        return 'dm_panel_failed', conf
    if not hmatch and hconf >= 0.6:
        # sec_uid 直达已唯一锁人 → 面板名字门降级为软告警(治花体/emoji/长名 VL 误读)。
        # 抖音号搜索通道(无 sec)仍严格拦防发错人。气泡验证 _verify_bubble 仍是最终防线。
        if sec and TRUST_SECUID and TRUST_SECUID_ON_PANEL:
            print('  [软告警] 面板昵称未匹配 seen=%r，但 sec_uid 直达已锁人 → 照发' % hseen)
        else:
            print('  [跳过] 私信面板对话对象≠目标 seen=%r → wrong_chat 防发错人, 不发' % hseen)
            return 'wrong_chat', conf

    # ④ 发送前人工确认(spike 用 --confirm)
    if confirm:
        try:
            input('  ▶ 即将发给【%s】: %s\n    回车确认发送, Ctrl+C 中止... ' % (nick, msg[:40]))
        except EOFError:
            pass

    # ⑤ 点输入框 → 键入(SendInput unicode, 中文可靠)→ 点发送↑(固定坐标, 不用 Enter 防换行)
    click_norm(WEB_C_INPUT[0], WEB_C_INPUT[1], wait=1.0, label='输入框')
    type_text(msg)
    print('  [待发] %s' % msg[:34])
    click_norm(WEB_C_SEND[0], WEB_C_SEND[1], wait=1.5, label='发送↑')
    print('  ✅ 已点发送')

    # ⑥ 送达核对(复用 PC 版): 负向拒收提示 + 正向气泡含本文案。挡不住 7911 静默限频。
    # _check_rejected 返回 4 元组 (rejected, modal_type, reason, sample)——必须拆包，
    # 否则「非空元组恒为真」→ 每条 web 发送都被误判 rejected(PC 路径已拆、web 曾漏拆)。
    rejected, post_modal_type, _modal_reason, _post_sample = _check_rejected()
    verified = False if (rejected or post_modal_type) else _verify_bubble(msg)
    if post_modal_type:
        print('  ⚠️  [post-send modal] type=%s → blocked_%s(账号已风控)'
              % (post_modal_type, post_modal_type))
        return 'blocked_%s' % post_modal_type, conf
    if rejected:
        print('  [拒绝] 检测到对方拒收/无法送达 → rejected(不计成功)')
        return 'rejected', conf
    if not verified:
        return 'unverified', conf
    print('  ✅ 已确认气泡发出')
    return 'sent', conf


def main_web(contacts_csv, max_n=None, confirm=False):
    with open(contacts_csv, encoding='utf-8-sig') as f:
        contacts = list(csv.DictReader(f))
    if not contacts:
        print('名单为空, 退出')
        return
    cap = min(DAILY_LIMIT, max_n) if max_n else DAILY_LIMIT
    if len(contacts) > cap:
        print('⚠️ 名单 %d > 上限 %d, 截断' % (len(contacts), cap))
        contacts = contacts[:cap]

    print('=== douyin_dm_WEB_grounded  model=%s  conf>=%s  DPI_AWARE=%s ===' % (MODEL, MIN_CONF, DPI_AWARE))
    print('共 %d 条; 前提: ①Edge 登录抖音网页版+最大化 ②输入法【英文】 ③私信前确认 URL 能直达主页' % len(contacts))
    if confirm:
        print('   [spike] --confirm: 每条发送前等你回车确认。')

    log = 'sent_log_%s.csv' % datetime.now().strftime('%Y%m%d')
    fields = ['douyin_id', 'nickname', 'message', 'has_works',
              '_comment_id', '_sec_uid', '_dispatch_id',
              'status', 'sent_at', '_ocr_confidence', '_ocr_seen']
    first_write = not os.path.exists(log)
    with open(log, 'a', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        if first_write:
            w.writeheader()
        for i, c in enumerate(contacts, 1):
            print('\n========== [%d/%d] ==========' % (i, len(contacts)))
            ocr_conf = None
            try:
                status, ocr_conf = process_web(c, confirm=confirm)
            except KeyboardInterrupt:
                print('\n  ⏹ 用户中止')
                status = 'cancelled'
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


def measure_web():
    """量 web 布局的【第一个作品 / 点赞♡ / 视频弹层关闭X】归一化坐标写 .env(关注走视觉,不用量)。
    步骤:依次把鼠标悬停到元素上按回车采集。点赞♡和关闭X 要先【手动点开一个作品进视频弹层】再量。"""
    focus_douyin()
    time.sleep(0.8)
    W, H = pyautogui.size()
    print('=== web 坐标测量  分辨率 %dx%d  DPI_AWARE=%s ===' % (W, H, DPI_AWARE))
    print('提示:① 停在【有作品的用户主页】量作品封面;')
    print('     ② 【手动点开第一个作品进视频弹层】量点赞♡;')
    print('     ③ 弹层不要关,接着量右上角【关闭X】(关弹层回主页用,Alt+左会退错人)。\n')
    updates = {}
    x, y = _countdown('用户主页【作品宫格 第一个作品 封面中心】')
    updates['AKKE_WEB_C_WORK_FIRST'] = '%d,%d' % (round(x / W * 1000), round(y / H * 1000))
    print('   ✓ 作品@(%d,%d) 归一化=%s\n' % (x, y, updates['AKKE_WEB_C_WORK_FIRST']))
    x, y = _countdown('视频弹层【点赞♡ 心形图标 中心】')
    updates['AKKE_WEB_C_LIKE'] = '%d,%d' % (round(x / W * 1000), round(y / H * 1000))
    print('   ✓ 点赞@(%d,%d) 归一化=%s\n' % (x, y, updates['AKKE_WEB_C_LIKE']))
    x, y = _countdown('视频弹层【右上角 关闭X 按钮 中心】')
    updates['AKKE_WEB_C_VIDEO_CLOSE'] = '%d,%d' % (round(x / W * 1000), round(y / H * 1000))
    print('   ✓ 关闭X@(%d,%d) 归一化=%s\n' % (x, y, updates['AKKE_WEB_C_VIDEO_CLOSE']))
    _write_env_kv(updates)
    print('✅ 已写入 .env:%s' % updates)
    print('   下次发送自动启用「关注+点赞+点完关弹层」。')
    return 0


if __name__ == '__main__':
    _set_dpi_aware()
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--measure' in flags:
        if not KEY:
            print('❌ 缺 ANTHROPIC_API_KEY / OPENROUTER_API_KEY', file=sys.stderr)
            sys.exit(2)
        sys.exit(measure_web())
    if '--calibrate' in flags:
        # 复用 PC 版校准(自动量输入框/发送↑写 .env)。前提: Edge 里【已手动打开一个私信浮层】。
        print('网页版校准: 请先在 Edge 里手动点开任意一个私信聊天浮层(右侧能看到输入框+发送↑)再继续。')
        sys.exit(calibrate())
    if not args:
        print(__doc__)
        sys.exit(2)
    if not KEY:
        print('❌ 缺 ANTHROPIC_API_KEY / OPENROUTER_API_KEY', file=sys.stderr)
        sys.exit(2)
    max_n = None
    for fl in flags:
        if fl.startswith('--max='):
            try:
                max_n = int(fl.split('=', 1)[1])
            except Exception:
                pass
    main_web(args[0], max_n=max_n, confirm=('--confirm' in flags))
