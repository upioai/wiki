#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_realtime_touch_consume_wuying — 路 B·无影消费端：tail 实时触达队列 → 查话术表 → 调
douyin_comment_grounded.py 发【三连(点赞+评论1+评论2)】。与监控脚本同机常驻、串行消费。

闭环里它的位置：
  _realtime_touch_watch_wuying.py 侦测 lead 新视频(<10min) → 写队列 jsonl
    → 【本脚本】tail 队列 → 按 sec_uid 查 _gen-realtime-touch-table.py 出的话术表
    → 写 1 行 contacts CSV → 调 douyin_comment_grounded.py(三连) → 追加 comment_log
    → comment_log 摆渡回 mac → mark-second-touch.ts --from-comment-log 写回 second_touch_state。

无影够不到 Supabase，「同人每天≤1次」去重在本地做（--touched 持久化 sec_uid→date）；
DB 的「≥1天间隔 / 最多 7 次」长节奏在 mac 端 mark-second-touch + 下次 pool 再生成时 enforce（分层）。

队列记录(每行)：{aweme_id, sec_uid, name, comment_id, age_min, desc, video_url}
话术表：{ "<sec_uid>": {name, comment1, comment2, comment_id, status}, ... }
contacts CSV 列(对齐 douyin_comment_grounded main 版三连接口)：
  douyin_id(=昵称搜索词) nickname message(=评论1) message2(=评论2,可空) has_works _comment_id _sec_uid _dispatch_id

前置(无影)：抖音 PC 前台最大化 / 分辨率锁 2560×1600 / 输入法英文 / .env 有 OCR key（douyin_comment_grounded 要）。
⚠️ 调起 douyin_comment_grounded 期间它全程控鼠标键盘 → 别碰；本脚本串行(一次一条)，不并发抢 GUI。
   监控脚本只读 API、不碰 GUI，可与本脚本同机并跑。

用法(无影，脚本目录 C:\akke-wuying\wuying-dm\)：
  # 端点模式(推荐，去 mac 摆渡)：池子/话术表走 HTTP 拉、发前 claim 领号、发后 complete 回写 e2e。
  # token/account 也可放 .env(PT_ENDPOINT_SECRET / AKKE_ACCOUNT_ID)，则 --api-base 一个就够。
  set PT_ENDPOINT_SECRET=xxx & set AKKE_ACCOUNT_ID=c5d236e1-...
  python _realtime_touch_consume_wuying.py --api-base https://akke.vercel.app/api/internal/pt
  # 直连 Supabase 模式(无影正解，端点对无影是死路 HTTP 000)：本地话术表 + 直调 RPC 领号/回写。
  # 需 .env 有 SUPABASE_URL + (SUPABASE_SCOPED_JWT+ANON 或 SERVICE_ROLE_KEY) + AKKE_ORG_ID + AKKE_ACCOUNT_ID。
  python _realtime_touch_consume_wuying.py --db
  python _realtime_touch_consume_wuying.py                       # 本地表模式(旧，无 DB)：常驻自动发
  python _realtime_touch_consume_wuying.py --confirm             # 调起时逐条 y/n 把关
  python _realtime_touch_consume_wuying.py --once                # 把当前队列消费一遍就退(测试用)
  python _realtime_touch_consume_wuying.py --dry-run             # 只查表/写CSV、不真调发送脚本(端点模式不领号/不回写)
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# 评论1 JIT 生成器（同目录 _jit_comment1_wuying.py）：逮到视频那刻看视频标题/正文现场生成对视频
# 内容的反应；空正文/异常/命中红线 → 「赞赞赞」兜底（绝不空）。
import importlib.util as _ilu
_jspec = _ilu.spec_from_file_location("jitc1", os.path.join(HERE, "_jit_comment1_wuying.py"))
jit = _ilu.module_from_spec(_jspec)
_jspec.loader.exec_module(jit)

# ⚠️ 2026-06-12 fanny 拍板：评论2 逐字搬首触原文【照发、不挡空】（带促销/微信词也发，封号风险走
# 节奏层兜：同人每天≤1、累计≤7）。评论1 是 JIT 干净生成（prompt 禁红线词、命中即退赞赞赞）。
# 所以原来那道「公开评论违规闸→跳过整条」已撤——不再因红线词跳过任何触达。

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _default(name):
    return os.path.join(HERE, name)


def load_json(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return default
    return default


# ── 抖音号反查（DM 通道同款 enrich，镜像 wuying_poll_agent.resolve_handles）──────
# 把 sec_uid 反查成唯一抖音号写进 CSV 的 douyin_id 列 → executor(douyin_comment_grounded)
# 见 query!=nick 就开 expected_number「按号精确核身 + 扫描多条挑本人」，根治撞名错评
# (wrong_user)。best-effort：未装 f2 / 无 cookie.txt / 限流 → 返回 ""，调用方回退昵称搜
# （行为同今天）。同目录需 cookie.txt（登录态 Cookie 头整串），匿名 ttwid 该接口大概率失败。
_handle_cookie = None  # None=未加载；""=加载过无 cookie；非空=cookie 头串（进程内缓存）


def _load_cookie_str():
    # 优先复用 DM 通道的 DB 登录态 cookie：import wuying_poll_agent 会顺带 load .env，它的
    # _load_cookie_str 从 accounts.cookie_data 取（发送号→任意活跃号）。这样 cookie 走 DB、
    # 自动续期、且不必把登录 cookie 落到公网摆渡。DB 不可达/导入失败 → 回退本目录 cookie.txt。
    try:
        from wuying_poll_agent import _load_cookie_str as _dm_cookie
        s = _dm_cookie()
        if s:
            return s
    except Exception:
        pass
    p = os.path.join(HERE, "cookie.txt")
    if os.path.exists(p):
        try:
            return open(p, encoding="utf-8-sig").read().strip().replace("\n", " ")
        except Exception:
            return ""
    return ""


def resolve_number(sec, log=print):
    """sec_uid → 抖音号；命中返回号串，无号/反查不可用/异常返回 ""（调用方回退昵称搜）。"""
    global _handle_cookie
    if not sec:
        return ""
    try:
        from resolve_douyin_numbers import resolve_batch
    except Exception as e:
        log('  [handle] 抖音号反查不可用(%s) → 按昵称搜(撞名风险)。装: pip install "f2==0.0.1.7" httpx' % e)
        return ""
    if _handle_cookie is None:
        _handle_cookie = _load_cookie_str()
        log("  [handle] cookie %s" % (("就绪(%d字符)" % len(_handle_cookie))
                                      if _handle_cookie else "缺失 → 接口大概率失败,回退昵称"))
    try:
        m = resolve_batch([sec], _handle_cookie)
        return ((m.get(sec) or {}).get("number") or "").strip()
    except Exception as e:
        log("  [handle] 反查异常(%s) → 回退昵称" % e)
        return ""


# ── 云端端点（去 mac 摆渡 + 跨通道去重 + 实时回写）─────────────────────────────
# 无影够不到 Supabase，但能 HTTP 打 Vercel 端点（Vercel 有库权限）。三件事：
#   GET  {base}/pool?account=&token=     → 池子+评论2表（取代 mac 重生成+gist 摆渡话术表）
#   POST {base}/claim                    → 发送前原子领号（今天碰过/冷却/已停 → 不发）
#   POST {base}/complete                 → 验证发出后回写（带 aweme+发布时间，算 e2e 落库）
# 端点失败一律软降级（pool 回退本地缓存表；claim 失败保守跳过该条；complete 失败只告警，
# comment_log 摆渡回 mac 跑 mark-second-touch.ts 作 backstop）。stdlib urllib，无三方依赖。

def _api(method, url, token, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_get_pool(base, token, account):
    """返回 {sec_uid: {name, comment2, comment_id}}（话术表形态）。失败抛异常由调用方降级。"""
    url = f"{base.rstrip('/')}/pool?account={account}&token={urllib.request.quote(token)}"
    payload = _api("GET", url, token)
    table = {}
    for r in payload.get("pool", []):
        sec = r.get("secUid")
        if sec:
            table[sec] = {"name": r.get("name") or "",
                          "comment2": r.get("comment2") or "",
                          "comment_id": r.get("commentId") or ""}
    return table


def api_claim(base, token, account, sec):
    """原子领号。返回 (claimed: bool, reason: str)。"""
    payload = _api("POST", f"{base.rstrip('/')}/claim", token,
                   {"douyinUserId": sec, "accountId": account})
    return bool(payload.get("claimed")), payload.get("reason", "?")


def api_complete(base, token, account, sec, comment_id, aweme_id, create_time):
    """验证发出后回写 touched + e2e（create_time = 发布 epoch；sentAt 默认服务端 now）。"""
    return _api("POST", f"{base.rstrip('/')}/complete", token, {
        "douyinUserId": sec, "accountId": account,
        "sourceCommentId": comment_id or None, "awemeId": aweme_id,
        "videoPublishedAt": create_time,
    })


# ── 直连 Supabase 模式（无影正解：端点对无影是死路 HTTP 000，改直打 PostgREST RPC）───────
# 镜像 wuying_poll_agent 的 DM/RC 通道：apikey=anon 过网关 + Authorization=Bearer <scoped JWT
# role=wuying_worker>（回退 service_role key）。claim_second_touch / mark_second_touch 已在
# 20260618114439 迁移 GRANT 给 wuying_worker（两 RPC 是 SECURITY DEFINER，绕 RLS、调用方只需
# EXECUTE）。这条路替掉端点模式的 /claim+/complete 和 comment_log 摆渡回写——话术表(评论2)
# 仍读本地文件(无对应 RPC、维持摆渡)，故称「本地话术表 + DB 领号 + DB 回写」。

def _load_env_for_db():
    # consume 默认不 load .env（本地表模式不需要）；--db 要 SUPABASE_* / AKKE_ORG_ID，显式加载。
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    for p in (os.path.join(HERE, ".env"), os.path.join(os.path.dirname(HERE), ".env")):
        if os.path.exists(p):
            load_dotenv(p, override=False)


def setup_db(emit, account):
    """读 .env 组装直连配置；缺关键 env 返回 None（调用方降级本地表模式）。"""
    _load_env_for_db()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    scoped = os.environ.get("SUPABASE_SCOPED_JWT")
    anon = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    service = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    org = os.environ.get("AKKE_ORG_ID")
    if scoped and anon:
        apikey, bearer = anon, scoped      # 受控角色(无影优先)
    else:
        apikey = bearer = service          # 回退 service_role
    missing = [n for n, v in [
        ("SUPABASE_URL", url), ("AKKE_ORG_ID", org),
        ("AKKE_ACCOUNT_ID(--account)", account),
        ("SUPABASE_SCOPED_JWT+ANON 或 SERVICE_ROLE_KEY", bearer)] if not v]
    if missing:
        emit("❌ --db 缺 env: %s" % ", ".join(missing))
        return None
    return {"url": url.rstrip("/"), "apikey": apikey, "bearer": bearer, "org": org}


def _db_rpc(db, name, payload, timeout=30):
    req = urllib.request.Request(
        f"{db['url']}/rest/v1/rpc/{name}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"apikey": db["apikey"], "Authorization": f"Bearer {db['bearer']}",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else None


def db_claim(db, account, sec):
    """直调 claim_second_touch（RETURNS TABLE → array）。返回 (claimed, reason)。"""
    rows = _db_rpc(db, "claim_second_touch",
                   {"p_org_id": db["org"], "p_douyin_user_id": sec, "p_account_id": account})
    row = (rows[0] if isinstance(rows, list) and rows else (rows or {}))
    return bool(row.get("claimed")), row.get("reason", "?")


def db_complete(db, account, sec, comment_id, video_pub_iso, e2e_min):
    """直调 mark_second_touch(touched)（RETURNS 复合行 → object）。返回回写后的行。"""
    res = _db_rpc(db, "mark_second_touch", {
        "p_org_id": db["org"], "p_douyin_user_id": sec, "p_account_id": account,
        "p_source_comment_id": comment_id or None, "p_status": "touched",
        "p_video_published_at": video_pub_iso, "p_e2e_minutes": e2e_min})
    return res[0] if isinstance(res, list) and res else (res or {})


def read_comment_log_row(aweme_id, sec):
    """读今天 comment_log，取本次触达那行（按 _aweme_id 命中，回退 _sec_uid 最后一行）。
    给 second_touch_log 拿三连分步时刻 like_at/comment1_at/comment2_at + 各步 status。无则 {}。"""
    path = os.path.join(HERE, "comment_log_%s.csv" % datetime.now().strftime("%Y%m%d"))
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return {}
    hit = [r for r in rows if (r.get("_aweme_id") or "") == aweme_id]
    if not hit:
        hit = [r for r in rows if (r.get("_sec_uid") or "") == sec]
    return hit[-1] if hit else {}


def db_log(db, account, sec, comment_id, touch_seq, video_url, video_pub_iso, e2e_min, lr):
    """直调 log_second_touch：INSERT 一行 second_touch_log（append-only，完整时间线）。
    lr = read_comment_log_row 的行（带 like_at/comment1_at/comment2_at + 各步 status）。"""
    def _t(v):
        v = (v or "").strip()
        return v or None
    return _db_rpc(db, "log_second_touch", {
        "p_org_id": db["org"], "p_douyin_user_id": sec, "p_account_id": account,
        "p_source_comment_id": comment_id or None, "p_touch_seq": touch_seq,
        "p_touched_video_aweme_id": (lr.get("_aweme_id") or None),
        "p_touched_video_url": video_url or None,
        "p_touched_video_published_at": video_pub_iso,
        "p_like_at": _t(lr.get("like_at")),
        "p_comment1_at": _t(lr.get("comment1_at")),
        "p_comment2_at": _t(lr.get("comment2_at")),
        "p_like_status": _t(lr.get("liked")),
        "p_comment1_status": _t(lr.get("status")),
        "p_comment2_status": _t(lr.get("status2")),
        "p_comment1_text": _t(lr.get("message")),
        "p_comment2_text": _t(lr.get("message2")),
        "p_e2e_minutes": e2e_min})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", default=_default("realtime-touch-queue.jsonl"))
    ap.add_argument("--table", default=_default("realtime-touch-table.json"))
    ap.add_argument("--consumed", default=_default("realtime-touch-consumed.json"))
    ap.add_argument("--touched", default=_default("realtime-touch-touched.json"))
    ap.add_argument("--comment-script", default=_default("douyin_comment_grounded.py"))
    ap.add_argument("--csv-tmp", default=_default("realtime-touch-contacts.csv"))
    ap.add_argument("--log", default=_default("realtime-touch-consume.log"))
    ap.add_argument("--interval-sec", type=float, default=30)
    ap.add_argument("--max-detect-age-min", type=float, default=20,
                    help="队列记录侦测时间超过此值就跳过(触达窗口已过，发了价值低)")
    ap.add_argument("--confirm", action="store_true", help="调起 douyin_comment_grounded 时逐条 y/n")
    ap.add_argument("--dry-run", action="store_true", help="只查表/写CSV、不真调发送脚本")
    ap.add_argument("--once", action="store_true", help="消费当前队列一遍就退")
    ap.add_argument("--duration-min", type=float, default=1440)
    # 直连 Supabase（无影正解）：本地话术表 + 直调 claim_second_touch/mark_second_touch RPC。
    # 端点对无影是死路(HTTP 000)，--db 走 PostgREST 同 DM/RC 通道。读 .env 的 SUPABASE_* +
    # AKKE_ORG_ID + AKKE_ACCOUNT_ID；缺则自动降级本地表模式。与 --api-base 互斥（--db 优先）。
    ap.add_argument("--db", action="store_true",
                    help="直连 Supabase 调 RPC 领号/回写（去 comment_log 摆渡；无影正解）")
    # 云端端点（设了 --api-base 即启用：池子/话术表走端点拉、发前 claim、发后 complete）。
    # 不设则维持旧行为（本地话术表 + 本地 touched 日去重，无 DB 回写）。
    ap.add_argument("--api-base", default=os.environ.get("PT_API_BASE", ""),
                    help="如 https://akke.vercel.app/api/internal/pt（设了就用端点，去 mac 摆渡）")
    ap.add_argument("--token",
                    default=os.environ.get("PT_ENDPOINT_SECRET") or os.environ.get("FLY_WORKER_SECRET", ""),
                    help="端点鉴权 token（默认读 PT_ENDPOINT_SECRET，没有则回退 FLY_WORKER_SECRET）")
    ap.add_argument("--account", default=os.environ.get("AKKE_ACCOUNT_ID", ""),
                    help="本机 messaging 账号 uuid（默认读 env AKKE_ACCOUNT_ID）")
    args = ap.parse_args()

    use_api = bool(args.api_base and args.token and args.account)

    consumed = set(load_json(args.consumed, []))     # 已处理 aweme_id
    touched = load_json(args.touched, {})            # sec_uid -> 'YYYY-MM-DD'（同人每天≤1）

    def emit(msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save_state():
        json.dump(sorted(consumed), open(args.consumed, "w"), ensure_ascii=False)
        json.dump(touched, open(args.touched, "w"), ensure_ascii=False)

    # 直连 Supabase（--db）：与 --api-base 互斥（端点对无影是死路，--db 优先）。配置不全自动降级。
    db = None
    if args.db:
        if use_api:
            emit("⚠️ --db 与 --api-base 同设 → 用 --db 直连（端点对无影 HTTP 000），忽略 --api-base")
            use_api = False
        db = setup_db(emit, args.account)
        if db is None:
            emit("→ --db 配置不全，退回本地表模式（无 DB 去重/回写）；检查 .env")
    use_db = db is not None

    _mode = ("直连Supabase(本地话术表+DB领号+DB回写) " if use_db
             else ("端点模式(拉表+领号+回写) " + args.api_base if use_api else "本地表模式(无DB)"))
    emit(f"消费端启动(无影) | 队列 {os.path.basename(args.queue)} | {_mode} | "
         f"{'自动发' if not args.confirm else '逐条确认'}{' | DRY-RUN' if args.dry_run else ''}")
    if args.api_base and not use_api and not use_db:
        emit("⚠️ 设了 --api-base 但缺 --token/--account → 降级本地表模式（端点不生效）")

    start = time.monotonic()
    deadline = start + args.duration_min * 60
    sent = skipped = 0
    while True:
        # 话术表：端点模式每轮 GET 拉最新（去 mac 摆渡）+ 写穿本地缓存；失败回退本地缓存表。
        # 本地模式：读 mac 摆渡来的本地文件（旧行为）。
        if use_api:
            try:
                table = api_get_pool(args.api_base, args.token, args.account)
                json.dump(table, open(args.table, "w", encoding="utf-8"), ensure_ascii=False)
            except Exception as e:
                table = load_json(args.table, {})
                emit(f"⚠️ 拉池子端点失败({type(e).__name__})，回退本地缓存表 {len(table)} 人")
        else:
            table = load_json(args.table, {})            # 每轮重读，支持热更新话术表
        records = []
        if os.path.exists(args.queue):
            with open(args.queue, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        records.append(json.loads(ln))
                    except Exception:
                        continue

        for rec in records:
            aweme_id = rec.get("aweme_id")
            sec = rec.get("sec_uid")
            if not aweme_id or not sec or aweme_id in consumed:
                continue
            consumed.add(aweme_id)                   # 先标已处理：崩了也不重复触达同一视频
            if rec.get("gate") == "expired":         # 2026-06-16 超时项(发布>20min)只为上页/分析落盘,绝不触达
                skipped += 1
                save_state()
                continue
            name = rec.get("name") or ""
            today = datetime.now().strftime("%Y-%m-%d")

            # 闸1：侦测窗口过期（队列积压时别再发陈旧的）
            det = rec.get("detected_at")
            if det:
                try:
                    age = (datetime.now().astimezone() -
                           datetime.fromisoformat(det)).total_seconds() / 60
                    if age > args.max_detect_age_min:
                        skipped += 1
                        emit(f"⏰ 跳过(侦测超时 {age:.0f}min>{args.max_detect_age_min:.0f}) ★{name} {aweme_id}")
                        save_state()
                        continue
                except Exception:
                    pass

            # 闸2：同人今天已触达（本地 1/天，DB 长节奏在 mac 回写时 enforce）
            if touched.get(sec) == today:
                skipped += 1
                emit(f"🔁 跳过(今日已触达) ★{name} {sec[:12]}")
                save_state()
                continue

            # 闸2b：跨通道/跨机原子领号（--db 直连 或 端点）。今天被任一通道碰过/冷却/已停 → 作废本条
            # （= PM「同一用户重复拉到要作废」）。dry-run 不领号（不 mutate DB）。
            if (use_db or use_api) and not args.dry_run:
                try:
                    if use_db:
                        ok, why = db_claim(db, args.account, sec)
                    else:
                        ok, why = api_claim(args.api_base, args.token, args.account, sec)
                except Exception as e:
                    ok, why = False, f"claim异常:{type(e).__name__}"
                if not ok:
                    skipped += 1
                    emit(f"🚫 跳过(领号未通过:{why}) ★{name} {sec[:12]}")
                    save_state()
                    continue

            # 评论2：查表逐字搬首触原文（照发、不挡空；带促销/微信词也发）。无 dm_content（多为
            # RC-only，worklist 取不到）→ 评论2 空，消费端只发 点赞+评论1。
            row = table.get(sec) or {}
            comment2 = (row.get("comment2") or "").strip()
            cmt_id = row.get("comment_id") or rec.get("comment_id") or ""

            # 评论1：JIT —— 看这条新视频的标题/正文现场生成对视频内容的反应（jit 内部对空正文/异常/
            # 红线词都退「赞赞赞」兜底，必非空）。desc 由监控脚本带全文进队列。
            comment1 = jit.gen_comment1(rec.get("desc", ""))

            # 抖音号 enrich（DM 同款）：sec_uid → 唯一抖音号，命中则 douyin_id 列填号、executor 用号
            # 精确核身扫描挑本人；未命中(无号/限流/无 cookie)回退昵称搜（撞名风险，行为同今天）。
            number = resolve_number(sec, emit)
            query = number or name
            if number:
                emit(f"   [handle] {sec[:12]} → 抖音号 {number}（按号搜）")

            # e2e 锚点：视频发布(create_time, epoch秒) → 现在 的分钟数；--db/端点回写都用。
            ct = rec.get("create_time")
            video_pub_iso = e2e_min = None
            if ct:
                try:
                    cti = int(float(ct))
                    video_pub_iso = datetime.fromtimestamp(cti, tz=timezone.utc).isoformat()
                    e2e_min = round((time.time() - cti) / 60, 1)
                except Exception:
                    pass

            # 写 1 行 contacts CSV（列对齐 douyin_comment_grounded 三连接口）
            # _aweme_id/_create_time 透传进 comment_log（douyin_comment_grounded 原样带回未知列），
            # 作 e2e backstop/审计。⚠️ 端点模式下 e2e 已由 complete 实时回写，别再跑
            # mark-second-touch.ts --from-comment-log（非幂等，会把 touch_count 重复 ++）。
            cols = ["douyin_id", "nickname", "message", "message2",
                    "has_works", "_comment_id", "_sec_uid", "_dispatch_id",
                    "_aweme_id", "_create_time"]
            with open(args.csv_tmp, "w", encoding="utf-8-sig", newline="") as cf:
                w = csv.writer(cf)
                w.writerow(cols)
                w.writerow([query, name, comment1, comment2, 1, cmt_id, sec, "",
                            aweme_id, rec.get("create_time") or ""])

            emit(f"🔥 触达 ★{name} {aweme_id} | 评论1={comment1[:24]} | "
                 f"评论2={'(无)' if not comment2 else comment2[:18]}")
            if args.dry_run:
                emit(f"   [DRY-RUN] 已写 CSV {os.path.basename(args.csv_tmp)}，不调发送脚本")
                touched[sec] = today
                sent += 1
                save_state()
                continue

            # 调 douyin_comment_grounded.py 发三连（串行、等它跑完）
            cmd = [sys.executable, args.comment_script, args.csv_tmp]
            if args.confirm:
                cmd.append("--confirm")
            # ⏱️ 执行器硬超时（2026-06-16 教训：一次 GUI 卡死无超时把整条流水线冻了 10h、
            #    全天 0 真发）。默认 300s，超时自动 kill+跳过，不再卡死全天。
            # 📝 自动模式把执行器 stdout/stderr 落 executor-runs.log（带阶段时间戳），
            #    下次卡死能定位卡在哪一步；--confirm 交互模式保留控制台（要键盘输入 y/n）。
            exec_timeout = float(os.environ.get("AKKE_EXEC_TIMEOUT_SEC", "300"))
            exec_log_path = _default("executor-runs.log")
            try:
                if args.confirm:
                    r = subprocess.run(cmd, cwd=HERE, timeout=exec_timeout)
                    rc = r.returncode
                else:
                    with open(exec_log_path, "a", encoding="utf-8") as lf:
                        lf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                                 f"{name} {aweme_id} =====\n")
                        lf.flush()
                        r = subprocess.run(cmd, cwd=HERE, timeout=exec_timeout,
                                           stdout=lf, stderr=subprocess.STDOUT)
                    rc = r.returncode
                if rc == 0:
                    sent += 1
                    touched[sec] = today
                    emit(f"   ✅ 发送脚本返回 0（实发以抖音气泡 + comment_log 为准）")
                    # 实时回写 DB（--db 直连 或 端点）：touched + e2e（发布→完成）。失败只告警，
                    # comment_log 摆渡回 mac 跑 mark-second-touch.ts 作 backstop。
                    if use_db:
                        try:
                            cr = db_complete(db, args.account, sec, cmt_id, video_pub_iso, e2e_min)
                            emit(f"   ↩️ 回写DB(直连): touch_count={cr.get('touch_count')} "
                                 f"e2e={cr.get('last_e2e_minutes')}min ladder={cr.get('ladder_status')}")
                            # 顺带 INSERT 一行 second_touch_log（完整时间线：三连分步时刻 + 视频链接）。
                            # 读 comment_log 拿 like_at/comment1_at/comment2_at + 各步 status；失败只告警。
                            try:
                                lr = read_comment_log_row(aweme_id, sec)
                                db_log(db, args.account, sec, cmt_id, cr.get("touch_count"),
                                       rec.get("video_url"), video_pub_iso, e2e_min, lr)
                                emit(f"   🧾 流水落库 second_touch_log seq={cr.get('touch_count')} "
                                     f"like={lr.get('like_at','')[11:16]} c1={lr.get('comment1_at','')[11:16]} "
                                     f"c2={lr.get('comment2_at','')[11:16]}")
                            except Exception as e:
                                emit(f"   ⚠️ log_second_touch 失败({type(e).__name__})；状态已回写，仅流水时间线缺这行")
                        except Exception as e:
                            emit(f"   ⚠️ 直连 mark_second_touch 失败({type(e).__name__})；comment_log 摆渡兜底")
                    elif use_api:
                        try:
                            cr = api_complete(args.api_base, args.token, args.account,
                                              sec, cmt_id, aweme_id, rec.get("create_time"))
                            emit(f"   ↩️ 回写DB: touch_count={cr.get('touchCount')} "
                                 f"e2e={cr.get('e2eMinutes')}min ladder={cr.get('ladderStatus')}")
                        except Exception as e:
                            emit(f"   ⚠️ complete 回写失败({type(e).__name__})；comment_log 摆渡兜底")
                else:
                    skipped += 1
                    emit(f"   ⚠️ 发送脚本退出码 {rc}（本条不计已触达，下次队列不会重试因已 consumed）")
            except subprocess.TimeoutExpired:
                skipped += 1
                emit(f"   ⏱️ 执行器超时 {exec_timeout:.0f}s → 已 kill、跳过本条"
                     f"（防卡死全天；卡在哪看 executor-runs.log）")
            except Exception as e:
                skipped += 1
                emit(f"   ❌ 调发送脚本异常: {type(e).__name__}: {str(e)[:80]}")
            save_state()

        if args.once:
            break
        if time.monotonic() >= deadline:
            emit("到达 --duration-min 上限，退出")
            break
        time.sleep(args.interval_sec)

    _tail = ("直连DB已实时回写，无需摆渡 comment_log" if use_db
             else "comment_log 摆渡回 mac 跑 mark-second-touch.ts")
    emit(f"=== 结束 | 触达 {sent} / 跳过 {skipped} | {_tail} ===")


if __name__ == "__main__":
    main()
