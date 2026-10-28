# -*- coding: utf-8 -*-
"""抖音号身份比对：OCR 读到的号 vs 搜索用的号。只依赖标准库（云电脑不一定装了 f2/httpx）。

DY-001（2026-10-19）：douyin_dm_grounded 按号搜人后，VL 读主页「抖音号:」再与搜索号逐字比，
号对不上就一票否决。10-06~19 被判 wrong_user 的 34 个目标里，10 个第 1 条结果就是本人——
昵称逐字一致，号被 VL 读成 Al→AI / sdjnqi→sdjngi / ll596888.→ll596888 / jinruiwuliuh→jinruiwuliu。
逐字比把 OCR 输出当成了精确值。
"""
import os

# VL 抄 ASCII 号的系统性误读（casefold 之后）：I/1/| ↔ l、o ↔ 0、q ↔ g。同类内互换代价 0。
_CONFUSABLE_CLASSES = ('il1|', 'o0', 'qg')
_CLASS_OF = {ch: cls for cls in _CONFUSABLE_CLASSES for ch in cls}
# 碰到数字的编辑（数字被增/删/换成别的）代价超出容错上限：生产误读样本里没有一例动到数字
# （都是字母混淆 / 吞字母 / 互换 / 吞末尾标点），而 name4917 vs name4918 恰恰是
# 「同昵称、真不同的号」最可能长的样子。
_DIGIT_EDIT = 3


def norm_no(x):
    """抖音号归一化：去所有空白字符 + casefold（号是 ASCII 唯一串，大小写/空格差异不该判错人）。"""
    return ''.join(str(x or '').split()).casefold()


def _indel_cost(c):
    return _DIGIT_EDIT if c.isdigit() else 1


def _sub_cost(ca, cb):
    if ca == cb or (ca in _CLASS_OF and _CLASS_OF.get(cb) == _CLASS_OF[ca]):
        return 0
    return _DIGIT_EDIT if ca.isdigit() or cb.isdigit() else 1


def _edit_distance(a, b):
    prev = [0]
    for cb in b:
        prev.append(prev[-1] + _indel_cost(cb))
    for ca in a:
        cur = [prev[0] + _indel_cost(ca)]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + _indel_cost(ca), cur[j - 1] + _indel_cost(cb),
                           prev[j - 1] + _sub_cost(ca, cb)))
        prev = cur
    return prev[-1]


def number_ocr_near(seen, expected):
    """读到的号与搜的号是否只差在 OCR 误读上。**调用方必须另有昵称高置信一致才能采信**——
    本函数只回答「号的差异像不像读错」，不单独构成本人证据。

    只对含字母、长度 ≥6 的自定义号容错：纯数字号（short_id）相邻号段真有不同的人
    （实测同一搜索结果页 58439654234 / 58439654245 / 58439654273 是三个人），一位都不放。
    规则：去末尾 . _ -（VL 常吞掉）→ 易混字符归一 → 编辑距离 ≤ 2（数字换数字不计入可容错的读错）。
    例外：不含数字且 ≤8 位的纯字母号只放 1 次编辑——6 位里 2 次编辑就是 1/3 字符不同，
    mikeab / mikecd 这类同昵称的另一个人会被当成本人；生产误读样本里纯字母号都是 1 次编辑
    （Al→AI、sdjnqi→sdjngi、吞末尾字母）。含数字的号、≥9 位的号维持 2。
    AKKE_NUMBER_OCR_TOLERANCE=0 关闭（回到逐字比对）。
    """
    if os.environ.get('AKKE_NUMBER_OCR_TOLERANCE', '1') != '1':
        return False
    a, b = norm_no(seen).rstrip('._-'), norm_no(expected).rstrip('._-')
    if len(b) < 6 or not any(ch.isalpha() for ch in b) or not a:
        return False
    limit = 1 if (len(b) <= 8 and not any(ch.isdigit() for ch in b)) else 2
    return _edit_distance(a, b) <= limit
