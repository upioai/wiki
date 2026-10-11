"""首触「先关注+点赞，再私信」一半一半对照（2026-10-11 Jade 批）。

AKKE_FOLLOW_AB=1 时按目标身份稳定哈希分组，同一个人永远同组：
  B 组 = 关注 + 点赞最新作品（无视 AKKE_SKIP_FOLLOW；点赞仍需 AKKE_C_WORK_FIRST / AKKE_C_LIKE）
  A 组 = 关注、点赞都跳过（记 skipped_ab）
键优先 _sec_uid（= dispatch_queue.douyin_user_id），事后按 arm() 同口径重算组别即可切回复率，不用落库。
单独成文件是因为 douyin_dm_grounded.py 卡在单文件行数天花板（scripts/file-size-baseline.json）。
"""
import hashlib
import os


def enabled() -> bool:
    return os.environ.get('AKKE_FOLLOW_AB', '0') == '1'


def arm(c) -> str:
    """'A' / 'B'；未开对照返回 ''。三个身份字段都空 → 'A'（不做多余动作）。"""
    if not enabled():
        return ''
    key = (c.get('_sec_uid') or c.get('douyin_id') or c.get('nickname') or '').strip()
    if not key:
        return 'A'
    return 'B' if int(hashlib.md5(key.encode('utf-8')).hexdigest(), 16) % 2 else 'A'


def run(c, follow_user, like_latest_work, nick):
    """按组执行关注/点赞，把 follow_ab_arm / follow_status / like_status 写进 c（sent_log 落这三列）。"""
    c['follow_ab_arm'] = a = arm(c)
    if a == 'A':
        c['follow_status'] = c['like_status'] = 'skipped_ab'
    else:
        c['follow_status'] = follow_user(force=(a == 'B'))
        c['like_status'] = like_latest_work(nick)
    print('  [关注/点赞] 组=%s 关注=%s 点赞=%s' % (a or '-', c['follow_status'], c['like_status']))
