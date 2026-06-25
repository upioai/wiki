#!/usr/bin/env python3
"""creator_publisher.py — 抖音创作服务平台(creator.douyin.com)发图文 PoC.

云电脑端跑(Edge 已登录态), 复用 worker/scripts/wuying-dm/douyin_dm_grounded.py
的视觉/键入/截图 helper.

PoC scope (2026-06-22 头版):
  - 单条图文, 立即发, 不带定时
  - 默认 dry-run (停在「发布」按钮前 + 截图标记), 加 --commit 才真发
  - manifest 字段: title / body / images (本地路径列表, 1-9 张)
  - 图片必须先放到云电脑磁盘上(路径作 args 传入)
  - 不动 DB, 不入队, 不调 cron — 命令行单次跑通

用法:
  # 直接传参
  python creator_publisher.py --title "标题" --body "正文..." ^
      --images "C:\\akke-wuying\\tuwen\\1.png,C:\\akke-wuying\\tuwen\\2.png"

  # 用 manifest
  python creator_publisher.py --manifest manifest.json
  # manifest.json: {"title": "...", "body": "...", "images": ["1.png","2.png"]}

  # 真发 (不加默认 dry-run)
  python creator_publisher.py --manifest m.json --commit

  # 跳过窗口置前 (Edge 已在前台, 跑得稳定时用)
  python creator_publisher.py --manifest m.json --skip-focus

前提:
  1. Edge 登录抖音创作服务平台(creator.douyin.com)
     ★ 开始前手动开好 creator 发布页或主页都行, 脚本会 Ctrl+L 直达发布页
  2. 输入法切英文 (中文 IME 会抢 URL 首字 + 输入框首字)
  3. .env 同 douyin_dm_grounded.py 同目录 (ANTHROPIC_API_KEY = OpenRouter key)
  4. 1-9 张图片已放到本地路径 (paste.rs / 远程文件下载等渠道, 由调用方备)
  5. Windows + Edge + 100% 缩放推荐 (其它缩放靠 AKKE_DPI_AWARE=1 兜底)

环境变量:
  AKKE_CREATOR_UPLOAD_URL    覆盖默认上传页 URL
  AKKE_TUWEN_PUBLISH_WAIT    发布后等待几秒再校验 (默认 5)
  AKKE_TUWEN_UPLOAD_WAIT     图片上传后等几秒 (默认 8)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pyautogui
import pyperclip

# 找 wuying-dm 目录加入 sys.path (复用 douyin_dm_grounded 低层 helper).
# 三路 fallback 兼容: ①repo 结构 worker/scripts/wuying-dm ②云电脑扁平 C:\akke-wuying\wuying-dm
# ③env AKKE_WUYING_DM_DIR 显式覆盖. 任一命中即停.
_THIS_DIR = Path(__file__).resolve().parent
_DM_CANDIDATES = [
    Path(os.environ.get('AKKE_WUYING_DM_DIR', '') or '__missing__'),
    _THIS_DIR.parent / 'wuying-dm',
    Path('C:/akke-wuying/wuying-dm'),
    Path('/Users') / os.environ.get('USER', '') / 'akke-wuying' / 'wuying-dm',
]
for _cand in _DM_CANDIDATES:
    if _cand.exists() and (_cand / 'douyin_dm_grounded.py').exists():
        sys.path.insert(0, str(_cand))
        print(f'[bootstrap] wuying-dm = {_cand}', file=sys.stderr)
        break
else:
    print('ERROR: 找不到 wuying-dm 目录 (含 douyin_dm_grounded.py).\n'
          '  方案: ①把 douyin_dm_grounded.py 放到 C:\\akke-wuying\\wuying-dm\\\n'
          '         ②set $env:AKKE_WUYING_DM_DIR = "<目录>"',
          file=sys.stderr)
    sys.exit(99)

# 复用 DM grounded 的全部低层 helper (含 .env 加载 + pyautogui 默认配置)
from douyin_dm_grounded import (  # noqa: E402
    _vision, _shot, _pjson,
    locate, click_norm, type_unicode, focus_douyin,
)

CREATOR_UPLOAD_URL = os.environ.get(
    'AKKE_CREATOR_UPLOAD_URL',
    'https://creator.douyin.com/creator-micro/content/upload?default-tab=3',
)
PUBLISH_WAIT = int(os.environ.get('AKKE_TUWEN_PUBLISH_WAIT', '5'))
UPLOAD_WAIT = int(os.environ.get('AKKE_TUWEN_UPLOAD_WAIT', '8'))


# ---------- 流程步骤 ----------

def goto_creator_upload(wait: float = 5.0) -> None:
    """Edge 地址栏 Ctrl+L 直达 creator 发布页 (图文 tab).

    URL 用 type_unicode 注入绕开中文 IME (PC 版 DM 通道踩过坑, type_text 会被吞字).
    """
    print(f'  [nav] Ctrl+L → {CREATOR_UPLOAD_URL}')
    pyautogui.hotkey('ctrl', 'l'); time.sleep(0.8)
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.2)
    pyautogui.press('delete'); time.sleep(0.2)
    type_unicode(CREATOR_UPLOAD_URL); time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(wait)


def click_image_tab() -> bool:
    """页面顶部 tab: 「发布视频」/「发布图文」. 视觉定位「图文」tab 并点击.

    部分入口默认就是图文 tab (URL default-tab=3 已经选了), 找不到也不抛错;
    后续上传按钮失败再回头报错.
    """
    pt = locate(
        '抖音创作服务平台发布页【顶部 tab 栏】里的「发布图文」或「发图文」tab 按钮('
        '通常在「发布视频」tab 旁边, 可能带相机/图片图标). 不要选「发布视频」.',
        region=(0.10, 0.05, 0.90, 0.30),
    )
    if pt is None:
        print('  [tab] 没定位到「图文」tab → 假设 URL 已默认到图文 tab, 继续')
        return False
    pyautogui.click(pt[0], pt[1])
    time.sleep(1.8)
    print(f'  [tab] 已点「图文」tab @ ({pt[0]},{pt[1]})')
    return True


def click_upload_button() -> bool:
    """页面中央大块「点击或拖拽图片」/「上传图片」上传区. 点击会弹 Windows 文件对话框."""
    pt = locate(
        '抖音发布页正文区中央的【图片上传按钮/拖拽区域】(大块虚线边框, '
        '里面有「点击或拖拽上传图片」或「上传图片」文案, 或者一个大 + 号图标). '
        '注意不要选页面顶部的 tab 按钮.',
        region=(0.15, 0.20, 0.85, 0.80),
    )
    if pt is None:
        return False
    pyautogui.click(pt[0], pt[1])
    print(f'  [upload] 已点上传区 @ ({pt[0]},{pt[1]}), 等文件对话框弹出')
    time.sleep(2.8)
    return True


def paste_image_paths(image_paths: list[str]) -> None:
    """Windows 文件选择对话框: 多文件路径用空格分隔 + 双引号包裹, paste 进文件名输入框 + 回车.

    例: "C:\\a\\1.png" "C:\\a\\2.png"
    对话框打开后焦点默认在文件名输入框, 不用再点.
    """
    path_str = ' '.join(f'"{p}"' for p in image_paths)
    print(f'  [picker] 粘贴 {len(image_paths)} 个路径: {path_str[:120]}...')
    pyperclip.copy(path_str)
    time.sleep(0.4)
    pyautogui.hotkey('ctrl', 'v'); time.sleep(0.6)
    pyautogui.press('enter')
    print(f'  [picker] 回车提交, 等图片上传 {UPLOAD_WAIT}s')
    time.sleep(UPLOAD_WAIT)


def fill_title(title: str) -> bool:
    """图文「作品标题」输入框 (限 20 字内, 必填)."""
    pt = locate(
        '抖音发布页里的【作品标题输入框】(单行输入框, 上方或左侧有「标题」字样, '
        '通常在图片预览区下方、正文描述框上方, 字数限制 20 字). '
        '不要选下方的多行正文描述框.',
        region=(0.15, 0.20, 0.85, 0.60),
    )
    if pt is None:
        return False
    pyautogui.click(pt[0], pt[1]); time.sleep(0.4)
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.1)
    pyautogui.press('delete'); time.sleep(0.3)
    type_unicode(title); time.sleep(0.8)
    print(f'  [title] 已填: {title[:30]}')
    return True


def fill_body(body: str) -> bool:
    """图文「作品描述/正文」多行输入框 + 话题标签确认.

    #xxx 话题在抖音 creator 输入时会弹下拉建议浮层(列热门话题 + 热度数),
    必须按 Enter 选第一个建议才会把 #xxx 转成蓝色话题链接; 不选则只是普通文本.
    所以 body 不是一次性键入, 而是按 # 分段键入, 每个 #xxx 后等浮层 + Enter.
    """
    pt = locate(
        '抖音发布页里的【作品描述/正文输入框】(多行大文本区域, 在标题输入框下方, '
        '可能带「输入内容, 让更多人看到吧」或「分享此刻的想法」占位文字, '
        '支持 # 话题 / @ 提及). 不要选上方单行的标题框.',
        region=(0.15, 0.25, 0.85, 0.75),
    )
    if pt is None:
        return False
    pyautogui.click(pt[0], pt[1]); time.sleep(0.4)
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.1)
    pyautogui.press('delete'); time.sleep(0.3)

    # 把 body 按 #xxx 切成 token 列表, 例:
    #   "Akke 验证 #全屋定制 #装修避坑"
    #   → ["Akke 验证 ", "#全屋定制", " ", "#装修避坑", ""]
    import re
    tokens = re.split(r'(#\S+)', body)
    tag_count = 0
    for tok in tokens:
        if not tok:
            continue
        type_unicode(tok)
        if tok.startswith('#'):
            # 等浮层弹出 + Enter 选第一个建议(最热门 = 最稳的官方话题)
            time.sleep(0.9)
            pyautogui.press('enter')
            time.sleep(0.5)
            tag_count += 1
        else:
            time.sleep(0.15)

    print(f'  [body] 已填 ({len(body)} chars, {tag_count} 个 #标签已选)')
    return True


def set_schedule(at_str: str) -> bool:
    """设定时发布:
      1. 滚到「发布设置」段 (发布时间行可见)
      2. 点「定时发布」单选按钮
      3. 点右侧时间输入框 (默认 'YYYY-MM-DD HH:MM' + 日历图标)
      4. Ctrl+A 删 + 键入目标时间 + Enter
      5. Esc 关 datepicker (如果还开着)

    at_str: 'YYYY-MM-DD HH:MM', 必须现在 +2h ~ +14天 内 (creator 硬限).
    输入框是否支持直接键入待 PoC 验证 (截图看是 type-style input + 日历图标 popover).
    """
    # 1. 滚到发布设置段
    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, int(sh * 0.5))
    time.sleep(0.2)
    for _ in range(8):
        pyautogui.scroll(-500)
        time.sleep(0.12)
    time.sleep(0.6)
    print('  [schedule] 已滚到发布设置段')

    # 2. 点「定时发布」单选按钮
    pt = locate(
        '抖音 creator 发布页【发布时间】行里的【定时发布单选按钮】(单选圆点 + 「定时发布」三个字), '
        '在「立即发布」单选按钮的右边. 不要选左边的「立即发布」.',
        region=(0.10, 0.50, 0.70, 0.95),
    )
    if pt is None:
        return False
    pyautogui.click(pt[0], pt[1])
    time.sleep(0.7)
    print(f'  [schedule] 已点「定时发布」单选 @ ({pt[0]},{pt[1]})')

    # 3. 点右侧时间输入框 (datepicker 日历控件弹出)
    pt = locate(
        '抖音 creator 发布页里的【定时发布时间输入框】(细长输入框, 框里显示 "YYYY-MM-DD HH:MM" '
        '形如 "2026-06-22 19:05" 的默认时间, 右边带日历图标), 在「定时发布」单选按钮右边.',
        region=(0.25, 0.55, 0.70, 0.95),
    )
    if pt is None:
        return False
    pyautogui.click(pt[0], pt[1])
    time.sleep(1.2)
    print(f'  [schedule] 已点时间输入框 @ ({pt[0]},{pt[1]}), datepicker 应该弹出')

    # 4. datepicker 默认显示当月, 跨月先点 ">" 翻页到目标月
    # creator 允许 +14 天 = 最多跨 1 个月. 但我们 cap 12 防意外
    from datetime import datetime as _dt
    at_dt = _dt.strptime(at_str, '%Y-%m-%d %H:%M')
    now = _dt.now()
    months_ahead = (at_dt.year - now.year) * 12 + (at_dt.month - now.month)
    if months_ahead < 0:
        print(f'  ERROR: 目标月份在过去 ({at_dt.year}-{at_dt.month:02d})', file=sys.stderr)
        return False
    if months_ahead > 2:
        print(f'  ERROR: 目标月份 {months_ahead} 月之后, creator 只允许 14 天内', file=sys.stderr)
        return False

    for i in range(months_ahead):
        pt_next = locate(
            '抖音 creator datepicker 日历控件【顶部右侧】的【向右箭头 ">"】(下一月切换按钮), '
            '在年月文字(形如 "2026年 6月") 的右边, 通常是 ">" 或 "›" 这种箭头符号. '
            '注意不要选左侧的 "<" (上一月).',
            region=(0.30, 0.40, 0.75, 0.70),
        )
        if pt_next is None:
            print(f'  WARN: 月切换 {i + 1}/{months_ahead} 找不到 ">"', file=sys.stderr)
            return False
        pyautogui.click(pt_next[0], pt_next[1])
        time.sleep(0.5)
        print(f'  [schedule] 翻到下个月 ({i + 1}/{months_ahead})')

    # 5. 点目标日期格
    target_day = at_dt.day
    pt = locate(
        f'抖音 creator 发布页弹出的【日期选择器(datepicker)】里, 数字「{target_day}」的日期格. '
        f'日历控件顶部应显示「{at_dt.year}年 {at_dt.month}月」. '
        f'中间是 7 列日历表(日 一 二 三 四 五 六), 每行 7 个数字日期格. '
        f'找【当月】的数字「{target_day}」(深色清晰数字, 不是其他月份的灰色数字).',
        region=(0.30, 0.50, 0.75, 0.95),
    )
    if pt is None:
        print(f'  WARN: datepicker 里没定位到日期 {target_day}', file=sys.stderr)
        return False
    pyautogui.click(pt[0], pt[1])
    time.sleep(0.6)
    print(f'  [schedule] 已点日期 {at_dt.year}-{at_dt.month:02d}-{target_day:02d} @ ({pt[0]},{pt[1]}), 时间保留 creator 默认 (+2h)')

    # 6. 在 datepicker 浮层【外面的空白区域】点一下, 持久化日期选择 + 关掉浮层.
    # 实测: 单纯 Esc 关 popover 只是收起浮层, 不一定持久化选择 (datepicker 是浮层组件
    # 通常需要 blur 才提交). 点屏幕左侧空白处 (远离表单 + datepicker) 触发 blur.
    sw, sh = pyautogui.size()
    blank_x = int(sw * 0.10)  # 屏幕左侧 10% 处 — 左侧菜单栏旁边的空白
    blank_y = int(sh * 0.50)  # 屏幕中部高度
    pyautogui.click(blank_x, blank_y)
    time.sleep(0.6)
    print(f'  [schedule] 已点空白处 ({blank_x},{blank_y}) 持久化日期选择')

    path, _ = _shot('_schedule_set.png')
    # 截图上画红圈圈出脚本点的日期格位置, 方便肉眼复核
    try:
        from PIL import Image, ImageDraw
        im = Image.open(path)
        draw = ImageDraw.Draw(im)
        r = 30
        x, y = pt
        draw.ellipse((x - r, y - r, x + r, y + r), outline='red', width=4)
        draw.text((x + r + 10, y - r - 15), f'DAY? {at_dt.month}/{target_day}', fill='red')
        im.save(path)
    except Exception:
        pass
    print(f'  [schedule] 截图(已画红圈): {path}')
    return True


def add_music() -> bool:
    """点「选择音乐」框 → 弹推荐音乐面板 → 选第一首推荐 → 关闭面板.

    creator 后台发图文「扩展信息」段有「选择音乐」行(占位文字「点击添加合适作品风格音乐」+ 音符♪图标).
    点击会弹出全屏音乐面板, 列推荐音乐. 选第一个最稳(默认 = 平台官方推荐, 合规).
    """
    # 1. 滚到「扩展信息」段 (选择音乐行可见)
    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, int(sh * 0.5))
    time.sleep(0.2)
    for _ in range(6):
        pyautogui.scroll(-500)
        time.sleep(0.12)
    time.sleep(0.6)

    # 2. 点右侧【✏ 选择音乐】按钮 (白底+笔形 edit 图标+文字"选择音乐", 在选择音乐行最右端).
    # 左侧灰色长条「点击添加合适作品风格音乐」是占位预览, 不响应 — 实测踩过坑.
    pt = locate(
        '抖音 creator 发布页【扩展信息】段【选择音乐】行最【右端】的【白色边框按钮】, '
        '按钮内含【笔形/铅笔(✏ edit)图标 + 「选择音乐」三个字】, 整体白底有边框, 看起来像编辑按钮. '
        '注意: 不要选左边那个灰色长条占位框(显示「点击添加合适作品风格音乐」+ ♪音符图标)— 那是预览, 不响应.',
        region=(0.40, 0.30, 0.80, 0.85),
    )
    if pt is None:
        return False
    pyautogui.click(pt[0], pt[1])
    # 面板滑入动画 + 列表加载 + 音乐封面图加载, 2s 不够 (上版 NOT FOUND 根因)
    time.sleep(4.5)
    print(f'  [music] 已点选择音乐 @ ({pt[0]},{pt[1]}), 等面板弹出 + 列表加载 4.5s')

    # 3. 右侧滑出的【选择音乐】面板里点列表第一首.
    # prompt 简化 + region 放宽 (上版 NOT FOUND 根因之二: prompt 过于具体 + region 过窄)
    pt_first = locate(
        '抖音 creator 发布页右侧的【选择音乐】侧栏面板里, 【最顶部第一首音乐项】的中心位置. '
        '面板顶部有 tab 栏(推荐/热门榜...), tab 下面就是音乐列表, '
        '选列表第一行的中部 (一行结构: 左小封面 + 中音乐名 + 右人数文字). '
        '不要选 tab 按钮 / 搜索框 / 右上 X 关闭.',
        region=(0.60, 0.10, 1.00, 0.55),
    )
    if pt_first is None:
        # 再放宽到右半屏全部
        pt_first = locate(
            '抖音 creator 选择音乐面板里【第一行音乐】(最上面那行). '
            '行结构: 小封面 + 音乐名文字 + 时长 + 人数.',
            region=(0.50, 0.10, 1.00, 0.70),
        )
    if pt_first is None:
        print('  WARN: 音乐面板里没定位到第一首音乐项 (面板没弹出/动画未完/VL 没识别)', file=sys.stderr)
        try:
            _shot('_music_panel_notfound.png')
            print('  截图存证: screenshots\\_music_panel_notfound.png', file=sys.stderr)
        except Exception:
            pass
        return False
    pyautogui.click(pt_first[0], pt_first[1])
    time.sleep(1.2)
    print(f'  [music] 已点第一首推荐音乐 @ ({pt_first[0]},{pt_first[1]}), 选中态(粉红高亮)')

    # 4. 选中态下右侧出现【红色"使用"按钮】, 必须点它才真正应用音乐
    pt_use = locate(
        '抖音 creator【选择音乐】面板里, 刚选中(粉红高亮)的【第一行音乐项】右侧出现的【红色"使用"按钮】'
        '(实心红色矩形背景, 白字"使用"二字), 在该行最右端、紧挨"XX万人使用"文字之后. '
        '注意: 这个红按钮是选中音乐项后才显示的, 没选中前看不到.',
        region=(0.85, 0.15, 1.00, 0.40),
    )
    if pt_use is None:
        # fallback 放宽 region
        pt_use = locate(
            '抖音 creator 选择音乐面板里【红色"使用"按钮】(实心红底白字"使用"), '
            '在选中音乐项行的右端. 不要选"XX万人使用"文字, 是按钮形态的"使用".',
            region=(0.80, 0.15, 1.00, 0.45),
        )
    if pt_use is None:
        print('  WARN: 没找到「使用」按钮, 音乐未真正应用 (只选中了未确认)', file=sys.stderr)
    else:
        pyautogui.click(pt_use[0], pt_use[1])
        time.sleep(1.2)
        print(f'  [music] 已点「使用」按钮 @ ({pt_use[0]},{pt_use[1]}), 音乐已应用')

    # 5. 关闭音乐面板 (Esc 通常生效)
    pyautogui.press('escape')
    time.sleep(0.8)

    path, _ = _shot('_music_set.png')
    # 截图上画红圈圈出脚本点的音乐项 + 使用按钮位置, 方便肉眼复核
    try:
        from PIL import Image, ImageDraw
        im = Image.open(path)
        draw = ImageDraw.Draw(im)
        r = 25
        x, y = pt_first
        draw.ellipse((x - r, y - r, x + r, y + r), outline='red', width=3)
        draw.text((x + r + 8, y - r - 12), '1st row', fill='red')
        if pt_use is not None:
            xu, yu = pt_use
            draw.ellipse((xu - r, yu - r, xu + r, yu + r), outline='red', width=3)
            draw.text((xu + r + 8, yu - r - 12), 'USE btn', fill='red')
        im.save(path)
    except Exception:
        pass
    print(f'  [music] 截图(已画红圈): {path}')
    return True


def click_publish(commit: bool = False) -> bool:
    """定位底部「发布」按钮.

    commit=False (默认 dry-run): 定位到坐标 + 截图标记, 但不点击.
    commit=True: 真点击 + 等待跳转.

    发布按钮在 creator 发布页【表单容器底部】, 默认视口外要先滚到底.
    用鼠标滚轮 (在页面中央位置滚), End/PageDown 键在某些 scrollable container 不生效.
    """
    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, int(sh * 0.5))
    time.sleep(0.2)
    for _ in range(10):
        pyautogui.scroll(-500)
        time.sleep(0.12)
    time.sleep(0.8)
    print('  [publish] 已滚到表单底部, 开始定位发布按钮')

    # region 必须包含屏幕右侧: 2026-06-23 1920×1080 实测发布按钮在右下角 (~x=1700+),
    # 旧 region 右边界 0.70 直接砍掉真按钮区域, VL 被迫从左半选错元素到 (647,931).
    # 主 region = 右半屏底 20% (publish 一定在这里, 存草稿在它左边但仍在 region 内).
    pt = locate(
        '抖音发布页【底部操作栏】右下角的【红色「发布」主按钮】(实心红色填充背景, 白字「发布」). '
        '它在屏幕最右下, 是所有底部按钮里【最右边那个红色】的. '
        '区分: 左边是灰色「存草稿/暂存离开」按钮(不要选, 是灰色不是红色).',
        region=(0.50, 0.80, 1.00, 1.00),
    )
    if pt is None:
        # fallback: 整个底部条 (左半也含进来防 1366×768 这种特殊分辨率)
        pt = locate(
            '抖音 creator 发布页【底部操作栏】里的【红色「发布」主按钮】(实心红色填充, '
            '白字「发布」), 屏幕最右下. 注意: 不要选灰色「存草稿/暂存离开」按钮, '
            '不要选左侧菜单栏的"发布作品"链接, 不要选页面顶部 logo.',
            region=(0.00, 0.75, 1.00, 1.00),
        )
    if pt is None:
        return False
    if not commit:
        print(f'  [publish DRY-RUN] 发布按钮定位到 ({pt[0]},{pt[1]}), 未点击')
        path, _ = _shot('_publish_btn_dryrun.png')
        # 在截图上画红圈圈出定位点 (Windows 截图不抓鼠标光标, 不画用户看不到)
        try:
            from PIL import Image, ImageDraw
            im = Image.open(path)
            draw = ImageDraw.Draw(im)
            r = 50
            x, y = pt
            draw.ellipse((x - r, y - r, x + r, y + r), outline='red', width=6)
            draw.line((x - r - 20, y, x + r + 20, y), fill='red', width=3)
            draw.line((x, y - r - 20, x, y + r + 20), fill='red', width=3)
            draw.text((x + r + 10, y - r - 20), f'PUBLISH? ({x},{y})', fill='red')
            im.save(path)
            print(f'  [publish DRY-RUN] 截图(已画红圈): {path}')
        except Exception as e:
            print(f'  [publish DRY-RUN] 画圈失败 ({e}), 截图原图: {path}')
        return True
    pyautogui.click(pt[0], pt[1])
    print(f'  [publish COMMIT] 已点发布 @ ({pt[0]},{pt[1]}), 等 {PUBLISH_WAIT}s 跳转')
    time.sleep(PUBLISH_WAIT)

    # 验证码兜底: 抖音 web 自动化发布会偶发滑块/数字/SMS 验证码
    # 检测到就暂停等用户人工过 + Enter 续跑. 无人值守版后续加 Lark 告警.
    _detect_and_pause_for_captcha()

    return True


def _detect_and_pause_for_captcha() -> None:
    """点发布后检测验证码弹窗. 弹了 print 提示 + input() 阻塞等用户 Enter."""
    path, _ = _shot('_after_publish_check.png')
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    prompt = (
        '这是抖音创作服务平台点击「发布」按钮之后的截图. 判断:'
        '页面上是否弹出了【验证码弹窗】(可能形式: 滑块拖动 / 数字识别 / 选字识别 / '
        '手机号短信验证 / "请完成安全验证" / "拖动滑块" / "点击图中..." 等)? '
        '注意: 普通的"发布成功"提示 / 作品列表页 / 普通表单都不算验证码.'
        '只回严格JSON:{"has_captcha":true/false,"type":"slider/number/sms/none","seen_text":"..."}'
    )
    try:
        d = _pjson(_vision(b64, prompt))
    except Exception as e:
        print(f'  [captcha] 检测失败 ({type(e).__name__}: {e}), 假定无验证码继续', file=sys.stderr)
        return

    if not d.get('has_captcha'):
        print(f'  [captcha] 未检测到验证码 (seen: {str(d.get("seen_text", ""))[:50]})')
        return

    captcha_type = d.get('type', 'unknown')
    seen_text = d.get('seen_text', '')
    print('', file=sys.stderr)
    print(f'  ⚠️  【验证码弹窗】({captcha_type}) — 检测到: {seen_text}', file=sys.stderr)
    print('  ⚠️  请在 Edge 里手动过完验证码 (滑块拖到底/点字/输数字等) → 看到发布成功后', file=sys.stderr)
    print('  ⚠️  回到这里按 [Enter] 续跑 verify_published 步骤', file=sys.stderr)

    # P3 #8: 推 Lark 告警让运营 30s 内回云电脑过验证码 (不影响主流程)
    _push_lark_captcha_alert(captcha_type, seen_text)

    try:
        input('  等待 [Enter] 续跑... ')
    except (KeyboardInterrupt, EOFError):
        print('  [captcha] 用户取消, abort', file=sys.stderr)
        raise SystemExit(2)
    print('  [captcha] 已续跑')


def _push_lark_captcha_alert(captcha_type: str, seen_text: str) -> None:
    """检测到验证码 → 推 Lark 卡到 LARK_WEBHOOK_AKKE_BOT 让运营 30s 内回去过.

    Best-effort: webhook 没配或推送失败都不影响主流程 (input() 等运营 Enter 续跑).
    """
    webhook = os.environ.get('LARK_WEBHOOK_AKKE_BOT', '').strip()
    if not webhook:
        return  # webhook 没配, silent skip (本地测试常态)
    if not webhook.startswith('http'):
        webhook = f'https://open.larksuite.com/open-apis/bot/v2/hook/{webhook}'

    payload = {
        'msg_type': 'interactive',
        'card': {
            'header': {
                'title': {'tag': 'plain_text', 'content': '⚠️ 云电脑图文发布卡验证码'},
                'template': 'red',
            },
            'elements': [
                {
                    'tag': 'div',
                    'text': {
                        'tag': 'lark_md',
                        'content': (
                            f'**类型**: {captcha_type}\n'
                            f'**抖音提示**: {seen_text or "(无)"}\n\n'
                            f'**云电脑 agent 已暂停**, 请尽快回云电脑 (creator.douyin.com Edge 窗口)'
                            f'手动过完验证码 + 回 PowerShell 按 [Enter] 续跑.\n\n'
                            f'若 5 分钟内未过, agent 会一直挂着等; 实在过不去可在 PowerShell Ctrl+C 让 row 失败'
                            f'(30 分钟后 RPC 自动回 approved, 可重试).'
                        )
                    }
                },
                {
                    'tag': 'note',
                    'elements': [
                        {'tag': 'plain_text', 'content': '🤖 cloudpc-tuwen / creator_publisher.py'}
                    ]
                }
            ]
        }
    }
    try:
        req = urllib.request.Request(
            webhook,
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f'  [captcha] Lark 告警已推送 (HTTP {resp.status})')
    except Exception as e:
        print(f'  [captcha] Lark 告警推送失败 ({type(e).__name__}: {e}), 不影响主流程', file=sys.stderr)


def verify_published(title_prefix: str) -> dict:
    """彻底修版 (2026-06-25): 多信号校验, 不再单 VL 看 toast 那一闪而过.

    历史: 06-24 真发 3 行实际成功但 daily-report 显示 3 ❌ failed (exit 8) — VL
    截 "发布成功" toast 经常错过 (toast 显示 1-2s 就消失, VL 截图常落在跳转后页面
    上, 没识别为 "发布成功" 字样, 返回 published=false). 实际作品已经在 creator
    后台「作品管理」里了.

    新版优先信号:
      ① URL 检测 (最可靠): 发布成功后 Edge 跳到 creator-micro/content/manage 或
         work/finish 等. 用 Ctrl+L → Ctrl+C 读地址栏 URL 判定. 浏览器跳转 100% 准.
      ② VL 看页面 (兜底): 是否离开了发布页 (没有大图片上传区 + 没有发布按钮).
         离开 = 提交成功 (跳转中). 比看 toast 准很多.
      ③ 老 VL 看 toast (二次兜底): 万一前两个都炸了再退到老逻辑.
    任一 hit = published. 都 miss = 真失败.
    """
    import pyperclip

    # ── 信号 1: URL via Ctrl+L → Ctrl+C ───────────────────────────────
    time.sleep(2)  # 等跳转完成 (creator 后台跳转通常 < 1.5s)
    url = ''
    try:
        pyperclip.copy('')  # 清空 clipboard, 防读到旧内容
        time.sleep(0.15)
        pyautogui.hotkey('ctrl', 'l'); time.sleep(0.35)  # 焦点到地址栏 + 全选
        pyautogui.hotkey('ctrl', 'c'); time.sleep(0.4)
        pyautogui.press('escape'); time.sleep(0.15)  # 取消地址栏聚焦
        url = pyperclip.paste().strip()
    except Exception as e:
        print(f'  [verify] URL 信号读取失败 ({type(e).__name__}: {e}), 走 VL 兜底', file=sys.stderr)

    print(f'  [verify] URL = {url[:120]}')

    # creator 后台发完会跳的几种 URL pattern, 任一 hit = 成功:
    URL_SUCCESS_PATTERNS = (
        '/creator-micro/content/manage',  # 作品管理列表
        '/work/detail',                    # 作品详情页
        '/work/finish',                    # 发布完成页
        '/upload/finish',                  # 上传完成页
    )
    for p in URL_SUCCESS_PATTERNS:
        if p in url:
            return {'published': True, 'method': 'url', 'url': url[:120]}

    # ── 信号 2: VL 看是不是离开了发布页 (兜底) ────────────────────────
    path, _ = _shot('_after_publish.png')
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    prompt_left = (
        '这是抖音创作服务平台 (creator.douyin.com) 的某个页面截图. 判断:'
        '页面是否【还在发布作品页】 (页面有大块"图片上传区/拖拽上传"区域, 或者底部有红色「发布」按钮)?'
        '如果页面已经离开发布作品页 (例如跳到「作品管理」列表 / 作品详情 / 数据中心 / 创作中心首页等), 回 false.'
        '只回严格JSON: {"on_publish_page": true/false, "reason": "..."}'
    )
    try:
        d = _pjson(_vision(b64, prompt_left))
        if d.get('on_publish_page') is False:
            return {'published': True, 'method': 'vl-left-page', 'url': url[:120], 'detail': d}
    except Exception as e:
        print(f'  [verify] VL 信号 2 失败 ({type(e).__name__}: {e}), 走老兜底', file=sys.stderr)

    # ── 信号 3 (二次兜底): 老 VL 看 toast 逻辑 ─────────────────────────
    prompt_old = (
        '这是抖音创作服务平台发布完图文之后的截图. 判断:'
        '(a) 是否成功发布(页面有「发布成功」/「作品已发布」提示, 或跳到作品列表/详情页)?'
        f'(b) 如果跳到了作品列表, 能否看到一条标题以「{title_prefix[:12]}」开头的最新作品?'
        '只回严格JSON:{"published":true/false,"title_seen":"...","confidence":0~1}'
    )
    try:
        d = _pjson(_vision(b64, prompt_old))
        d['method'] = 'vl-toast-fallback'
        d['url'] = url[:120]
        return d
    except Exception as e:
        return {'published': False, 'error': f'{type(e).__name__}: {e}', 'method': 'all-failed', 'url': url[:120]}


# ---------- 入口 ----------

def _load_manifest(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _download_image_urls(urls: list[str]) -> list[str]:
    """把远程图 URL 列表下到本地 staging 目录, 返回本地路径列表.

    路径必须极短: Windows 文件选择对话框「文件名」输入框 ~260 字符上限,
    6 张长路径 "C:\\akke-wuying\\tuwen-staging\\20260622_181908\\V3VRP-1.png" * 6
    = ~337 字符撞限只接收前 4 个 (2026-06-22 实测踩坑). 改用:
      base   = C:\\tw  (env AKKE_TUWEN_STAGING 覆盖, 默认 C:\\tw)
      sub    = HHMMSS (6 字符)
      name   = <i>.png (1.png/2.png... 直接序号, 不保留 paste.rs hash)
    一张全路径 ~22 字符, 6 张总 ~160 字符 << 260 安全.
    """
    import urllib.request
    from datetime import datetime as _dt

    base = Path(os.environ.get('AKKE_TUWEN_STAGING') or 'C:/tw')
    staging = base / _dt.now().strftime('%H%M%S')
    staging.mkdir(parents=True, exist_ok=True)
    print(f'  [staging] 下载 {len(urls)} 张图到 {staging}')

    local_paths = []
    for i, url in enumerate(urls, 1):
        name = f'{i}.png'  # 不保留 paste.rs hash, 直接序号防路径过长
        dst = staging / name
        # 重试 4 次指数退避 (paste.rs 偶发 503/断流, 实测 6 张图第 2 张挂过)
        last_err = None
        for attempt in range(1, 5):
            try:
                if dst.exists():
                    dst.unlink()
                # urlretrieve 默认无 timeout 会永久 hang (实测 paste.rs SSL read 卡死).
                # 换 urlopen(timeout=30) + read 全字节, 强制 30s 内必返回否则抛 TimeoutError.
                with urllib.request.urlopen(url, timeout=30) as resp:
                    dst.write_bytes(resp.read())
                actual = dst.stat().st_size
                print(f'    #{i} {url[:60]}... → {dst.name} ({actual} bytes, attempt {attempt})')
                last_err = None
                break
            except Exception as e:
                last_err = e
                wait = 2 * attempt
                print(f'    #{i} attempt {attempt} 失败 ({type(e).__name__}: {e}), {wait}s 后重试', file=sys.stderr)
                import time as _t
                _t.sleep(wait)
        if last_err is not None:
            print(f'    #{i} 4 次重试全失败, raise', file=sys.stderr)
            raise last_err
        local_paths.append(str(dst))
    return local_paths


def _cleanup_staging(image_paths: list[str]) -> None:
    """删除 staging 目录 (从下载的图路径推出 parent).

    安全护栏: 只删路径含 'tw' 或 'tuwen-staging' 的目录, 防误删用户本地路径.
    main 末尾 try/finally 调用, 保证不管成功/失败/异常都清.
    """
    import shutil
    if not image_paths:
        return
    staging = Path(image_paths[0]).parent
    s = str(staging).replace('\\', '/').lower()
    if not ('/tw/' in s or 'tuwen-staging' in s):
        print(f'  [cleanup] 跳过 (非 staging 目录: {staging})', file=sys.stderr)
        return
    try:
        shutil.rmtree(staging, ignore_errors=True)
        print(f'  [cleanup] 已删 staging: {staging}')
    except Exception as e:
        print(f'  [cleanup] 删除失败 (忽略): {e}', file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description='抖音 creator.douyin.com 发图文 PoC')
    p.add_argument('--manifest', help='JSON 文件: {title, body, images[]}')
    p.add_argument('--title')
    p.add_argument('--body')
    p.add_argument('--images', help='逗号分隔的图片路径')
    p.add_argument('--commit', action='store_true', help='真发 (不加默认 dry-run)')
    p.add_argument('--skip-focus', action='store_true', help='不调 focus_douyin')
    p.add_argument('--schedule', help='定时发布时间 "YYYY-MM-DD HH:MM" (现在 +2h ~ +14天 内). 只挑日期, 小时分钟用 creator 默认 +2h. 不传则立即发布.')
    p.add_argument('--no-music', action='store_true', help='不加音乐 (默认加第一首推荐)')
    args = p.parse_args()

    if args.schedule:
        from datetime import datetime as _dt, timedelta as _td
        try:
            _at = _dt.strptime(args.schedule, '%Y-%m-%d %H:%M')
        except ValueError:
            print(f'ERROR: --schedule 必须是 "YYYY-MM-DD HH:MM" 格式, 给的: {args.schedule}', file=sys.stderr)
            return 2
        _now = _dt.now()
        if _at < _now + _td(hours=2):
            print(f'ERROR: --schedule 太早, 要求 ≥ 现在+2h = {(_now + _td(hours=2)).strftime("%Y-%m-%d %H:%M")}', file=sys.stderr)
            return 2
        if _at > _now + _td(days=14):
            print(f'ERROR: --schedule 太晚, 要求 ≤ 现在+14天 = {(_now + _td(days=14)).strftime("%Y-%m-%d %H:%M")}', file=sys.stderr)
            return 2

    downloaded_from_urls = False
    if args.manifest:
        m = _load_manifest(args.manifest)
        title = m['title']
        body = m['body']
        # image_urls (远程 URL 数组) 优先于 images (本地路径).
        # 有 image_urls 就 curl 下载到 staging 目录, 再当作本地路径用 (复用文件对话框 paste 流程).
        if m.get('image_urls'):
            images = _download_image_urls(m['image_urls'])
            downloaded_from_urls = True
        else:
            images = m.get('images', [])
        # manifest 内 --schedule 也允许覆盖 CLI (manifest 里写 schedule_at 字段)
        if m.get('schedule_at') and not args.schedule:
            args.schedule = m['schedule_at']
    else:
        if not (args.title and args.body and args.images):
            p.error('需要 --title --body --images 或 --manifest')
        title = args.title
        body = args.body
        images = [s.strip() for s in args.images.split(',') if s.strip()]

    for path in images:
        if not Path(path).exists():
            print(f'ERROR: 图不存在: {path}', file=sys.stderr)
            return 2
    if not (1 <= len(images) <= 18):
        print(f'ERROR: 图片张数 {len(images)} 不在 1-18 范围', file=sys.stderr)
        return 2
    if len(title) > 30:
        print(f'WARN: 标题 {len(title)} 字 > 20 字限制 (会被截或拒)', file=sys.stderr)

    print('===========================================')
    print('抖音 creator 发图文 PoC')
    print('===========================================')
    print(f'  title:  {title}')
    print(f'  body:   {body[:60]}{"..." if len(body) > 60 else ""}')
    print(f'  images: {len(images)} 张')
    for i, ip in enumerate(images, 1):
        print(f'    #{i}: {ip}')
    print(f'  mode:   {"COMMIT (真发)" if args.commit else "DRY-RUN (不点发布)"}')
    print()

    try:
        # 步 -1: 清残留模态 (上次失败 loop 留下的 Windows 文件选择对话框等会吞键盘 → 这次 Ctrl+V/Enter 全发不进新页面).
        # 2026-06-24 烟测踩坑: 第 1 次 loop 失败后文件选择器停留, 第 2 次 loop click_upload_button 没弹新对话框,
        # paste_image_paths 的 Ctrl+V/Enter 进了旧对话框被吞, 后续 fill_title NOT FOUND → exit 5.
        # 修: 起手压 3 下 Esc, 让任何系统级/页面级 modal 关掉, 再开 fresh 流程.
        print('[step -1] 清残留模态 (Esc x3)')
        for _ in range(3):
            pyautogui.press('escape')
            time.sleep(0.2)

        # 步 0
        if not args.skip_focus:
            print('[step 0] focus Edge/抖音窗口')
            if not focus_douyin():
                print('  → focus 失败. 请手动把 Edge (打开了 creator.douyin.com 的标签) 置前后, 加 --skip-focus 重跑',
                      file=sys.stderr)
                return 3

        # 步 1
        print('[step 1] 直达 creator 发布页')
        goto_creator_upload(wait=6.0)

        # 步 2 (可选 - 图文 tab)
        print('[step 2] 切到「图文」tab (可能默认就在, 失败不阻断)')
        click_image_tab()

        # 步 3
        print('[step 3] 点上传区 → 弹文件对话框')
        if not click_upload_button():
            print('  ERROR: 上传按钮没找到. 截图 screenshots/_loc.png 看是不是页面没 load 完 / VL prompt 不对',
                  file=sys.stderr)
            return 4

        # 步 3.5
        paste_image_paths(images)

        # 步 4
        print('[step 4] 填标题')
        if not fill_title(title):
            print('  ERROR: 标题输入框没找到', file=sys.stderr)
            return 5

        # 步 5
        print('[step 5] 填正文')
        if not fill_body(body):
            print('  ERROR: 正文输入框没找到', file=sys.stderr)
            return 6

        # 步 5.3 (默认加音乐, --no-music 跳过)
        if not args.no_music:
            print('[step 5.3] 加音乐 (选第一首推荐)')
            if not add_music():
                print('  WARN: 加音乐失败 (面板/选项没定位到). 不阻断, 继续下一步.', file=sys.stderr)
                # 不 return — 音乐对图文非必填, abort 太苛刻

        # 步 5.5 (仅 --schedule)
        if args.schedule:
            print(f'[step 5.5] 设定时发布: {args.schedule}')
            if not set_schedule(args.schedule):
                print('  ERROR: 设定时失败 (定时单选/时间框/datepicker 一处没定位到)', file=sys.stderr)
                return 65

        # 步 6
        print(f'[step 6] {"真发" if args.commit else "DRY-RUN"}')
        if not click_publish(commit=args.commit):
            print('  ERROR: 发布按钮没找到', file=sys.stderr)
            return 7

        # 步 7 (仅 commit)
        if args.commit:
            print('[step 7] 校验已发布 (VL 看跳转后页面)')
            result = verify_published(title)
            print(f'  result: {result}')
            if result.get('published'):
                print('=== ✓ 真发成功 ===')
                return 0
            print('=== ⚠ 已点发布但未确认结果 (VL 没认出来, 自己再去 creator 后台核对) ===')
            return 8
        print('=== ✓ DRY-RUN 全程通, 加 --commit 真发 ===')
        return 0
    finally:
        # 双保险: 退出前再 Esc x3 收尾, 防失败路径的对话框堆给下一次 subprocess 启动.
        # (起手的 Esc 是清上次的; 这里的 Esc 是不让自己的失败影响下次.)
        try:
            for _ in range(3):
                pyautogui.press('escape')
                time.sleep(0.15)
        except Exception:
            pass

        # 清理 staging: 仅当 manifest 含 image_urls 触发下载时清, 用户自带本地 --images 不动.
        # 保证不管 commit/dry-run/失败/异常都执行, 避免 staging 堆积占盘.
        if downloaded_from_urls and images:
            _cleanup_staging(images)


if __name__ == '__main__':
    sys.exit(main())
