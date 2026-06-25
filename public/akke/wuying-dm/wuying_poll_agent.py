"""
wuying_poll_agent.py — 每台 Wuying 内常驻的 dispatch_queue 轮询 agent。

工作循环（每 60s）:
  1. rpc claim_dispatch(p_account_id, p_limit=3) 抢 3 条 pending → claimed
  2. 写 contacts.csv（含 _dispatch_id）
  3. subprocess: python douyin_dm.py --auto contacts.csv
  4. 读 sent_log_YYYYMMDD.csv，找本批 _dispatch_id 的行
  5. 逐行 rpc complete_dispatch(p_id, p_status, p_ocr_confidence, p_error_message)
     - sent → 串接 mark_lead_contacted 永久去重
     - skipped/failed/aborted → 仅写 dispatch_queue.status

Setup (每台 Wuying 一次性):
  pip install pyautogui pyperclip pillow python-dotenv   # 本 agent 用 stdlib urllib 调 RPC,不需 supabase 包
  写 .env（同目录）。连库二选一:
    受控角色(推荐,读全库+仅 dispatch 两 RPC):
      SUPABASE_URL=...  NEXT_PUBLIC_SUPABASE_ANON_KEY=...  SUPABASE_SCOPED_JWT=<role=wuying_worker 的自签 JWT>
    或回退 service_role(老机器):
      SUPABASE_URL=...  SUPABASE_SERVICE_ROLE_KEY=...
    另外(douyin_dm_grounded.py 视觉定位用,非本 agent):OPENROUTER_API_KEY=<sk-or 封顶 key>
    AKKE_ACCOUNT_ID=<这台 Wuying 1:1 绑的 accounts.id>
  加到启动文件夹（bootstrap.bat 会做）。

设计要点:
  - 单台 Wuying = 单个 account_id，dispatch_queue 行已经按 account_id 分桶
  - claim_dispatch 内置 15min 死锁回收（Wuying 重启自动回收没回写完的）
  - 失败的行让 dispatch_queue.status=failed 留 audit，不重试（避免重复 DM 风险）
  - douyin_dm.py 写 sent_log 到 cwd，本 agent 解析后调 RPC 即可
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

WORK_DIR = Path(__file__).resolve().parent
os.chdir(WORK_DIR)

# ── 版本标记 ─────────────────────────────────────────────────────────────────
# 云电脑不装 git、update.bat 只下载 raw .py，运行时取不到 git SHA。故硬编码版本串，
# 每次有意义改动手动 bump（日期+特性名），启动横幅打印 → 运营/PM 一眼核对"是不是最新版"。
AGENT_VERSION = '2026-06-24+captcha-char-sample'

try:
    from dotenv import load_dotenv
    load_dotenv(WORK_DIR / '.env')
except ImportError:
    pass

try:
    import wuying_window_lock as _wl  # 单窗口·DM优先串行锁(AKKE_WINDOW_LOCK=1 才生效，否则 no-op)
except ModuleNotFoundError:
    # 只在「同机串行跑 DM+route-B」的机器需要(配 AKKE_WINDOW_LOCK=1)。单 DM 机没下此文件也要正常跑
    # → 缺失退化成 no-op 桩，别让顶层 import 崩整脚本(照搬 douyin_dm_grounded.py 惯例)。
    import contextlib as _ctxlib
    class _WLStub:
        @_ctxlib.contextmanager
        def dm_batch(self):
            yield
        @_ctxlib.contextmanager
        def window_turn(self, *a, **k):
            yield
        def dm_keepalive(self):
            pass
    _wl = _WLStub()

# ── config ──────────────────────────────────────────────────────────────────

ACCOUNT_ID = os.environ.get('AKKE_ACCOUNT_ID')
SUPABASE_URL = os.environ.get('SUPABASE_URL') or os.environ.get('NEXT_PUBLIC_SUPABASE_URL')

# 连库鉴权两种模式(PostgREST 网关只认 anon/service_role 当 apikey,不认自定义角色 JWT):
#   受控角色(无影优先):apikey=anon 过网关 + Authorization=Bearer <受控 JWT>(role=wuying_worker,
#                       仅 SELECT + claim_dispatch/complete_dispatch)
#   回退(老机器):service_role,apikey 与 Bearer 同为 service_role key
_SCOPED_JWT = os.environ.get('SUPABASE_SCOPED_JWT')
_ANON_KEY = os.environ.get('NEXT_PUBLIC_SUPABASE_ANON_KEY') or os.environ.get('SUPABASE_ANON_KEY')
_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
if _SCOPED_JWT and _ANON_KEY:
    _APIKEY, _BEARER = _ANON_KEY, _SCOPED_JWT
else:
    _APIKEY = _BEARER = _SERVICE_KEY

for k, v in [('AKKE_ACCOUNT_ID', ACCOUNT_ID),
             ('SUPABASE_URL', SUPABASE_URL),
             ('DB 鉴权(SUPABASE_SCOPED_JWT+anon 或 SUPABASE_SERVICE_ROLE_KEY)', _BEARER)]:
    if not v:
        print(f'❌ env missing: {k}', file=sys.stderr)
        sys.exit(2)

POLL_INTERVAL = int(os.environ.get('AKKE_POLL_INTERVAL', '60'))
CLAIM_LIMIT = int(os.environ.get('AKKE_CLAIM_LIMIT', '3'))

# 二次触达（公开评论）支线开关。默认关 —— 现有只发 DM 的机器不受影响。
# 设 AKKE_SECOND_TOUCH_ENABLED=1 才在 DM 批之后跑评论批（同机分时段轮转，
# 两个 subprocess 串行、不抢抖音窗口）。设计 spec 2026-06-06-second-touch-auto-dispatch。
SECOND_TOUCH_ENABLED = os.environ.get('AKKE_SECOND_TOUCH_ENABLED', '').lower() in ('1', 'true', 'yes')
SECOND_TOUCH_CLAIM_LIMIT = int(os.environ.get('AKKE_SECOND_TOUCH_CLAIM_LIMIT', '3'))

# 反向评论 RC 首触（楼中回复）支线开关。默认关。设 AKKE_REVERSE_COMMENT_ENABLED=1 才在
# DM/二触批之后串行跑 RC 批（同机分时段轮转，不抢抖音窗口）。执行器 douyin_rc_reply_grounded.py
# 内置 60–90s 间隔 + 日上限 15。设计 spec 2026-06-07-reverse-comment-first-touch。
REVERSE_COMMENT_ENABLED = os.environ.get('AKKE_REVERSE_COMMENT_ENABLED', '').lower() in ('1', 'true', 'yes')
REVERSE_COMMENT_CLAIM_LIMIT = int(os.environ.get('AKKE_REVERSE_COMMENT_CLAIM_LIMIT', '3'))

# DM 自动回复·捕获支线开关。默认关。设 AKKE_DM_AUTOREPLY_CAPTURE=1 才在每轮(DM/二触/RC
# 批之后)串行跑 douyin_dm_autoreply.py capture(只读扫私信列表写回客户回复，不发)。
# 需先量 AKKE_C_DM_INBOX 坐标让它自导航到私信列表。发送(send)仍手动，验证后再自动。
DM_AUTOREPLY_CAPTURE_ENABLED = os.environ.get('AKKE_DM_AUTOREPLY_CAPTURE', '').lower() in ('1', 'true', 'yes')
# 捕获节流：默认 180s=3min 扫一次收件箱。红点门控后扫描廉价(只读 UIA、零 LLM、没货秒退)，
# 旧的 43200s=12h 大节流(为防预览指纹比对的噪声+占窗口)已无必要——降到分钟级才"回了就回"。
# 配合 webhook 秒级生成，端到端 ~3-4min。想更紧(120s≈2min)可设 env，但与 route-B/RC 同号共存、
# 窗口锁(PR4)上线前别压太低、防抢二触 consume 的窗口。设 0 = 每轮都扫。
DM_AUTOREPLY_INTERVAL = int(os.environ.get('AKKE_DM_AUTOREPLY_INTERVAL', '180'))
# 捕获子进程硬超时（秒）。正常扫收件箱只需秒级；一旦在某个 GUI 状态上挂住（如收件箱进了
# 乱状态、模态卡住），没超时会让 subprocess.run 永久阻塞 → 整个 poll 循环冻死、心跳停、DM
# 全停（2026-06-25 饭粒凌晨 01:00 卡死 8.5h 的根因）。240s 足够最慢的一次正常捕获，超了
# 必是挂死 → 杀子进程、跳过本轮捕获、循环继续转（心跳照常跳）。
DM_AUTOREPLY_TIMEOUT_SEC = int(os.environ.get('AKKE_DM_AUTOREPLY_TIMEOUT_SEC', '240'))

# 抖音号反查（根治撞名）。默认开 —— 派单只带 sec_uid + 昵称，按昵称搜会撞同名别人
# （2026-06-09 淡雅事故）。云电脑国内 IP 能通抖音主页接口，发送前用 sec_uid 反查唯一抖音号
# 当搜索词，命中则精准定位、未命中回退昵称（不比现状差）。需 pip install "f2==0.0.1.7" httpx。
# 设 AKKE_HANDLE_RESOLVE=0 可关（退回按昵称搜）。
HANDLE_RESOLVE_ENABLED = os.environ.get('AKKE_HANDLE_RESOLVE', '1').lower() in ('1', 'true', 'yes')
_handle_cookie = None  # 懒加载的登录态 cookie 头串（DB 优先，cookie.txt 兜底）

# enrich 硬门（2026-06-10，wrong_user 根治第二段）：DM 一触没有抖音号（派单缓存 + 现场反查
# 都没拿到）就【不跑 GUI】—— 按昵称搜大众名/特殊字符名大概率撞别人，白烧一次完整 GUI 流程
# 还可能发错人。确定无号 → complete skipped/enrich_failed（计入失败自动抑制）；瞬时反查失败
# → 留 claimed 等 15min 死锁回收自动重试。设 AKKE_ENRICH_REQUIRE_NUMBER=0 退回旧昵称回退行为。
ENRICH_REQUIRE_NUMBER = os.environ.get('AKKE_ENRICH_REQUIRE_NUMBER', '1').lower() in ('1', 'true', 'yes')


def _rpc(name: str, payload: dict):
    """调 PostgREST RPC(stdlib urllib,无 supabase 依赖)。失败抛异常,由调用方/主循环兜。"""
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


def _get(path_and_query: str):
    """PostgREST 表查询（GET）。失败抛异常，由调用方兜。"""
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/{path_and_query}',
        headers={
            'apikey': _APIKEY,
            'Authorization': f'Bearer {_BEARER}',
            'Accept': 'application/json',
        },
        method='GET',
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else None


def _upload_captcha_sample(rel_path: str, modal_type: str) -> str | None:
    """把 grounded 落在 captcha_samples/ 的验证码截图上传到 Supabase Storage
    (bucket: captcha-samples, public)，返回 public URL；best-effort，失败回 None
    不拖垮告警链路。stdlib urllib，与 _rpc 同风格、不引 supabase-py。

    任务2(简单字符验证码自动填写)卡点①「积累验证截图」的传输段——截图先在云电脑
    grounded 侧落盘，这里上传成可点 URL 进 captcha_alerts.metadata.screenshot_url，
    Lark 监控群卡片渲染成链接，人就能直接看是哪种验证码、判断该上哪套识别方案。"""
    try:
        local = WORK_DIR / rel_path
        if not local.exists():
            print(f'  [warn] captcha sample 不存在: {local}', file=sys.stderr)
            return None
        data = local.read_bytes()
        objname = '%s/%s' % (ACCOUNT_ID or 'noacct', os.path.basename(rel_path))
        req = urllib.request.Request(
            f'{SUPABASE_URL}/storage/v1/object/captcha-samples/{objname}',
            data=data,
            headers={
                'apikey': _APIKEY,
                'Authorization': f'Bearer {_BEARER}',
                'Content-Type': 'image/png',
                'x-upsert': 'true',
            },
            method='POST',
        )
        urllib.request.urlopen(req, timeout=30).read()
        url = f'{SUPABASE_URL}/storage/v1/object/public/captcha-samples/{objname}'
        print(f'  [验证码样本] 已上传 {url}')
        return url
    except Exception as e:
        print(f'  [warn] captcha sample upload failed: {e}', file=sys.stderr)
        return None


def _fmt_cookie(cd) -> str:
    """accounts.cookie_data(Cookie-Editor 导出的 [{name,value},...])→ 'k=v; k=v' 头串。"""
    if isinstance(cd, list):
        pairs = ['%s=%s' % (c.get('name'), c.get('value'))
                 for c in cd if c.get('name') and c.get('value')]
        if pairs:
            return '; '.join(pairs)
    return ''


def _load_cookie_str() -> str:
    """抖音号反查要登录态 cookie。优先发送号(野荞)自己的 cookie_data;但云电脑发送号通常【没有】
    web cookie(走 GUI 登录,不导入)→ 回退到【任意带 cookie 的活跃账号】(反查只用其登录态调主页
    只读接口,与发送号无关,Mac enrich 也是用别的号);再回退本目录 cookie.txt;都没有返回 ''(回退昵称)。"""
    # ① 发送号自己的
    try:
        rows = _get(f'accounts?id=eq.{ACCOUNT_ID}&select=cookie_data')
        s = _fmt_cookie((rows or [{}])[0].get('cookie_data'))
        if s:
            return s
    except Exception as e:
        print('  [handle] 取发送号 cookie 失败(%s)' % e, file=sys.stderr)
    # ② 任意带 cookie 的活跃账号(优先 scraping/engagement —— 它们的 cookie 本就用于读浏览)
    try:
        rows = _get('accounts?status=eq.active&cookie_data=not.is.null'
                    '&select=cookie_data&order=type.asc&limit=20')
        for r in (rows or []):
            s = _fmt_cookie(r.get('cookie_data'))
            if s:
                return s
    except Exception as e:
        print('  [handle] 取备用账号 cookie 失败(%s)' % e, file=sys.stderr)
    # ③ cookie.txt 兜底
    p = WORK_DIR / 'cookie.txt'
    if p.exists():
        return p.read_text(encoding='utf-8-sig').strip().replace('\n', ' ')
    return ''


def cache_writeback(resolved: dict, nick_by_sec: dict) -> None:
    """把现场反查结果回写共享缓存 douyin_user_handles（号 / ""=查过无果→NULL）。
    resolved: {sec: {'number':…, 'nickname':…}}；nickname 优先用反查到的【实时昵称】
    （用户改名后 DB 旧昵称会让 OCR 门误杀），反查没带昵称才回退 DB 昵称。
    best-effort：受控角色没写权限(旧库)或网络失败只打日志，不影响发送。
    回写后派单侧（cloud-pc-dispatch 读缓存）命中率随之上升，逐步不再需要现场反查。"""
    rows = [{'sec_uid': s,
             'unique_id': (v.get('number') or None),
             'nickname': (v.get('nickname') or '').strip() or nick_by_sec.get(s) or None}
            for s, v in resolved.items()]
    if not rows:
        return
    try:
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/douyin_user_handles',
            data=json.dumps(rows).encode(),
            headers={
                'apikey': _APIKEY,
                'Authorization': f'Bearer {_BEARER}',
                'Content-Type': 'application/json',
                'Prefer': 'resolution=merge-duplicates,return=minimal',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=30):
            pass
        print(f'  [handle] 反查结果回写共享缓存 {len(rows)} 条')
    except Exception as e:
        print(f'  [handle] 缓存回写失败(不影响发送): {e}', file=sys.stderr)


def resolve_handles(rows: list[dict]) -> dict:
    """sec_uid → {'number': 抖音号, 'nickname': 实时昵称}。best-effort：未开/缺 f2/无 cookie/
    异常 → 返回空 map。三态同 resolve_batch：number=命中 / ""=确定无号 / 键缺失=瞬时失败。"""
    if not HANDLE_RESOLVE_ENABLED:
        return {}
    sec_uids = [(r.get('douyin_user_id') or '').strip() for r in rows]
    sec_uids = [s for s in sec_uids if s]
    if not sec_uids:
        return {}
    try:
        from resolve_douyin_numbers import resolve_batch
    except Exception as e:
        print('  [handle] 抖音号反查不可用(%s) → 本批按昵称搜(撞名风险)。'
              '装一次: pip install "f2==0.0.1.7" httpx' % e, file=sys.stderr)
        return {}
    global _handle_cookie
    if _handle_cookie is None:
        _handle_cookie = _load_cookie_str()
        print('  [handle] cookie %s' % (('就绪(%d字符)' % len(_handle_cookie))
                                        if _handle_cookie else '缺失 → 接口大概率失败,回退昵称'))
    try:
        m = resolve_batch(sec_uids, _handle_cookie)
        got = sum(1 for v in m.values() if v.get('number'))
        print('  [handle] 抖音号反查 %d/%d 命中（命中用唯一抖音号搜，未命中回退昵称）'
              % (got, len(sec_uids)))
        return m
    except Exception as e:
        print('  [handle] 反查异常(%s) → 本批回退昵称' % e, file=sys.stderr)
        return {}


# DM 执行脚本可配（2026-06-17）：默认 PC 客户端版 douyin_dm_grounded.py(--auto)；
# 浏览器(Edge web)通道设 AKKE_DM_SCRIPT=douyin_dm_web_grounded.py —— 它默认自动发、不收 --auto，
# 故脚本名含 'web' 时自动【不传 --auto】(也可 AKKE_DM_PASS_AUTO=0/1 显式覆盖)。向后兼容：不设 env=原行为。
DM_SCRIPT_NAME = os.environ.get('AKKE_DM_SCRIPT', 'douyin_dm_grounded.py')
DOUYIN_DM_PY = WORK_DIR / DM_SCRIPT_NAME
_auto_default = '0' if 'web' in DM_SCRIPT_NAME.lower() else '1'
DM_PASS_AUTO = os.environ.get('AKKE_DM_PASS_AUTO', _auto_default).lower() in ('1', 'true', 'yes')
if not DOUYIN_DM_PY.exists():
    print(f'❌ DM 脚本不存在: {DOUYIN_DM_PY}（AKKE_DM_SCRIPT={DM_SCRIPT_NAME}）', file=sys.stderr)
    sys.exit(2)

DOUYIN_COMMENT_PY = WORK_DIR / 'douyin_comment_grounded.py'  # 评论支线发送脚本
if SECOND_TOUCH_ENABLED and not DOUYIN_COMMENT_PY.exists():
    print(f'❌ AKKE_SECOND_TOUCH_ENABLED=1 但 douyin_comment_grounded.py 不存在: {DOUYIN_COMMENT_PY}', file=sys.stderr)
    sys.exit(2)

DOUYIN_RC_PY = WORK_DIR / 'douyin_rc_reply_grounded.py'  # RC 楼中回复执行器（#137）
if REVERSE_COMMENT_ENABLED and not DOUYIN_RC_PY.exists():
    print(f'❌ AKKE_REVERSE_COMMENT_ENABLED=1 但 douyin_rc_reply_grounded.py 不存在: {DOUYIN_RC_PY}', file=sys.stderr)
    sys.exit(2)

DOUYIN_AUTOREPLY_PY = WORK_DIR / 'douyin_dm_autoreply.py'  # DM 自动回复捕获(只读)
if DM_AUTOREPLY_CAPTURE_ENABLED and not DOUYIN_AUTOREPLY_PY.exists():
    print(f'❌ AKKE_DM_AUTOREPLY_CAPTURE=1 但 douyin_dm_autoreply.py 不存在: {DOUYIN_AUTOREPLY_PY}', file=sys.stderr)
    sys.exit(2)

# ── helpers ─────────────────────────────────────────────────────────────────

def claim_batch() -> list[dict]:
    data = _rpc('claim_dispatch', {
        'p_account_id': ACCOUNT_ID,
        'p_limit': CLAIM_LIMIT,
    })
    return data or []


def write_contacts_csv(rows: list[dict], handle_map: dict | None = None,
                       fresh_nick: dict | None = None) -> Path:
    handle_map = handle_map or {}
    fresh_nick = fresh_nick or {}
    path = WORK_DIR / 'contacts.csv'
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=[
            'douyin_id', 'nickname', 'message', 'has_works',
            '_comment_id', '_sec_uid', '_dispatch_id',
        ])
        w.writeheader()
        for r in rows:
            # douyin_id 列 = 搜索栏 query：命中抖音号则用唯一抖音号（根治撞名），否则回退昵称。
            # nickname 列 = OCR 身份门比对目标：优先反查到的【实时昵称】（DB 昵称是抓评论时存的，
            # 用户改名后必失配 → 误杀 wrong_user），反查没昵称才回退 DB 昵称。
            sec = (r.get('douyin_user_id') or '').strip()
            nick = ((fresh_nick.get(sec) or '').strip()
                    or (r.get('customer_name') or '').strip() or 'unknown')
            query = (handle_map.get(sec) or '').strip() or nick
            w.writerow({
                'douyin_id': query,
                'nickname': nick,
                'message': r['message'],
                'has_works': 1 if r.get('has_works') else 0,
                '_comment_id': r['comment_id'],
                '_sec_uid': r['douyin_user_id'],
                '_dispatch_id': r['id'],
            })
    return path


def run_douyin_dm(csv_path: Path) -> int:
    cmd = [sys.executable, str(DOUYIN_DM_PY)]
    if DM_PASS_AUTO:
        cmd.append('--auto')   # PC 版需要;web 版默认自动发、不传
    cmd.append(str(csv_path))
    proc = subprocess.run(cmd, cwd=str(WORK_DIR))
    return proc.returncode


def read_sent_log() -> list[dict]:
    path = WORK_DIR / f'sent_log_{datetime.now():%Y%m%d}.csv'
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def map_status(raw: str) -> str:
    """sent_log.status → dispatch_queue.status enum."""
    if not raw:
        return 'failed'
    if raw == 'sent':
        return 'sent'
    if raw in ('wrong_user', 'cancelled', 'rejected'):
        # rejected = 对方拒收/无法送达 → skipped（不串 mark_lead_contacted → 不写 sent 消息 → 不计入成功配额）
        return 'skipped'
    if raw == 'aborted':
        return 'aborted'
    if raw.startswith('blocked_'):
        # blocked_sms / blocked_slider / blocked_face = 风控 modal 命中（任务 3 modal 检测 PR）。
        # 写 aborted 让本条 lead 回池（不计配额、可重发），同时 _mark_account_health
        # 走特殊分支：emit P0 红牌 + 强制 cool 账号（连撞 mark_account_failure 3 次到阈值）。
        return 'aborted'
    if raw.startswith('error:'):
        return 'failed'
    return 'failed'


def complete_one(disp_id: str, log_row: dict, resolved_number: str | None = None) -> None:
    raw_status = (log_row.get('status') or '').strip()
    rpc_status = map_status(raw_status)
    ocr_raw = (log_row.get('_ocr_confidence') or '').strip()
    ocr_conf = float(ocr_raw) if ocr_raw else None
    err = None if rpc_status == 'sent' else raw_status[:500]
    # B2 观测(2026-06-16)：wrong_user/cancelled 把 _ocr_seen 拼进 error_message，事后纯查 DB 就能
    # 按 A/B/C 分类(号匹配却(非主页) / 搜到别人的号 / 纯(非主页))量真实占比；否则库里只剩笼统
    # 「wrong_user」标签、分不出。只拼这两类——rejected 等保持原文(complete_dispatch 对 'rejected'
    # 精确匹配做 1-shot 抑制，拼了会破)。
    if raw_status in ('wrong_user', 'cancelled'):
        _seen = (log_row.get('_ocr_seen') or '').strip()
        if _seen:
            err = ('%s | seen=%s' % (err, _seen))[:500]
    payload = {
        'p_id': disp_id,
        'p_status': rpc_status,
        'p_ocr_confidence': ocr_conf,
        'p_error_message': err,
    }
    # 现场反查到的号回写 dispatch_queue.douyin_number（派单 INSERT 时缓存未命中写的 NULL）。
    # RPC 用 COALESCE 不覆盖已有值。旧 RPC（无 p_douyin_number 参数）直接忽略未知键即可。
    if resolved_number:
        payload['p_douyin_number'] = resolved_number
    try:
        _rpc('complete_dispatch', payload)
        print(f'  ✓ completed {disp_id[:8]} → {rpc_status} (ocr={ocr_conf})')
    except Exception as e:
        print(f'  !! complete_dispatch({disp_id[:8]}) failed: {e}', file=sys.stderr)

    # 接「云电脑通道触发 cool」闭环 (PR #389/#390 后续 PR3)：把单条 outcome 翻译成
    # accounts.consecutive_failures 信号。云电脑 GUI 路径物理上取不到 IM API status_code
    # (7911/22102 是 web 通道的事)，但累计「unverified/error」3 次就足够把号判 cooling
    # (mark_account_failure RPC 内置阈值=3 → status='cooling')。24h 后由
    # /api/cron/account-cooldown-resume 自动恢复；进入瞬间由 /api/cron/account-cooldown-notify
    # 推 Lark 橙牌。
    _mark_account_health(raw_status, log_row)


def _mark_account_health(raw_status: str, log_row: dict | None = None) -> None:
    """根据本次发送 outcome 维护 accounts 健康状态机。

    分类决策（云电脑 DM 通道；UI 层信号无法精确分 7911/7173/etc）：
      sent                                   → mark_account_success（清零 consecutive_failures）
      rejected / wrong_user / cancelled      → 不动账号（对方拒收 / 搜错人 / 假阳性定位，非账号问题）
      unverified / error:*                   → mark_account_failure（+1；满 3 自动 → status='cooling'）
      blocked_sms / blocked_slider / blocked_face → **立即 cool 账号 + emit P0 红牌**
                                              （任务 3 modal 检测：VL 命中 SMS/滑块/人脸是
                                              明确的账号被风控信号，不该等累计 3 次；连撞
                                              mark_account_failure 3 次直接到阈值触发 cooling）
      blocked_char (字符图片验证码)           → 只计 1 次失败 + emit P1 橙牌（任务 2 卡点①攒样本：
                                              字符验证码是频率挑战不是封号，不立即 force-cool；
                                              截图已由 grounded 落盘、这里上传 Storage 进告警 metadata）
      aborted                                → 不动（agent 主动跳过，不是发送结果）

    失败不抛——账号健康记账失败不能拖垮 dispatch 主链路（complete_dispatch 已成功才是关键）。
    """
    if not ACCOUNT_ID:
        return
    raw = (raw_status or '').strip()
    if raw == 'sent':
        try:
            _rpc('mark_account_success', {'p_account_id': ACCOUNT_ID})
        except Exception as e:
            print(f'  [warn] mark_account_success failed: {e}', file=sys.stderr)
        return
    if raw.startswith('blocked_'):
        modal_type = raw.split('_', 1)[1] if '_' in raw else 'unknown'
        # 攒样本(卡点①)：grounded 落盘的验证码截图上传 Storage，URL 进告警 metadata。
        screenshot_url = None
        if log_row:
            samp = (log_row.get('_captcha_sample') or '').strip()
            if samp:
                screenshot_url = _upload_captcha_sample(samp, modal_type)
        meta = {'source': 'cloud_pc_modal', 'modal_type': modal_type, 'raw_status': raw}
        if screenshot_url:
            meta['screenshot_url'] = screenshot_url

        if modal_type == 'char':
            # 简单字符图片验证码(任务2 目标)：phase-1 还不能自动识别填写 → 本条已回池，
            # 这里只攒截图 + 提醒人工过；不像 sms/滑块/人脸那样立即 force-cool —— 字符验证码
            # 是「操作频繁」频率挑战、不是封号，cool 太狠还会断掉继续攒样本的机会。只计 1 次
            # 失败(连撞 3 次由 mark_account_failure 阈值自然 cool 兜底)。
            try:
                _rpc('mark_account_failure', {
                    'p_account_id': ACCOUNT_ID,
                    'p_reason': f'cloud_pc_modal/{raw[:200]}',
                    'p_kind': 'hard',
                })
            except Exception as e:
                print(f'  [warn] mark_account_failure(char) failed: {e}', file=sys.stderr)
            try:
                _rpc('emit_captcha_alert', {
                    'p_account_id': ACCOUNT_ID,
                    'p_level': 'p1_orange',
                    'p_type': 'captcha_char',
                    'p_message': (
                        '⚠️ 账号撞【字符图片验证码】· 本条已回池 · 需人工过验证码'
                        + ('（截图见卡片链接）' if screenshot_url else '（截图未上传）')
                    ),
                    'p_metadata': meta,
                })
            except Exception as ee:
                print(f'  [warn] emit_captcha_alert (char) failed: {ee}', file=sys.stderr)
            return

        # SMS / 滑块 / 人脸 modal 命中 = 明确的账号被风控信号。
        # 立即 cool：连撞 mark_account_failure 3 次到阈值（hack：没有"立即 cool" RPC，
        # mark_account_failure 内置阈值=3 自动转 cooling；3 次同源调用是最稳的等价实现）。
        # 同时 emit P0 红牌让运营秒响应（人脸活检需要本人扫码过脸）。
        try:
            for i in range(3):
                _rpc('mark_account_failure', {
                    'p_account_id': ACCOUNT_ID,
                    'p_reason': f'cloud_pc_modal/{raw[:200]}',
                    'p_kind': 'hard',
                })
        except Exception as e:
            print(f'  [warn] mark_account_failure x3 failed: {e}', file=sys.stderr)
        try:
            _rpc('emit_captcha_alert', {
                'p_account_id': ACCOUNT_ID,
                'p_level': 'p0_red',
                'p_type': f'captcha_{modal_type}',
                'p_message': (
                    f"⚠️ 账号撞 {modal_type.upper()} 风控弹窗 · 已立即停号"
                    f" · {'需本人扫码过脸' if modal_type == 'face' else '需本人介入'}"
                ),
                'p_metadata': meta,
            })
        except Exception as ee:
            print(f'  [warn] emit_captcha_alert (modal) failed: {ee}', file=sys.stderr)
        return
    # send_fail = RC 通道的"发了但没确认到回复气泡"（同 DM 的 unverified 语义）。
    # type_fail 是 RC 历史包袱（v1-v11 type_fail 的预检假阴性源已经废了，但 prod 上偶尔还会写）。
    if raw == 'unverified' or raw in ('send_fail', 'type_fail') or raw.startswith('error:'):
        try:
            result = _rpc('mark_account_failure', {
                'p_account_id': ACCOUNT_ID,
                'p_reason': f'cloud_pc_dm/{raw[:200]}',
                'p_kind': 'hard',
            })
            # 任务3 监控 agent MVP：累计满阈值刚转 cooling 时 emit captcha_alert
            # 让 /api/cron/captcha-monitor (* * * * *) 推 Lark 橙牌。
            # PostgREST 返回 RETURNS TABLE 是 [{"new_count":…,"new_status":…}]。
            new_status = None
            new_count = None
            if isinstance(result, list) and result:
                new_status = (result[0] or {}).get('new_status')
                new_count = (result[0] or {}).get('new_count')
            if new_status == 'cooling':
                try:
                    _rpc('emit_captcha_alert', {
                        'p_account_id': ACCOUNT_ID,
                        'p_level': 'p1_orange',
                        'p_type': 'account_cooldown_auto',
                        'p_message': f"账号连续失败 {new_count} 次自动 cool（cloud_pc_dm/{raw[:120]}）",
                        'p_metadata': {'reason': raw[:500], 'source': 'cloud_pc_dm', 'new_count': new_count},
                    })
                except Exception as ee:
                    print(f'  [warn] emit_captcha_alert failed: {ee}', file=sys.stderr)
        except Exception as e:
            print(f'  [warn] mark_account_failure failed: {e}', file=sys.stderr)
        return
    # rejected / wrong_user / cancelled / aborted / 其它 → 不动账号健康
    return


def process_batch(claimed: list[dict]) -> None:
    # ① 派单侧已带号(dispatch_queue.douyin_number，读共享缓存填的)先收下——反查瞬时失败时回退用。
    cached_map = {}
    for r in claimed:
        sec = (r.get('douyin_user_id') or '').strip()
        num = (r.get('douyin_number') or '').strip()
        if sec and num:
            cached_map[sec] = num
    if cached_map:
        print(f'  [handle] 派单侧已带抖音号 {len(cached_map)}/{len(claimed)}')
    # ② 【全量】现场反查（含缓存命中的）：a) 拿实时昵称给 OCR 身份门——用户改名后 DB 旧昵称
    #    必失配 → 误杀 wrong_user；b) 核号——用户改过抖音号时缓存是旧号，搜出来是别人。
    #    每条 1 次只读接口，串发节奏下成本可忽略；结果（号+昵称）回写共享缓存自愈。
    resolved = resolve_handles(claimed)
    cache_writeback(resolved, {(r.get('douyin_user_id') or '').strip(): (r.get('customer_name') or '')
                               for r in claimed})
    handle_map = {}
    fresh_nick = {}
    definite_none: set = set()
    for r in claimed:
        sec = (r.get('douyin_user_id') or '').strip()
        if not sec:
            continue
        v = resolved.get(sec)
        if v is not None:
            if (v.get('nickname') or '').strip():
                fresh_nick[sec] = v['nickname'].strip()
            num = (v.get('number') or '').strip()
            if num:
                handle_map[sec] = num
                if cached_map.get(sec) and cached_map[sec] != num:
                    print(f'  [handle] {fresh_nick.get(sec) or sec[:12]} 抖音号已变更 '
                          f'{cached_map[sec]} → {num}（缓存过期，按新号搜）')
            else:
                definite_none.add(sec)   # ""=接口确定该用户无抖音号（实时结果优先于缓存）
        elif cached_map.get(sec):
            # 反查瞬时失败/未开启 → 回退缓存号照发（昵称门退回 DB 昵称，旧行为）
            handle_map[sec] = cached_map[sec]

    # ② enrich 硬门（默认开，AKKE_ENRICH_REQUIRE_NUMBER=0 退回旧昵称回退）：
    #    无号不跑 GUI —— 确定无号记 enrich_failed；瞬时反查失败留 claimed 等回收重试。
    sendable: list[dict] = []
    for r in claimed:
        sec = (r.get('douyin_user_id') or '').strip()
        nick = (r.get('customer_name') or '?')
        if handle_map.get(sec) or not ENRICH_REQUIRE_NUMBER:
            sendable.append(r)
        elif sec in definite_none:
            print(f'  ✗ {nick} enrich_failed(确定无抖音号) → skip，不跑 GUI')
            try:
                _rpc('complete_dispatch', {
                    'p_id': r['id'], 'p_status': 'skipped',
                    'p_ocr_confidence': None, 'p_error_message': 'enrich_failed',
                })
            except Exception as e:
                print(f'  !! complete_dispatch({r["id"][:8]}) failed: {e}', file=sys.stderr)
        else:
            print(f'  ⏳ {nick} 抖音号反查瞬时失败 → 留 claimed，15min 死锁回收后自动重试')
    if not sendable:
        return

    csv_path = write_contacts_csv(sendable, handle_map, fresh_nick)
    print(f'  → wrote {csv_path} ({len(sendable)} rows)')
    rc = run_douyin_dm(csv_path)
    print(f'  → douyin_dm.py exit {rc}')

    # dispatch_id → 现场反查到的抖音号映射，complete_one 时回写 dispatch_queue.douyin_number
    # （派单 INSERT 时缓存未命中、写的 NULL；complete 时补上 → dashboard / 复盘可见）。
    number_by_dispatch = {
        r['id']: handle_map[(r.get('douyin_user_id') or '').strip()]
        for r in sendable
        if handle_map.get((r.get('douyin_user_id') or '').strip())
    }

    # 读取 sent log，按 _dispatch_id 找本批的行
    claimed_ids = {r['id'] for r in sendable}
    log_rows = read_sent_log()
    log_by_id = {}
    for r in log_rows:
        did = (r.get('_dispatch_id') or '').strip()
        if did in claimed_ids:
            # 同一 dispatch 可能因 retry 多行（暂没设计 retry，但容错）
            log_by_id[did] = r  # 最新的覆盖

    for r in sendable:
        log_row = log_by_id.get(r['id'])
        if not log_row:
            # 完全没在 log 里 → douyin_dm.py 没跑到这一行（崩了 / abort 在它之前）
            print(f'  ?? {r["id"][:8]} not in sent_log → mark failed (no log entry)')
            payload = {
                'p_id': r['id'],
                'p_status': 'failed',
                'p_ocr_confidence': None,
                'p_error_message': 'no entry in sent_log (douyin_dm.py crashed before this row?)',
            }
            num = number_by_dispatch.get(r['id'])
            if num:
                payload['p_douyin_number'] = num
            try:
                _rpc('complete_dispatch', payload)
            except Exception as e:
                print(f'  !! complete failed for {r["id"][:8]}: {e}', file=sys.stderr)
            continue
        complete_one(r['id'], log_row, number_by_dispatch.get(r['id']))


# ── 二次触达（公开评论）支线 ─────────────────────────────────────────────────
# 镜像 DM 流程：claim_second_touch_dispatch → contacts CSV → douyin_comment_grounded.py
# → comment_log → complete_second_touch_dispatch。CSV/log 用独立文件名，不和 DM 撞。

def claim_second_touch_batch() -> list[dict]:
    data = _rpc('claim_second_touch_dispatch', {
        'p_account_id': ACCOUNT_ID,
        'p_limit': SECOND_TOUCH_CLAIM_LIMIT,
    })
    return data or []


def write_comment_contacts_csv(rows: list[dict], handle_map: dict | None = None,
                               fresh_nick: dict | None = None) -> Path:
    handle_map = handle_map or {}
    fresh_nick = fresh_nick or {}
    path = WORK_DIR / 'contacts_second_touch.csv'
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=[
            'douyin_id', 'nickname', 'message', 'has_works',
            '_comment_id', '_sec_uid', '_dispatch_id',
        ])
        w.writeheader()
        for r in rows:
            sec = (r.get('douyin_user_id') or '').strip()
            # nickname = OCR 身份门比对目标：优先实时昵称（改名误杀防护），回退 DB 昵称
            nick = ((fresh_nick.get(sec) or '').strip()
                    or (r.get('customer_name') or '').strip() or 'unknown')
            query = (handle_map.get(sec) or '').strip() or nick
            w.writerow({
                'douyin_id': query,          # 搜索栏 query：命中抖音号用唯一号搜，否则回退昵称
                'nickname': nick,
                'message': r['message'],     # 原样复用的私信原文（PM 决策）
                'has_works': 1 if r.get('has_works') else 0,
                '_comment_id': r.get('source_comment_id') or r['comment_id'],
                '_sec_uid': r['douyin_user_id'],
                '_dispatch_id': r['id'],
            })
    return path


def run_douyin_comment(csv_path: Path) -> int:
    # 默认自动发（douyin_comment_grounded 不带 --confirm 即 auto）
    proc = subprocess.run(
        [sys.executable, str(DOUYIN_COMMENT_PY), str(csv_path)],
        cwd=str(WORK_DIR),
    )
    return proc.returncode


def read_comment_log() -> list[dict]:
    path = WORK_DIR / f'comment_log_{datetime.now():%Y%m%d}.csv'
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def map_comment_status(raw: str) -> str:
    """comment_log.status → second_touch_dispatch_queue.status enum。
    回 'skipped' 的几种由 RPC 据 error_message(=raw) 再分流（见 complete_second_touch_dispatch）：
      · wrong_user / send_unverified / error:*  → 【可能已发公开评论】→ RPC 落 deferred(冷却1天)，
        不重试免重复刷屏（验证闸误判会把真发出的当没发，重发=公开刷屏，2026-06-13 教训）；
      · cancelled                                → 确未发 → RPC 不 defer，下轮可重入。
    回 'failed' 的是真·没发出（focus_fail/no_works/...），允许下轮重试。"""
    if not raw:
        return 'failed'
    if raw == 'sent':
        return 'sent'
    if raw == 'aborted':
        return 'aborted'
    # 可能已发(send_unverified/error:*) 或确未发但要 defer(wrong_user) 或确未发可重入(cancelled)
    # 一律回 skipped，交 RPC 按 error_message 决定 defer 与否。
    if raw in ('wrong_user', 'cancelled', 'send_unverified') or raw.startswith('error'):
        return 'skipped'
    # no_works / video_not_open / panel_fail / focus_fail → 确未发出 → failed，下轮可重入
    return 'failed'


def complete_second_touch_one(disp_id: str, log_row: dict) -> None:
    raw_status = (log_row.get('status') or '').strip()
    rpc_status = map_comment_status(raw_status)
    ocr_raw = (log_row.get('_ocr_confidence') or '').strip()
    ocr_conf = float(ocr_raw) if ocr_raw else None
    err = None if rpc_status == 'sent' else raw_status[:500]
    try:
        _rpc('complete_second_touch_dispatch', {
            'p_id': disp_id,
            'p_status': rpc_status,
            'p_ocr_confidence': ocr_conf,
            'p_error_message': err,
        })
        print(f'  ✓ 二触 completed {disp_id[:8]} → {rpc_status} (ocr={ocr_conf})')
    except Exception as e:
        print(f'  !! complete_second_touch_dispatch({disp_id[:8]}) failed: {e}', file=sys.stderr)


def process_second_touch_batch(claimed: list[dict]) -> None:
    resolved = resolve_handles(claimed)
    handle_map = {s: v['number'] for s, v in resolved.items() if (v.get('number') or '').strip()}
    fresh_nick = {s: v['nickname'].strip() for s, v in resolved.items()
                  if (v.get('nickname') or '').strip()}
    csv_path = write_comment_contacts_csv(claimed, handle_map, fresh_nick)
    print(f'  → wrote {csv_path} ({len(claimed)} rows，二触评论)')
    rc = run_douyin_comment(csv_path)
    print(f'  → douyin_comment_grounded.py exit {rc}')

    claimed_ids = {r['id'] for r in claimed}
    log_by_id = {}
    for r in read_comment_log():
        did = (r.get('_dispatch_id') or '').strip()
        if did in claimed_ids:
            log_by_id[did] = r  # 最新覆盖

    for r in claimed:
        log_row = log_by_id.get(r['id'])
        if not log_row:
            # 脚本跑完(exit 0)但 comment_log 找不到本行 → 不知是否已发，按【可能已发】处理：
            # 回 skipped + error_message='unverified%'，RPC 落 deferred(冷却1天) 不重试，
            # 免得它其实已发出公开评论、下轮又重发刷屏（2026-06-13 壹王情深教训）。
            print(f'  ?? 二触 {r["id"][:8]} not in comment_log → 可能已发，defer(冷却1天)不重试')
            try:
                _rpc('complete_second_touch_dispatch', {
                    'p_id': r['id'],
                    'p_status': 'skipped',
                    'p_ocr_confidence': None,
                    'p_error_message': 'unverified: no comment_log entry (script crashed after posting?)',
                })
            except Exception as e:
                print(f'  !! complete failed for {r["id"][:8]}: {e}', file=sys.stderr)
            continue
        complete_second_touch_one(r['id'], log_row)


# ── 反向评论 RC 首触（楼中回复）支线 ─────────────────────────────────────────
# 镜像二触：claim_rc_dispatch → contacts_rc CSV → douyin_rc_reply_grounded.py（自动）
# → rc_reply_log → complete_rc_dispatch（sent 写 outreach_events + 消费 lead_claim）。
# claim 返回已 JOIN 视频上下文（comment_text/video_title/video_url/video_author），直接写 CSV。

def claim_rc_batch() -> list[dict]:
    data = _rpc('claim_rc_dispatch', {
        'p_account_id': ACCOUNT_ID,
        'p_limit': REVERSE_COMMENT_CLAIM_LIMIT,
    })
    return data or []


def write_rc_contacts_csv(rows: list[dict]) -> Path:
    path = WORK_DIR / 'contacts_rc.csv'
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=[
            'douyin_id', 'nickname', 'message', 'has_works',
            '_comment_id', '_sec_uid', '_video_title', '_video_author',
            '_comment_text', '_video_url', '_dispatch_id',
        ])
        w.writeheader()
        for r in rows:
            nick = (r.get('customer_name') or '').strip() or 'unknown'
            w.writerow({
                'douyin_id': nick,           # RC=楼中回复，按视频定位；搜索列保持昵称口径
                'nickname': nick,
                'message': r['message'],     # /rc/gen 生成 + 过门的回复文案
                'has_works': 1 if r.get('has_works') else 0,
                '_comment_id': r.get('source_comment_id') or r['comment_id'],
                '_sec_uid': r['douyin_user_id'],
                '_video_title': r.get('video_title') or '',
                '_video_author': r.get('video_author') or '',
                '_comment_text': r.get('comment_text') or '',
                '_video_url': r.get('video_url') or '',
                '_dispatch_id': r['id'],
            })
    return path


def run_douyin_rc(csv_path: Path) -> int:
    # 不带 --confirm 即自动发；执行器内置 60–90s 间隔 + 日上限 15。
    proc = subprocess.run(
        [sys.executable, str(DOUYIN_RC_PY), str(csv_path)],
        cwd=str(WORK_DIR),
    )
    return proc.returncode


def read_rc_reply_log() -> list[dict]:
    path = WORK_DIR / f'rc_reply_log_{datetime.now():%Y%m%d}.csv'
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def map_rc_status(raw: str) -> str:
    """rc_reply_log.status → rc_dispatch_queue.status enum（照二触 map_comment_status 范式）。"""
    if not raw:
        return 'failed'
    if raw == 'sent':
        return 'sent'
    if raw in ('comment_not_found', 'cancelled', 'wrong_floor') or 'skip' in raw or 'not_found' in raw:
        return 'skipped'   # 非目标/没找到/取消/错楼门拦截 → 不写账本，下轮可重入
    if raw == 'aborted':
        return 'aborted'
    if raw.startswith('blocked_'):
        # SMS/滑块/人脸 modal 命中 → aborted（回池，本条 lead 可重发；账号侧由
        # _mark_account_health 处理立即 cool）
        return 'aborted'
    # send_fail / type_fail / error:* / 搜视频失败 → 发送失败
    return 'failed'


def complete_rc_one(disp_id: str, log_row: dict) -> None:
    raw_status = (log_row.get('status') or '').strip()
    rpc_status = map_rc_status(raw_status)
    err = None if rpc_status == 'sent' else raw_status[:500]
    try:
        _rpc('complete_rc_dispatch', {
            'p_id': disp_id,
            'p_status': rpc_status,
            'p_ocr_confidence': None,
            'p_error_message': err,
        })
        print(f'  ✓ RC completed {disp_id[:8]} → {rpc_status}')
    except Exception as e:
        print(f'  !! complete_rc_dispatch({disp_id[:8]}) failed: {e}', file=sys.stderr)

    # RC 通道账号健康接入（任务 3 modal 检测扩展 PR）：之前 RC 流程完全不动 accounts
    # 状态，撞 modal/连续 send_fail 也无声。现在跟 DM 通道同款 _mark_account_health：
    #   sent → mark_success（清零）
    #   send_fail → mark_failure（满 3 累计 cool）
    #   blocked_* → 立即 cool + emit P0 红牌（不等累计）
    #   其它（comment_not_found / wrong_floor / cancelled）→ 不动（非账号问题）
    _mark_account_health(raw_status)


def process_rc_batch(claimed: list[dict]) -> None:
    csv_path = write_rc_contacts_csv(claimed)
    print(f'  → wrote {csv_path} ({len(claimed)} rows，RC 楼中回复)')
    rc = run_douyin_rc(csv_path)
    print(f'  → douyin_rc_reply_grounded.py exit {rc}')

    claimed_ids = {r['id'] for r in claimed}
    log_by_id = {}
    for r in read_rc_reply_log():
        did = (r.get('_dispatch_id') or '').strip()
        if did in claimed_ids:
            log_by_id[did] = r  # 最新覆盖

    for r in claimed:
        log_row = log_by_id.get(r['id'])
        if not log_row:
            print(f'  ?? RC {r["id"][:8]} not in rc_reply_log → mark failed')
            try:
                _rpc('complete_rc_dispatch', {
                    'p_id': r['id'],
                    'p_status': 'failed',
                    'p_ocr_confidence': None,
                    'p_error_message': 'no entry in rc_reply_log (script crashed before this row?)',
                })
            except Exception as e:
                print(f'  !! complete failed for {r["id"][:8]}: {e}', file=sys.stderr)
            continue
        complete_rc_one(r['id'], log_row)


# ── main ────────────────────────────────────────────────────────────────────

def main():
    st_note = (f'  second_touch={"on" if SECOND_TOUCH_ENABLED else "off"}'
               f'  rc={"on" if REVERSE_COMMENT_ENABLED else "off"}')
    print(f'=== wuying_poll_agent  v={AGENT_VERSION}  account={ACCOUNT_ID[:8]}…  every {POLL_INTERVAL}s  claim={CLAIM_LIMIT}{st_note} ===')
    print(f'work_dir={WORK_DIR}')

    _last_autoreply = 0.0   # 收件箱捕获节流时戳(0=启动后第一轮先扫一次,之后每 DM_AUTOREPLY_INTERVAL 扫)
    while True:
        try:
            t0 = time.time()
            did_work = False

            # 路线 D：DM 自动回复(轮内最优先 — 活跃对话客户在等，优先级 自动回复>一触>二触；PR4)。
            #   ① 进程内排在一触/二触之前先跑；② 接窗口锁置 .dm-want → 跨进程 route-B 让位。
            #   mode 由 AKKE_DM_AUTOREPLY_MODE 控：capture=只读捕获(零发送风险)；both(默认)=捕获+发已审批回复。
            if DM_AUTOREPLY_CAPTURE_ENABLED and (time.time() - _last_autoreply) >= DM_AUTOREPLY_INTERVAL:
                _last_autoreply = time.time()
                _ar_mode = os.environ.get('AKKE_DM_AUTOREPLY_MODE', 'both')
                _iv = DM_AUTOREPLY_INTERVAL
                _iv_h = f'{_iv//3600}h' if _iv >= 3600 else (f'{_iv//60}min' if _iv >= 60 else f'{_iv}s')
                print(f'[{datetime.now():%H:%M:%S}] 收件箱捕获(每{_iv_h}一次) mode={_ar_mode}')
                try:
                    with _wl.dm_batch():   # 置 .dm-want → route-B 让位(autoreply 优先于一触/二触)
                        # 硬超时：捕获挂死不能拖垮整个 poll 循环（见 DM_AUTOREPLY_TIMEOUT_SEC）。
                        subprocess.run([sys.executable, str(DOUYIN_AUTOREPLY_PY), _ar_mode],
                                       cwd=str(WORK_DIR), timeout=DM_AUTOREPLY_TIMEOUT_SEC)
                except subprocess.TimeoutExpired:
                    print(f'!! dm-autoreply ({_ar_mode}) 超时 {DM_AUTOREPLY_TIMEOUT_SEC}s 已杀子进程，跳过本轮捕获继续转',
                          file=sys.stderr)
                except Exception as e:
                    print(f'!! dm-autoreply ({_ar_mode}) error: {e}', file=sys.stderr)

            # 路线 A：DM 私信（现有）
            claimed = claim_batch()
            if claimed:
                did_work = True
                print(f'\n[{datetime.now():%H:%M:%S}] claimed {len(claimed)} DM dispatches')
                process_batch(claimed)

            # 路线 B：二次触达评论（同机分时段轮转 —— DM 批之后串行跑，
            # 两个 subprocess 不并发，物理上同一时刻只操作一个抖音窗口）。
            if SECOND_TOUCH_ENABLED:
                st_claimed = claim_second_touch_batch()
                if st_claimed:
                    did_work = True
                    print(f'\n[{datetime.now():%H:%M:%S}] claimed {len(st_claimed)} second-touch dispatches')
                    process_second_touch_batch(st_claimed)

            # 路线 C：反向评论 RC 首触（楼中回复，DM/二触 批之后串行，不抢窗口）。
            if REVERSE_COMMENT_ENABLED:
                rc_claimed = claim_rc_batch()
                if rc_claimed:
                    did_work = True
                    print(f'\n[{datetime.now():%H:%M:%S}] claimed {len(rc_claimed)} RC dispatches')
                    process_rc_batch(rc_claimed)

            if did_work:
                print(f'[{datetime.now():%H:%M:%S}] loop done in {time.time() - t0:.1f}s')

            # sleep 到下一轮（不重叠）；idle 轮不打 log 避免污染
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print('\n[interrupted] bye.')
            break
        except Exception as e:
            print(f'!! poll loop error: {type(e).__name__}: {e}', file=sys.stderr)
            time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
