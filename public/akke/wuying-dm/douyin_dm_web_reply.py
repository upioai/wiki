# -*- coding: utf-8 -*-
"""douyin_dm_web_reply — 无影【Edge 网页版】DM 自动回复·就地发送原语。

对应 PC 客户端版的 douyin_dm_reply_inbox.reply_in_inbox，被 douyin_dm_autoreply.send()
在 web 通道（AKKE_DM_SCRIPT 含 'web'）时调用。

为什么不读私信会话列表点开会话(像 PC 版那样)：网页版没有可靠的 UIA 会话列表点开路径，
但 web 首触链(douyin_dm_web_grounded)已验证可靠 —— sec_uid 地址栏直达对方主页 →
主页身份门 → 点「私信」开右侧浮层 → 面板身份门 → 打字 → 发送↑ → 验气泡。回复对象的
sec_uid 由 claim_dm_replies 返回(conversations.douyin_user_id 存的就是 sec_uid)，故
回复 = 复用 process_web，只把 message 换成 draft、关掉关注/点赞(回复对象首触时早已关注)。

⚠️ 关 follow/like 不能靠预设环境变量：douyin_dm_grounded 用 load_dotenv(override=True)
会把 .env 的 AKKE_WEB_DO_FOLLOW/LIKE 覆盖回来。故 import 后直接改模块全局(函数调用时
查全局，确定生效)。

前提：同 douyin_dm_web_grounded —— Edge 登录该号网页版、最大化、输入法【英文】、坐标已校准。
"""
from __future__ import annotations

import douyin_dm_web_grounded as _web

# 回复对象在首触时已被关注，自动回复不重复关注/点赞：省时，且避免误点成取消关注。
_web.DO_FOLLOW = False
_web.DO_LIKE = False


def reply_in_web(nick: str, sec_uid: str, draft: str, confirm: bool = False) -> str:
    """给 (nick, sec_uid) 发一条 draft 回复。返回 process_web 的 status 字符串：
    sent / no_secuid / wrong_user / dm_panel_failed / wrong_chat / rejected / unverified / error:<msg>。
    只有 'sent' 算成功；调用方据此 complete_dm_reply(sent/failed)。"""
    sec = (sec_uid or "").strip()
    if not sec:
        return "no_secuid"
    c = {"nickname": nick, "_sec_uid": sec, "message": draft, "douyin_id": ""}
    status, _conf = _web.process_web(c, confirm=confirm)
    return status


if __name__ == "__main__":
    # 手动单条 spike：py douyin_dm_web_reply.py <sec_uid> <昵称> "<草稿>"
    # 默认 --confirm(发送前回车确认)，加 --no-confirm 直发。
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    confirm = "--no-confirm" not in sys.argv
    if len(args) < 3:
        sys.exit('用法: py douyin_dm_web_reply.py <sec_uid> <昵称> "<草稿>" [--no-confirm]')
    sec_uid, nick, draft = args[0], args[1], args[2]
    print("status =", reply_in_web(nick, sec_uid, draft, confirm=confirm))
