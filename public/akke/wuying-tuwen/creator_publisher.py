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
# 视频通路 (2026-07-08 视频批量分发线): 不带 default-tab, creator 落地默认就是「发布视频」tab
CREATOR_UPLOAD_URL_VIDEO = os.environ.get(
    'AKKE_CREATOR_UPLOAD_URL_VIDEO',
    'https://creator.douyin.com/creator-micro/content/upload',
)
PUBLISH_WAIT = int(os.environ.get('AKKE_TUWEN_PUBLISH_WAIT', '5'))
UPLOAD_WAIT = int(os.environ.get('AKKE_TUWEN_UPLOAD_WAIT', '8'))
# 视频上传+转码比图片慢得多 (几十 MB), 用 VL 轮询判完成, 这里是硬超时
VIDEO_UPLOAD_TIMEOUT = int(os.environ.get('AKKE_TUWEN_VIDEO_UPLOAD_TIMEOUT', '300'))
VIDEO_UPLOAD_POLL = int(os.environ.get('AKKE_TUWEN_VIDEO_UPLOAD_POLL', '15'))


# ---------- 流程步骤 ----------

def goto_creator_upload(wait: float = 5.0, url: str | None = None) -> None:
    """Edge 地址栏 Ctrl+L 直达 creator 发布页 (默认图文 tab; 视频通路传 CREATOR_UPLOAD_URL_VIDEO).

    URL 用 type_unicode 注入绕开中文 IME (PC 版 DM 通道踩过坑, type_text 会被吞字).
    """
    url = url or CREATOR_UPLOAD_URL
    print(f'  [nav] Ctrl+L → {url}')
    pyautogui.hotkey('ctrl', 'l'); time.sleep(0.8)
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.2)
    pyautogui.press('delete'); time.sleep(0.2)
    type_unicode(url); time.sleep(0.5)
    pyautogui.press('enter')
    # 上一条任务中途失败会留脏表单 (图已传 / 草稿未存); Ctrl+L 跳转时浏览器会弹
    # 「离开此网站?未保存的更改将丢失」确认框. 不处理就卡在旧脏页 → 本条 tab/上传区
    # NOT FOUND (exit 4 连锁, 2026-06-29 夏夏日志实测). Enter = 默认「离开」按钮, 放行跳转.
    # ⚠ 绝不能用 Esc 关这个框 (Esc = 取消 = 留在脏页, 反而坐实连锁失败).
    time.sleep(1.2)
    pyautogui.press('enter')
    time.sleep(wait)


def locate_retry(desc, region=None, tries=3, wait=1.4):
    """locate 带重试: 每次重新截图再问 VL.

    治两类瞬时失败: ① 页面/popover 还没渲染完 (NOT FOUND) ② VL 对小字偶发假阴性
    (尤其 2560 高分屏小字, 见 douyin_dm_grounded._shot_crop 注释). 单次 locate 无重试
    是 2026-06-29 大面积 exit 4/5/65 的放大器之一.
    """
    for k in range(tries):
        pt = locate(desc, region=region)
        if pt is not None:
            return pt
        if k < tries - 1:
            print(f'  retry locate ({k + 1}/{tries}) in {wait:.1f}s...')
            time.sleep(wait)
    return None


def click_image_tab() -> bool:
    """页面顶部 tab: 「发布视频」/「发布图文」. 视觉定位「图文」tab 并点击.

    部分入口默认就是图文 tab (URL default-tab=3 已经选了), 找不到也不抛错;
    后续上传按钮失败再回头报错.
    """
    pt = locate_retry(
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
    pt = locate_retry(
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


def click_video_tab() -> bool:
    """视频通路: 确认/切到「发布视频」tab. creator 上传页默认落在视频 tab, 找不到不阻断."""
    pt = locate_retry(
        '抖音创作服务平台发布页【顶部 tab 栏】里的「发布视频」或「上传视频」tab 按钮('
        '通常是第一个 tab, 在「发布图文」tab 旁边). 不要选「发布图文」, 不要选「发布全景视频」.',
        region=(0.10, 0.05, 0.90, 0.30),
        tries=2,
    )
    if pt is None:
        print('  [tab] 没定位到「发布视频」tab → 假设默认已在视频 tab, 继续')
        return False
    pyautogui.click(pt[0], pt[1])
    time.sleep(1.8)
    print(f'  [tab] 已点「发布视频」tab @ ({pt[0]},{pt[1]})')
    return True


def click_video_upload_button() -> bool:
    """视频通路: 页面中央大块「点击上传/拖拽视频」上传区. 点击弹 Windows 文件对话框."""
    pt = locate_retry(
        '抖音发布页正文区中央的【视频上传按钮/拖拽区域】(大块虚线边框, '
        '里面有「点击上传」或「将视频文件拖入此区域」文案, 或者一个云朵/上传图标). '
        '注意不要选页面顶部的 tab 按钮.',
        region=(0.15, 0.20, 0.85, 0.80),
    )
    if pt is None:
        return False
    pyautogui.click(pt[0], pt[1])
    print(f'  [upload] 已点视频上传区 @ ({pt[0]},{pt[1]}), 等文件对话框弹出')
    time.sleep(2.8)
    return True


def wait_video_uploaded(timeout_sec: int = None) -> bool:
    """视频通路: VL 轮询等视频上传+预处理完成.

    完成信号 = 页面出现视频预览播放器 / 「重新上传」按钮 / 封面选择区 —— 上传中只有
    进度百分比. 每 VIDEO_UPLOAD_POLL 秒问一次 VL, 超 VIDEO_UPLOAD_TIMEOUT 判失败.
    """
    timeout_sec = timeout_sec or VIDEO_UPLOAD_TIMEOUT
    t0 = time.time()
    print(f'  [upload] 等视频上传完成 (VL 轮询, 每 {VIDEO_UPLOAD_POLL}s, 超时 {timeout_sec}s)')
    while time.time() - t0 < timeout_sec:
        time.sleep(VIDEO_UPLOAD_POLL)
        pt = locate(
            '抖音发布页里【视频已上传完成】的标志元素, 任选其一: '
            '「重新上传」按钮 / 视频预览播放器(有播放键的视频缩略图) / 「选择封面」或封面候选图区域. '
            '如果页面还在显示上传进度百分比或转码中, 就是 NOT FOUND.',
            region=(0.10, 0.10, 0.95, 0.90),
        )
        if pt is not None:
            print(f'  [upload] 视频上传完成 (耗时 {time.time() - t0:.0f}s)')
            return True
        print(f'  [upload] 还在上传/转码... ({time.time() - t0:.0f}s)')
    print(f'  [upload] 超时 {timeout_sec}s 视频仍未上传完成', file=sys.stderr)
    return False


def paste_image_paths(image_paths: list[str]) -> None:
    """Windows 文件选择对话框: 多文件路径用空格分隔 + 双引号包裹, paste 进文件名输入框 + 回车.

    例: "C:\\a\\1.png" "C:\\a\\2.png"
    对话框打开后焦点默认在文件名输入框, 不用再点.
    """
    path_str = ' '.join(f'"{p}"' for p in image_paths)
    print(f'  [picker] 输入 {len(image_paths)} 个路径: {path_str[:120]}...')
    # 文件对话框焦点默认在文件名框. Windows 会把【上次的文件名】预填进去, 先 Ctrl+A + Delete 清掉.
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.2)
    pyautogui.press('delete'); time.sleep(0.2)
    # 用 SendInput 逐字符键入路径, 【不走剪贴板】.
    # 2026-06-29 实测: 无影/云电脑上 pyperclip + Ctrl+V 粘进去是空的 → 对话框卡死、4 张图一张没传 →
    # 后续 fill_title NOT FOUND → exit 5。DM 通道早有结论「SendInput 是无影上唯一可靠输入」
    # (见 douyin_dm_grounded.type_text 注释), 图文这块此前漏改。回退保留剪贴板兜底。
    try:
        type_unicode(path_str)
    except Exception as e:
        print(f'  [picker] SendInput 键入失败({e}), 回退剪贴板', file=sys.stderr)
        pyperclip.copy(path_str); time.sleep(0.4); pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.6)
    pyautogui.press('enter')
    print(f'  [picker] 回车提交, 等图片上传 {UPLOAD_WAIT}s')
    time.sleep(UPLOAD_WAIT)


def fill_title(title: str, video: bool = False) -> bool:
    """「作品标题」输入框 (图文限 20 字, 视频限 30 字).

    视频页坑 (2026-07-08 夏夏 PoC): 标题框和描述框上下紧挨, VL 易点到框上方空白 →
    键入落空. 对策: 收紧 region 到标题行那一带 + 明确"点在占位文字上" + 点两下确保
    caret 进框.
    """
    if video:
        desc = ('抖音发布视频页【基础信息】区里最上面那个【作品标题输入框】: 单行, '
                '当前显示灰色占位文字「填写作品标题，为作品获得更多流量」, 右上角有「0/30」字数统计. '
                '请把点选目标定在那行占位文字正中间. 不要选它下方那个更大的「添加作品简介」多行描述框.')
        # 标题行在页面上部窄带 (约屏高 18%~32%), 收紧防点到上方 header 空白
        region = (0.28, 0.16, 0.78, 0.34)
    else:
        desc = ('抖音发布页里的【作品标题输入框】(单行输入框, 上方或左侧有「标题」字样, '
                '通常在图片预览区下方、正文描述框上方, 字数限制 20 字). '
                '不要选下方的多行正文描述框.')
        region = (0.15, 0.20, 0.85, 0.60)
    pt = locate_retry(desc, region=region)
    if pt is None:
        return False
    # 视频页点两下: 第一下有时只 hover/选中占位, 第二下才真正把 caret 落进 input
    pyautogui.click(pt[0], pt[1]); time.sleep(0.25)
    if video:
        pyautogui.click(pt[0], pt[1]); time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.1)
    pyautogui.press('delete'); time.sleep(0.3)
    type_unicode(title); time.sleep(0.8)
    print(f'  [title] 已填: {title[:30]}')
    return True


def fill_body(body: str, video: bool = False) -> bool:
    """「作品描述/正文」多行输入框 + 话题标签确认.

    #xxx 话题在抖音 creator 输入时会弹下拉建议浮层(列热门话题 + 热度数),
    必须按 Enter 选第一个建议才会把 #xxx 转成蓝色话题链接; 不选则只是普通文本.
    所以 body 不是一次性键入, 而是按 # 分段键入, 每个 #xxx 后等浮层 + Enter.
    """
    if video:
        desc = ('抖音发布视频页【基础信息】区里那个大的【作品简介/描述输入框】: 多行文本区, '
                '当前显示灰色占位文字「添加作品简介」, 底部有「#添加话题」「@好友」按钮和「0/1000」统计. '
                '请把点选目标定在「添加作品简介」占位文字上. 不要选它上方那个单行的标题框(0/30 那个).')
        # 描述框在标题下方 (约屏高 26%~46%)
        region = (0.28, 0.24, 0.78, 0.48)
    else:
        desc = ('抖音发布页里的【作品描述/正文输入框】(多行大文本区域, 在标题输入框下方, '
                '可能带「输入内容, 让更多人看到吧」或「分享此刻的想法」占位文字, '
                '支持 # 话题 / @ 提及). 不要选上方单行的标题框.')
        region = (0.15, 0.25, 0.85, 0.75)
    pt = locate_retry(desc, region=region)
    if pt is None:
        return False
    pyautogui.click(pt[0], pt[1]); time.sleep(0.3)
    if video:
        # 视频页描述框同标题: 点两下确保 caret 进 contenteditable
        pyautogui.click(pt[0], pt[1]); time.sleep(0.3)
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


def _verify_schedule_value(at_str: str) -> bool:
    """VL 看【定时发布时间框】是否已显示目标时间 — 判断"直接键入"是否生效."""
    try:
        path, (W, H) = _shot('_schedule_verify.png')
        from PIL import Image
        with Image.open(path) as im:
            crop = im.crop((int(0.25 * W), int(0.50 * H), int(0.75 * W), int(0.95 * H)))
            cpath = os.path.join('screenshots', '_schedule_verify_crop.png')
            crop.save(cpath)
        b64 = base64.b64encode(open(cpath, 'rb').read()).decode()
        prompt = (
            '这是抖音创作发布页的定时发布设置区截图. '
            '判断【定时发布时间输入框】里显示的日期时间, 是否就是 "%s" '
            '(年-月-日 和 时:分 都要一致, 允许秒位/格式细微差异). '
            '只回严格JSON: {"match": true 或 false}' % at_str
        )
        d = _pjson(_vision(b64, prompt))
        return bool(d.get('match'))
    except Exception as e:
        print(f'  [schedule] 键入校验异常 ({type(e).__name__}: {e}), 保守当未生效', file=sys.stderr)
        return False


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
    # 1. 滚到发布设置段 (滚到底, 发布设置是最后一段 · 就在发布按钮上方, 不会滚过头).
    # 视频页表单比图文长, 14 轮保证到底; 图文页多滚几轮到底后 no-op 无害.
    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, int(sh * 0.5))
    time.sleep(0.2)
    for _ in range(14):
        pyautogui.scroll(-500)
        time.sleep(0.12)
    time.sleep(0.6)
    print('  [schedule] 已滚到发布设置段')

    # 2. 点「定时发布」单选按钮
    pt = locate_retry(
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
    pt_box = locate_retry(
        '抖音 creator 发布页里的【定时发布时间输入框】(细长输入框, 框里显示 "YYYY-MM-DD HH:MM" '
        '形如 "2026-06-22 19:05" 的默认时间, 右边带日历图标), 在「定时发布」单选按钮右边.',
        region=(0.25, 0.55, 0.70, 0.95),
    )
    if pt_box is None:
        return False
    pyautogui.click(pt_box[0], pt_box[1])
    time.sleep(1.2)
    print(f'  [schedule] 已点时间输入框 @ ({pt_box[0]},{pt_box[1]})')

    # 4. 【优先】直接往时间框键入完整日期时间 — 绕开最脆的"日历点日期格".
    # 2026-06-29 夏夏日志实测: 翻月/点日期格 VL 常 miss (exit 65), 而时间框多支持直接 type.
    # 键入还能顺带把【时分】也设准 (旧日历路径只点日期, 时间留 creator 默认 +2h, 不精确).
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.2)
    pyautogui.press('delete'); time.sleep(0.3)
    type_unicode(at_str); time.sleep(0.6)
    pyautogui.press('enter'); time.sleep(0.8)
    # 点屏幕左侧空白处 blur, 持久化输入
    sw, sh = pyautogui.size()
    pyautogui.click(int(sw * 0.10), int(sh * 0.50)); time.sleep(0.6)
    if _verify_schedule_value(at_str):
        print(f'  [schedule] 直接键入生效 → {at_str}')
        _shot('_schedule_typed.png')
        return True
    print('  [schedule] 直接键入未生效, 回退【日历点选】路径', file=sys.stderr)

    # 4b. 回退: 重新点开时间框 → datepicker 翻月 → 点日期格 (全部带重试)
    pyautogui.click(pt_box[0], pt_box[1]); time.sleep(1.2)
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
        pt_next = locate_retry(
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

    # 点目标日期格
    target_day = at_dt.day
    pt = locate_retry(
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


def click_publish(commit: bool = False, video: bool = False) -> bool:
    """定位底部「发布」按钮.

    commit=False (默认 dry-run): 定位到坐标 + 截图标记, 但不点击.
    commit=True: 真点击 + 等待跳转.

    发布按钮在 creator 发布页【表单容器底部】, 默认视口外要先滚到底.
    用鼠标滚轮 (在页面中央位置滚), End/PageDown 键在某些 scrollable container 不生效.

    ⚠ 图文页 vs 视频页按钮位置不同 (2026-07-08 夏夏 PoC 实测):
      - 图文页: 「发布」在底部操作栏【右下角】
      - 视频页: 页面更长 (多了扩展信息/发布设置两段), 「发布」在底部【左中位置】,
        右边没东西; 灰色「暂存离开」在它右边. 用右半屏 region 会漏掉 → NOT FOUND.
    """
    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, int(sh * 0.5))
    time.sleep(0.2)
    # 视频页表单更长, 多滚几轮确保到底
    for _ in range(16 if video else 10):
        pyautogui.scroll(-500)
        time.sleep(0.12)
    time.sleep(0.8)
    print('  [publish] 已滚到表单底部, 开始定位发布按钮')

    if video:
        # 视频页: 发布按钮在底部【左中】, 灰色「暂存离开」在它右边
        pt = locate(
            '抖音发布视频页【最底部】的【红色「发布」主按钮】(实心红色填充背景, 白字「发布」). '
            '它在页面底部偏左位置, 右边紧挨着一个灰色「暂存离开」按钮. '
            '选那个红色实心的「发布」, 不要选灰色的「暂存离开」, 不要选左侧菜单栏的红色「高清发布」.',
            region=(0.12, 0.72, 0.75, 1.00),
        )
        if pt is None:
            pt = locate(
                '抖音发布视频页底部的红色「发布」按钮 (实心红底白字「发布」两个字). '
                '不要选灰色「暂存离开」, 不要选页面左上角侧栏的「高清发布」.',
                region=(0.00, 0.65, 1.00, 1.00),
            )
        if pt is None:
            return False
    else:
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


CAPTCHA_TIMEOUT_SEC = int(os.environ.get('AKKE_TUWEN_CAPTCHA_TIMEOUT_SEC', str(12 * 3600)))  # 默认 12h
CAPTCHA_PAGE_CHECK_INTERVAL_SEC = int(os.environ.get('AKKE_TUWEN_CAPTCHA_PAGE_CHECK_SEC', '60'))  # 每 60s VL 看一次


def _is_still_on_publish_page() -> bool:
    """VL 截图判断 Edge 是否仍在发布页 (有大块图上传区 / 底部有发布按钮).

    捕获 "运营自助过完 captcha + 自己点了发送按钮 → 页面跳走" 信号 — 无需运营回
    PowerShell 按 Enter. 真发后页面跳作品列表 / 数据中心等, 不再在发布页.

    返回 True = 还在发布页 (continue 等). 返回 False = 跳走了 (= 运营点过发送, 可以
    接 verify_published).

    失败 (VL 报错 / 截图失败) 保守返回 True, 继续等运营按 Enter.
    """
    try:
        path, _ = _shot('_captcha_pagecheck.png')
        b64 = base64.b64encode(Path(path).read_bytes()).decode()
        prompt = (
            '这是抖音创作服务平台 (creator.douyin.com) 的某个页面截图. 判断:'
            '页面是否【还在发布作品页】 (页面有大块"图片上传区/拖拽上传"区域, 或者底部有红色「发布」按钮)?'
            '注意: 验证码弹窗本身覆盖在发布页之上, 此时也算"还在发布页", 回 true.'
            '如果页面已经离开发布作品页 (例如跳到「作品管理」列表 / 作品详情 / 数据中心 / 创作中心首页等), 回 false.'
            '只回严格JSON: {"on_publish_page": true/false, "reason": "..."}'
        )
        d = _pjson(_vision(b64, prompt))
        on = d.get('on_publish_page')
        if on is False:
            print(f'  [captcha] VL 检测页面已离开发布页 — 运营点了发送, 自动接续. ({d.get("reason", "")[:60]})')
            return False
        return True
    except Exception as e:
        print(f'  [captcha] 页面检测失败 ({type(e).__name__}: {e}), 保守继续等 Enter', file=sys.stderr)
        return True


def _wait_for_captcha_done(timeout_sec: int) -> str:
    """暂停等运营过 captcha. 双信号检测, 任一 hit 就返回:
      - 信号 A: 黑窗口按 Enter → 返回 'enter'
      - 信号 B: VL 看到页面已离开发布页 (运营自助点了发送) → 返回 'page-left'
      - timeout_sec 超时 → 返回 'timeout' (agent 转 needs_review)

    每 CAPTCHA_PAGE_CHECK_INTERVAL_SEC 秒做一次 VL page check (默认 60s).
    每 5min 提醒剩余时间 (避免日志刷屏).
    """
    try:
        import msvcrt  # type: ignore[import-not-found]
        has_keyboard = True
    except ImportError:
        # 非 Windows fallback: 没法 polling 键盘, 退到纯 VL 检测
        msvcrt = None  # type: ignore
        has_keyboard = False

    deadline = time.time() + timeout_sec
    next_page_check_at = time.time() + CAPTCHA_PAGE_CHECK_INTERVAL_SEC
    next_reminder_at = time.time() + 300  # 每 5min 一次提醒
    mode = 'Enter / 自己点发送' if has_keyboard else 'VL 检测页面跳走'
    print(f'  [captcha] 等运营过 captcha (双信号: {mode}, 超时 {timeout_sec // 3600}h{(timeout_sec % 3600) // 60}m)', flush=True)

    while time.time() < deadline:
        # 信号 A: keyboard Enter
        if has_keyboard and msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ('\r', '\n'):
                print('  [captcha] 收到 Enter, 接续 verify_published')
                return 'enter'
            # 其它键吞掉

        # 信号 B: VL 看 page state (周期检测)
        if time.time() >= next_page_check_at:
            next_page_check_at = time.time() + CAPTCHA_PAGE_CHECK_INTERVAL_SEC
            if not _is_still_on_publish_page():
                return 'page-left'

        # 周期提醒
        if time.time() >= next_reminder_at:
            next_reminder_at = time.time() + 300
            remaining = int(deadline - time.time())
            h, m = remaining // 3600, (remaining % 3600) // 60
            print(f'  [captcha] 仍在等运营... 剩 {h}h{m}m', flush=True)

        time.sleep(0.2)

    return 'timeout'


def _detect_and_pause_for_captcha() -> None:
    """点发布后检测验证码弹窗. 弹了推 Lark 告警 + 等 Enter 续跑, 超时转 needs_review."""
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
    print(f'  ⚠️  回到这里按 [Enter] 续跑 (超时 {CAPTCHA_TIMEOUT_SEC}s)', file=sys.stderr)

    # 推 Lark 告警 (视频更新 bot 群 + @ 当前 assignee 邮箱). best-effort 不影响 wait.
    _push_lark_captcha_alert(captcha_type, seen_text)

    # 双信号等运营: 黑窗口 Enter / VL 看到页面已跳走 (运营自己点了发送) / 超时
    signal = _wait_for_captcha_done(CAPTCHA_TIMEOUT_SEC)
    if signal == 'timeout':
        h = CAPTCHA_TIMEOUT_SEC // 3600
        m = (CAPTCHA_TIMEOUT_SEC % 3600) // 60
        print(f'  [captcha] 超 {h}h{m}m 无人过 → abort 本 row 转 needs_review, 不挡后续作品', file=sys.stderr)
        raise SystemExit(10)
    print(f'  [captcha] 已续跑 (信号 = {signal})')


def _resolve_lark_at_tag_for_current_assignee() -> str:
    """读 AKKE_TUWEN_ASSIGNEE + LARK_USER_ID_MAP_JSON, 返回 <at email="..."></at> 真 @ 标签.

    没配 / 解析失败 → 退回纯文本 @{assignee} (Lark 自动匹配同名群成员).
    """
    assignee = os.environ.get('AKKE_TUWEN_ASSIGNEE', '').strip()
    if not assignee:
        return '@运营'
    try:
        raw = os.environ.get('LARK_USER_ID_MAP_JSON', '').strip()
        if raw:
            id_map = json.loads(raw)
            ident = id_map.get(assignee)
            if ident:
                if '@' in ident:
                    return f'<at email="{ident}"></at>'
                if ident.startswith('ou_'):
                    return f'<at user_id="{ident}"></at>'
    except Exception:
        pass
    return f'@{assignee}'


def _push_lark_captcha_alert(captcha_type: str, seen_text: str) -> None:
    """检测到验证码 → 推 Lark 卡到 LARK_WEBHOOK_TUWEN_AUTO (视频更新 bot 数据监控群).

    2026-06-26 改:
      - webhook LARK_WEBHOOK_AKKE_BOT → LARK_WEBHOOK_TUWEN_AUTO (所有 tuwen 卡统一走视频更新群)
      - 卡里 <at email="..."> 真 @ 当前 assignee (从 AKKE_TUWEN_ASSIGNEE + LARK_USER_ID_MAP_JSON 解析)
      - 提到 input timeout 后会自动 abort 转 needs_review, 不挡后续 row

    Best-effort: webhook / map 没配都不影响主流程 (input 仍按超时等运营 Enter).
    """
    webhook = os.environ.get('LARK_WEBHOOK_TUWEN_AUTO', '').strip()
    if not webhook:
        # 老 env 兼容: 没 LARK_WEBHOOK_TUWEN_AUTO 时退回 AKKE_BOT (老 .env 未更新时不静默掉)
        webhook = os.environ.get('LARK_WEBHOOK_AKKE_BOT', '').strip()
    if not webhook:
        # 2026-06-28: 修"跳验证码没提醒". 两个 webhook 都没配时, 之前 silent return — 验证码弹了
        # 运营完全无感知, 作品卡死在 needs_review. 现在至少大字打 stderr (进 agent 日志留痕),
        # 并明确告诉怎么修. 无人值守下日志没人盯仍会漏, 所以根治是给云电脑配上 webhook.
        print('', file=sys.stderr)
        print('  🚨🚨🚨 [captcha] 检测到验证码, 但 LARK_WEBHOOK_TUWEN_AUTO / LARK_WEBHOOK_AKKE_BOT 都没配!', file=sys.stderr)
        print(f'  🚨 类型={captcha_type} · 抖音提示={seen_text or "(无)"} — 无法推 Lark 告警!', file=sys.stderr)
        print('  🚨 请立刻回云电脑 (creator.douyin.com Edge 窗口) 人工过验证码 + 按 [Enter] 续跑.', file=sys.stderr)
        print('  🚨 根治: 在云电脑 worker/scripts/wuying-dm/.env 或 supabase agent-config/wuying.json 配 LARK_WEBHOOK_TUWEN_AUTO', file=sys.stderr)
        return  # 仍无法推送, 但已留痕 (非静默)
    if not webhook.startswith('http'):
        webhook = f'https://open.larksuite.com/open-apis/bot/v2/hook/{webhook}'

    at_tag = _resolve_lark_at_tag_for_current_assignee()
    assignee = os.environ.get('AKKE_TUWEN_ASSIGNEE', '').strip() or '?'

    payload = {
        'msg_type': 'interactive',
        'card': {
            'header': {
                'title': {'tag': 'plain_text', 'content': f'⚠️ 云电脑图文发布卡验证码 · {assignee}'},
                'template': 'red',
            },
            'elements': [
                {
                    'tag': 'div',
                    'text': {
                        'tag': 'lark_md',
                        'content': (
                            f'{at_tag} **类型**: {captcha_type}\n'
                            f'**抖音提示**: {seen_text or "(无)"}\n\n'
                            f'**云电脑 agent 已暂停**, 请尽快回云电脑 (creator.douyin.com Edge 窗口) '
                            f'手动过完验证码 + 回 PowerShell 按 [Enter] 续跑.\n\n'
                            f'**{CAPTCHA_TIMEOUT_SEC}s 内未过** → agent 自动 abort 本 row 转 needs_review, '
                            f'不挡你后面同号其他作品发布. 之后可手动重发这条 slug.'
                        )
                    }
                },
                {
                    'tag': 'note',
                    'elements': [
                        {'tag': 'plain_text', 'content': f'🤖 cloudpc-tuwen / creator_publisher.py · assignee={assignee}'}
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


def _download_video_url(url: str) -> str:
    """把远程视频 URL 下到本地 staging (C:\\tw\\HHMMSS\\v.mp4), 返回本地路径.

    与 _download_image_urls 同款短路径策略 (Windows 文件对话框 260 字符上限).
    视频几十 MB, 分块流式读 + 单块 60s 超时; 3 次重试指数退避.
    dewm 服务冷启动首条 ~30s 才开始吐字节, 超时别设太短.
    """
    from datetime import datetime as _dt

    base = Path(os.environ.get('AKKE_TUWEN_STAGING') or 'C:/tw')
    staging = base / _dt.now().strftime('%H%M%S')
    staging.mkdir(parents=True, exist_ok=True)
    dst = staging / 'v.mp4'
    print(f'  [staging] 下载视频到 {dst}')

    last_err = None
    for attempt in range(1, 4):
        try:
            if dst.exists():
                dst.unlink()
            req = urllib.request.Request(url, headers={'User-Agent': 'akke-tuwen-video/1.0'})
            with urllib.request.urlopen(req, timeout=60) as resp, open(dst, 'wb') as f:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
            size = dst.stat().st_size
            if size < 100 * 1024:
                raise IOError(f'视频只有 {size} bytes (<100KB), 疑似错误响应体')
            print(f'    → {dst.name} ({size / 1024 / 1024:.1f} MB, attempt {attempt})')
            return str(dst)
        except Exception as e:
            last_err = e
            wait = 5 * attempt
            print(f'    attempt {attempt} 失败 ({type(e).__name__}: {e}), {wait}s 后重试', file=sys.stderr)
            time.sleep(wait)
    raise last_err


def _dedup_video(src: str) -> str:
    """轻量去重二改: 掐头去尾各 ~0.5s + x264 重编码 (码率/编码指纹全变, MD5 必变).

    躲平台搬运查重的最低成本手段 (需求 2026-07-08 确认纳入 scope). 镜像翻转默认不开
    (家居视频常含文字/logo, 翻转露馅). ffmpeg/ffprobe 不在 PATH 时 WARN + 返回原片继续
    (宁可原样发也别把整条任务打死; 想强制去重的在部署时装 ffmpeg).

    ffmpeg 路径可用 AKKE_FFMPEG / AKKE_FFPROBE 覆盖 (默认找 PATH).
    """
    import shutil as _shutil
    import subprocess as _sp

    ffmpeg = os.environ.get('AKKE_FFMPEG') or _shutil.which('ffmpeg')
    ffprobe = os.environ.get('AKKE_FFPROBE') or _shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        print('  [dedup] WARN: ffmpeg/ffprobe 没找到, 跳过去重直接用原片 '
              '(装 ffmpeg 后自动生效, 或设 AKKE_FFMPEG)', file=sys.stderr)
        return src

    try:
        out = _sp.run(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', src],
            capture_output=True, text=True, timeout=60,
        )
        duration = float(out.stdout.strip())
    except Exception as e:
        print(f'  [dedup] WARN: ffprobe 取时长失败 ({e}), 跳过去重用原片', file=sys.stderr)
        return src

    head, tail = 0.5, 0.5
    if duration < 10:
        print(f'  [dedup] 视频只有 {duration:.1f}s (<10s), 不掐头尾只重编码')
        head = tail = 0.0
    keep = duration - head - tail

    dst = str(Path(src).with_name('vd.mp4'))
    cmd = [
        ffmpeg, '-y', '-ss', f'{head:.2f}', '-i', src, '-t', f'{keep:.2f}',
        '-c:v', 'libx264', '-crf', '26', '-preset', 'veryfast',
        '-c:a', 'aac', '-b:a', '128k',
        '-map_metadata', '-1',  # 抹掉原片全部 metadata (来源指纹)
        '-movflags', '+faststart',
        dst,
    ]
    print(f'  [dedup] ffmpeg 掐头{head}s尾{tail}s + 重编码 ({duration:.1f}s → {keep:.1f}s)')
    try:
        r = _sp.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            print(f'  [dedup] WARN: ffmpeg exit {r.returncode}, 用原片. stderr尾: {r.stderr[-300:]}', file=sys.stderr)
            return src
        size = Path(dst).stat().st_size
        print(f'  [dedup] 完成: {Path(dst).name} ({size / 1024 / 1024:.1f} MB)')
        return dst
    except Exception as e:
        print(f'  [dedup] WARN: ffmpeg 异常 ({type(e).__name__}: {e}), 用原片', file=sys.stderr)
        return src


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
    p.add_argument('--manifest', help='JSON 文件: {title, body, images[]} 或 {title, body, video/video_url}')
    p.add_argument('--title')
    p.add_argument('--body')
    p.add_argument('--images', help='逗号分隔的图片路径')
    p.add_argument('--video', help='本地视频路径 (与 --images 二选一, 走「发布视频」tab)')
    p.add_argument('--video-url', help='远程视频 URL (下载到 staging 再上传, 如 dewm /dewm/download 直链)')
    p.add_argument('--dedup', action='store_true', help='视频先过 ffmpeg 轻量去重 (掐头尾+重编码) 再上传')
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
    video_path: str | None = None
    dedup = args.dedup
    if args.manifest:
        m = _load_manifest(args.manifest)
        title = m['title']
        body = m['body']
        images = []
        if m.get('video_url'):
            video_path = _download_video_url(m['video_url'])
            downloaded_from_urls = True
        elif m.get('video'):
            video_path = m['video']
        # image_urls (远程 URL 数组) 优先于 images (本地路径).
        # 有 image_urls 就 curl 下载到 staging 目录, 再当作本地路径用 (复用文件对话框 paste 流程).
        elif m.get('image_urls'):
            images = _download_image_urls(m['image_urls'])
            downloaded_from_urls = True
        else:
            images = m.get('images', [])
        if m.get('dedup'):
            dedup = True
        # manifest 内 --schedule 也允许覆盖 CLI (manifest 里写 schedule_at 字段)
        if m.get('schedule_at') and not args.schedule:
            args.schedule = m['schedule_at']
    else:
        if not (args.title and args.body and (args.images or args.video or args.video_url)):
            p.error('需要 --title --body 加 (--images 或 --video/--video-url), 或 --manifest')
        title = args.title
        body = args.body
        images = [s.strip() for s in (args.images or '').split(',') if s.strip()]
        if args.video_url:
            video_path = _download_video_url(args.video_url)
            downloaded_from_urls = True
        elif args.video:
            video_path = args.video

    is_video = video_path is not None
    if is_video and images:
        print('ERROR: 视频和图片不能同时传 (二选一)', file=sys.stderr)
        return 2

    if is_video:
        if not Path(video_path).exists():
            print(f'ERROR: 视频不存在: {video_path}', file=sys.stderr)
            return 2
        if dedup:
            video_path = _dedup_video(video_path)
    else:
        for path in images:
            if not Path(path).exists():
                print(f'ERROR: 图不存在: {path}', file=sys.stderr)
                return 2
        if not (1 <= len(images) <= 18):
            print(f'ERROR: 图片张数 {len(images)} 不在 1-18 范围', file=sys.stderr)
            return 2
    if len(title) > 30:
        print(f'WARN: 标题 {len(title)} 字 > 限制 (图文 20/视频 30, 会被截或拒)', file=sys.stderr)

    print('===========================================')
    print(f'抖音 creator 发{"视频" if is_video else "图文"}')
    print('===========================================')
    print(f'  title:  {title}')
    print(f'  body:   {body[:60]}{"..." if len(body) > 60 else ""}')
    if is_video:
        print(f'  video:  {video_path} ({Path(video_path).stat().st_size / 1024 / 1024:.1f} MB, dedup={"on" if dedup else "off"})')
    else:
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
        goto_creator_upload(wait=6.0, url=CREATOR_UPLOAD_URL_VIDEO if is_video else None)

        # 步 2 (可选 - tab 确认)
        if is_video:
            print('[step 2] 确认「发布视频」tab (默认就在, 失败不阻断)')
            click_video_tab()
        else:
            print('[step 2] 切到「图文」tab (可能默认就在, 失败不阻断)')
            click_image_tab()

        # 步 3
        print('[step 3] 点上传区 → 弹文件对话框')
        if not (click_video_upload_button() if is_video else click_upload_button()):
            print('  ERROR: 上传按钮没找到. 截图 screenshots/_loc.png 看是不是页面没 load 完 / VL prompt 不对',
                  file=sys.stderr)
            return 4

        # 步 3.5
        if is_video:
            paste_image_paths([video_path])
            if not wait_video_uploaded():
                print('  ERROR: 视频上传超时/失败', file=sys.stderr)
                return 45
            # 上传完页面还在渲染表单 (标题/描述框、封面候选). 太早填会落空 →
            # 标题描述整片空白 (2026-07-08 夏夏 PoC 实测). 停 4s 等表单稳定.
            print('  [settle] 等表单渲染稳定 4s')
            time.sleep(4)
        else:
            paste_image_paths(images)

        # 步 4
        print('[step 4] 填标题')
        if not fill_title(title, video=is_video):
            if is_video:
                # 视频页标题非必填 (描述才是主体), 找不到不打死整条
                print('  WARN: 视频页标题输入框没找到, 跳过标题继续', file=sys.stderr)
            else:
                print('  ERROR: 标题输入框没找到', file=sys.stderr)
                return 5

        # 步 5
        print('[step 5] 填正文')
        if not fill_body(body, video=is_video):
            print('  ERROR: 正文输入框没找到', file=sys.stderr)
            return 6

        # 步 5.3 (图文默认加音乐; 视频有原声不加)
        if not args.no_music and not is_video:
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
        if not click_publish(commit=args.commit, video=is_video):
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

        # 清理 staging: 仅当远程 URL 触发下载时清, 用户自带本地 --images/--video 不动.
        # 保证不管 commit/dry-run/失败/异常都执行, 避免 staging 堆积占盘.
        if downloaded_from_urls:
            if images:
                _cleanup_staging(images)
            elif video_path:
                _cleanup_staging([video_path])


if __name__ == '__main__':
    sys.exit(main())
