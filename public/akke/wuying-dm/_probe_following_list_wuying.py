r"""_probe_following_list_wuying — 路 B·Task A 落地探针：从【无影(中国 IP)】拉某号【自己关注了谁】的完整列表。

与 _probe_follow_feed_wuying.py 同一套签名/cookie 逻辑，区别只在端点 + 语义：
  · follow/feed   = 「我关注的人发了啥」（作品流，用来逮新视频）
  · following/list = 「我关注了谁」（关注名单，用来算真实关注数 + 入库 account_follows）

目的：把「真实关注数」搬进库前，先在无影上验三件事——
  1. 无影上 f2 能签名（ABogusManager 出 endpoint）；
  2. 无影中国 IP + active cookie 能拉到 user/following/list 且 status_code=0；
  3. 返回里有 followings[].sec_uid + nickname（够入 account_follows）+ total（=真实关注数）。

两个未知数（只能无影上验，handoff 里点名）：
  ① ABogus 对 following/list 端点通不通（可能要调 params：user_id 要数字 uid 还是 sec_uid / max_time 翻页）。
     → 本探针在首页 status_code≠0 时**打印原始返回头**，照着错误提示调 params。
  ② 每个号「自己的 sec_user_id」——已存在 accounts.sec_uid（migration 20260527141304 种好 3 个号）。
     无影够不到 Supabase，所以这里直接 --sec-uid 传，或 --account 用内置便捷映射（见 KNOWN_ACCOUNTS）。

前置（无影 PowerShell，一次性）：
  pip install f2==0.0.1.7        # 硬钉这版(它锁 httpx==0.27.2，别乱升 httpx)

用法（无影）：
  # 便捷：按名字（内置 sec_uid 映射）
  python _probe_following_list_wuying.py --account 野荞 --pages 50 --sleep 1.5 --out following_yeqiao.json
  # 或显式传 sec_uid
  python _probe_following_list_wuying.py --sec-uid MS4wLjABAAAA... --account-id 81cc9678-... --out x.json
  # cookie 默认读同目录 dy_cookie.txt，或 --cookie / --cookie-file

--out 写出的 JSON 摆渡回 mac，喂：
  pnpm tsx scripts/sync-account-follows.ts --from-following-list=following_yeqiao.json
"""
import argparse
import json
import os
import sys
import time

import httpx

try:
    from f2.apps.douyin.utils import ABogusManager, TokenManager
except Exception as e:
    print(f"X 没装 f2：{e}\n   先在无影 PowerShell 跑：pip install f2==0.0.1.7", file=sys.stderr)
    sys.exit(2)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")
FOLLOWING_LIST = "https://www.douyin.com/aweme/v1/web/user/following/list/"

# 便捷映射：accounts.sec_uid（migration 20260527141304 种好）。无影够不到 DB，内置 3 个常量省得手敲。
# 名字 / handle / account-uuid 前缀都能命中。
KNOWN_ACCOUNTS = {
    "野荞": ("81cc9678-9b53-413b-9f40-7964341f4af1", "MS4wLjABAAAABOBXTdk_t9h_ofTO_ImGTf9xGAkuI6GEUd0Rzfsb0_z6DybhIMMXRdz-7Ip3xZ1x"),
    "小文": ("81cc9678-9b53-413b-9f40-7964341f4af1", "MS4wLjABAAAABOBXTdk_t9h_ofTO_ImGTf9xGAkuI6GEUd0Rzfsb0_z6DybhIMMXRdz-7Ip3xZ1x"),
    "饭粒": ("c5d236e1-fd80-4cff-b349-506dfd09699b", "MS4wLjABAAAAF-LwyaXZEnaDm9NUW57PK1Vf2eaCZw237MQdgR7yJ4wa1iGBl2cFICng6cY3I3VQ"),
    "一筑": ("c5d236e1-fd80-4cff-b349-506dfd09699b", "MS4wLjABAAAAF-LwyaXZEnaDm9NUW57PK1Vf2eaCZw237MQdgR7yJ4wa1iGBl2cFICng6cY3I3VQ"),
    "夏夏": ("8af08f10-6db5-4d83-8942-b908349efbda", "MS4wLjABAAAAPftcHtSKx1u9_P1DkGFZTBmIpfxJ7fSnCFx-rRPuqi_n4KBK3lBTEtB6jdCbjdmS"),
    "零星": ("8af08f10-6db5-4d83-8942-b908349efbda", "MS4wLjABAAAAPftcHtSKx1u9_P1DkGFZTBmIpfxJ7fSnCFx-rRPuqi_n4KBK3lBTEtB6jdCbjdmS"),
}


def cookie_value(cookie_str, name):
    """从 cookie 串里抠某个键的值（如 msToken）。没有返回 ""。"""
    for kv in cookie_str.split(";"):
        kv = kv.strip()
        if kv.startswith(name + "="):
            return kv[len(name) + 1:]
    return ""


def resolve_mstoken(cookie_str):
    """following/list 必带 msToken。cookie 里有就用；没有就用 f2 TokenManager 现造真 msToken
    （无影实测 2026-06-25：dy_cookie.txt 常不含 msToken，gen_real_msToken() 能现申请）。
    返回 (msToken, 来源标签)。两条都不行返回造的假 token 兜底（多半还是不行，但留信息）。"""
    ck = cookie_value(cookie_str, "msToken")
    if ck:
        return ck, "cookie"
    try:
        real = TokenManager.gen_real_msToken()
        if real:
            return real, "f2现造"
    except Exception as e:
        print(f"  ⚠️ gen_real_msToken 失败：{type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
    try:
        return TokenManager.gen_false_msToken(), "f2假造(兜底)"
    except Exception:
        return "", "无"


def following_params(sec_uid, max_time, offset, count, mstoken):
    """user/following/list 参数。common 块照 follow/feed；following 专属在下半段。

    关键差异（2026-06-25 无影实测）：following/list 比 follow/feed 多要 msToken——直接用 f2 底层签名
    时 feed 不计较、following/list 不带就回 status_code=8「用户未登录」。所以这里把 cookie 里的
    msToken 抠出来一起带上（参与 a_bogus 签名）。
    ⚠️ user_id：web 端正式是 user_id=数字 uid + sec_user_id=sec_uid。我们只有 sec_uid，先 user_id 留空、
       只靠 sec_user_id 试；若签名通(非未登录)但报找不到用户，再换数字 uid。
    源排序 source_type=1=按关注时间倒序；翻页用 max_time（传上一页返回的 min_time）。
    """
    return {
        # ---- common（同 feed_params）----
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "pc_client_type": "1", "version_code": "290100", "version_name": "29.1.0",
        "cookie_enabled": "true", "screen_width": "1920", "screen_height": "1080",
        "browser_language": "zh-CN", "browser_platform": "Win32", "browser_name": "Edge",
        "browser_version": "130.0.0.0", "browser_online": "true", "engine_name": "Blink",
        "engine_version": "130.0.0.0", "os_name": "Windows", "os_version": "10",
        "cpu_core_num": "12", "device_memory": "8", "platform": "PC", "downlink": "10",
        "effective_type": "4g", "round_trip_time": "100",
        # ---- following/list 专属 ----
        "user_id": "",               # 先留空只靠 sec_user_id；不行再换数字 uid，见 docstring
        "sec_user_id": sec_uid,
        "offset": str(offset),
        "min_time": "0",
        "max_time": str(max_time),
        "count": str(count),
        "source_type": "1",          # 1=最近关注倒序
        "gps_access": "0",
        "address_book_access": "0",
        "is_top": "1",
        "msToken": mstoken,          # ← following/list 必带，否则「用户未登录」
    }


def fetch_following(cookie_str, sec_uid, max_time, offset, count, mstoken):
    # Referer 指到本号主页（部分端点校验来源页）。
    headers = {"User-Agent": UA, "Referer": f"https://www.douyin.com/user/{sec_uid}", "Cookie": cookie_str,
               "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-CN,zh;q=0.9"}
    try:
        params = following_params(sec_uid, max_time, offset, count, mstoken)
        ep = ABogusManager.model_2_endpoint(UA, FOLLOWING_LIST, params)
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


def resolve_target(args):
    """定位 (account_id, sec_uid, label)。--sec-uid 优先；否则 --account 查内置映射。"""
    if args.sec_uid:
        return (args.account_id or "", args.sec_uid.strip(), args.account or args.sec_uid[:12])
    if args.account:
        key = args.account.strip()
        if key in KNOWN_ACCOUNTS:
            acc_id, sec = KNOWN_ACCOUNTS[key]
            return (acc_id, sec, key)
        # account-uuid 前缀匹配
        for label, (acc_id, sec) in KNOWN_ACCOUNTS.items():
            if acc_id.startswith(key):
                return (acc_id, sec, label)
        print(f"X --account '{key}' 不在内置映射：{list(KNOWN_ACCOUNTS)}\n   用 --sec-uid 直接传。", file=sys.stderr)
        sys.exit(2)
    print("X 要么 --account <名字>，要么 --sec-uid <MS4w...>（+可选 --account-id <uuid>）。", file=sys.stderr)
    sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default=None, help="便捷：野荞/小文/饭粒/一筑/夏夏/零星 或 account-uuid 前缀")
    ap.add_argument("--sec-uid", dest="sec_uid", default=None, help="显式传本号自己的 sec_uid（优先）")
    ap.add_argument("--account-id", dest="account_id", default=None, help="入库用的 account UUID（配 --sec-uid）")
    ap.add_argument("--cookie", default=None, help="直接传 cookie 串（优先）")
    ap.add_argument("--cookie-file", default=None, help="cookie 文件路径（默认同目录 dy_cookie.txt）")
    ap.add_argument("--pages", type=int, default=50, help="最多翻几页（每页约 20 个）；has_more=0 提前停")
    ap.add_argument("--count", type=int, default=20, help="每页拉几个")
    ap.add_argument("--sleep", type=float, default=1.5, help="翻页间隔秒（防限流）")
    ap.add_argument("--out", default=None, help="写出 JSON 给 sync-account-follows.ts --from-following-list")
    args = ap.parse_args()

    account_id, sec_uid, label = resolve_target(args)
    cookie_str = load_cookie(args)
    mstoken, mstoken_src = resolve_mstoken(cookie_str)
    print(f"号 {label}（acct {account_id[:8] or '?'}） sec_uid={sec_uid[:20]}…")
    print(f"cookie {len(cookie_str)} 字节 | msToken={mstoken_src}({len(mstoken)}字节) "
          f"| 最多 {args.pages} 页 × {args.count} 间隔 {args.sleep}s\n")

    max_time = 0
    offset = 0
    total_reported = None
    follows = {}                # sec_uid -> {sec_uid, nickname, uid}
    for page in range(1, args.pages + 1):
        data, err = fetch_following(cookie_str, sec_uid, max_time, offset, args.count, mstoken)
        if err:
            print(f"第{page}页 X {err}  → 限流/超时/cookie失效，停在这页（前面的照常算）。")
            break
        sc = data.get("status_code")
        items = data.get("followings") or data.get("data") or []
        if not isinstance(items, list):
            items = []
        if total_reported is None:
            total_reported = data.get("total")
        print(f"第{page}页 status_code={sc} 本页 {len(items)} 个 has_more={data.get('has_more')} "
              f"total={data.get('total')} max_time={data.get('max_time')} min_time={data.get('min_time')}")
        # 首页就非 0：把原始返回头打出来，照着调 params（未知数①）。
        if sc not in (0, None) and page == 1:
            print("  ⚠️ status_code≠0，原始返回头（照这个调 params，多半是 user_id 要数字 uid）：")
            print("  " + json.dumps(data, ensure_ascii=False)[:600])
        for it in items:
            sec = it.get("sec_uid")
            if not sec:
                continue
            follows[sec] = {"sec_uid": sec, "nickname": it.get("nickname", ""), "uid": it.get("uid", "")}
        if not items:
            print("  （本页 0 个，停。）")
            break
        if not data.get("has_more"):
            print("  （has_more=0，关注名单到底了，停。）")
            break
        # 翻页：max_time 推进到本页最早关注时刻；offset 也跟着累加做双保险。
        new_max = data.get("min_time")
        if new_max and new_max != max_time:
            max_time = new_max
        else:
            offset += len(items)
        if page < args.pages:
            time.sleep(args.sleep)

    print("\n=== 汇总 ===")
    print(f"号 {label} 真实关注数 total（接口报）= {total_reported}　| 本次实拉到 {len(follows)} 个（去重）")
    if total_reported and len(follows) < total_reported:
        print(f"⚠️ 只拉到 {len(follows)}/{total_reported}，加 --pages 继续翻（或被限流，加大 --sleep 重跑）。")

    if args.out:
        payload = {
            "account_id": account_id,
            "account_name": label,
            "sec_uid": sec_uid,
            "total": total_reported,
            "fetched": len(follows),
            "follows": list(follows.values()),
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 写出 {args.out}（{len(follows)} 个）。摆渡回 mac 后：")
        print(f"   pnpm tsx scripts/sync-account-follows.ts --from-following-list={os.path.basename(args.out)}")
    else:
        print("\n（没传 --out，只探不落盘；要入库加 --out following_<号>.json）")
    print("\n判读：首页 status_code=0 + total>0 + followings 有 sec_uid/nickname → 签名通、抓取器成立。")


if __name__ == "__main__":
    main()
