"""
wecom_add_agent.py — 云电脑常驻：无人值守把「有大有小」客户线索(enterprise_leads)在
企业微信里自动加好友。对称于抖音的 wuying_poll_agent.py，但目标表/通道是企微加好友。

= claim-enterprise-leads.ts(本机拉单) + 人工摆渡 + wecom_add_contact_grounded.py(云电脑发) +
  writeback-enterprise-lead.ts(本机回写) 这套【半自动】流程，收敛成云电脑上的一个【全自动】循环。
  话术选择 / status 映射 / RPC 全部沿用 yuanyurou 已合并的 enterprise_leads 那套，零分叉。

工作循环(每 AKKE_WECOM_POLL_INTERVAL 秒)：
  1. 今日剩余 = AKKE_WECOM_DAILY_LIMIT - 今日本机已发请求数(add_status=已发请求 by 本人)
  2. 先捞【本机自己 claimed 但仍"待加"】的(上轮崩了/瞬时失败的)重试；不够再 claim_enterprise_leads
     RPC(FOR UPDATE SKIP LOCKED 原子软认领，多机/多企微号并发不撞车)补满本批
  3. 按 note 选话术(移植 claim-enterprise-leads.ts 的 T1/T2/T3 + 规则)，死号跳过→回写"无效"
  4. 写 CSV(她 grounded 的列约定) → 直写 verify_template(通过率统计用)
  5. subprocess: python wecom_add_contact_grounded.py <csv>
  6. 读 sent_log_wecom_YYYYMMDD.csv，按她的 mapGuiStatus 映射 → writeback_enterprise_lead RPC 回写
     瞬时态(error/wrong_user/cancelled/aborted/空)不回写 → 留"待加"，下轮第 2 步自动重试；
     同一条本会话重试超 AKKE_WECOM_MAX_RETRY 次 → 回写"加不上"(转人工)，免无限刷

Setup(云电脑一次性)：
  pip install pyautogui pyperclip pillow opencv-python python-dotenv pygetwindow
  企业微信PC登录最大化、分辨率锁死、输入法英文；首次跑过 wecom_add_contact_grounded.py --capture 标坐标
  写 .env(与 grounded 同目录)：
    SUPABASE_URL=...                          (或 NEXT_PUBLIC_SUPABASE_URL)
    SUPABASE_SERVICE_ROLE_KEY=<你自己的 service key>   (跟随 enterprise_leads 那套的 key 模型)
    AKKE_WECOM_OPERATOR=饭粒                  (认领人显示名=claimed_by；回写也用它)
    AKKE_WECOM_ORG_ID=<有大有小 org uuid>     (不填则按名「有大有小」解析)
    OPENROUTER_API_KEY=<sk-or...>            (AI 选话术 + grounded VL 身份门用)
    AKKE_WECOM_DAILY_LIMIT=20  AKKE_WECOM_CLAIM_LIMIT=5  AKKE_WECOM_POLL_INTERVAL=300
    AKKE_WECOM_PROVINCE=福建省                (可选，只加某省)
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

WORK_DIR = Path(__file__).resolve().parent
os.chdir(WORK_DIR)

AGENT_VERSION = '2026-06-30+wecom-add-unattended-v2-recheck'

try:
    from dotenv import load_dotenv
    load_dotenv(WORK_DIR / '.env')
except ImportError:
    pass

SUPABASE_URL = os.environ.get('SUPABASE_URL') or os.environ.get('NEXT_PUBLIC_SUPABASE_URL')
# enterprise_leads 那套整体走 service_role(claim-enterprise-leads.ts / writeback 都是)，跟随之。
SERVICE_KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
               or os.environ.get('SUPABASE_SERVICE_KEY'))
OPERATOR = (os.environ.get('AKKE_WECOM_OPERATOR')
            or os.environ.get('AKKE_OPERATOR_NAME') or '').strip()
ORG_ID = os.environ.get('AKKE_WECOM_ORG_ID', '').strip()
PROVINCE = (os.environ.get('AKKE_WECOM_PROVINCE') or '').strip() or None
DAILY_LIMIT = int(os.environ.get('AKKE_WECOM_DAILY_LIMIT', '20'))
CLAIM_LIMIT = int(os.environ.get('AKKE_WECOM_CLAIM_LIMIT', '5'))
POLL_INTERVAL = int(os.environ.get('AKKE_WECOM_POLL_INTERVAL', '300'))
MAX_RETRY = int(os.environ.get('AKKE_WECOM_MAX_RETRY', '3'))
NO_PERSONALIZE = os.environ.get('AKKE_WECOM_NO_PERSONALIZE') == '1'
GROUNDED = os.environ.get('AKKE_WECOM_GROUNDED', 'wecom_add_contact_grounded.py')

# 复查支线：每轮顺带复查 N 条最老的「已发请求」是否已通过 → 自动 已发请求→已通过。
# 默认开（量小、保守、跟发请求共享窗口串行）；关掉设 AKKE_WECOM_RECHECK=0。
RECHECK_ON = os.environ.get('AKKE_WECOM_RECHECK', '1') != '0'
RECHECK_LIMIT = int(os.environ.get('AKKE_WECOM_RECHECK_LIMIT', '3'))
RECHECK_MIN_AGE_H = int(os.environ.get('AKKE_WECOM_RECHECK_MIN_AGE_H', '24'))  # 发出 <24h 不查(对方还没点)
# 每个号复查后至少隔这么久才再查一次（否则没通过的"最老 3 条"会被每轮重搜 → 浪费+风控）。
RECHECK_COOLDOWN_H = int(os.environ.get('AKKE_WECOM_RECHECK_COOLDOWN_H', '12'))
# 每轮从最老的这么多条候选里挑（滤掉冷却期内的再取 LIMIT 条 → 轮转覆盖全部而非死磕头几条）。
RECHECK_CANDIDATES = int(os.environ.get('AKKE_WECOM_RECHECK_CANDIDATES', '40'))
RECHECK_SCRIPT = os.environ.get('AKKE_WECOM_RECHECK_SCRIPT', 'wecom_accept_scan.py')

# 本会话内每个号上次复查时间（内存，重启清零）；实现"每号每 COOLDOWN_H 最多复查一次 + 轮转"。
_last_recheck: dict[str, datetime] = {}

# ── 3 条话术 + 选择规则：原样移植 scripts/claim-enterprise-leads.ts(同源同字，别各写一份)──
T1 = '装修喜讯：加微申领每平方补贴300块，装修全屋定制兔宝宝ENF级多层实木 征集样板房'   # 42字·补贴+样板房
T2 = '您好 活动568每平米（原价868） 兔宝宝ENF多层板全屋定制 包设计安装 假一赔三'      # 43字·报价+全包+保障
T3 = '兔宝宝/千年舟/莫干山全屋定制样板房 ENF多层板598每平（原价898）假一赔三 加微发报价配置'  # 49字·多品牌+配置
TEMPLATES = [T1, T2, T3]
DEFAULT_IDX = 2   # 无线索/LLM 不可用 → 话术2(最通用)

_RE_DEAD = re.compile(r'空号|无效号|关机|停机|不存在|错误号码|号码有误')
_RE_NEG = re.compile(r'没需求|不需要|不考虑|不认可|调侃|拒接|拒绝|挂了|挂电话|骚扰|不感兴趣')
_RE_CLUE = re.compile(
    r'面积|平米|平方|小区|毛坯|翻新|精装|新房|旧房|风格|预算|测量|闲鱼|客服安排|'
    r'改造|刷墙|贴砖|装修|户型|交房|动工|拎包')

# AI 选话术：默认跟 claim-enterprise-leads.ts 同款 chat 模型，保证选择口径一致。
PICK_KEY = os.environ.get('OPENROUTER_API_KEY') or os.environ.get('ANTHROPIC_API_KEY')
PICK_BASE = os.environ.get('LLM_BASE_URL', 'https://openrouter.ai/api/v1')
PICK_MODEL = (os.environ.get('AKKE_WECOM_PICK_MODEL')
              or os.environ.get('LLM_CHAT_MODEL') or 'qwen/qwen3-235b-a22b-2507')

for k, v in [('SUPABASE_URL', SUPABASE_URL),
             ('SUPABASE_SERVICE_ROLE_KEY', SERVICE_KEY),
             ('AKKE_WECOM_OPERATOR(认领人名)', OPERATOR)]:
    if not v:
        print(f'❌ env missing: {k}', file=sys.stderr)
        sys.exit(2)

# 本会话内同一条线索的瞬时失败重试计数(进程重启清零)
_retry_count: dict[str, int] = {}


def _req(path, method='GET', data=None, extra_headers=None):
    headers = {'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}',
               'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{path}',
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=40) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else None


def resolve_org() -> str:
    if ORG_ID:
        return ORG_ID
    try:
        rows = _req('organizations?name=eq.%s&select=id' % urllib.parse.quote('有大有小'))
        if rows:
            return rows[0]['id']
    except Exception as e:
        print('  [warn] 解析「有大有小」org 失败: %s' % e, file=sys.stderr)
    return '00000000-0000-0000-0000-000000000001'


# ── 话术选择(移植 claim-enterprise-leads.ts 的 buildVerify)─────────────────
def _pick_template(note: str) -> int:
    if not PICK_KEY:
        return DEFAULT_IDX
    prompt = (
        '根据这条全屋定制客户的跟进备注，判断下面 3 条加微信话术里哪一条最匹配该客户，只回数字 1/2/3：\n'
        '1 =【加微补贴300/平 + 征集样板房】适合：新房/毛坯/刚交房/还没开工/图优惠/价格敏感\n'
        '2 =【568报价 + 包设计安装 + 假一赔三】适合：想了解价格/要全包整装/旧房整体翻新/想尽快动工\n'
        '3 =【兔宝宝/千年舟/莫干山多品牌 + 发产品配置】适合：想了解材料/对比品牌/关注环保/要详细配置\n'
        '客户备注：「%s」\n只回一个数字 1、2 或 3。' % re.sub(r'\s+', ' ', note)[:200])
    try:
        body = json.dumps({'model': PICK_MODEL, 'max_tokens': 10, 'temperature': 0.2,
                           'messages': [{'role': 'user', 'content': prompt}]}).encode()
        req = urllib.request.Request(PICK_BASE.rstrip('/') + '/chat/completions', data=body,
                                     headers={'Authorization': 'Bearer ' + PICK_KEY,
                                              'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=40) as resp:
            out = json.loads(resp.read().decode())
        txt = (out['choices'][0]['message']['content'] or '').strip()
        m = re.search(r'[123]', txt)
        return int(m.group(0)) if m else DEFAULT_IDX
    except Exception as e:
        print('  [warn] AI 选话术失败(用默认话术%d): %s' % (DEFAULT_IDX, e), file=sys.stderr)
        return DEFAULT_IDX


def _search_key(lead: dict) -> str:
    return (lead.get('wechat') or '').strip() or (lead.get('phone') or '').strip()


def build_verify(lead: dict) -> tuple[str | None, int | None, bool]:
    """返回 (verify_msg, idx, dead)。dead=死号且无微信号 → 跳过(回写无效)。"""
    note = (lead.get('note') or '').strip()
    has_wechat = bool((lead.get('wechat') or '').strip())
    if note and _RE_DEAD.search(note) and not has_wechat:
        return None, None, True
    if (not NO_PERSONALIZE) and note and _RE_CLUE.search(note) and not _RE_NEG.search(note):
        idx = _pick_template(note)
        return TEMPLATES[idx - 1], idx, False
    return TEMPLATES[DEFAULT_IDX - 1], DEFAULT_IDX, False


# ── 拉单 ───────────────────────────────────────────────────────────────────
_SEL = 'id,source_id,name,phone,wechat,province,city,note'


def daily_done(org: str) -> int:
    """今日本机已发请求数(风控日上限按这个数)。"""
    today = datetime.now().strftime('%Y-%m-%dT00:00:00')
    try:
        rows = _req('enterprise_leads?org_id=eq.%s&claimed_by=eq.%s'
                    '&add_status=eq.%s&add_status_at=gte.%s&select=id'
                    % (org, urllib.parse.quote(OPERATOR),
                       urllib.parse.quote('已发请求'), today))
        return len(rows or [])
    except Exception as e:
        print('  [warn] 查今日已发数失败(按 0 算): %s' % e, file=sys.stderr)
        return 0


def retry_pool(org: str, limit: int) -> list[dict]:
    """本机 claimed 但仍"待加"的(上轮崩/瞬时失败)，优先重试。"""
    if limit <= 0:
        return []
    try:
        rows = _req('enterprise_leads?org_id=eq.%s&claimed_by=eq.%s&add_status=eq.%s'
                    '&order=claimed_at.asc&limit=%d&select=%s'
                    % (org, urllib.parse.quote(OPERATOR), urllib.parse.quote('待加'),
                       limit, _SEL))
        return rows or []
    except Exception as e:
        print('  [warn] 查重试池失败: %s' % e, file=sys.stderr)
        return []


def claim_new(org: str, limit: int) -> list[dict]:
    if limit <= 0:
        return []
    try:
        return _req('rpc/claim_enterprise_leads', method='POST',
                    data={'p_org_id': org, 'p_limit': limit, 'p_by': OPERATOR,
                          'p_province': PROVINCE}) or []
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        print('  ❌ claim_enterprise_leads RPC 失败(%s): %s' % (e.code, detail), file=sys.stderr)
        return []


# ── CSV / verify_template / 回写 ────────────────────────────────────────────
def write_csv(items: list[dict]) -> Path:
    """items: [{lead, verify_msg}]，写她 grounded 的列约定。"""
    p = WORK_DIR / ('_wecom_add_%s.csv' % datetime.now().strftime('%Y%m%d%H%M%S'))
    with open(p, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['search_key', 'name', 'province',
                                          '_source_id', 'note', 'verify_msg'])
        w.writeheader()
        for it in items:
            l = it['lead']
            w.writerow({
                'search_key': _search_key(l), 'name': l.get('name') or '',
                'province': l.get('province') or '', '_source_id': l.get('source_id') or '',
                'note': re.sub(r'\s+', ' ', (l.get('note') or '')).strip(),
                'verify_msg': it['verify_msg']})
    return p


def stamp_templates(org: str, stamps: list[tuple[str, int]]) -> None:
    """直写 verify_template(通过率统计用)；best-effort，失败不阻断。"""
    by_idx: dict[int, list[str]] = {1: [], 2: [], 3: []}
    for sid, idx in stamps:
        by_idx.setdefault(idx, []).append(sid)
    for idx, sids in by_idx.items():
        if not sids:
            continue
        inlist = ','.join('"%s"' % s for s in sids)
        try:
            _req('enterprise_leads?org_id=eq.%s&source_id=in.(%s)' % (org, inlist),
                 method='PATCH', data={'verify_template': idx})
        except Exception as e:
            print('  [warn] verify_template 回写失败(话术%d): %s' % (idx, e), file=sys.stderr)


def map_gui_status(s: str):
    """复刻 writeback-enterprise-lead.ts 的 mapGuiStatus；None=不回写(留待加重试)。"""
    t = (s or '').strip()
    if t == '已发请求':
        return ('已发请求', None)
    if t == 'already':
        # ⚠️ already 是 grounded 纯视觉猜测(见"发消息"而非"添加")→ 常把非好友误判为已是好友
        #（2026-07-01 实证 2 条非好友被误判、还被当"已通过"推了群）。不再算"已通过"：
        # 改判「加不上·转人工核」，既不污染"已通过"/推群/战报，也留 grounded 存的
        # screenshots/wecom_already_*.png 供人工核实。真已是好友的少数也一并转人工，代价可接受。
        return ('加不上', '疑似已是好友(VL判定未实核)·转人工核')
    if t == 'not_found':
        return ('加不上', '搜不到（可能没开手机号搜索）')
    if t == '加不上':
        return ('加不上', None)
    return None


def writeback(org: str, source_id: str, status: str, note: str | None) -> None:
    try:
        _req('rpc/writeback_enterprise_lead', method='POST',
             data={'p_org_id': org, 'p_source_id': source_id,
                   'p_status': status, 'p_note': note})
        print('  ✓ %s → %s%s' % (source_id, status, '(%s)' % note if note else ''))
    except Exception as e:
        print('  [warn] 回写失败 %s: %s' % (source_id, e), file=sys.stderr)


def read_sent_log() -> dict:
    log = WORK_DIR / ('sent_log_wecom_%s.csv' % datetime.now().strftime('%Y%m%d'))
    out = {}
    if not log.exists():
        return out
    with open(log, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            sid = row.get('_source_id')
            if sid:
                out[sid] = row   # 同 id 取最后一行=最新
    return out


def run_grounded(csv_path: Path) -> int:
    print('  → subprocess: python %s %s' % (GROUNDED, csv_path.name))
    return subprocess.run([sys.executable, GROUNDED, str(csv_path)], cwd=str(WORK_DIR)).returncode


# ── 发请求主流程 ────────────────────────────────────────────────────────────
def add_step(org: str):
    done = daily_done(org)
    remaining = DAILY_LIMIT - done
    print('[%s] 今日已发请求 %d / 上限 %d → 剩余 %d' % (
        datetime.now().strftime('%H:%M:%S'), done, DAILY_LIMIT, remaining))
    if remaining <= 0:
        print('  今日配额用尽，待机。')
        return
    want = min(CLAIM_LIMIT, remaining)

    # 1. 先重试本机自己 claimed 但仍待加的，不够再 claim 新的
    leads = retry_pool(org, want)
    if leads:
        print('  重试池 %d 条(上轮未完成)' % len(leads))
    if len(leads) < want:
        fresh = claim_new(org, want - len(leads))
        if fresh:
            print('  新认领 %d 条' % len(fresh))
        leads += fresh
    if not leads:
        print('  无「待加」线索，待机。')
        return

    # 2. 选话术 / 死号跳过 / 超重试上限转人工
    items, stamps = [], []
    for l in leads:
        sid = l.get('source_id') or ''
        if _retry_count.get(sid, 0) >= MAX_RETRY:
            print('  [give-up] %s 本会话重试 %d 次仍失败 → 加不上(转人工)' % (sid, MAX_RETRY))
            writeback(org, sid, '加不上', '自动执行多次异常，转人工')
            continue
        if not _search_key(l):
            writeback(org, sid, '无效', '无手机号且无微信号')
            continue
        verify, idx, dead = build_verify(l)
        if dead or not verify or idx is None:
            writeback(org, sid, '无效', '死号(备注: %s)' % (l.get('note') or '')[:20])
            continue
        items.append({'lead': l, 'verify_msg': verify})
        stamps.append((sid, idx))
    if not items:
        print('  本批全部跳过(死号/无号/转人工)。')
        return
    print('  本批 %d 条可加: %s' % (
        len(items), ', '.join(_search_key(it['lead']) for it in items)))

    # 3. 写 CSV + 记话术编号 → 跑 grounded → 回写
    csv_path = write_csv(items)
    stamp_templates(org, stamps)
    run_grounded(csv_path)
    log = read_sent_log()
    for it in items:
        sid = it['lead'].get('source_id') or ''
        row = log.get(sid)
        if not row:
            _retry_count[sid] = _retry_count.get(sid, 0) + 1
            print('  [warn] %s 在 sent_log 没找到(GUI 没跑到?)，留待加重试' % sid)
            continue
        mapped = map_gui_status(row.get('status') or '')
        if mapped:
            writeback(org, sid, mapped[0], mapped[1])
            _retry_count.pop(sid, None)
        else:
            _retry_count[sid] = _retry_count.get(sid, 0) + 1
            print('  [retry] %s status=%s 瞬时态，不回写，留待加(第%d次)' % (
                sid, row.get('status'), _retry_count[sid]))


# ── 复查支线：已发请求 → 已通过（不扫名单，逐条复搜看是否已成好友）─────────────
def recheck_pool(org: str, limit: int) -> list[dict]:
    """本机 claimed、状态已发请求、且发出 >RECHECK_MIN_AGE_H 小时的，最老优先复查。"""
    if limit <= 0:
        return []
    cutoff = (datetime.now() - timedelta(hours=RECHECK_MIN_AGE_H)).isoformat()
    try:
        return _req('enterprise_leads?org_id=eq.%s&claimed_by=eq.%s&add_status=eq.%s'
                    '&add_status_at=lt.%s&order=add_status_at.asc&limit=%d'
                    '&select=source_id,name,phone,wechat'
                    % (org, urllib.parse.quote(OPERATOR), urllib.parse.quote('已发请求'),
                       urllib.parse.quote(cutoff), limit)) or []
    except Exception as e:
        print('  [warn] 查复查池失败: %s' % e, file=sys.stderr)
        return []


def write_recheck_csv(rows: list[dict]) -> Path:
    p = WORK_DIR / ('_wecom_recheck_%s.csv' % datetime.now().strftime('%Y%m%d%H%M%S'))
    with open(p, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['search_key', 'name', '_source_id'])
        w.writeheader()
        for r in rows:
            w.writerow({'search_key': _search_key(r), 'name': r.get('name') or '',
                        '_source_id': r.get('source_id') or ''})
    return p


def read_recheck_log() -> dict:
    log = WORK_DIR / ('recheck_log_wecom_%s.csv' % datetime.now().strftime('%Y%m%d'))
    out = {}
    if not log.exists():
        return out
    with open(log, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            sid = row.get('_source_id')
            if sid:
                out[sid] = row   # 同 id 取最后一行=最新
    return out


def recheck_step(org: str):
    """复查已发请求是否已通过；passed→已通过，其余保守不动。
    从最老的一批候选里滤掉冷却期(COOLDOWN_H)内刚查过的，再取 LIMIT 条 → 轮转覆盖全部，
    避免"没通过的最老几条被每轮重搜"。"""
    candidates = recheck_pool(org, RECHECK_CANDIDATES)
    if not candidates:
        return
    cutoff = datetime.now() - timedelta(hours=RECHECK_COOLDOWN_H)
    due = [r for r in candidates
           if _last_recheck.get(r.get('source_id') or '', datetime.min) < cutoff]
    rows = due[:RECHECK_LIMIT]
    if not rows:
        print('  [recheck] %d 条候选都在 %dh 冷却期内，本轮跳过' % (len(candidates), RECHECK_COOLDOWN_H))
        return
    now = datetime.now()
    for r in rows:                       # 标记已查(不管结果)→进冷却，下轮轮到别的
        _last_recheck[r.get('source_id') or ''] = now
    print('  [recheck] 复查 %d/%d 条已发请求(发出>%dh，冷却%dh): %s' % (
        len(rows), len(candidates), RECHECK_MIN_AGE_H, RECHECK_COOLDOWN_H,
        ', '.join(_search_key(r) for r in rows)))
    csv_path = write_recheck_csv(rows)
    print('  → subprocess: python %s %s' % (RECHECK_SCRIPT, csv_path.name))
    subprocess.run([sys.executable, RECHECK_SCRIPT, str(csv_path)], cwd=str(WORK_DIR))
    log = read_recheck_log()
    for r in rows:
        sid = r.get('source_id') or ''
        row = log.get(sid)
        if not row:
            _last_recheck.pop(sid, None)   # 没查到(GUI 没跑到)→撤销冷却，下轮再查
            print('  [recheck] %s 没复查到(GUI 没跑到?)，下轮再查' % sid)
            continue
        if (row.get('state') or '') == 'passed':
            writeback(org, sid, '已通过', '复查:对方已通过(%s)' % (row.get('seen') or '')[:20])
        # pending/notyet/error → 保守不动，留 已发请求 下轮再查


def tick(org: str):
    add_step(org)
    if RECHECK_ON:
        try:
            recheck_step(org)
        except Exception as e:
            print('  [warn] 复查支线异常(不影响发请求): %s' % e, file=sys.stderr)


def main():
    org = resolve_org()
    print('=== wecom_add_agent %s ===' % AGENT_VERSION)
    print('org=%s operator=%s province=%s daily=%d claim=%d poll=%ds' % (
        org, OPERATOR, PROVINCE or '(全部)', DAILY_LIMIT, CLAIM_LIMIT, POLL_INTERVAL))
    print('复查支线: %s (每轮复查 %d 条发出>%dh 的已发请求 → 已通过)' % (
        '开' if RECHECK_ON else '关', RECHECK_LIMIT, RECHECK_MIN_AGE_H))
    print('前置: 企业微信PC前台最大化 + 分辨率锁 + 已 --capture 标坐标 + 输入法英文\n')
    while True:
        try:
            tick(org)
        except Exception as e:
            print('  ❌ tick 异常(继续轮询): %s' % e, file=sys.stderr)
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
