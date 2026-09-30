#!/usr/bin/env python3
"""wuying_tuwen_agent.py — 云电脑端 tuwen creator_web 通道独立 poll agent.

独立跑, 不混进 wuying_poll_agent.py (DM/RC/二触) — 单独启停 + 单独进程 +
物理上独立窗口 (tuwen 用 Edge creator.douyin.com; DM 用抖音 PC 客户端).

启动:
  python worker/scripts/wuying-tuwen/wuying_tuwen_agent.py

主循环 (默认 30s):
  1. claim_tuwen_job(p_channel='creator_web', p_assignee=AKKE_TUWEN_ASSIGNEE) 拉单
  2. 每行: 构造 manifest.json (title/body/image_urls/schedule_at)
  3. subprocess 调 ./creator_publisher.py --manifest <path> --commit
  4. exit 0 → complete_tuwen_job('published'); 非 0 → 'failed'
  5. 清理 manifest 文件 (creator_publisher.py 已清 staging 图)

env (从同目录的 ../wuying-dm/.env 自动加载, 跟 DM 通道共用 Supabase 鉴权):
  SUPABASE_URL                       必填
  SUPABASE_SERVICE_ROLE_KEY          必填 (service role 写库)
  AKKE_TUWEN_ASSIGNEE                必填 (拉单按 assignee 过滤, 如 xiaxia)
  AKKE_TUWEN_POLL_INTERVAL           默认 300s (周发 4 条, 入队后 5min 内开始足够; 想快设 30)
  AKKE_TUWEN_CLAIM_LIMIT             默认 1 (一轮拉 1 条, 避免连发触发风控)
  AKKE_TUWEN_MANIFEST_DIR            manifest 临时落盘目录, 默认 C:\\akke-wuying

不需要 AKKE_TUWEN_ENABLED — 启用 = 跑这个进程; 不启用 = 别跑.

与 DM 通道关系: 独立进程独立窗口, DM 通道 (wuying_poll_agent.py) 任何时刻都不会
被这个 agent 搅. 同事的 DM 机器零侵入.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ── 加载 .env (同 DM 通道共用; stdlib 解析, 不依赖 python-dotenv) ──────────
_THIS_DIR = Path(__file__).resolve().parent


def _load_dotenv_stdlib(path: Path) -> None:
    """简易 .env 解析: KEY=VALUE 一行, # 注释, 引号去掉. 只补充不在 os.environ 的 key."""
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception as e:
        print(f'⚠ .env 解析异常 ({path}: {e}), 靠 shell env 兜底', file=sys.stderr)


# 多路径 fallback: ①AKKE_WUYING_DM_DIR env ②同目录(深圳机 C:\akke-wuying 扁平) ③repo 结构 ../wuying-dm ④云电脑扁平 C:\akke-wuying\wuying-dm
for _cand in [
    Path(os.environ.get('AKKE_WUYING_DM_DIR', '') or '__missing__'),
    _THIS_DIR,
    _THIS_DIR.parent / 'wuying-dm',
    Path('C:/akke-wuying/wuying-dm'),
]:
    if _cand.exists() and (_cand / '.env').exists():
        _load_dotenv_stdlib(_cand / '.env')
        print(f'[bootstrap] .env loaded from {_cand}', file=sys.stderr)
        break

# ── Supabase 鉴权 (跟 wuying_poll_agent.py DM 通道同款两模式) ──────────────
# 模式 A (无影优先): apikey=anon + Authorization=Bearer SCOPED_JWT (role=wuying_worker)
# 模式 B (回退):      apikey=service_role + Authorization=Bearer service_role
SUPABASE_URL = os.environ.get('SUPABASE_URL') or os.environ.get('NEXT_PUBLIC_SUPABASE_URL')
_SCOPED_JWT = os.environ.get('SUPABASE_SCOPED_JWT')
_ANON_KEY = os.environ.get('NEXT_PUBLIC_SUPABASE_ANON_KEY') or os.environ.get('SUPABASE_ANON_KEY')
_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
if _SCOPED_JWT and _ANON_KEY:
    _APIKEY, _BEARER = _ANON_KEY, _SCOPED_JWT
else:
    _APIKEY = _BEARER = _SERVICE_KEY

if not SUPABASE_URL:
    print('❌ env missing: SUPABASE_URL', file=sys.stderr)
    sys.exit(2)
if not _BEARER:
    print('❌ env missing: SUPABASE_SCOPED_JWT+ANON_KEY 或 SUPABASE_SERVICE_ROLE_KEY', file=sys.stderr)
    sys.exit(2)


# ── 中心化 env 下发 (2026-06-26 加, 免运营手动改 .env) ─────────────────────
# 拉私有 Supabase Storage bucket agent-config/wuying.json, 把里面的 env 覆盖到
# os.environ. 当前下发的:
#   - LARK_WEBHOOK_TUWEN_AUTO (验证码告警卡推到视频更新群)
#   - LARK_USER_ID_MAP_JSON   (告警卡里真 @ 弹运营手机)
# 想换 webhook / 加新运营 → 改本地 .env.local 后跑
#   pnpm tsx scripts/upload-agent-env-overrides.ts
# 运营下次重启 agent 自动拉新版, 完全无感知.
#
# fetch 失败 silent skip — 退回本地 .env 老值 (绝不 break agent 启动).
#
# 2026-06-26 修: 必须用 service-role key 拉 Storage. scoped JWT (role=wuying_worker)
# 没 Storage 读权限, 只能 RPC. 如果 .env 没 SUPABASE_SERVICE_ROLE_KEY, fallback 用
# _BEARER (会失败但不 break), 此时只能走 .env 老值.
def _fetch_env_overrides() -> None:
    # Storage 需要 service-role, 不是 scoped JWT
    storage_key = _SERVICE_KEY or _BEARER
    if not storage_key:
        print('[env-override] no key for storage fetch, skip', file=sys.stderr)
        return

    bucket = 'agent-config'
    obj = 'wuying.json'
    url = f'{SUPABASE_URL}/storage/v1/object/{bucket}/{obj}'
    try:
        req = urllib.request.Request(url, headers={
            'apikey': storage_key,
            'Authorization': f'Bearer {storage_key}',
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            overrides = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print('[env-override] supabase storage 没 wuying.json, skip (退回本地 .env)', file=sys.stderr)
        elif e.code in (400, 401, 403):
            print(f'[env-override] http {e.code} (.env 缺 SUPABASE_SERVICE_ROLE_KEY?), skip', file=sys.stderr)
        else:
            print(f'[env-override] http {e.code}, skip', file=sys.stderr)
        return
    except Exception as e:
        print(f'[env-override] fetch failed ({type(e).__name__}: {e}), skip', file=sys.stderr)
        return

    if not isinstance(overrides, dict):
        print('[env-override] payload 不是 JSON object, skip', file=sys.stderr)
        return

    # 中心化下发 = 权威值, 强制覆盖本地 .env (本地老/缺都用云端 fresh)
    loaded_keys = []
    for k, v in overrides.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        os.environ[str(k)] = str(v)
        loaded_keys.append(str(k))
    print(f'[env-override] loaded {len(loaded_keys)} keys from supabase: {sorted(loaded_keys)}', file=sys.stderr)


_fetch_env_overrides()


# ── Tuwen 配置 ─────────────────────────────────────────────────────────────
ASSIGNEE = os.environ.get('AKKE_TUWEN_ASSIGNEE', '').strip()
POLL_INTERVAL = int(os.environ.get('AKKE_TUWEN_POLL_INTERVAL', '300'))
CLAIM_LIMIT = int(os.environ.get('AKKE_TUWEN_CLAIM_LIMIT', '1'))
MANIFEST_DIR = Path(os.environ.get('AKKE_TUWEN_MANIFEST_DIR', 'C:/akke-wuying'))

if not ASSIGNEE:
    print('❌ env missing: AKKE_TUWEN_ASSIGNEE', file=sys.stderr)
    sys.exit(2)

# ── 执行器 ─────────────────────────────────────────────────────────────────
# AKKE_TUWEN_PUBLISHER=pc_client → 抖音 PC 客户端「投稿」发 (douyin_pc_video_publisher.py, 深圳机自营号视频线,
# 只发视频); 默认 creator_web → Edge 开 creator.douyin.com 发 (creator_publisher.py). 两者退出码同一套.
PUBLISHER = os.environ.get('AKKE_TUWEN_PUBLISHER', 'creator_web').strip() or 'creator_web'
_PC_PUBLISHER_PY = next((p for p in (_THIS_DIR / 'douyin_pc_video_publisher.py',
                                     _THIS_DIR.parent / 'wuying-dm' / 'douyin_pc_video_publisher.py') if p.exists()),
                        _THIS_DIR / 'douyin_pc_video_publisher.py')
CREATOR_PUBLISHER_PY = _PC_PUBLISHER_PY if PUBLISHER == 'pc_client' else _THIS_DIR / 'creator_publisher.py'
# 下载 + 上传转码 5min + 验证码等人 30min (publisher 默认) + 其余步骤, 留足余量
PC_PUBLISH_TIMEOUT = int(os.environ.get('AKKE_PC_PUBLISH_TIMEOUT', '3000'))


# ── 与同机 DM / route-B 共用的 GUI 串行锁 (wuying-dm/wuying_window_lock.py, AKKE_WINDOW_LOCK=1 才生效) ──
# creator_publisher 全程 pyautogui 控鼠标键盘; 同机 route-B 三连 / DM 也在控 → 并发会互抢焦点、字打进别的窗口.
# 发一条视频常超过锁的 STALE_SEC(5min, 上传转码就 5min), 不续期会被对面判「持有者已死」抢走 → 持锁期间每 60s 续一次.
def _load_window_lock():
    for d in (Path(os.environ.get('AKKE_WUYING_DM_DIR', '') or '__missing__'), _THIS_DIR, _THIS_DIR.parent / 'wuying-dm',
              Path('C:/akke-wuying/wuying-dm')):
        if (d / 'wuying_window_lock.py').exists():
            sys.path.insert(0, str(d))
            try:
                import wuying_window_lock as wl
                return wl
            except Exception as e:
                print(f'⚠ window lock 加载失败 ({e}), 不加锁', file=sys.stderr)
                return None
    return None


_WL = _load_window_lock()


@contextlib.contextmanager
def gui_turn():
    if _WL is None or not getattr(_WL, 'ENABLED', False):
        yield
        return
    with _WL.window_turn('dm'):  # 按 DM 同级抢锁: 不等 .dm-want, route-B 会在当前三连结束后让出
        stop = threading.Event()

        def _keepalive():
            while not stop.wait(60):
                try:
                    _WL._touch(_WL._LOCK)
                except Exception as e:  # 续期静默停了锁会被 route-B 抢走 → 至少留一行日志
                    print(f'  !! window lock 续期失败: {type(e).__name__}: {e}', file=sys.stderr)

        t = threading.Thread(target=_keepalive, daemon=True)
        t.start()
        try:
            yield
        finally:
            stop.set()
if not CREATOR_PUBLISHER_PY.exists():
    print(f'❌ creator_publisher.py 不在 {CREATOR_PUBLISHER_PY}', file=sys.stderr)
    sys.exit(2)

# ── claimer (host:pid 给 claim_tuwen_job audit 用) ──────────────────────────
HOST = os.environ.get('COMPUTERNAME') or os.environ.get('HOSTNAME') or 'unknown'
CLAIMER = f'tuwen-agent:{HOST}:{os.getpid()}'


def _rpc(name: str, payload: dict):
    """调 PostgREST RPC. urllib stdlib, 不依赖 supabase package."""
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/rpc/{name}',
        data=json.dumps(payload).encode(),
        headers={
            'apikey': _APIKEY,
            'Authorization': f'Bearer {_BEARER}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else None


def claim_batch() -> list[dict]:
    return _rpc('claim_tuwen_job', {
        'p_assignee': ASSIGNEE,
        'p_claimer':  CLAIMER,
        'p_limit':    CLAIM_LIMIT,
        'p_channel':  'creator_web',
    }) or []


def _looks_like_video(url: str) -> bool:
    """media_urls[0] 是不是视频直链 (dewm 去水印下载 / .mp4).

    视频批量分发线 (2026-07-08): 视频 URL 塞进 media_urls[0] 复用现有 schema,
    不加新字段. 靠 URL 特征区分视频 vs 图文.
    """
    u = (url or '').lower().split('?')[0]
    return '/dewm/download' in (url or '').lower() or u.endswith('.mp4') or u.endswith('.mov')


def process_one(row: dict) -> None:
    """构造 manifest → subprocess 调 creator_publisher.py --commit → 回写 complete."""
    disp_id = row['id']
    title = row.get('title') or ''
    body = row.get('body') or ''
    media_urls = row.get('media_urls') or []
    schedule_at_iso = row.get('publish_at')

    is_video = bool(media_urls) and _looks_like_video(media_urls[0])

    if is_video:
        # 视频通路 (2026-07-08 视频批量分发): media_urls[0] = 视频直链, 走抖音「定时发布」.
        # 定时时间存 content.video_schedule_at (本地 'YYYY-MM-DD HH:MM'), 与 claim gate 用的
        # publish_at 解耦 —— 入队时 publish_at 置 NULL 让【一次启动脚本】就拉到该人全部视频,
        # 每条各自按 video_schedule_at 设抖音定时, 抖音到点自动放出. 人跑一次即走.
        content = row.get('content') if isinstance(row.get('content'), dict) else {}
        # dedup 默认开是给 enqueue-tuwen-video 那条（别人视频的去水印直链）用的；
        # 自营号视频线 (enqueue-ai-video-cloudpc) 发自己渲染的成片, 入队时写 dedup=false、ai_declaration=true
        manifest: dict = {
            'title': title,
            'body':  body,
            'video_url': media_urls[0],
            'dedup': bool((content or {}).get('dedup', True)),
            'ai_declaration': bool((content or {}).get('ai_declaration')),
        }
        vsa = (content or {}).get('video_schedule_at')
        if (content or {}).get('publish_on_claim'):
            # 入队时 --on-claim: publish_at 已经闸住了领单时刻 → 领到就立即发, 不设抖音定时
            # (深圳机 PC 客户端的定时浮层点不稳, 见 enqueue-ai-video-cloudpc.ts)
            vsa = None
            schedule_at_iso = None
            print(f'  [tuwen] {disp_id[:8]} 到点领单, 立即发 (目标 {(content or {}).get("target_publish_at")})')
        if not vsa and schedule_at_iso:  # 回退: 没存 content 时用 publish_at
            try:
                vsa = datetime.fromisoformat(schedule_at_iso.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M')
            except Exception:
                vsa = None
        if vsa:
            manifest['schedule_at'] = vsa
            print(f'  [tuwen] {disp_id[:8]} 视频定时 → {vsa}')
    else:
        manifest = {
            'title': title,
            'body':  body,
            'image_urls': media_urls,
        }
        if schedule_at_iso:
            try:
                # publish_at 是 timestamptz ISO 字符串; creator_publisher 要 'YYYY-MM-DD HH:MM' 本地
                dt = datetime.fromisoformat(schedule_at_iso.replace('Z', '+00:00')).astimezone()
                manifest['schedule_at'] = dt.strftime('%Y-%m-%d %H:%M')
            except Exception as e:
                print(f'  ⚠ schedule_at 解析失败 ({schedule_at_iso}: {e}), 改立即发', file=sys.stderr)

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    mf_path = MANIFEST_DIR / f'manifest-tuwen-{disp_id[:8]}.json'
    try:
        mf_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        print(f'  ✗ write manifest failed for {disp_id[:8]}: {e}', file=sys.stderr)
        _safe_complete(disp_id, 'failed', f'manifest write: {e}')
        return

    if PUBLISHER == 'pc_client' and not is_video:
        _safe_complete(disp_id, 'failed', 'AKKE_TUWEN_PUBLISHER=pc_client 只发视频, 这条是图文 (assignee 配错了?)')
        mf_path.unlink(missing_ok=True)
        return

    print(f'  [tuwen] {disp_id[:8]}  manifest={mf_path.name}  → {CREATOR_PUBLISHER_PY.name} --commit')
    with gui_turn():
        try:
            proc = subprocess.run(
                [sys.executable, str(CREATOR_PUBLISHER_PY), '--manifest', str(mf_path), '--commit'],
                cwd=str(CREATOR_PUBLISHER_PY.parent),
                # pc_client 持着和 DM 共用的窗口锁跑, 续期线程会一直续 → 子进程卡死 = DM 永远拿不到锁. 给硬上限.
                timeout=PC_PUBLISH_TIMEOUT if PUBLISHER == 'pc_client' else None,
            )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rc = 124
            print(f'  !! {CREATOR_PUBLISHER_PY.name} 超过 {PC_PUBLISH_TIMEOUT}s 已杀, 关残留的创作者中心子窗口', file=sys.stderr)
            subprocess.run([sys.executable, str(CREATOR_PUBLISHER_PY), '--cleanup'],
                           cwd=str(CREATOR_PUBLISHER_PY.parent), timeout=120)
    print(f'  [tuwen] {disp_id[:8]} exit={rc}')

    # status 映射 (2026-06-25 彻底修 + 06-26 加 exit 10): 区分 3 类:
    # - exit 0 = published (真成功)
    # - exit 8 = needs_review (verify_published 多层信号没 hit, 发布按钮已点 + 表单已提交,
    #   实际大概率已发, 抽查 creator 后台. 详 PR #548)
    # - exit 10 = needs_review (新, 06-26): 弹了验证码 + agent 等了 CAPTCHA_TIMEOUT_SEC
    #   没人按 Enter → 主动 abort 转 needs_review, 不挡同 assignee 后续 row.
    # - 其它 rc (4/5/6/7) = failed (真错: NOT FOUND step 4/5/6/7)
    if rc == 0:
        status = 'published'
        err = None
    elif rc == 8:
        status = 'needs_review'
        err = '⚠ 发布按钮已点 + 表单已提交, 但 verify_published 多层信号都没 hit. 大概率已发出, 抽查 creator 后台「作品管理」确认. (creator_publisher exit 8)'
    elif rc == 66:
        status = 'failed'
        err = '自主声明「内容由AI生成」没勾上, 已停在发布前没发 (creator_publisher exit 66). 截图 screenshots/_ai_decl_verify.png'
    elif rc == 10:
        status = 'needs_review'
        err = '⚠ 弹验证码超时无人过 (3min), agent abort 转 needs_review. 之后可手动重发本 slug. (creator_publisher exit 10)'
    elif rc == 124:
        status = 'needs_review'
        err = f'⚠ 发布脚本超过 {PC_PUBLISH_TIMEOUT}s 被杀, 可能停在任一步 (含已点发布). 先去 PC 客户端「投稿 → 内容管理」核对再决定重发'
    else:
        status = 'failed'
        err = f'creator_publisher exit {rc}'
    _safe_complete(disp_id, status, err)

    try:
        mf_path.unlink(missing_ok=True)
    except Exception:
        pass


def _safe_complete(disp_id: str, status: str, error_message: str | None) -> None:
    try:
        _rpc('complete_tuwen_job', {
            'p_id': disp_id,
            'p_status': status,
            'p_aweme_id': None,
            'p_error_message': error_message,
        })
        print(f'  ✓ {disp_id[:8]} → {status}')
    except Exception as e:
        print(f'  !! complete_tuwen_job({disp_id[:8]}) failed: {e}', file=sys.stderr)


def main():
    print(f'=== wuying_tuwen_agent  assignee={ASSIGNEE}  every {POLL_INTERVAL}s  limit={CLAIM_LIMIT} ===')
    print(f'  claimer            = {CLAIMER}')
    print(f'  supabase           = {SUPABASE_URL}')
    print(f'  creator_publisher  = {CREATOR_PUBLISHER_PY}')
    print(f'  manifest_dir       = {MANIFEST_DIR}')
    print()

    while True:
        try:
            t0 = time.time()
            claimed = claim_batch()
            if claimed:
                print(f'\n[{datetime.now():%H:%M:%S}] claimed {len(claimed)} tuwen creator_web jobs')
                for row in claimed:
                    process_one(row)
                print(f'[{datetime.now():%H:%M:%S}] loop done in {time.time() - t0:.1f}s')
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print('\n[interrupted] bye.')
            break
        except Exception as e:
            print(f'!! poll loop error: {type(e).__name__}: {e}', file=sys.stderr)
            time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
