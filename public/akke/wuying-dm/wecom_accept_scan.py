"""
wecom_accept_scan.py — 复查「已发请求」的好友是否已通过，自动 已发请求→已通过。

思路（不扫名单、不匹配昵称）：对每条已发请求的线索，用**当初那个手机号/微信号再搜一次**，
VL 读卡片状态——已是好友/发消息=对方通过了；等待验证=还没通过；又可加/搜不到=没通过(保守不动)。
search key 跟当初发请求一模一样 → join key 就是号码本身，零匹配歧义。

**只搜不加**：全程不点「添加」，绝不会重发请求。复用 wecom_add_contact_grounded 的搜人+VL+坐标，
只把判定逻辑换成"通过没通过"。

用法:  python wecom_accept_scan.py recheck.csv     (wecom_add_agent.py 复查支线调用)
  输入 CSV 列: search_key, name, _source_id
  输出 recheck_log_wecom_YYYYMMDD.csv 列: _source_id search_key name state seen checked_at
    state: passed(已通过) / pending(还在等待验证) / notyet(还没通过/搜不到，保守不动) / error

前置同 grounded：企业微信PC前台最大化、分辨率锁、输入法英文、坐标已标(共用同一份)。
"""
from __future__ import annotations

import base64
import csv
import os
import random
import sys
import time
from datetime import datetime

import pyautogui

# 复用 grounded 的全部原语（focus/搜索/点击/VL/坐标）；import 即加载它的 .env、坐标、模型配置。
# 它有 __main__ 守卫，import 不会触发 main/capture。
import wecom_add_contact_grounded as g

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 复查节奏：只搜不加、比发请求轻，但仍是搜索操作，慢一点稳。
MIN_INTERVAL = int(os.environ.get('AKKE_WECOM_RECHECK_MIN_INTERVAL', '30'))
MAX_INTERVAL = int(os.environ.get('AKKE_WECOM_RECHECK_MAX_INTERVAL', '60'))

# passed 二次把关（收紧，2026-07-06 假绿卡教训）：只认无歧义「已是好友」证据。
# 病根=旧逻辑把「发消息」当通过，但同公司同事/服务号/某些未通过态也有「发消息」→ 误判高发
# （同 PR #721 对 grounded already 的判定，这里落到复查路径）。
_CONF_MIN = float(os.environ.get('AKKE_WECOM_RECHECK_CONF_MIN', '0.9'))
_PASS_MARKERS = ('已是好友', '已经是好友', '你们已经是好友', '已添加到通讯录', '已在通讯录', '对方已通过')
_PENDING_MARKERS = ('等待验证', '等待对方', '申请已发送', '重新发送', '已发送申请', '正在验证')

_RECHECK_PROMPT = (
    '这是企业微信PC客户端搜索「%s」后的截图。我**之前已给该用户发过好友申请**，'
    '现在只判断对方是否已经通过，不要做任何操作。只回严格JSON:'
    '{"state":"passed|pending|notyet","confidence":0~1,"seen":"卡片上看到的原文按钮/提示文字"}。判定从严:'
    ' passed=有**明确成为好友的证据**：卡片显式出现“已是好友/你们已经是好友/已添加到通讯录/已在通讯录”等字样，且**没有**“等待验证”。'
    ' ⚠️单独一个“发消息”按钮**不算**通过——同公司同事、服务号、以及某些未通过状态也会有“发消息”，必须另有上面那些明确好友字样才判 passed。'
    ' pending=申请还在等待(显示“等待验证/等待对方验证/申请已发送/重新发送”);'
    ' notyet=显示可“添加到通讯录/添加”(尚未成为好友)、或“搜不到/该用户不存在”、或只看到“发消息”而无明确好友字样(证据不足，保守不判通过)。'
    ' 拿不准一律选 notyet，宁漏标不误标。'
)


def recheck_one(search_key: str, name: str):
    """搜一次该号、VL 判通过没通过。只搜不加。返回 (state, seen)。"""
    g.focus_wecom()
    # 1) 点搜索框 → Esc 清旧 → 键入号（与 grounded.process 的搜索段同步）
    g.click_fixed_or_vl(g.C_SEARCH, '企业微信顶部的搜索输入框', wait=1.0, label='搜索框',
                        region=(0.0, 0.0, 0.6, 0.12))
    pyautogui.press('esc')
    time.sleep(0.2)
    g.click_fixed_or_vl(g.C_SEARCH, '企业微信顶部的搜索输入框', wait=0.8, label='搜索框',
                        region=(0.0, 0.0, 0.6, 0.12))
    g.type_text(search_key)
    time.sleep(1.5)
    # 2) 点「网络查找手机号/邮箱」那一条（必经，否则出不来人物卡）
    try:
        g.click_fixed_or_vl(
            g.C_RESULT,
            '搜索框正下方下拉列表里"网络查找手机号/邮箱"或"搜索:%s"那一条' % search_key,
            wait=1.2, label='网络查找入口', region=(0.0, 0.04, 0.55, 0.7))
    except RuntimeError:
        pass
    time.sleep(3.0)
    # 3) 截图 + recheck 专用 VL 判定（绝不点添加）
    path, _ = g._shot('_wecom_recheck.png')
    b64 = base64.b64encode(open(path, 'rb').read()).decode()
    try:
        d = g._pjson(g._vision(b64, _RECHECK_PROMPT % search_key))
        st = str(d.get('state', 'notyet'))
        seen = str(d.get('seen', ''))
        try:
            conf = float(d.get('confidence', 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if st not in ('passed', 'pending', 'notyet'):
            st = 'notyet'
        # passed 代码层二次把关：宁漏标不误标。VL 说 passed 也要过三道闸——
        # ①有「等待验证」等待标记=还没通过 → pending；②置信不够 → 保守 notyet；
        # ③seen 里没有任何无歧义「已是好友」字样(只有"发消息"等弱信号)→ 不判通过。
        if st == 'passed':
            if any(k in seen for k in _PENDING_MARKERS):
                st, seen = 'pending', '收紧:见等待验证标记,非通过 | ' + seen
            elif conf < _CONF_MIN:
                st, seen = 'notyet', '收紧:passed置信%.2f<%.2f,保守不动 | %s' % (conf, _CONF_MIN, seen)
            elif not any(k in seen for k in _PASS_MARKERS):
                st, seen = 'notyet', '收紧:无明确已是好友证据(发消息不算),保守不动 | ' + seen
    except Exception as e:
        st, seen = 'error', '%s:%s' % (type(e).__name__, e)
    # 清搜索框回干净态（Esc 两下：关卡片 + 清搜索）
    for _ in range(2):
        pyautogui.press('esc')
        time.sleep(0.2)
    return st, seen


def main(recheck_csv: str):
    with open(recheck_csv, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print('复查名单为空, 退出')
        return
    print('=== wecom_accept_scan  共 %d 条待复查（只搜不加）===' % len(rows))
    print('前置: 企业微信PC前台+最大化、分辨率锁、输入法英文')
    log = 'recheck_log_wecom_%s.csv' % datetime.now().strftime('%Y%m%d')
    fields = ['_source_id', 'search_key', 'name', 'state', 'seen', 'checked_at']
    first = not os.path.exists(log)
    g.focus_wecom()
    with open(log, 'a', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        if first:
            w.writeheader()
        for i, r in enumerate(rows, 1):
            sk = (r.get('search_key') or '').strip()
            print('\n--- [%d/%d] 复查 %s (%s) ---' % (i, len(rows), sk, r.get('name', '')))
            if not sk:
                continue
            try:
                st, seen = recheck_one(sk, r.get('name', ''))
            except Exception as e:
                st, seen = 'error', '%s' % e
            print('  → %s (%s)' % (st, seen))
            w.writerow({'_source_id': r.get('_source_id', ''), 'search_key': sk,
                        'name': r.get('name', ''), 'state': st, 'seen': seen,
                        'checked_at': datetime.now().isoformat()})
            f.flush()
            if i < len(rows):
                time.sleep(random.randint(MIN_INTERVAL, MAX_INTERVAL))
    print('\n✅ 复查完成. 日志: %s' % log)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python wecom_accept_scan.py recheck.csv')
        sys.exit(2)
    main(sys.argv[1])
