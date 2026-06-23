"""_realtime_touch_watch_wuying — 路 B 落地·无影常驻版：实时盯关注流，逮「≥1天没回·高/中意向 lead」
<10min 内的新视频 → 写可触达队列（无影另一脚本消费去点赞+双评论）。

与 _realtime_touch_watch.py（mac·路 A·Supabase 取 cookie）同一判定逻辑，区别只两处：
  1. **cookie 不从 Supabase 取（无影够不到 Supabase），改从本地文件/参数直接喂**——照
     _probe_follow_feed_wuying.py 的 load_cookie。
  2. **默认路径全部相对脚本同目录**（无影是 Windows，没有 /tmp），lead 池/队列/seen/log
     都落脚本旁；可 --pool/--queue 覆盖。

本脚本只做【侦测+判定】(后台 cookie 接口、只读、不写抖音)。命中可触达的写
队列 jsonl，消费脚本读它去点赞评论。aweme_id 去重落盘防重复触达。

前置（无影 PowerShell，一次性）：
  pip install f2==0.0.1.7        # 硬钉这版(它锁 httpx==0.27.2，别乱升 httpx)
  # cookie 文件：从 mac 摆渡过来的那串，存成 dy_cookie.txt(放脚本同目录或 --cookie-file 指定)
  # lead 池：把 mac 的 special-follow-饭粒.json 摆渡到脚本同目录(或 --pool 指定)

用法（无影，脚本目录 C:\akke-wuying\wuying-dm\）：
  python _realtime_touch_watch_wuying.py                       # 读同目录 dy_cookie.txt + special-follow-饭粒.json
  python _realtime_touch_watch_wuying.py --cookie-file dy_cookie.txt --pool special-follow-饭粒.json \
    --pages 3 --interval-sec 60 --max-age-min 10 --duration-min 1440
"""
import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# 复用无影探针的 fetch_feed / ago / load_cookie（同端点同签名，cookie 从文件读）
spec = importlib.util.spec_from_file_location(
    "pfw", os.path.join(HERE, "_probe_follow_feed_wuying.py"))
pfw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pfw)

# 无影 PowerShell 控制台默认 cp936，emoji/中文 print 会炸；强制 utf-8（写文件本就 utf-8）。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_pool(path):
    """rows[].sec_uid|douyin_user_id → {name, comment_id}。"""
    doc = json.load(open(path, encoding="utf-8"))
    rows = doc.get("rows", doc) if isinstance(doc, dict) else doc
    pool = {}
    for r in rows:
        sec = r.get("sec_uid") or r.get("douyin_user_id")
        if sec:
            pool[sec] = {"name": r.get("name") or r.get("customer_name", ""),
                         "comment_id": r.get("comment_id", "")}
    return pool


def _default(name):
    return os.path.join(HERE, name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", default=None, help="直接传 cookie 串（优先）")
    ap.add_argument("--cookie-file", default=None, help="cookie 文件路径（默认同目录 dy_cookie.txt）")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--interval-sec", type=float, default=60)
    ap.add_argument("--max-age-min", type=float, default=10, help="发布<=此值才触达;同时挡掉历史回灌")
    ap.add_argument("--pool", default=_default("special-follow-饭粒.json"))
    ap.add_argument("--queue", default=_default("realtime-touch-queue.jsonl"))
    ap.add_argument("--seen", default=_default("realtime-touch-seen.json"))
    ap.add_argument("--log", default=_default("realtime-touch.log"))
    ap.add_argument("--duration-min", type=float, default=1440)
    args = ap.parse_args()

    cookie = pfw.load_cookie(args)  # 从 --cookie / --cookie-file / 同目录 dy_cookie.txt 读
    if not cookie:
        print("没找到 cookie（--cookie / --cookie-file / 同目录 dy_cookie.txt 都空）"); return
    if not os.path.exists(args.pool):
        print(f"lead 池不存在：{args.pool}（把 mac 的 special-follow-饭粒.json 摆渡过来）"); return
    pool = load_pool(args.pool)
    seen = set()
    if os.path.exists(args.seen):
        try:
            seen = set(json.load(open(args.seen)))
        except Exception:
            seen = set()

    def emit(msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    emit(f"实时触达侦测启动(无影) | cookie {len(cookie)}字节 | lead池 {len(pool)} | "
         f"每 {args.interval_sec:.0f}s 拉 {args.pages} 页 | 触达闸 发布<={args.max_age_min:.0f}min")

    # 用单调时钟做计时（Date.now 类的 wall-clock 跳变不影响时长判断）
    start = __import__("time").monotonic()
    deadline = start + args.duration_min * 60
    time_mod = __import__("time")
    cycle = 0
    actionable = 0
    expired = 0
    while time_mod.monotonic() < deadline:
        cycle += 1
        cursor = 0
        lead_seen_this = 0
        for _ in range(args.pages):
            data, err = pfw.fetch_feed(cookie, cursor)
            if err:
                emit(f"[轮{cycle}] feed 错误: {err}")
                break
            for it in (data.get("data") or []):
                aw = it.get("aweme") if isinstance(it.get("aweme"), dict) else it
                au = aw.get("author") or {}
                sec = au.get("sec_uid")
                ct = aw.get("create_time")
                aweme_id = aw.get("aweme_id") or aw.get("awemeId")
                if not sec or not ct or not aweme_id:
                    continue
                if sec not in pool:
                    continue                              # 只关心 lead
                if aweme_id in seen:
                    continue                              # 已处理过这条视频
                lead_seen_this += 1
                age = (pfw.ago(ct) or 0) * 1440           # 天→分钟
                info = pool[sec]
                desc = (aw.get("desc") or "")            # 全文进队列（JIT 评论1 评视频要靠它）；log 里才截短
                seen.add(aweme_id)
                # 2026-06-16 超时项也落盘：≤闸=gate:actionable（去触达）；>闸=gate:expired（只为上页/分析,
                # 消费端按 gate 跳过、绝不触达）。两类都带 aweme_id/age_min,页面 20–60min 边缘档才有数据。
                gate = "actionable" if age <= args.max_age_min else "expired"
                rec = {"detected_at": datetime.now(timezone.utc).isoformat(),
                       "aweme_id": aweme_id, "sec_uid": sec, "name": info["name"],
                       "comment_id": info["comment_id"], "age_min": round(age, 1),
                       "create_time": ct,  # 视频发布 epoch(秒)；消费端 complete 端点算 e2e 用
                       "desc": desc, "video_url": f"https://www.douyin.com/video/{aweme_id}",
                       "gate": gate}
                with open(args.queue, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if gate == "actionable":
                    actionable += 1
                    emit(f"🔥 可触达! ★{info['name']} 发布{age:.0f}min前 | {desc[:40]} | aweme={aweme_id} → 入队点赞+双评论")
                else:
                    expired += 1
                    emit(f"⏰ 放弃(超时·已落盘 gate=expired,不触达) ★{info['name']} 发布{age:.0f}min前(>{args.max_age_min:.0f}) | aweme={aweme_id} | {desc[:40]}")
            cursor = data.get("cursor") or cursor
            if not data.get("has_more"):
                break
        json.dump(sorted(seen), open(args.seen, "w"), ensure_ascii=False)
        if cycle % 10 == 0 or lead_seen_this:
            emit(f"[轮{cycle} | {(time_mod.monotonic()-start)/60:.0f}min] 本轮 lead 新视频 {lead_seen_this} | "
                 f"累计 可触达 {actionable} / 超时放弃 {expired}")
        if time_mod.monotonic() + args.interval_sec < deadline:
            time_mod.sleep(args.interval_sec)
        else:
            break
    emit(f"=== 结束 {cycle} 轮 | 可触达 {actionable} / 超时 {expired} | 队列 {args.queue} ===")


if __name__ == "__main__":
    main()
