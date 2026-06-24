"""
抖音PC批量【RC 反向评论 · 回复用户的意向评论】— Qwen grounded + 实测固定坐标。
2026-06-06 PM 拍板术语后立项（PoC 阶段）：RC = 在用户那条高/中意向评论下回复（源视频评论楼），
不是评对方自己视频（那是二次触达，走 douyin_comment_grounded.py）。

对象 = 从未触达的高/中意向新 leads（与 DM 共池互斥），上游 CSV 由
scripts/dm-phone-sender/_pull-rc-reply-poc-*.py 出（含源视频标题/作者/评论原文三列定位用）。

完整路径（与搜人流不同，这里是【搜视频】流）：
  搜索框输入源视频标题 → 点放大镜(回车不触发) → 点「视频」tab → VL 定位目标视频卡片(标题+作者双因子) →
  点开 → VL 核作者名(防同名错视频) → 空格暂停 → 确保评论面板 → 评论楼里 VL 定位用户那条评论(昵称+内容双因子,
  最多滚 N 屏) → 点该评论下「回复」 → 剪贴板粘贴(v7) → 正向核框内有字 → VL 定位「发送」→ 复核 → 返回。

v7 破法（2026-06-06 下午）：回复框是 webview contenteditable，SendInput 直键吃不进（v1-v6 全栽，
同一 type_text 打私信框 OK）。改 paste-first：pyperclip.copy + Ctrl+V，contenteditable 普遍认 paste 事件。
保留 v5 的正向校验（先确认框里真有字才发）+ 发后清空复核，三重防假 sent。

v8 真相修正（2026-06-06 手工实验定案）：粘贴和直键【人工操作都能进框】——v1-v7 的"打字进不去"
是全屏截图喂 Qwen VL 的假阴性（DM 流 5/31 同坑，见 douyin_dm_grounded._shot_crop 注释）。
v8 把框内文字判定/发后残留判定改用 C_COMMENT 锚定的裁剪放大图（vl_yesno_box）。输入保持 v7 粘贴优先。

v10 双道发送复核：占位态判定 fail 后再到评论楼里找刚发的回复气泡（看结果不看输入框），
两道都 fail 才记 send_fail——真发成功被误记 fail（倪敏案）的窗口进一步压缩；方向不变：歧义只会多报 fail，绝不假报 sent。

I/O:
  contacts-rc-reply.csv 列: douyin_id(=用户昵称) nickname message(=回复文案) has_works _comment_id _sec_uid
                            _video_title _video_author _comment_text
  rc_reply_log_YYYYMMDD.csv 列: 上述 + status sent_at note
  status: sent / video_not_found / wrong_video / comment_not_found / wrong_floor / panel_fail /
          focus_fail / cancelled / aborted / error:<msg>
          (wrong_floor = 错楼铁门：回复框占位「回复 @昵称」与目标不一致，拒发)

⚠️ 公开外发 + 回复在别人(源账号)的视频评论区，比二触更显眼。PoC 期【必须 --confirm 逐条 y/n】。
前置同 DM: ①抖音PC前台最大化 ②分辨率锁定 ③输入法英文 ④.env 有 OCR key ⑤跑批别碰鼠标键盘。

用法:
  python douyin_rc_reply_grounded.py contacts-rc-reply.csv --confirm   # PoC 必须带 --confirm
  python douyin_rc_reply_grounded.py contacts-rc-reply.csv             # 投产后才允许自动发
"""
from __future__ import annotations

import base64
import csv
import ctypes
import json
import os
import random
import re
import sys
import time
from datetime import datetime

import pyautogui
import pyperclip

import douyin_dm_grounded as dm


def _paste_text(text):
    """v7 破法：剪贴板粘贴进回复框（contenteditable 认 paste 事件，不认 SendInput 直键）。
    copy 后回读校验一次（无影客户端剪贴板同步可能干扰本机剪贴板），再 Ctrl+V。
    调用前提：目标输入框已聚焦（点「回复」自带聚焦，勿重复点框/ensure_front——v5 教训）。"""
    pyperclip.copy(text)
    time.sleep(0.4)
    try:
        if pyperclip.paste() != text:
            print('  [paste] 剪贴板回读不一致（无影同步干扰？）→ 重写一次')
            pyperclip.copy(text)
            time.sleep(0.6)
    except Exception as e:
        print('  [paste] 回读失败(%s)，直接试粘贴' % e)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.9)

# ── 固定坐标（归一化 0~1000，env 覆盖；缺省 None → VL 定位）────────────────
C_VIDEO_TAB = dm._coord('AKKE_C_VIDEO_TAB', None)       # 搜索结果页「视频」tab
C_BACK = dm._coord('AKKE_C_BACK', None)                 # 视频页左上角返回
# 底部评论/回复框 + 发送键：复用二次触达 douyin_comment_grounded 实测坐标@2560×1600。
# 抖音PC 点评论「回复」后激活的是【底部这个框】（进入"回复 @x"态），不是内联框。
C_COMMENT = dm._coord('AKKE_C_COMMENT', (798, 949))           # 底部评论/回复输入框中心
C_COMMENT_SEND = dm._coord('AKKE_C_COMMENT_SEND', (984, 950)) # 发送键兜底(打字后才出现)

MIN_INTERVAL = int(os.environ.get('AKKE_RC_MIN_INTERVAL', '60'))
MAX_INTERVAL = int(os.environ.get('AKKE_RC_MAX_INTERVAL', '90'))
DAILY_LIMIT = int(os.environ.get('AKKE_RC_DAILY_LIMIT', '15'))
MAX_SCROLLS = int(os.environ.get('AKKE_RC_MAX_SCROLLS', '6'))   # 评论楼最多滚屏次数


# ── 焦点加固（拷自 douyin_comment_grounded，保持本脚本自含，不依赖二触脚本版本）──

def foreground_title():
    try:
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        return buf.value or '(无标题)'
    except Exception as e:
        return '(取标题失败:%s)' % e


def force_foreground_douyin():
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        w = dm._best_douyin_window()
        if w is None or not getattr(w, '_hWnd', None):
            return False
        hwnd = w._hWnd
        fg = u.GetForegroundWindow()
        cur_tid = k.GetCurrentThreadId()
        fg_tid = u.GetWindowThreadProcessId(fg, 0)
        u.AttachThreadInput(cur_tid, fg_tid, True)
        u.ShowWindow(hwnd, 3)        # SW_MAXIMIZE（勿用 SW_RESTORE）
        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)
        u.AttachThreadInput(cur_tid, fg_tid, False)
        time.sleep(0.6)
        try:
            w.maximize()
        except Exception:
            pass
        time.sleep(0.4)
    except Exception as e:
        print('  [force] 失败: %s' % e)
    t = foreground_title()
    return ('抖音' in t) or ('douyin' in t.lower())


def ensure_douyin_front(tries=3):
    for _ in range(tries):
        dm.focus_douyin()
        if force_foreground_douyin():
            return True
        time.sleep(0.6)
    print('  [警告] 重夺焦点 %d 次仍非抖音(=%r) → 跳过本条' % (tries, foreground_title()))
    return False


# ── VL 判定小工具 ────────────────────────────────────────────────────

def vl_yesno(question):
    """整屏截图问 VL 一个是/否问题。返回 bool（解析失败按 False 保守处理）。
    注意 dm._shot(name) 要带扩展名、返回 (path, size)；需自行读文件 b64 编码喂 _vision。"""
    try:
        path, _ = dm._shot('_yesno.png')
        b64 = base64.b64encode(open(path, 'rb').read()).decode()
        txt = dm._vision(b64, question + ' 只回严格JSON：{"found": true/false}')
        j = dm._pjson(txt)
        return bool(j and j.get('found'))
    except Exception as e:
        print('  [vl_yesno] %s' % e)
        return False


def _shot_crop_reply(name, pad_x=200, pad_y=90):
    """截全屏后裁剪到【回复输入框+发送键】附近（锚定 C_COMMENT/C_COMMENT_SEND）。
    全屏小字 Qwen VL 易假阴性——DM 流 2026-05-31 实测 7 条里 5 条已进框被判"没进"(见
    douyin_dm_grounded._shot_crop)；RC 流 v1-v7 的"打字进不去"同根，2026-06-06 手工实验
    证伪：粘贴/直键人工操作都能进框，是全屏 VL 校验在说谎。"""
    os.makedirs('screenshots', exist_ok=True)
    path = os.path.join('screenshots', name)
    img = pyautogui.screenshot()
    W, H = img.size
    fxs = (C_COMMENT[0], C_COMMENT_SEND[0])
    fys = (C_COMMENT[1], C_COMMENT_SEND[1])
    x0 = max(0, int((min(fxs) - pad_x) / 1000 * W))
    x1 = min(W, int((max(fxs) + pad_x) / 1000 * W))
    y0 = max(0, int((min(fys) - pad_y) / 1000 * H))
    y1 = min(H, int((max(fys) + pad_y) / 1000 * H))
    img.crop((x0, y0, x1, y1)).save(path)
    return path


def vl_yesno_box(question):
    """对【回复框区域裁剪放大图】问是/否（v8：替代全屏 vl_yesno 做框内文字判定）。"""
    try:
        path = _shot_crop_reply('_yesno_box.png')
        b64 = base64.b64encode(open(path, 'rb').read()).decode()
        txt = dm._vision(b64, question + ' 只回严格JSON：{"found": true/false}')
        j = dm._pjson(txt)
        return bool(j and j.get('found'))
    except Exception as e:
        print('  [vl_yesno_box] %s' % e)
        return False


def verify_reply_target(nick):
    """错楼铁门（2026-06-10 试点错发后加）：点「回复」后、打字前，读输入框灰色占位
    「回复 @昵称：」并与目标昵称在【代码里】精确比对——VL 只抄字不判断（照 dm.ocr_verify
    抖音号核身的稳法）。返回 (ok, seen)。
    防误杀（v8-v11 假阴性教训）：只在【明确读到占位且明确不一致】时拦；读不到/异常不拦，
    放行交给发后双复核。"""
    try:
        path = _shot_crop_reply('_target.png')
        b64 = base64.b64encode(open(path, 'rb').read()).decode()
        txt = dm._vision(b64, '这张图是抖音评论回复输入框附近的局部截图。输入框里若有灰色占位提示'
                              '（形如「回复 @某某：」或「回复 @某某」），请原样抄出@后面的昵称；'
                              '没有这种占位提示就填空串。只回严格JSON：{"reply_to":"昵称或空串"}')
        j = dm._pjson(txt)
        seen = str((j or {}).get('reply_to', '')).strip().rstrip(':：')
    except Exception as e:
        print('  [错楼门] 读取异常(%s) → 不拦' % e)
        return True, '(error)'
    if not seen:
        print('  [错楼门] 没读到「回复 @…」占位 → 不拦（UI 变体/已进字）')
        return True, '(no_placeholder)'
    a = re.sub(r'\s+', '', seen)
    b = re.sub(r'\s+', '', nick or '')
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    # 占位里的昵称可能被截断/丢 emoji：较短串是较长串的子串/前 6 字前缀一致即算同人
    ok = bool(short) and (short in long_ or (len(short) >= 6 and long_.startswith(short[:6])))
    print('  [错楼门] 占位昵称=%r 目标=%r → %s' % (seen, nick, 'PASS' if ok else '⛔ WRONG_FLOOR'))
    return ok, seen


def clean_title(t):
    """搜索词清洗：去 #话题 和 @提及，截前 22 字（太长搜索召回差）。"""
    t = re.sub(r'[#@][^\s#@]+', ' ', t or '')
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:22]


# ── 各步骤 ──────────────────────────────────────────────────────────

def _clear_search_box():
    """清空搜索框已有内容（避免上一条残留拼接）。"""
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.2)
    pyautogui.press('delete'); time.sleep(0.2)


def open_video_by_url(video_url):
    """【路径1·ID直达】把 video_url 粘进搜索框，VL 判定是否直接进了单条视频播放页。
    全自动，不需人工。返回 True=直达成功 / False=没直达(调用方自动降级路径2)。"""
    if not video_url:
        return False
    ensure_douyin_front()
    dm.click_norm(dm.C_SEARCH[0], dm.C_SEARCH[1], wait=1.0, label='搜索框')
    _clear_search_box()
    dm.type_text(video_url)
    dm.click_norm(dm.C_SEARCH_BTN[0], dm.C_SEARCH_BTN[1], wait=3.0, label='放大镜')
    return vl_yesno('当前是否【直接打开了一个单条视频的播放页】'
                    '（屏幕中央有一个正在播放/暂停的大视频，而不是一排搜索结果卡片列表）？')


def open_video_via_source(author, title):
    """【路径2·源账号主页】搜源账号名(干净,不带#话题) → 用户tab → 进主页 → 作品墙 VL 认目标视频。
    复用今天验证过的搜人导航。返回 'ok' / 'wrong_video' / 'video_not_found'。"""
    if not author:
        return 'video_not_found'
    ensure_douyin_front()
    dm.click_norm(dm.C_SEARCH[0], dm.C_SEARCH[1], wait=1.0, label='搜索框')
    _clear_search_box()
    dm.type_text(author)
    pyautogui.press('enter')
    time.sleep(2.5)
    dm.click_norm(dm.C_USERTAB[0], dm.C_USERTAB[1], wait=2.0, label='用户tab')
    dm.click_norm(dm.C_FIRST[0], dm.C_FIRST[1], wait=2.5, label='第一条结果(源账号)头像')
    # 软核对进对了源账号主页（防同名账号）。
    # v11(2026-06-08 实锤)：vl_yesno 用整屏图判顶部小字账号名是已知假阴性源——用户肉眼证脚本
    # 进对了「西安老王聊装修」本人主页，VL 仍判 false → 误杀 6 条 wrong_video（同 box 验字病根）。
    # 不再因这道核对硬 return；真正把关交给下面【作品墙找目标视频标题】：进对主页→找得到目标
    # 标题视频→ok；进错主页→找不到→video_not_found。同名风险由"标题必须命中"兜住。
    if not vl_yesno('当前抖音个人主页顶部的账号名是不是「%s」？'
                    '（允许 emoji/认证标/副标题/标点/空格差异，核心字符一致就算是）' % author[:14]):
        print('  [路径2] VL 核主页名未过（整屏图易假阴性）→ 不硬拦，靠作品墙找目标视频把关')
    # 作品墙 VL 定位目标视频（滚屏找）
    q = clean_title(title)
    _W, _H = pyautogui.size()
    for i in range(MAX_SCROLLS + 1):
        pt = dm.locate('这个人主页【作品】宫格里，标题或封面文字包含「%s」的那个视频缩略图中心'
                       % q[:14], region=(0.0, 0.15, 1.0, 1.0))
        if pt:
            print('  [路径2] 作品墙第%d屏找到目标视频' % (i + 1))
            pyautogui.click(pt[0], pt[1]); time.sleep(3.0)
            return 'ok'
        if i < MAX_SCROLLS:
            pyautogui.moveTo(int(_W * 0.5), int(_H * 0.5))
            pyautogui.scroll(-600); time.sleep(1.5)
    return 'video_not_found'


def search_video(title, author, video_url):
    """两路全自动定位源视频：先试 ID/URL 直达，不行自动降级搜源账号主页。
    返回 'ok' / 'video_not_found' / 'wrong_video'。"""
    # 路径1：URL 直达
    if open_video_by_url(video_url):
        if not author or vl_yesno('这个视频播放页上能看到作者名「%s」吗？' % author[:14]):
            print('  [定位] ✓ URL 直达成功（ID 定位可用）')
            return 'ok'
        print('  [定位] URL 直达打开的不是目标作者 → 降级路径2')
    else:
        print('  [定位] URL 不能直达（PC端不支持搜索框打开链接）→ 降级路径2 搜源账号')
    # 路径2：源账号主页
    return open_video_via_source(author, title)


def ensure_comment_panel():
    pt = dm.locate('抖音视频播放页【右侧面板顶部】的「评论」分类标签（详情/TA的作品/评论 里的评论）')
    if pt:
        pyautogui.click(pt[0], pt[1])
        time.sleep(1.5)
        return True
    print('  [警告] 没定位到「评论」tab，按面板已在评论态继续试')
    return False


def find_user_comment(nick, comment_text):
    """评论楼里找该用户那条评论（昵称+内容双因子），最多滚 MAX_SCROLLS 屏。返回坐标或 None。"""
    snippet = (comment_text or '')[:10]
    _W, _H = pyautogui.size()
    for i in range(MAX_SCROLLS + 1):
        pt = dm.locate('右侧评论列表里【昵称为「%s」】发的那条评论的文本中心（内容大致是"%s…"，以昵称匹配为主）'
                       % (nick[:14], snippet), region=(0.55, 0.1, 1.0, 0.95))
        if pt:
            print('  [定位] 第%d屏找到评论 -> (%d,%d)' % (i + 1, pt[0], pt[1]))
            return pt
        if i < MAX_SCROLLS:
            pyautogui.moveTo(int(_W * 0.78), int(_H * 0.5))
            pyautogui.scroll(-600)
            time.sleep(1.5)
    return None


def reply_to_comment(pt, nick, text):
    """悬停评论 → 点「回复」(激活底部输入框进入"回复@x"态) → 点底部框聚焦 → 打字 → 发送。
    返回 'sent'/'focus_fail'/'comment_not_found'/'blocked_<type>'。"""
    # Pre-action 风控弹窗预检（任务 3 modal 检测扩展 PR）：评论面板已经打开，
    # 此时如果有 SMS/滑块/人脸弹窗就别再操作，否则会推高风控等级。
    # 复用 douyin_dm_grounded 里的 helper（同款 prompt + VL 调用）。
    _mt, _mtext = dm._check_pre_action_modal('_rc_pre_modal.png')
    if _mt:
        return 'blocked_%s' % _mt

    pyautogui.moveTo(pt[0], pt[1])   # 悬停让「回复」按钮浮现
    time.sleep(1.0)
    rp = dm.locate('评论「%s」附近的「回复」小按钮（悬停评论时出现的灰色"回复"二字）' % nick[:12],
                   region=(0.55, 0.1, 1.0, 0.95))
    if rp:
        pyautogui.click(rp[0], rp[1])
    else:
        # 兜底：点评论文本本身（部分版本点正文即进回复态）
        print('  [回复] VL 没定位到「回复」→ 兜底点评论正文')
        pyautogui.click(pt[0], pt[1])
    # v6 教训保留：点「回复」后底部回复框【已自动聚焦】，不 ensure_front、不重复点框。
    # v7 破法：SendInput 直键进不去 webview contenteditable（v1-v6 实锤）→ 改剪贴板粘贴。
    time.sleep(1.8)
    # 错楼铁门（2026-06-10）：打字前核占位「回复 @昵称」。VL 定位 find_user_comment 错楼时
    # （试点实锤：回复挂到他人评论下），这里是最后一道不依赖楼层定位的防线。
    ok, _seen = verify_reply_target(nick)
    if not ok:
        pyautogui.press('esc'); time.sleep(0.8)   # 退出回复态，不留半截输入
        return 'wrong_floor'
    # v8：框内文字判定改用【裁剪放大图】——全屏图 VL 假阴性是 v1-v7 误判"打字进不去"的根因
    box_has_text = lambda: vl_yesno_box(
        '这张图是抖音评论回复输入框附近的局部截图。输入框里是否有一段【已经打进去的回复文字】'
        '（不是"回复@xxx"这种灰色占位提示，而是真实可见的中文句子）？')
    _paste_text(text)
    # ① box_has_text 只当【要不要补粘】的提示，不再当【发不发】的门。
    # v11 根因（2026-06-08 实锤）：box_has_text 是已知假阴性源（VL 看裁剪图小字常误判"没字"，
    # v1-v7 烧 7 版的坑）。手动地面实验证明「点回复→Ctrl+V→回车」机制全通；DM 成功流程(type_text)
    # 打完字也【不做注入后 VL 验字】直接发。今天 6/14 type_fail 全是这道预检假阴性误杀（字其实进了/
    # 发了却被 return type_fail 没走发送）。改法：补粘提高真实进框率，但【绝不因 box 判定 return】——
    # 无条件走发送，交发后双复核（占位态恢复 / 评论楼气泡）当唯一裁决：真发→sent，真没进→send_fail。
    if not box_has_text():
        print('  [补粘] box 判没字（多半 VL 假阴性）→ 点框补焦点再粘一次')
        dm.click_norm(C_COMMENT[0], C_COMMENT[1], wait=1.0, label='回复框补点聚焦')
        _paste_text(text)
        if not box_has_text():
            print('  [补粘2] box 仍判没字 → SendInput 直键再补一次（不早退）')
            dm.type_text(text)
            time.sleep(0.8)
            if not box_has_text():
                print('  [v11] box×3 判没字但不早退 → 照常走发送，交发后双复核裁决')
    # ② 发送：先 VL 找红色「发送」（实测截图：发送键在输入框最右、@😊🖼 图标更右侧或下方，
    #    文字够长才出现），找不到就【按回车】发送（抖音PC回复框回车=发布；固定坐标会点到图标排，已弃用）
    sp = dm.locate('抖音右侧评论面板底部回复框最右侧的红色"发送"按钮（红底白字"发送"二字，不是@/表情/图片图标）',
                   region=(0.82, 0.9, 1.0, 1.0))
    if sp:
        print('  [发送] VL 定位 -> (%d,%d)' % sp)
        pyautogui.click(sp[0], sp[1]); time.sleep(2.0)
    else:
        print('  [发送] VL 没找到发送键 → 按回车发送')
        pyautogui.press('enter'); time.sleep(2.0)
    # ③ 复核（v9）：问"清空"改问"恢复占位态"。v8 问"是否残留文字"假阳性——回车后 2s 截图里
    #    拍到刚发出去的回复气泡/UI 残影也被算"有字"（2026-06-06 实测：倪敏已真发成功仍被判 send_fail）。
    #    正向问法：框回到灰色占位提示 = 真发出去了。多等 1.5s 让气泡动画走完再拍。
    time.sleep(1.5)
    # Post-send 风控弹窗检测（任务 3 modal 检测扩展 PR）：发完出现 SMS/滑块/人脸 =
    # 明确账号被风控信号，必须立即停号。优先级高于"框态恢复"复核。
    _mt_post, _mtext_post = dm._check_pre_action_modal('_rc_post_modal.png')
    if _mt_post:
        return 'blocked_%s' % _mt_post
    if vl_yesno_box('这张图是抖音评论回复输入框附近的局部截图。【最底部的那个输入框本身】是否已恢复为'
                    '空框/灰色占位提示（如"回复 @xxx"或"留下你的精彩评论"），即用户输入的文字已不在输入框里？'):
        return 'sent'
    # v10 二道复核：框态判定 fail 时直接看【结果】——评论楼里找刚发出的回复气泡。
    # 倪敏案（v8）：真发成功但框态被误判 fail，气泡复核能自动捞回；两道都 fail 才记 send_fail。
    # 用 dm.locate 带 region（评论面板）走裁剪路径，不吃全屏小字假阴性。
    print('  [复核] 框未恢复占位态 → 二道复核：评论楼里找刚发的回复气泡')
    snippet = (text or '')[:12]
    bp = None
    try:
        bp = dm.locate('右侧评论列表里「%s」那条评论的下方回复区中，内容以「%s…」开头的那条【新发出的回复】文本中心'
                       % (nick[:14], snippet), region=(0.55, 0.1, 1.0, 0.95))
    except Exception as e:
        print('  [复核] 气泡定位异常(%s)' % e)
    if bp:
        print('  [复核] 气泡已出现在评论楼 → 实际已发出，按 sent 记')
        return 'sent'
    print('  [复核] 框未清空且评论楼没看到气泡 → 按未发出处理（人工核对气泡，假阴性时日志可改）')
    return 'send_fail'


def reset_back():
    """退出视频页回到可搜索状态（点左上角返回，兜底 Esc）。"""
    for _ in range(3):
        if C_BACK:
            dm.click_norm(C_BACK[0], C_BACK[1], wait=1.2, label='返回(固定坐标)')
        else:
            bp = None
            try:
                bp = dm.locate('抖音视频播放页【左上角】的返回按钮(向左箭头)', region=(0.0, 0.0, 0.3, 0.22))
            except Exception:
                pass
            if bp:
                pyautogui.click(bp[0], bp[1]); time.sleep(1.2)
            else:
                pyautogui.press('esc'); time.sleep(1.0)
        if not vl_yesno('当前是否还在某个视频的播放页（画面中央有正在播放/暂停的视频）？'):
            return True
    print('  [复位] ⚠️ 3次仍在视频页，下一条搜索可能落空，留意日志')
    return False


def process(c, auto):
    nick = c['nickname']
    text = c['message']
    title = c.get('_video_title') or ''
    author = c.get('_video_author') or ''
    video_url = c.get('_video_url') or ''
    print('\n=== %s ===' % nick)
    print('  源视频: %s @%s' % (title[:30], author))
    print('  视频URL: %s' % video_url)
    print('  回复文案: %s' % text)

    st = search_video(title, author, video_url)
    if st != 'ok':
        print('  [跳过] 搜视频: %s' % st)
        return st
    pyautogui.press('space'); time.sleep(0.8)   # 暂停，防自动跳下一条评错楼
    ensure_comment_panel()
    pt = find_user_comment(nick, c.get('_comment_text') or '')
    if not pt:
        print('  [跳过] 评论楼 %d 屏内没找到该用户评论' % (MAX_SCROLLS + 1))
        reset_back()
        return 'comment_not_found'
    if not auto:
        ans = input('  >>> 确认在「%s」的评论下回复？(y/n) ' % nick).strip().lower()
        if ans != 'y':
            reset_back()
            return 'cancelled'
    st = reply_to_comment(pt, nick, text)
    reset_back()
    return st


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    auto = '--confirm' not in sys.argv[2:]
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    print('共 %d 条 | %s | 日上限 %d' % (len(rows), '自动发' if auto else '逐条确认(--confirm)', DAILY_LIMIT))
    log_path = 'rc_reply_log_%s.csv' % datetime.now().strftime('%Y%m%d')
    new_log = not os.path.exists(log_path)
    sent = 0
    with open(log_path, 'a', newline='', encoding='utf-8-sig') as lf:
        w = csv.writer(lf, lineterminator='\n')
        if new_log:
            w.writerow(list(rows[0].keys()) + ['status', 'sent_at', 'note'])
        for i, c in enumerate(rows):
            if sent >= DAILY_LIMIT:
                print('到日上限 %d，停' % DAILY_LIMIT)
                break
            try:
                st = process(c, auto)
            except KeyboardInterrupt:
                w.writerow(list(c.values()) + ['aborted', datetime.now().isoformat(), ''])
                print('\n手动中止')
                break
            except Exception as e:
                st = 'error:%s' % str(e)[:60]
                try:
                    reset_back()
                except Exception:
                    pass
            w.writerow(list(c.values()) + [st, datetime.now().isoformat(), ''])
            lf.flush()
            print('  [结果] %s' % st)
            if st == 'sent':
                sent += 1
            if i < len(rows) - 1:
                pause = random.randint(MIN_INTERVAL, MAX_INTERVAL)
                print('  …等 %ds' % pause)
                time.sleep(pause)
    print('\n完成：sent=%d / 总 %d，日志 %s' % (sent, len(rows), log_path))
    print('⚠️ 以抖音评论区实际气泡为准核对；comment_log 回传本机走 import-comment-log.ts 落库')


if __name__ == '__main__':
    main()
