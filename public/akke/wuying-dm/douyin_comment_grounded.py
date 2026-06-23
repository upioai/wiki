"""
抖音PC批量【二次触达·公开评论】— Qwen grounded + 实测固定坐标。2026-06-03 spike 验证云电脑能发评论后落地。
对象 = 已 DM 过、≥3 天没回的高/中意向潜在用户（私信没回 → 评论区再勾一次），不是给陌生新人发首评。
上游 CSV 由 gen-second-touch-comments.py --csv 出（接 second-touch-worklist 名单 + qwen3 二触话术）。

复用 douyin_dm_grounded 的导航/身份门/SendInput/坐标积木（同目录 import）。
潜在触达【三连】(2026-06-11 spec)：点赞 + 评论1(评视频) + 评论2(顺到产品)。与发私信【同机同号】可交替跑。

完整路径：
  搜人(固定坐标导航) → OCR身份门(防同名错评) → 点开第一条视频(VL/固定坐标) → 暂停 →
  【点赞】(空心爱心 VL/固定坐标，先赞后评) → 点「评论」tab确保评论面板 →
  评论1: 点评论框→打字前【硬拉抖音前台】→SendInput→点发送 →
  评论2: (有 message2 才发，间隔 4-8s) 同上再发一条顺产品 → 点返回回主页 → 下一条。

I/O 与 douyin_dm_grounded 同源（同一套备菜/日志工具）。**评论2 可选**：CSV 有 message2 列且非空才发第二条，
缺列/空值则只发评论1（向后兼容老 CSV）。
  contacts.csv 列: douyin_id(=昵称搜索词) nickname message(=评论1) message2(=评论2,可空) has_works _comment_id _sec_uid _dispatch_id
  comment_log_YYYYMMDD.csv 列: 上述 + status(评论1) liked status2(评论2) sent_at _ocr_confidence
                              + like_at comment1_at comment2_at(各步【完成那刻】ISO 时间戳,早返回的步留空;
                                second_touch_log 完整时间线用——消费端/批量回写读这三列)
  status/status2: sent(=真发验证闸过:评论已现身评论区+输入框清空) / send_unverified(点了发送但没验到出现→不计成功) /
                  wrong_user / no_works / focus_fail（前台抢不回抖音）/ cancelled / no_msg2(无第二条) / skip_c1_fail(评论1未验证发出故不补第二条) / error:<msg>
  liked: liked(=验证爱心已红) / like_unverified(点了但爱心没变红→不计) / like_skip(没定位到空心爱心,可能已赞) / like_focus_fail / n/a(没走到视频页)
  ⚠️ 入库铁律(2026-06-13):mark-second-touch 只认 status=='sent'。send_unverified/like_unverified 一律不当真发、不标 touched。

固定坐标（归一化 0~1000，env 覆盖），2026-06-03 实测 @2560×1600（用 _measure-comment.py 量）：
  AKKE_C_COMMENT=798,949        评论输入框中心
  AKKE_C_COMMENT_SEND=984,950   发送键中心（打字后才出现）
  AKKE_C_WORK_FIRST（可选）      主页作品宫格第一格；缺则 VL 定位
  AKKE_C_COMMENT_TAB（可选）     视频页右侧「评论」tab；缺则 VL 定位

⚠️ 公开外发：评论对所有人可见，比私信更需谨慎。默认【自动发】(OCR门通过即发)，--confirm 才逐条 y/n。
   身份门(OCR)拦同名错评。**发完务必以抖音评论区实际气泡为准核对**（延续 DM「以列表为准」铁律）。
   收尾/去重：发完把 comment_log 拷回本机跑 scripts/mark-second-touch.ts --from-comment-log 写回 second_touch_state（二次触达节奏阶梯，控制隔多久能再触一次）。

用法:
  python douyin_comment_grounded.py contacts.csv            # 默认【自动发】，OCR门通过即发，不打断
  python douyin_comment_grounded.py contacts.csv --confirm  # 逐条 y/n 确认(想人工把关时用)
前置(同发私信): ①抖音PC前台最大化 ②无影分辨率锁 2560×1600 ③输入法【英文模式】 ④.env 有 OCR key
⚠️ 跑批期间【别碰鼠标和键盘】——pyautogui 全程在控鼠标搜人/点击/打字，人手一动就抢焦点、点歪、字漏进终端。挂着让它自己跑完。
"""
from __future__ import annotations

import csv
import ctypes
import os
import random
import re
import sys
import time
from datetime import datetime

import pyautogui

import douyin_dm_grounded as dm
import wuying_window_lock as _wl  # 单窗口·DM优先串行锁(AKKE_WINDOW_LOCK=1 才生效，否则 no-op)

# 评论专属固定坐标（导航坐标复用 dm 模块的 C_SEARCH/C_USERTAB/C_FIRST）。
# 注意：本脚本【不】用 dm.C_WORK_FIRST —— 无影上的 douyin_dm_grounded 可能是旧版（6/1）没这属性，
# 自己定义同名 env，缺省 None → VL 定位首格视频。
C_COMMENT = dm._coord('AKKE_C_COMMENT', (798, 949))          # 评论输入框 实测 px(2044,1518)
C_COMMENT_SEND = dm._coord('AKKE_C_COMMENT_SEND', (984, 950)) # 发送键 实测 px(2519,1520)，打字后才出现
C_COMMENT_TAB = dm._coord('AKKE_C_COMMENT_TAB', None)        # 视频页右侧「评论」tab；None→VL定位
C_WORK_FIRST = dm._coord('AKKE_C_WORK_FIRST', None)          # 主页作品宫格第一格；None→VL定位
C_BACK = dm._coord('AKKE_C_BACK', None)                      # 视频页左上角「返回」按钮；None→VL定位。发完点它回主页
C_LIKE = dm._coord('AKKE_C_LIKE', None)                      # 视频右侧爱心点赞按钮；None→VL定位（潜在触达三连之一）
C_VIDEO = dm._coord('AKKE_C_VIDEO', (320, 460))             # 视频画面中心（点它暂停防连播跳走）；千分比，按需调 AKKE_C_VIDEO

MIN_INTERVAL = int(os.environ.get('AKKE_COMMENT_MIN_INTERVAL', '60'))
MAX_INTERVAL = int(os.environ.get('AKKE_COMMENT_MAX_INTERVAL', '60'))
DAILY_LIMIT = int(os.environ.get('AKKE_COMMENT_DAILY_LIMIT', '20'))
# 验证闸误判处理（两个机器特有开关，默认全 0=原严格逻辑，其它机器零影响）：
# ① AKKE_COMMENT_VERIFY_LENIENT=1（推荐先试）：仍真检测，但只核对评论【短前缀】+ 改成
#    「前缀出现 或 输入框已清空」任一成立即算发出——根治「长文案 VL 看不全→误判没发」。
# ② AKKE_COMMENT_TRUST_SEND=1（兜底）：点了发送即记 sent，完全不依赖 VL。代价是真没发的也当发了。
#    ⚠️两者都要求抖音焦点已稳（常驻前台）；focus_fail（打字前就丢焦点）不受影响、仍算失败可重试。
COMMENT_VERIFY_LENIENT = os.environ.get('AKKE_COMMENT_VERIFY_LENIENT', '0') == '1'
COMMENT_TRUST_SEND = os.environ.get('AKKE_COMMENT_TRUST_SEND', '0') == '1'


# ── 焦点加固（2026-06-03 spike 踩出来的两大坑，见 feedback_wuying_gui_fixed_coords_over_grounded）──
# SendInput 打字会打给【前台窗口】，后台进程 SetForegroundWindow 常被 Windows 静默拒，
# 字漏进 PowerShell 控制台。解法=AttachThreadInput 绕开；且必须 SW_MAXIMIZE 不能 SW_RESTORE
# （RESTORE 会把满屏窗口缩小→固定坐标落窗外点到桌面 Program Manager）。

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
    """硬把抖音拉到前台并保持最大化。返回是否成功（前台标题含「抖音」）。"""
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
        u.ShowWindow(hwnd, 3)        # SW_MAXIMIZE（别用 9 SW_RESTORE → 会缩小窗口）
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
    # 抖音 PC 窗口标题可能是中文「抖音」或英文「douyin」（重启/版本/语言而变）——两个都认。
    # 2026-06-17：无影重启后标题变 'douyin'，旧版只认 '抖音' → 焦点判定全 False → 整条 focus_fail。
    t = foreground_title()
    return '抖音' in t or 'douyin' in t.lower()


def ensure_douyin_front(tries=3):
    """先 pygetwindow 置前，再 ctypes 硬拉，确认前台是抖音。
    重夺焦点 + 重试 tries 次：人手碰一下鼠标会瞬时抢走焦点（实测「移动了鼠标」→前台变
    PowerShell），重试能从这种瞬时干扰恢复。tries 次仍不是抖音才返回 False（调用方据此跳过，
    不要带病往下点/打字）。⚠️ 跑批期间别碰鼠标键盘——pyautogui 全程在控鼠标。"""
    for i in range(tries):
        dm.focus_douyin()
        if force_foreground_douyin():
            return True
        time.sleep(0.6)
    print('  [警告] 重夺焦点 %d 次仍非抖音(=%r) → 调用方应跳过本条' % (tries, foreground_title()))
    return False


# ── 各步骤 ──────────────────────────────────────────────────────────

_REDLINE_RE = re.compile(
    r'微信|vx|wx|加\s*[vV]|给\s*个?\s*[vV]|➕\s*[vV]|'
    r'原价|专供价|活动价|底价|优惠价|报价|'
    r'\d{2,}\s*元|\d{2,}\s*块|\d{2,}\s*一?平|\d{2,}\s*/\s*平')


def has_redline(text):
    """公开评论红线词(价格/微信)检测——命中则不公开发(防限号)。"""
    return bool(_REDLINE_RE.search(text or ''))


def open_first_video():
    """点开主页作品宫格里【第一个非置顶】视频——置顶不是 watch 逮到的新视频，点它会照错视频生成评论
    (2026-06-18 实测好运无敌点到置顶『绿萝/通风』生活视频)。优先 VL 跳置顶；都没置顶就点最左上。
    VL 连失败才退固定坐标(可能误点置顶,告警)。返回是否点开了视频。"""
    pt = None
    for _ in range(3):
        pt = dm.locate('抖音个人主页【作品】宫格里第一个【非置顶】视频的封面缩略图中心——'
                       '置顶视频左上角有"置顶"小标签；跳过所有带"置顶"标签的，'
                       '点第一个没有该标签的(即最新发布的)视频；若都没有置顶标签就点最左上第一个')
        if pt:
            break
        time.sleep(1.2)
    if pt:
        pyautogui.click(pt[0], pt[1])
        time.sleep(3.0)
        return True
    if C_WORK_FIRST:
        print('  [作品] ⚠️ VL 跳置顶失败 → 退固定坐标(可能点到置顶,留意 comment1 对不对得上视频)')
        dm.click_norm(C_WORK_FIRST[0], C_WORK_FIRST[1], wait=3.0, label='第一个作品(固定坐标·可能含置顶)')
        return True
    return False


def pause_video():
    """暂停视频(防连播播完跳走→三连打偏)。2026-06-14 无影实测演进:
      ① 空格不暂停(焦点不在播放器→翻页);
      ② 点画面中心能暂停但【受网速/时机不稳】(点赞成了、轮到评论时可能已恢复/跳走);
      ③ 用户建议【点左下角播放/暂停控制键】最稳——这是专用按钮,toggle 明确。
    实现:先 hover 视频区唤出自动隐藏的底部控制条 → VL 定位左下角播放键点它 → 暂停。
    VL 没中退点画面中心兜底。坐标 AKKE_C_VIDEO=hover/兜底点(千分比)。需抖音前台,先硬拉。"""
    ensure_douyin_front()
    W, H = pyautogui.size()
    # 只移动不点(唤出控制条,不会误触发播放/暂停)
    pyautogui.moveTo(int(W * C_VIDEO[0] / 1000), int(H * C_VIDEO[1] / 1000))
    time.sleep(0.6)
    pt = dm.locate('抖音PC视频播放器【底部控制条最左边】的播放/暂停按钮(暂停=两竖⏸ / 播放=三角▶)，'
                   '位于视频左下角控制条上，不是点赞、不是评论、不是进度条',
                   region=(0.0, 0.84, 0.35, 1.0))
    if pt:
        pyautogui.click(pt[0], pt[1]); time.sleep(0.8)
        print('  [暂停] 点左下角播放/暂停键 -> (%d,%d)' % pt)
    else:
        dm.click_norm(C_VIDEO[0], C_VIDEO[1], wait=0.8, label='点视频中心(暂停兜底·VL未定位到播放键)')


def ensure_comment_panel():
    """确保右侧评论面板停在「评论」tab（打开视频后可能停在详情/相关推荐）。
    有 AKKE_C_COMMENT_TAB 用固定坐标，否则 VL 点「评论」tab。点了是幂等的（已在评论 tab 无副作用）。"""
    if C_COMMENT_TAB:
        dm.click_norm(C_COMMENT_TAB[0], C_COMMENT_TAB[1], wait=1.5, label='评论tab(固定坐标)')
        return True
    pt = dm.locate('抖音视频播放页【右侧面板顶部】的「评论」分类标签（详情/TA的作品/评论/问AI 里的评论）')
    if pt:
        pyautogui.click(pt[0], pt[1])
        time.sleep(1.5)
        return True
    print('  [警告] 没定位到「评论」tab，按面板已在评论态继续试')
    return False


def like_video():
    """给当前视频点赞（潜在触达三连：赞 + 评论1 + 评论2 的第一步）。
    **先赞后评**——评论面板弹出会盖住右侧点赞区，所以在 ensure_comment_panel 之前调。
    有 AKKE_C_LIKE 固定坐标直接点；否则 VL 定位右侧【未点赞(空心)】爱心，找到才点
    （已是实心/定位不到 → 跳过，幂等不取消赞）。返回 liked / like_skip / like_focus_fail。"""
    if not ensure_douyin_front():
        print('  [点赞跳过] 前台抢不回抖音')
        return 'like_focus_fail'
    # 先验:已经红了就别再点——固定坐标盲点已红的爱心会【取消赞】(toggle)。已赞=幂等返回 liked。
    # 2026-06-16:前置检查改【双采确认】(_is_liked_confirmed),单帧假阳性不再跳过点击(柴油根因:
    # 单帧误判已赞→没点却记 liked)。两帧都判红才信「确已赞」;否则继续往下定位空心爱心去点。
    if dm._is_liked_confirmed() is True:
        print('  ♥ 已是红心(双采确认·此前已赞),跳过点击')
        return 'liked'
    clicked = False
    if C_LIKE:
        dm.click_norm(C_LIKE[0], C_LIKE[1], wait=1.0, label='点赞(固定坐标)')
        clicked = True
    else:
        pt = dm.locate(
            '抖音视频播放页【右侧操作栏】里【未点赞】状态的爱心(点赞)按钮——空心/灰白描边，'
            '点了会变实心红心；若已是实心红心(已点赞)则不要定位返回',
            region=(0.6, 0.18, 1.0, 0.92))
        if pt:
            pyautogui.click(pt[0], pt[1]); time.sleep(1.0)
            clicked = True
        else:
            print('  [点赞跳过] 没定位到空心爱心(可能已赞/定位失败)，不取消赞继续评论')
            return 'like_skip'
    # 真发验证闸(2026-06-13):点完截右侧栏验爱心【真的变红】才算 liked。点空/未生效→like_unverified,
    # 不报假成功(昨晚"假发送"根因:点一下就返回 liked,实际爱心没红)。
    if dm._is_liked() is True:
        print('  ♥ 已点赞(验证:爱心已红)')
        return 'liked'
    print('  [点赞] ⚠️ 点了但验证爱心仍未变红(点空?) → like_unverified,不计成功')
    return 'like_unverified'


def _comment_posted(snippet):
    """真发验证闸(2026-06-13):发完核对评论【真的出现在评论区】+ 输入框已清空。
    返回 True/False/None。点发送≠发出(点空/被吞/限流静默丢)——昨晚"假发送"就是只点不验。
    复用 dm._vl_bool 全屏问(评论框在底、评论列表在右,都要看,不裁)。"""
    if COMMENT_VERIFY_LENIENT:
        # 宽松但仍真检测：长评论让 VL 核对整条文案易误判 false → 改只认【短前缀】+「任一成立」。
        prefix = (snippet or '')[:14]
        return dm._vl_bool('_cmt_verify.png',
            '这是抖音PC视频页截图,右侧是评论区、底部是评论输入框。判断刚刚这条评论是否已发出。'
            '满足【任一条】即算【已发出】:① 评论列表里(通常最上面)出现一条以「%s」开头、'
            '带"刚刚"或本人头像的评论;② 底部评论输入框已清空(显示灰色占位"留下你的精彩评论/'
            '说点什么",里面没有待发的字)。只有【两条都明显不成立】(既没看到这条评论、'
            '输入框里又还留着没发出去的字)才算【没发出】。没看清就回 null。'
            '只回严格JSON:{"posted":true或false或null}' % prefix,
            'posted')
    return dm._vl_bool('_cmt_verify.png',
        '这是抖音PC视频页截图,右侧是评论区、底部是评论输入框。判断:刚刚是否成功发出了一条内容'
        '包含「%s」的评论? 依据【两条都要满足】:① 该评论出现在评论列表里(通常在最上面,带"刚刚"'
        '或本人头像);② 底部评论输入框已清空(显示灰色占位"留下你的精彩评论/说点什么")。'
        '若没看到这条评论、或输入框里还留着没发出去的字 → 没发出。'
        '没看清就回 null。只回严格JSON:{"posted":true或false或null}' % snippet,
        'posted')


def post_comment(text):
    """点评论框→打字→发送→【验证真发出】。返回 'sent'(已验证出现) / 'send_unverified'(点了发送
    但评论区没出现/输入框还有字,不算成功) / 'focus_fail'(前台抢不回抖音,没打字)。
    打字前硬拉前台,避免 SendInput 漏进终端/桌面。"""
    if not ensure_douyin_front():
        print('  [跳过] 前台抢不回抖音 → 不点不打字（避免漏进终端/点歪）')
        return 'focus_fail'
    # 点两下评论框确保激活（间隔避免被当双击选词）
    dm.click_norm(C_COMMENT[0], C_COMMENT[1], wait=0.6, label='评论框(第1下)')
    dm.click_norm(C_COMMENT[0], C_COMMENT[1], wait=1.0, label='评论框(第2下)')
    fg = foreground_title()
    if '抖音' not in fg and 'douyin' not in fg.lower():
        print('  [跳过] 打字前前台=%r 非抖音 → 不打字（避免漏进终端）' % fg)
        return 'focus_fail'
    dm.type_text(text)
    time.sleep(0.8)
    # 发送键位置随评论长度/换行而变（长评论框变高，固定坐标对不上）。打完字后【现场 VL 定位
    # 「发送」二字】(文字按钮，比箭头好认)，限定右下区域；定位不到再退固定坐标。
    _W, _H = pyautogui.size()
    sp = dm.locate('抖音评论输入框右下角的红色"发送"按钮（发布评论用，红底白字"发送"）',
                   region=(0.6, 0.82, 1.0, 1.0))
    if sp:
        print('  [发送] VL 定位 -> (%d,%d)' % sp)
        pyautogui.click(sp[0], sp[1]); time.sleep(2.0)
    else:
        print('  [发送] VL 没定位到 → 退固定坐标 %s' % (C_COMMENT_SEND,))
        dm.click_norm(C_COMMENT_SEND[0], C_COMMENT_SEND[1], wait=2.0, label='发送(固定坐标)')
    # 真发验证闸:核对评论真出现在评论区+输入框已清空,验到才算 sent(否则 send_unverified 不计成功)。
    time.sleep(2.5 if COMMENT_VERIFY_LENIENT else 1.2)  # 宽松模式多等渲染:长评论上屏慢,早截图易误判没发
    posted = _comment_posted(text)
    if posted is True:
        print('  ✅ 评论已验证出现在评论区')
        return 'sent'
    if COMMENT_TRUST_SEND:
        # 本机 .env 开了信任点击：VL 验证常误判，但实测点发送后评论稳定发出 → 记 sent。
        # 代价：极少数真没发出的（被限流静默丢等）会被当 sent 漏掉、不补发——对公开评论可接受
        # （重发刷屏伤号代价更大）。VL 结果仅打印备查。这是对「send_unverified 不当真发」铁律的
        # 【按机器】主动覆盖，只在设了 AKKE_COMMENT_TRUST_SEND=1 的机器生效。
        print('  [发送] VL 验证=%r 但本机 AKKE_COMMENT_TRUST_SEND=1 → 信任点击，记 sent' % posted)
        return 'sent'
    if posted is False:
        print('  [评论] ⚠️ 点了发送但评论区没出现这条/输入框还有字 → 没真发出(send_unverified)')
        return 'send_unverified'
    print('  [评论] 发送后验证读不清(None) → send_unverified(以抖音评论区气泡为准)')
    return 'send_unverified'


def reset_to_profile(nick):
    """发完/放弃评论后退出视频浮层，回到目标主页(可搜索状态)。

    实测 Esc 退不出(评论框/视频层吃焦点，4 次 Esc 仍卡在视频页 → 下一条搜索落空、
    停在上一个人，身份门 match=False)。视频页左上角有「返回」按钮，点它才回主页。
    优先固定坐标 AKKE_C_BACK；没 pin 就 VL 定位左上角返回键。点完用 ocr_verify 确认，
    没回到再点一次(最多 3 次)，仍不行兜底按 Esc。回主页后 C_SEARCH 可用(DM 串发即从主页搜)。
    """
    for i in range(3):
        clicked = False
        if C_BACK:
            dm.click_norm(C_BACK[0], C_BACK[1], wait=1.2, label='返回按钮(固定坐标)')
            clicked = True
        else:
            try:
                bp = dm.locate(
                    '抖音视频播放页【左上角】的「返回」按钮(向左的箭头 / "返回" 文字，'
                    '点了退出当前视频回到用户个人主页)',
                    region=(0.0, 0.0, 0.30, 0.22))
                if bp:
                    pyautogui.click(bp[0], bp[1]); time.sleep(1.2)
                    clicked = True
            except Exception as e:
                print('  [复位] VL 定位返回键失败: %s' % e)
        if not clicked:
            pyautogui.press('esc'); time.sleep(1.0)  # 兜底
        try:
            on_profile, _, _ = dm.ocr_verify(nick)
        except Exception:
            on_profile = False
        if on_profile:
            print('  [复位] 第%d次点返回后已回主页' % (i + 1))
            return True
    print('  [复位] ⚠️ 3次仍未确认回主页(下一条可能搜索落空，留意日志；可量 AKKE_C_BACK 固定坐标)')
    return False


def process(c, auto, times=None):
    """潜在触达三连：点赞 + 评论1 + 评论2。返回 (status, ocr_conf_or_None, liked, status2)。
    status = 评论1 结果（沿用旧口径，sent_log.status 仍指评论1）；liked = 点赞结果；
    status2 = 评论2 结果（无 message2 列时为 'no_msg2'）。
    times（可选 dict）：每步【完成那刻】写 ISO 时间戳 like_at/comment1_at/comment2_at，
    供 comment_log → second_touch_log 流水表画完整时间线（点赞/评论1/评论2 分步时刻）。
    早返回(身份门未过/无作品)不写，对应步留空。"""
    if times is None:
        times = {}
    nick = c['nickname']
    query = (c.get('douyin_id') or nick).strip() or nick
    comment = c['message']
    comment2 = (c.get('message2') or '').strip()  # 评论2（顺到产品）；空=只发一条
    print('\n=== %s ===' % nick)
    print('  评论1: %s' % comment)
    if comment2:
        print('  评论2: %s' % comment2)

    # 1) 搜人 → 进主页（复用 DM 固定坐标导航），带「非主页/加载中」重试(最多 3 次)。
    #    先 goto_home 钉死首页(否则上一条停视频/结果页→搜索框位置变→点空到视频上);
    #    OCR 落『(非主页)』/搜错时整条回首页重搜——与 _follow_grounded 同套修法(2026-06-13)。
    ensure_douyin_front()
    # 2) OCR 身份门（防同名错评，公开评论错人代价更大）。query=抖音号时传 expected_number：
    #    昵称比对没过（用户改名/特殊字符）还能靠主页「抖音号」精确核身兜底，少误杀 wrong_user。
    m, conf, seen = False, 0.0, ''
    expnum = query if query != nick else None
    # 有号(expnum)+量了行距 → 扫前 N 条、按号精确挑本人(2026-06-14,根治「目标不在第一条→点错人」)；
    # 否则保持原 3 次「非主页/搜错」重搜(都点第一条)，行为不变。
    scan = bool(expnum) and (dm.RESULT_ROW_DY > 0 or dm.RESULT_COL_DX > 0)
    attempts = dm.SEARCH_SCAN_N if scan else 3
    for attempt in range(attempts):
        col, row = (attempt % dm.SEARCH_NCOLS, attempt // dm.SEARCH_NCOLS) if scan else (0, 0)
        if attempt > 0:
            print('  [%s %d] 上次落 %r → 重搜点第%d条(列%d行%d)'
                  % ('扫描' if scan else '重试', attempt, seen, attempt + 1, col, row))
        dm.goto_home()
        dm.click_norm(dm.C_SEARCH[0], dm.C_SEARCH[1], wait=1.2, label='搜索框')
        dm.type_text(query)
        pyautogui.press('enter')
        time.sleep(2.5 + attempt * 1.5)  # 重试/翻条时多等结果页加载
        dm.click_user_tab()  # VL 点中「用户」tab,过滤成用户列表(固定坐标与第一条重叠点不中→落非主页)
        # 扫描时第 attempt 条 = 第一条 X 往右挪 col×列距、Y 往下挪 row×行距(2列网格)；否则只点第一条。
        dm.click_norm(dm.C_FIRST[0] + col * dm.RESULT_COL_DX, dm.C_FIRST[1] + row * dm.RESULT_ROW_DY,
                      wait=2.0, label='第%d条结果头像' % (attempt + 1 if scan else 1))
        m, conf, seen = dm.ocr_verify(nick, expected_number=expnum)
        print('  [OCR] match=%s conf=%.2f seen=%r threshold=%s' % (m, conf, seen, dm.MIN_CONF))
        if m and conf >= dm.MIN_CONF:
            break
    if not m or conf < dm.MIN_CONF:
        print('  [跳过] 身份门未过 → 不评论')
        return 'wrong_user', conf, 'n/a', 'n/a'

    # 3) 点开第一条视频
    if not open_first_video():
        print('  [跳过] 没点开视频(可能无作品/定位失败)')
        return 'no_works', conf, 'n/a', 'n/a'

    # 3.5) 暂停视频(防播完自动跳下一条→评论发错地方)
    pause_video()

    # 4) 逐条确认（--confirm）：发公开内容前给人最后把关。放在【点赞之前】——否决=赞和评论都不发。
    if not auto:
        prompt = '  ❓ 给【%s】发[赞+%s]？(y=发 / 回车=跳过) ' % (
            nick, '评论1' if not comment2 else '评论1+评论2')
        try:
            ans = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ''
        if ans not in ('y', 'yes', '是'):
            print('  [跳过] 人工放弃这条(未点赞未评论)')
            reset_to_profile(nick)
            return 'cancelled', conf, 'n/a', 'cancelled'

    # 4.5) 点赞（潜在触达三连第一步，先赞后评——评论面板会盖住点赞区）
    liked = like_video()
    times['like_at'] = datetime.now().isoformat()  # 点赞步完成时刻（结果见 liked 列）

    # 5) 确保评论面板停在「评论」tab
    ensure_comment_panel()

    # 6) 发评论1（post_comment 内已验证真发出：st=='sent' 才是【验证过出现在评论区】）
    st = post_comment(comment)
    times['comment1_at'] = datetime.now().isoformat()  # 评论1步完成时刻（结果见 status 列）

    # 6.5) 发评论2（顺到产品）——评论1【验证发出】且有第二条才发；两条之间留间隔别瞬发
    st2 = 'no_msg2'
    if comment2:
        if has_redline(comment2):
            st2 = 'skip_redline'  # 评论2 含价格/微信红线词,公开发会限号 → 只发评论1
            print('  [评论2跳过] 含红线词(价格/微信),公开发会限号 → 只发评论1。需公开安全版话术')
        elif st == 'sent':
            time.sleep(random.randint(4, 8))
            st2 = post_comment(comment2)
            times['comment2_at'] = datetime.now().isoformat()  # 评论2步完成时刻（结果见 status2 列）
        else:
            st2 = 'skip_c1_fail'  # 评论1 没验证发出，不发第二条免得只孤零零一条产品评论
            print('  [评论2跳过] 评论1 未验证发出(%s)，不补第二条' % st)

    # 回到干净状态给下一条：点返回 + ocr_verify 确认已回主页(否则下一条搜索落空)
    reset_to_profile(nick)
    return st, conf, liked, st2


def main(contacts_csv, auto):
    with open(contacts_csv, encoding='utf-8-sig') as f:
        contacts = list(csv.DictReader(f))
    if not contacts:
        print('名单为空, 退出')
        return
    if len(contacts) > DAILY_LIMIT:
        print('⚠️ 名单 %d > 上限 %d, 截断' % (len(contacts), DAILY_LIMIT))
        contacts = contacts[:DAILY_LIMIT]

    print('=== douyin_comment_grounded  model=%s  conf>=%s  %s ===' % (
        dm.MODEL, dm.MIN_CONF, '自动发' if auto else '逐条确认'))
    print('共 %d 条; 前置: ①抖音PC前台最大化 ②分辨率锁2560×1600 ③输入法【英文】' % len(contacts))
    print('   ⚠️ 公开评论：发完以抖音评论区气泡核对实发，不要只信本日志。')

    log = 'comment_log_%s.csv' % datetime.now().strftime('%Y%m%d')
    fields = ['douyin_id', 'nickname', 'message', 'message2', 'has_works',
              '_comment_id', '_sec_uid', '_dispatch_id',
              'status', 'liked', 'status2', 'sent_at', '_ocr_confidence',
              # 三连分步完成时刻（second_touch_log 完整时间线用；早返回的步留空）
              'like_at', 'comment1_at', 'comment2_at']
    first_write = not os.path.exists(log)
    with open(log, 'a', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        if first_write:
            w.writeheader()
        for i, c in enumerate(contacts, 1):
            print('\n========== [%d/%d] ==========' % (i, len(contacts)))
            ocr_conf = None
            liked = status2 = 'n/a'
            times = {}   # 三连分步完成时刻（process 填）
            try:
                with _wl.window_turn('rb'):   # DM 在忙(.dm-want)则先在此等 → DM 优先，发完让位
                    status, ocr_conf, liked, status2 = process(c, auto, times)
            except Exception as e:
                print('  ❌ 异常: %s' % e)
                status = 'error:%s' % e
                try:
                    pyautogui.press('esc'); time.sleep(0.5)
                except Exception:
                    pass
            w.writerow({**c, 'status': status, 'liked': liked, 'status2': status2,
                        'sent_at': datetime.now().isoformat(),
                        '_ocr_confidence': '' if ocr_conf is None else '%.3f' % ocr_conf,
                        'like_at': times.get('like_at', ''),
                        'comment1_at': times.get('comment1_at', ''),
                        'comment2_at': times.get('comment2_at', '')})
            f.flush()
            if i < len(contacts):
                time.sleep(random.randint(MIN_INTERVAL, MAX_INTERVAL))
    print('\n✅ 完成. 日志: %s' % log)


if __name__ == '__main__':
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print('用法: python douyin_comment_grounded.py contacts.csv [--confirm]', file=sys.stderr)
        print('      默认自动发(OCR门通过即发)；加 --confirm 才逐条 y/n 确认', file=sys.stderr)
        sys.exit(2)
    if not dm.KEY:
        print('❌ 缺 ANTHROPIC_API_KEY / OPENROUTER_API_KEY', file=sys.stderr)
        sys.exit(2)
    # 默认自动发；--confirm 才逐条确认（--auto 仍兼容接受=默认行为）
    main(args[0], auto='--confirm' not in flags)
