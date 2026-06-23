#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_jit_comment1_wuying — 路 B·无影侧 JIT 评论1 生成器：逮到 lead 新视频那刻，看视频标题/正文
现场生成一条【对这条视频内容的反应】公开评论。消费脚本 import 它、每条触达调一次。

规则（fanny 2026-06-12 锁定）：
  - 评论1 = 对【这条新视频内容】的真实反应/捧场，自然口语一句话，看 desc(视频标题+正文)来。
  - desc 为空/纯空白 → 「赞赞赞」表情兜底（实测可用、零 LLM）。
  - 生成结果万一带价格/微信/促销红线词 → 也退「赞赞赞」（绝不让评论1 空、也不公开发红线词）。
  - 公开评论，禁价格/微信/私信/促销词（评论2 才搬首触原文带那些）。

前置（无影一次性）：pip install f2==0.0.1.7 已带 httpx；OpenRouter key 从同目录 .env 读
  （OPENROUTER_API_KEY 或 LLM_API_KEY）。无 key 时直接退「赞赞赞」兜底、不报错。

用法：
  import：from _jit_comment1_wuying import gen_comment1; gen_comment1("视频正文…")
  CLI 冒烟：python _jit_comment1_wuying.py "复式loft改造 楼梯下做了储物柜"
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env")); load_dotenv()
except Exception:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FALLBACK = "赞赞赞"

_OR_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LLM_API_KEY")
_OR_BASE = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
_MODEL = os.environ.get("LLM_CHAT_MODEL", "qwen/qwen3-235b-a22b-2507")

# 公开评论红线词（与 gen-table PROMO_RE / 消费端 PUBLIC_VIOLATION_RE 同款）；评论1 命中即退兜底。
_VIOLATION_RE = re.compile(
    r"微信|威信|v信|vx|加微|微信号|v号|给个\s*v|加你|加我|让设计师"
    r"|活动期|专供价?|原价\s*\d|限量|锁价|优惠"
    r"|\d[\d.]*\s*(一平|平米|平方|万|元)",
    re.I,
)

_SYS = (
    "你是小艳，一个真诚接地气的全屋定制顾问。现在在一位潜在客户刚发的短视频下留一条【公开评论】，"
    "是对【这条视频内容】的真实反应/捧场。要求：\n"
    "- 紧扣视频讲的东西来评，像普通观众自然互动，口语一句话，12-30字。\n"
    "- 可以含蓄带一点家装/装修共鸣，但别硬广、别像广告、别自报身份。\n"
    "- 严禁出现：价格/报价/数字加平米、微信/V信/加我/私信/加你、活动/优惠/限量/锁价/专供 等导流促销词。\n"
    "- 别 @人、别堆表情，最多一个表情。\n"
    "只输出评论正文，不要任何解释或引号。"
)


def gen_comment1(desc, timeout=30):
    """desc=视频标题/正文。返回一条公开评论；空 desc / 无 key / 异常 / 命中红线 → 退「赞赞赞」。"""
    desc = (desc or "").strip()
    if not desc or not _OR_KEY:
        return FALLBACK
    try:
        import httpx
        r = httpx.post(
            f"{_OR_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {_OR_KEY}", "Content-Type": "application/json"},
            json={"model": _MODEL,
                  "messages": [{"role": "system", "content": _SYS},
                               {"role": "user", "content": f"视频标题/正文：「{desc}」\n请写这条公开评论。"}],
                  "temperature": 0.85, "max_tokens": 200},
            timeout=timeout,
        )
        r.raise_for_status()
        txt = (r.json()["choices"][0]["message"]["content"] or "")
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip().strip('"').strip("「」")
        if not txt or _VIOLATION_RE.search(txt):
            return FALLBACK
        return txt
    except Exception:
        return FALLBACK


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"key={'有' if _OR_KEY else '无(退兜底)'} model={_MODEL}")
    print("评论1:", gen_comment1(d))
