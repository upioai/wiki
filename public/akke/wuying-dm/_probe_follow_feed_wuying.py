r"""_probe_follow_feed_wuying — 路 B 落地·无影侧探针：验证关注流接口能不能从【无影(中国 IP)】拉到。

与 _probe_follow_feed.py 同一个端点/签名逻辑，区别只有一处：
**cookie 不从 Supabase 取（无影够不到 Supabase），改成从本地文件/参数直接喂。**

目的：把潜在触达实时监控整个搬到无影一台机之前，先验三件事——
  1. 无影上 f2 能装、能签名（ABogusManager 出 endpoint）；
  2. 无影中国 IP 能拉到 follow/feed 且 status_code=0；
  3. 返回里有 author.sec_uid + create_time（够判「谁更新了 + 新不新鲜」）。

升级版（深挖+覆盖核对）：
  --pages 大 + --sleep 翻页间隔防限流；翻到 has_more=0 自动停。
  --pool 给 lead 池(JSON)：每条标 ★(池子里的人) / ·(不在)，末尾汇总「关注流总作品 / 不同的人 /
         其中池子命中几人 / 池子总几人」，直接看监控覆盖够不够。

前置（无影 PowerShell，一次性）：
  pip install f2==0.0.1.7        # 硬钉这版(它锁 httpx==0.27.2，别乱升 httpx)

用法（无影）：
  python _probe_follow_feed_wuying.py --pages 50 --sleep 1.5            # 能挖多深挖多深
  python _probe_follow_feed_wuying.py --pages 50 --sleep 1.5 --pool special-follow.json   # 带覆盖核对
  python _probe_follow_feed_wuying.py --cookie-file C:\akke-wuying\wuying-dm\dy_cookie.txt
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import httpx

try:
    from f2.apps.douyin.utils import ABogusManager
except Exception as e:
    print(f"X 没装 f2：{e}\n   先在无影 PowerShell 跑：pip install f2==0.0.1.7", file=sys.stderr)
    sys.exit(2)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")
FOLLOW_FEED = "https://www.douyin.com/aweme/v1/web/follow/feed/"


def feed_params(cursor):
    return {
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "cursor": str(cursor), "level": "1", "count": "20", "pull_type": "",
        "pc_client_type": "1", "version_code": "290100", "version_name": "29.1.0",
        "cookie_enabled": "true", "screen_width": "1920", "screen_height": "1080",
        "browser_language": "zh-CN", "browser_platform": "Win32", "browser_name": "Edge",
        "browser_version": "130.0.0.0", "browser_online": "true", "engine_name": "Blink",
        "engine_version": "130.0.0.0", "os_name": "Windows", "os_version": "10",
        "cpu_core_num": "12", "device_memory": "8", "platform": "PC", "downlink": "10",
        "effective_type": "4g", "round_trip_time": "100",
    }


def ago(epoch):
    if not epoch:
        return None
    dt = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def fetch_feed(cookie_str, cursor):
    headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/", "Cookie": cookie_str,
               "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-CN,zh;q=0.9"}
    try:
        ep = ABogusManager.model_2_endpoint(UA, FOLLOW_FEED, feed_params(cursor))
        r = httpx.get(ep, headers=headers, timeout=20, follow_redirects=True)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    try:
        return r.json(), None
    except Exception:
        return None, f"feiJSON({len(r.text)}zijie): {r.text[:120]!r}"


def load_cookie(args):
    if args.cookie:
        return args.cookie.strip()
    path = args.cookie_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), "dy_cookie.txt")
    if not os.path.exists(path):
        print(f"X cookie 文件不存在：{path}\n   把 mac 摆渡过来的 cookie 串存成这个文件，或用 --cookie 直接传。",
              file=sys.stderr)
        sys.exit(2)
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def load_pool(path):
    """lead 池 → {sec_uid: name}。没传/不存在 → 空 dict（不标★，只统计总量）。"""
    if not path or not os.path.exists(path):
        return {}
    doc = json.load(open(path, encoding="utf-8"))
    rows = doc.get("rows", doc) if isinstance(doc, dict) else doc
    pool = {}
    for r in rows:
        sec = r.get("sec_uid") or r.get("douyin_user_id")
        if sec:
            pool[sec] = r.get("name") or r.get("customer_name", "")
    return pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", default=None, help="直接传 cookie 串（优先）")
    ap.add_argument("--cookie-file", default=None, help="cookie 文件路径（默认同目录 dy_cookie.txt）")
    ap.add_argument("--pages", type=int, default=2, help="最多翻几页(每页约 12 条)；has_more=0 提前停")
    ap.add_argument("--sleep", type=float, default=1.0, help="翻页间隔秒（防限流，深挖建议 1.5）")
    ap.add_argument("--pool", default=None, help="lead 池 JSON：标★池子里的人 + 汇总覆盖")
    args = ap.parse_args()

    cookie_str = load_cookie(args)
    pool = load_pool(args.pool)
    print(f"cookie {len(cookie_str)} 字节 | lead池 {len(pool)} 人 | 最多 {args.pages} 页 间隔 {args.sleep}s\n")

    cursor = 0
    total = 0
    seen_authors = {}          # sec_uid -> nickname（不同的人）
    pool_hits = {}             # 命中池子的 sec_uid -> name
    for page in range(1, args.pages + 1):
        data, err = fetch_feed(cookie_str, cursor)
        if err:
            print(f"第{page}页 X {err}  → 限流/超时/cookie失效，停在这页（前面的照常算）。")
            break
        sc = data.get("status_code")
        items = data.get("data") or data.get("aweme_list") or []
        if not isinstance(items, list):
            items = []
        print(f"第{page}页 status_code={sc} 本页 {len(items)} 条 has_more={data.get('has_more')} cursor={data.get('cursor')}")
        for it in items:
            aw = it.get("aweme") if isinstance(it.get("aweme"), dict) else it
            au = (aw.get("author") or {})
            sec = au.get("sec_uid")
            nick = au.get("nickname", "?")
            ct = aw.get("create_time")
            d = ago(ct)
            if sec:
                seen_authors[sec] = nick
            star = " "
            if sec in pool:
                star = "★"
                pool_hits[sec] = pool[sec]
            print(f"   {star} {nick[:14]:<14} {str(sec)[:24]} "
                  f"发布={f'{d*1440:.0f}分前' if d is not None and d < 1 else (f'{d:.1f}天前' if d is not None else '?')} "
                  f"{(aw.get('desc') or '')[:24]}")
        total += len(items)
        cursor = data.get("cursor") or cursor
        if not data.get("has_more"):
            print("（has_more=0，关注流到底了，停。）")
            break
        if page < args.pages:
            time.sleep(args.sleep)

    print("\n=== 汇总 ===")
    print(f"关注流总作品 {total} 条 | 不同的人 {len(seen_authors)} 个")
    if pool:
        print(f"其中池子命中 {len(pool_hits)} 人 / 池子总 {len(pool)} 人  "
              f"(覆盖 {len(pool_hits)*100//max(len(pool),1)}%)")
        if pool_hits:
            print("命中名单：" + "、".join(list(pool_hits.values())[:30]) + (" …" if len(pool_hits) > 30 else ""))
        miss = len(pool) - len(pool_hits)
        print(f"⚠️ 池子里有 {miss} 人这次没在关注流出现 —— 要么没发新作品、要么【这个号没关注他们】。")
        print("   监控覆盖 = 号关注了谁。没关注的 lead 永远进不了流、逮不到。")
    else:
        print("（没传 --pool，只统计总量；要核对覆盖把 lead 池摆渡过来加 --pool special-follow.json）")
    print(f"\n判读：能深挖到 {total} 条 / {len(seen_authors)} 人。监控每 60s 翻几页够不够，看新作品冒出来的速度。")


if __name__ == "__main__":
    main()
