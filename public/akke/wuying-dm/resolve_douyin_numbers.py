#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在【云电脑（国内 IP）】上跑：用 sec_uid 解析抖音号(unique_id)，回写 CSV 一列。

为什么在云电脑跑：抖音 web 用户主页接口对 IP 敏感，Fly 东京返空；国内 IP 能通。
签名复用仓库 worker 同款 f2 ABogusManager（a_bogus），ttwid 用首页 GET 自动种。

依赖（云电脑先装一次）：
    pip install "f2==0.0.1.7" httpx

用法（在 C:\\akke-wuying\\ 下，contacts.csv 已经在那）：
    py resolve_douyin_numbers.py contacts.csv
输出：
    1) 终端打印 昵称 → 抖音号 对照表
    2) 写出 contacts_with_number.csv（在原 CSV 基础上加一列「抖音号」）

读 CSV 的 _sec_uid 列拿 sec_uid；没有该列就报错退出。
解析不到（unique_id 为空 / 接口失败）的那条「抖音号」留空，不影响其它。
"""

import csv
import sys
import time
import random


def _missing_dep(msg):
    # 被当模块 import 时（wuying_poll_agent / _realtime_touch_consume_wuying 懒加载，外面是
    # `except Exception` 回退昵称搜）必须抛 ImportError：SystemExit 不是 Exception 的子类，
    # sys.exit 会穿过调用方的 except 直接打死常驻 agent。只有命令行直跑才 exit(2)。
    if __name__ == "__main__":
        print(msg, file=sys.stderr)
        sys.exit(2)
    raise ImportError(msg)


try:
    import httpx
except ImportError:
    _missing_dep("缺 httpx：先跑  pip install httpx")

try:
    from f2.apps.douyin.utils import ABogusManager
except ImportError:
    _missing_dep('缺 f2：先跑  pip install "f2==0.0.1.7"')

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)
PROFILE_ENDPOINT = "https://www.douyin.com/aweme/v1/web/user/profile/other/"


def gen_false_ms_token() -> str:
    base = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-"
    return "".join(random.choice(base) for _ in range(126)) + "=="


def profile_params(sec_uid: str) -> dict:
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "publish_video_strategy_type": "2",
        "source": "channel_pc_web",
        "sec_user_id": sec_uid,
        "personal_center_strategy": "1",
        "pc_client_type": "1",
        "version_code": "290100",
        "version_name": "29.1.0",
        "cookie_enabled": "true",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Edge",
        "browser_version": "130.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "130.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "12",
        "device_memory": "8",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "100",
        "msToken": gen_false_ms_token(),
    }


def resolve_one(client: httpx.Client, sec_uid: str, cookie_str: str = "") -> dict:
    """返回 {'ok':bool,'number':str,'nickname':str,'note':str}。"""
    params = profile_params(sec_uid)
    endpoint = ABogusManager.model_2_endpoint(UA, PROFILE_ENDPOINT, params)
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    try:
        resp = client.get(endpoint, headers=headers)
        resp.raise_for_status()
        if not resp.text:
            return {"ok": False, "number": "", "nickname": "", "note": "empty body (换登录态 cookie 再试)"}
        data = resp.json()
    except Exception as e:
        return {"ok": False, "number": "", "nickname": "", "note": f"req err: {e}"}

    if data.get("status_code") != 0:
        return {"ok": False, "number": "", "nickname": "",
                "note": f"status_code={data.get('status_code')} {data.get('status_msg','')}"}
    user = data.get("user") or {}
    # 抖音号优先 unique_id；没设过的用户 unique_id 为空 → 退 short_id
    number = (user.get("unique_id") or "").strip() or str(user.get("short_id") or "").strip()
    nickname = (user.get("nickname") or "").strip()
    if not number:
        return {"ok": False, "number": "", "nickname": nickname, "note": "unique_id/short_id 都空"}
    return {"ok": True, "number": number, "nickname": nickname, "note": ""}


def make_client() -> "httpx.Client":
    """建 httpx client 并 GET 首页种匿名 ttwid（后续请求自动带）。"""
    client = httpx.Client(follow_redirects=True, timeout=15)
    try:
        client.get("https://www.douyin.com/", headers={"User-Agent": UA})
    except Exception:
        pass
    return client


def resolve_batch(sec_uids, cookie_str: str = "") -> dict:
    """批量 sec_uid → {"number": 抖音号, "nickname": 最新昵称}。三态：
      number 为号  = 命中；
      number 为 "" = 【确定】该用户无抖音号(接口通了但 unique_id/short_id 都空，可缓存「查过无果」)；
      键缺失       = 瞬时失败(限流/网络/cookie 失效)，调用方别当「无号」处理、留待重试。
    nickname 是接口返回的【实时昵称】——用户改名后 DB 旧昵称会让 OCR 身份门误杀 wrong_user，
    调用方应优先用它做比对/回写缓存。
    供 wuying_poll_agent 内联调用（云电脑国内 IP）；best-effort，异常不抛给调用方。"""
    out: dict = {}
    uniq = [s.strip() for s in sec_uids if s and s.strip()]
    if not uniq:
        return out
    client = make_client()
    try:
        for sec in uniq:
            if sec in out:
                continue
            # 瞬时失败(限流/网络/empty body)重试 3 次，退避递增 —— 接口间歇性风控,
            # 一击不中就放弃会让 agent 回退昵称硬搜烧单(2026-06-10 晚 5/7 失败率教训)
            res = None
            for attempt in range(3):
                res = resolve_one(client, sec, cookie_str)
                if res.get("ok") or res.get("note") == "unique_id/short_id 都空":
                    break
                time.sleep(random.uniform(3.0, 6.0) * (attempt + 1))
            if res.get("ok"):
                out[sec] = {"number": res["number"], "nickname": res.get("nickname") or ""}
            elif res.get("note") == "unique_id/short_id 都空":
                # 确定无号（接口正常返回）；昵称照样带回，缓存/OCR 门仍可用
                out[sec] = {"number": "", "nickname": res.get("nickname") or ""}
            else:
                # 终次失败打出原因，agent 窗口可见，别再盲猜风控还是 cookie
                print(f"  [handle] 反查失败 {sec[:18]}...: {res.get('note')}", file=sys.stderr)
            time.sleep(random.uniform(1.5, 3.0))  # 轻限速，别触发反爬
    finally:
        client.close()
    return out


def main():
    if len(sys.argv) < 2:
        print("用法: py resolve_douyin_numbers.py contacts.csv", file=sys.stderr)
        sys.exit(1)
    in_path = sys.argv[1]
    out_path = in_path.rsplit(".", 1)[0] + "_with_number.csv"

    # 同目录 cookie.txt（登录态 Cookie 头整串），有就带上 — 匿名 ttwid 这接口不认。
    import os
    cookie_str = ""
    cookie_path = os.path.join(os.path.dirname(os.path.abspath(in_path)) or ".", "cookie.txt")
    if os.path.exists(cookie_path):
        with open(cookie_path, "r", encoding="utf-8-sig") as cf:
            cookie_str = cf.read().strip().replace("\n", " ")
        print(f"[cookie] 读到 cookie.txt（{len(cookie_str)} 字符），带登录态请求")
    else:
        print("[cookie] 未找到 cookie.txt，走匿名 ttwid（该接口大概率失败，建议放 cookie.txt）")

    with open(in_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("CSV 没有数据行", file=sys.stderr)
        sys.exit(1)
    if "_sec_uid" not in rows[0]:
        print("CSV 缺 _sec_uid 列，无法解析", file=sys.stderr)
        sys.exit(1)

    client = httpx.Client(follow_redirects=True, timeout=15)
    # 先 GET 首页种 ttwid（匿名 cookie），后续请求自动带上
    try:
        client.get("https://www.douyin.com/", headers={"User-Agent": UA})
    except Exception as e:
        print(f"[warn] 首页种 ttwid 失败（继续试）: {e}", file=sys.stderr)

    print(f"解析 {len(rows)} 个用户的抖音号 …\n")
    ok = 0
    for r in rows:
        sec = (r.get("_sec_uid") or "").strip()
        name = (r.get("nickname") or r.get("douyin_id") or "?").strip()
        if not sec:
            r["抖音号"] = ""
            print(f"  {name:<12} → (无 sec_uid，跳过)")
            continue
        res = resolve_one(client, sec, cookie_str)
        r["抖音号"] = res["number"]
        if res["ok"]:
            ok += 1
            print(f"  {name:<12} → {res['number']}")
        else:
            print(f"  {name:<12} → 解析失败：{res['note']}")
        time.sleep(random.uniform(1.5, 3.0))  # 轻限速，别触发反爬

    client.close()

    fieldnames = list(rows[0].keys())
    if "抖音号" not in fieldnames:
        fieldnames.append("抖音号")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"\n完成：{ok}/{len(rows)} 解析成功 → {out_path}")
    if ok < len(rows):
        print("解析失败的多半是接口要登录态 cookie：若全失败，把抖音 web 登录 cookie")
        print("存成同目录 cookie.txt 我再给你加读取（当前版本走匿名 ttwid）。")


if __name__ == "__main__":
    main()
