#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_follow_grounded — 无影 GUI 批量关注（路 B 块0·覆盖补齐）。号在无影是原生登录的真抖音，
天然过反爬（raw commit/follow/user API 吃 403、Playwright chromium 被喂空白页，都走不通——
见 follow_targets_pw.py 注释 + 2026-06-11 实测）。本脚本仿 douyin_comment_grounded 的导航积木，
逐个：搜人 → 用户tab → 第一条结果 → OCR身份门(防关错人) → VL判已关注/点「关注」→ 校验 → 下一条。

为什么要它：潜在触达监控走 follow/feed，只能逮到【号已关注】的人发的视频。号现只关注 21/164(12%)，
没关注的逮不到。本脚本把池子里的 lead 关全，覆盖才能从 12%→100%，事件驱动闭环才成立。

⚠️ 风控：一次性关太多会触发限流/封号。默认按 dm.DAILY_LIMIT(30) 截断，分多天补齐；--limit 覆盖。
   连续失败早停。跑批期间【别碰鼠标键盘】——pyautogui 全程在控鼠标搜人/点击。
前置(同发私信)：抖音 PC 前台最大化 / 分辨率锁 2560×1600 / 输入法英文 / .env 有 OCR key。
搜索按【昵称】(GUI 搜不了 sec_uid)，OCR身份门防同名关错；sec_uid 仅做去重/日志。

用法(无影，脚本目录)：
  python _follow_grounded.py --from-db                                  # 直读库(免摆渡·WI-2 P1·推荐)
  python _follow_grounded.py --pool special-follow-饭粒.json            # 关 30 个(日限·老文件池)
  python _follow_grounded.py --pool special-follow-饭粒.json --limit 50 # 覆盖日限
  python _follow_grounded.py --pool special-follow-饭粒.json --confirm  # 逐个 y/n 确认
  python _follow_grounded.py --test "某昵称"                            # 只关 1 个验流程
--from-db(P1 自动化)：cron refresh-monitored-targets 已做 ring 分配+7天窗口，本号要关的(dm_failed+no_owner)
  直接进 monitored_targets，脚本读库取本号名单(.env 要 SUPABASE_*+AKKE_ORG_ID+AKKE_ACCOUNT_ID)。配 Windows
  任务计划程序每天跑一次(见同目录 follow-daily.bat)，followed.json 跨天去重+DAILY_LIMIT 截断天然处理「关30就停」。
输出: follow_log_YYYYMMDD.csv（name sec_uid status conf sent_at）+ followed.json(去重，跨天跳过已关)
"""
import argparse
import csv
import json
import os
import random
import sys
import time
from datetime import datetime

import pyautogui

import douyin_dm_grounded as dm
import wuying_window_lock as _wl  # 单窗口·DM优先串行锁(AKKE_WINDOW_LOCK=1 才生效，否则 no-op)

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 主页头部「关注」按钮：dm 模块已有 C_FOLLOW(默认 None→VL定位)。
C_FOLLOW = dm._coord("AKKE_C_FOLLOW", None)
# 头部区域(归一化)：搜结果进主页后，关注/已关注按钮在右上。VL 限域省 token、避免点到别处。
HEADER_REGION = (0.30, 0.05, 1.0, 0.38)


def _setup_db(account):
    """读 .env 组装 monitored_targets 直连配置（镜像 watch 的 _setup_db：scoped JWT / 回退 service）。
    缺关键 env 返回 None → --from-db 直接报错退出（不静默降级，免误以为关了一批）。"""
    try:
        from dotenv import load_dotenv
        for p in (os.path.join(HERE, ".env"), os.path.join(os.path.dirname(HERE), ".env")):
            if os.path.exists(p):
                load_dotenv(p, override=False)
    except Exception:
        pass
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    scoped = os.environ.get("SUPABASE_SCOPED_JWT")
    anon = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    service = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    org = os.environ.get("AKKE_ORG_ID")
    if scoped and anon:
        apikey, bearer = anon, scoped       # 受控 wuying_worker 角色（无影优先）
    elif service:
        apikey = bearer = service           # 回退 service_role
    else:
        return None
    if not (url and org and account):
        return None
    return {"url": url.rstrip("/"), "apikey": apikey, "bearer": bearer, "org": org}


def load_targets_db(db, account):
    """从 monitored_targets 读本号 status=watching 且 reason in (dm_failed, no_owner) 的【要关注】名单。
    sent_no_reply 不读（那是已关注·watch-only）。cron refresh-monitored-targets 已做 ring 分配+窗口，
    这里只读分到本号的，免 mac 摆渡（WI-2 P1）。返回 [{name, sec_uid, douyin_no, comment_id}]，与文件池同形。
    douyin_no 库里没有 → 空串退昵称搜（同文件池无号行为；大众名 wrong_user 风险照旧，v1 不补 enrich）。"""
    import urllib.request
    import urllib.parse
    q = urllib.parse.urlencode({
        "select": "douyin_user_id,nickname,source_comment_id",
        "org_id": "eq.%s" % db["org"],
        "account_id": "eq.%s" % account,
        "status": "eq.watching",
        "reason": "in.(dm_failed,no_owner)",
    })
    req = urllib.request.Request(
        "%s/rest/v1/monitored_targets?%s" % (db["url"], q),
        headers={"apikey": db["apikey"], "Authorization": "Bearer %s" % db["bearer"]})
    rows = json.load(urllib.request.urlopen(req, timeout=30))
    out = []
    for r in rows:
        sec = r.get("douyin_user_id")
        if sec:
            out.append({"name": r.get("nickname") or "", "sec_uid": sec,
                        "douyin_no": "", "comment_id": r.get("source_comment_id") or ""})
    return out


def ensure_front(tries=3):
    """硬拉抖音前台，仿 douyin_comment_grounded.ensure_douyin_front（人碰鼠标会瞬时抢焦点）。"""
    for _ in range(tries):
        if dm.focus_douyin():
            return True
        time.sleep(0.6)
    return dm.focus_douyin()


def already_followed():
    """是否已关注。改用 dm._is_followed()(裁剪头部+"读按钮文字"判别),不再用诱导式 locate。
    2026-06-12 修:旧 locate("找『已关注』按钮")恒返回坐标→恒判已关注→批量一个都不真关。
    只在【明确读到已关注】时返回 True;读不清(None)按"未关注"处理,继续尝试关注(click_follow
    那层只点文字确为『关注』的按钮,已关注态找不到该按钮会返回 no_button,不会误取消关注)。"""
    return dm._is_followed() is True


def click_follow():
    """点「关注」按钮：有固定坐标直接点；否则 VL 定位『关注』两字按钮。返回是否点了。"""
    if C_FOLLOW:
        dm.click_norm(C_FOLLOW[0], C_FOLLOW[1], wait=1.5, label="关注按钮(固定坐标)")
        return True
    try:
        pt = dm.locate("抖音用户主页头部的『关注』按钮——蓝色或红色实心、按钮文字就是『关注』两个字"
                       "（不是『已关注』，不是『相互关注』，不是『私信』）", region=HEADER_REGION)
    except Exception as e:
        print("  [关注] VL 定位异常: %s" % e)
        return False
    if not pt:
        return False
    pyautogui.click(pt[0], pt[1])
    time.sleep(1.5)
    return True


def follow_one(name, auto, douyin_no=""):
    """返回 status: followed / already / wrong_user / no_button / front_fail / clicked_unverified / cancelled。
    douyin_no：enrich 反查到的抖音号(可空)。有号则【搜号】——号唯一、搜出来就是本人，根治大众名
    (小燕子/红钻/清零)按昵称搜落到别人→wrong_user(2026-06-13 实测 38/57 栽这)。身份门同时核
    抖音号(ocr_verify expected_number)，比对更硬。无号退昵称搜(老行为)。仿 douyin_dm_grounded:738。"""
    print("\n=== %s%s ===" % (name, ("  [号:%s]" % douyin_no) if douyin_no else "  [无号·退昵称搜]"))
    if not ensure_front():
        return "front_fail", None

    query = douyin_no or name  # 有抖音号优先搜号(精确落本人)，否则搜昵称
    # 搜人→用户tab→第一条→OCR身份门，带「非主页/加载中」重试(最多 3 次)。
    # 2026-06-13: 同坐标搜"小米"进对主页、搜"小米手机"落『(非主页)』→ 偶发(结果页加载慢/点tab时
    # 还没切成用户列表→第一条点到综合里的视频)，整条重搜一次即恢复。先点回首页钉死状态(否则上一
    # 条停在视频/结果页时顶部搜索框位置变→点空到视频上;量了 AKKE_C_HOME 才生效)。商家/真同名导致
    # 的 wrong_user 重试也无害(最终仍判 wrong_user 跳过,不会乱关)。
    m, conf, seen = False, 0.0, ""
    for attempt in range(3):
        if attempt > 0:
            print("  [重试 %d/2] 上次落 %r(非主页/搜错) → 回首页整条重搜" % (attempt, seen))
        dm.goto_home()
        dm.click_norm(dm.C_SEARCH[0], dm.C_SEARCH[1], wait=1.2, label="搜索框")
        dm.type_text(query)
        pyautogui.press("enter")
        time.sleep(2.5 + attempt * 1.5)  # 重试时多等结果页加载
        dm.click_user_tab()  # VL 点中「用户」tab,过滤成用户列表(固定坐标与第一条重叠点不中→落非主页)
        dm.click_norm(dm.C_FIRST[0], dm.C_FIRST[1], wait=2.0, label="第一条结果头像")
        # 搜号时身份门加抖音号精确核身(昵称特殊字符 VL 易误拒，号 ASCII 精确兜底)。
        m, conf, seen = dm.ocr_verify(name, expected_number=(douyin_no or None))
        print("  [OCR] match=%s conf=%.2f seen=%r threshold=%s" % (m, conf, seen, dm.MIN_CONF))
        if m and conf >= dm.MIN_CONF:
            break
    if not m or conf < dm.MIN_CONF:
        return "wrong_user", conf

    if already_followed():
        print("  [跳过] 已关注/相互关注")
        return "already", conf

    if not auto:
        try:
            ans = input("  ❓ 关注【%s】？(y=关 / 回车=跳过) " % name).strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans not in ("y", "yes", "是"):
            return "cancelled", conf

    if not click_follow():
        print("  [关注] 没找到『关注』按钮（可能已关注/页面异常）")
        return "no_button", conf

    # 校验：按钮变「已关注」
    time.sleep(1.5)
    if already_followed():
        print("  ✅ 已关注")
        return "followed", conf
    return "clicked_unverified", conf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=os.path.join(HERE, "special-follow-饭粒.json"))
    ap.add_argument("--from-db", action="store_true",
                    help="直读 monitored_targets(reason in dm_failed,no_owner) 取关注名单，免 mac 摆渡文件池（WI-2 P1）")
    ap.add_argument("--account", default=os.environ.get("AKKE_ACCOUNT_ID", ""),
                    help="--from-db 时按本号取名单（默认 AKKE_ACCOUNT_ID）")
    ap.add_argument("--test", default=None, help="只关 1 个昵称验流程")
    ap.add_argument("--limit", type=int, default=None, help="本次最多关几个(默认=dm.DAILY_LIMIT 防风控)")
    ap.add_argument("--confirm", action="store_true", help="逐个 y/n 确认")
    ap.add_argument("--done", default=os.path.join(HERE, "followed.json"), help="已关 sec_uid 去重(跨天跳过)")
    args = ap.parse_args()

    if not dm.KEY:
        sys.exit("❌ 缺 ANTHROPIC_API_KEY / OPENROUTER_API_KEY（OCR身份门+VL定位要）")

    if args.test:
        targets = [{"name": args.test, "sec_uid": "", "douyin_no": ""}]
    elif args.from_db:
        if not args.account:
            sys.exit("❌ --from-db 需要 --account=<id> 或 .env 的 AKKE_ACCOUNT_ID")
        db = _setup_db(args.account)
        if not db:
            sys.exit("❌ --from-db 缺 .env：要 SUPABASE_URL/NEXT_PUBLIC_SUPABASE_URL + "
                     "(SUPABASE_SCOPED_JWT+ANON_KEY 或 SUPABASE_SERVICE_ROLE_KEY) + AKKE_ORG_ID")
        targets = load_targets_db(db, args.account)
        print("  从库读关注名单 monitored_targets(dm_failed+no_owner)·本号 %s：%d 人（cron 已做 ring 分配+窗口）"
              % (args.account[:8], len(targets)))
    else:
        if not os.path.exists(args.pool):
            sys.exit("lead 池不存在: %s（从 mac 摆渡 special-follow-饭粒[-enriched].json）" % args.pool)
        doc = json.load(open(args.pool, encoding="utf-8"))
        rows = doc.get("rows", doc) if isinstance(doc, dict) else doc
        targets = [{"name": r.get("name") or r.get("customer_name", ""),
                    "sec_uid": r.get("sec_uid") or r.get("douyin_user_id", ""),
                    "douyin_no": (r.get("douyin_no") or "").strip()}
                   for r in rows if (r.get("name") or r.get("customer_name"))]
        with_no = sum(1 for t in targets if t["douyin_no"])
        print("  池含抖音号 %d/%d（有号搜号·根治大众名 wrong_user；无号退昵称搜）" % (with_no, len(targets)))

    done = set()
    if os.path.exists(args.done):
        try:
            done = set(json.load(open(args.done)))
        except Exception:
            done = set()
    targets = [t for t in targets if not t["sec_uid"] or t["sec_uid"] not in done]

    limit = args.limit or dm.DAILY_LIMIT
    if len(targets) > limit:
        print("⚠️ 待关 %d > 本次上限 %d(防风控) → 只关前 %d，剩下明天再跑" % (len(targets), limit, limit))
        targets = targets[:limit]

    print("=== _follow_grounded  共 %d 个  conf>=%s  %s ===" % (
        len(targets), dm.MIN_CONF, "自动关" if not args.confirm else "逐个确认"))
    print("前置: ①抖音PC前台最大化 ②分辨率锁2560×1600 ③输入法英文。⚠️跑批期间别碰鼠标键盘。")

    log = os.path.join(HERE, "follow_log_%s.csv" % datetime.now().strftime("%Y%m%d"))
    first = not os.path.exists(log)
    ok = already = bad = 0
    consec = 0
    with open(log, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if first:
            w.writerow(["name", "sec_uid", "status", "conf", "sent_at"])
        for i, t in enumerate(targets, 1):
            print("\n========== [%d/%d] ==========" % (i, len(targets)))
            conf = None
            try:
                with _wl.window_turn('rb'):  # DM 在忙(.dm-want)则先在此等→DM 优先，关完让位；跟 consume 同款锁
                    status, conf = follow_one(t["name"], auto=not args.confirm, douyin_no=t.get("douyin_no", ""))
            except Exception as e:
                status = "error:%s" % str(e)[:60]
                print("  ❌ 异常: %s" % e)
                try:
                    pyautogui.press("esc")
                except Exception:
                    pass
            w.writerow([t["name"], t["sec_uid"], status,
                        "" if conf is None else "%.3f" % conf, datetime.now().isoformat()])
            f.flush()

            if status in ("followed", "clicked_unverified"):
                ok += 1
                consec = 0
                if t["sec_uid"]:
                    done.add(t["sec_uid"])
                    json.dump(sorted(done), open(args.done, "w"), ensure_ascii=False)
            elif status == "already":
                already += 1
                consec = 0
                if t["sec_uid"]:
                    done.add(t["sec_uid"])
                    json.dump(sorted(done), open(args.done, "w"), ensure_ascii=False)
            else:
                bad += 1
                consec += 1

            if consec >= 5:
                print("\n⚠️ 连续 %d 次失败 → 早停防限流/封号（cookie失效或被风控？换时段再跑）" % consec)
                break
            # 回主页干净态给下一条：搜索是从顶栏发起的，Esc 收一下结果浮层
            try:
                pyautogui.press("esc")
            except Exception:
                pass
            if i < len(targets):
                time.sleep(random.randint(dm.MIN_INTERVAL, dm.MAX_INTERVAL))

    print("\n=== 完成 关注 %d / 已关跳过 %d / 失败 %d | 日志 %s ===" % (ok, already, bad, log))
    print("覆盖进度看 followed.json(%d 人已关)。剩余明天再跑同命令(自动跳过已关)。" % len(done))


if __name__ == "__main__":
    main()
