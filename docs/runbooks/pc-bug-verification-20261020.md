# 个人微信 Bug 独立检验记录（2026-10-20）

检验方式：每个有修复 PR 的编号各派一个未参与实现的只读检查任务，按 `wecom-chat` 仓 `docs/runbooks/personal-wechat-bug-verifier.md` 的流程核对 PR 改动、源码路径与定向测试；本页由实现者依其结论整理，并非可外部验证的独立签名。没有在目标电脑或手机上操作，也没有回放真实客户对话。W-112 还没有 PR，W-115 的 #2252 在本记录之后才开，二者未检验。公开记录不包含客户身份、备注名、设备标识、原始聊天或截图。

| 编号 | 独立结论 | 已核对 | 未证实或未完成 |
|---|---|---|---|
| W-110 | `CODE_ONLY` | [#2244](https://github.com/Akke-AI/wecom-chat/pull/2244)：检验时 PR 未合并（现已合并，合并后未再核）。`ANDROID_RECALL_ACTIONS` 含 `android_recall_unreachable`，按 action 路由与鉴权的位置（`route.ts`、`reply-key-scope.ts`）都引用该数组，Kotlin 拼出的 action 名与白名单一致；服务端落点写停营销标记，`windows-title-recall.ts` 见到标记返回 `marketing_stopped`。定向测试 3 个用例（唯一认出写标记、重复上报不重写、重名 / 种类无效 / 缺客户名不写）；隔离目录跑相关 21 个测试通过。上报只在读到两类系统提示时发起，6 小时内去重 | 没有真机证据。合并后的服务端部署、APK 正式包（检验时只有 Draft 测试包）、装机均无证据。上报只接在一处核底检查，另两处拒收分支是否都经过这一处未逐条核。上报在后台线程，失败只写日志不重试。Kotlin 端测试依赖 CI gradle，检验时未出结果 |
| W-111 | `CODE_ONLY` | [#2246](https://github.com/Akke-AI/wecom-chat/pull/2246)：已合并（0dfba42e），服务端部署 run 成功，`/api/status` 的提交晚于它一个提交。服务端 `takebackAfterSec` 只对 `sales_typed` 返回值，`owesCustomerReply` 让「最后一行是客户」算欠答，两个让位出口都带该字段；端上 `takeback.py` 到点点开、补回放行一次。反例：销售在客户之后回过不接回、冷静期不抢话、转人工不排接回、超 1 小时作废。`test_takeback.py` 8 个、handoff 相关 146 个、相邻电脑端 275 个、发版脚本 11 个测试通过 | 电脑端 1.25.22 没有出包（检验时台账最新为 1.25.21，之后 1.25.23+35cc9dd 已出包；之后随 1.25.23+35cc9dd 出包），无装机、心跳、真机证据；没看到生产日志里的 `takeback_scheduled`。端上须在 1 小时内巡检到该会话，否则作废；其它端无同类接回。必选检查结论未核 |
| W-112 | `UNVERIFIED` | 修复在分支 `fix-friend-accept-scrollup` 实现中，没有 PR，没有可检验的代码。故障事实（16:42 点开时灰字已出屏、当天 `friend_accepted` 0 条）来自运行记录 | 未做代码复核、未跑测试，不能给出通过结论 |
| W-113 | `CODE_ONLY` | [#2248](https://github.com/Akke-AI/wecom-chat/pull/2248)：已合并（35cc9dd9，10-20 07:55）。`reuse_current` 判定只有一处（`runner.py` 约 6041 行）；召回复用那一处（约 4876 行）先核过完整标题。把代码换回修复前，`test_selected_row_with_unreadable_title_is_clicked_next_round` 两个参数都失败；既有反例「绿底且标题读得出照旧复用不点」通过；该文件 7 个用例通过。PR 检查均成功 | 没有真机证据；发版台账最新为 1.25.21，之后 1.25.23+35cc9dd 已出包，1.25.23 未出包、未装机。强制点击后标题仍读不出是否反复点同一行未测；只记一个待点行，多行交替未测；测试只模拟标题读不出，没模拟右边确实没打开。整个测试目录没跑出结果 |
| W-114 | `CODE_ONLY` | [#2250](https://github.com/Akke-AI/wecom-chat/pull/2250)：检验时 PR 未合并，必选检查 `tsc + lint + test` 红（`handleWindowsRecall` 1015 行超过 1013 的上限，非逻辑错误）。贴图被拒、重贴、未落地、空白、回车被拒五处失败路径都改接核对；端上 `imagesSent` / `imagesRequested` 与服务端字段一致，`images_incomplete:S/R` 追加在原因后，不覆盖前缀；图全发、请求 0、非整数、已标记过都不改原因。电脑端新增测试 5 个、服务端 188 个、电脑端相关 226 个通过 | `poster_unverifiable`（贴后抓不到图）那条路径仍是直接清空，没有核对也不报 `poster_draft_left`；重贴第二次的清空没核对；缺图无结构化列；无自动补发。合并、服务端部署、1.25.25 出包、装机、真机均无证据 |
| W-115 | `UNVERIFIED` | 修复在分支 `fix-slot-wait-keeps-first-touch` 进行中，检验时没有 PR（之后开了 #2252，未检验）。故障事实（`idle_history.py` 约 2265 行提前返回）已在 `origin/main` 核到；日志 22:05–22:08 一段已核，23:39 通过好友那一幕据运行记录 | 未做修复复核、未跑测试，不能给出通过结论 |
| W-116 | `CODE_ONLY` | [#2261](https://github.com/Akke-AI/wecom-chat/pull/2261)：检验时 PR 未合并。`weixin-friend-daily.ts` 新增按北京通过日、只算 Windows 电脑的计数，读失败为 null；`runner.py` 约 2617 行记通过窗口改为 `FT.NOTICE_MAX_AGE_SEC`（`first_touch.py` 约 325 行，3 天），与首触同一常量；版本 1.25.28。服务端 `windows_friend_accepted` 对较老的通过时间只往早改、已有不覆盖，无营销恢复类副作用。定向 pytest 166 passed、vitest 9 passed | 服务端部署、正式包、目标电脑安装、真机场景均未验；看板渲染未核；存量缺通过日期的记录不会自动补回；读取上限 20000 行截断未处理 |

代码复核与真实场景验收是不同层级。本记录没有把 PR 合并或出包推断为真实场景已通过。各条装机并在生产日志里看到对应那一行后，再更新为 `PASS`；若仍复发，标 `FAIL` 并保留待关注。

## W-99（普通代回）与 W-103 —— 独立检验 agent，2026-10-20

检验人没有参与实现，只读远程分支取证。

**W-99：CODE_ONLY**
- `bottom_recheck.py` 只剔居中灰字，通话记录（带左右）保留；10-12 那类真分歧的三条用例照拦。
- 检验人实测：把新测试拷到 `origin/main` 的代码上跑，3 条失败、3 条通过，说明测试确实复现了原问题。
- `vision.py` 里只有一处在比对两个模型的读数，没有遗漏的同族调用点。
- 非阻断：两份读数都只有灰字时判为一致。检验后在 #2257 补了用例 `test_两边都只读到灰字_判一致`（只有一边只读到灰字时仍判不一致）；这种画面下游走「读不出气泡」，不会出话。
- 缺：装机，以及真实灰字场景下普通代回不再出现 `bottom_recheck_mismatch` 的日志。

**W-103：CODE_ONLY，只能称止血**
- 端上 `autostart.restart_unsafe_root` 与服务端 `restartUnsafeRoot` 判据一致；读不到自启体检时退回老行为；不安全目录的机器照常下载。
- 部署后生产试运行（2026-10-20 01:31 UTC，输出见 wecom-chat #2258 评论）：电脑 B 判 `update_staging`、电脑 A 判 `codex_virtualized`，都不自动重启；试点电脑 C 照旧。
- 拉起失败的根因未查明。
- 非阻断：14 天、500 行窗口内自启体检刷屏时，可能有机器读不到安装目录，退回老行为；端上这道闸也会拒绝人工下发的重启。

**相关：自动升级停摆（不属于任何 W 编号）**
10-18 ~ 10-20 电脑 A、B 停在 1.25.14，W-104 ~ W-111「待装机」，原因有两条：
① Winds 没配试点名单，发版又没填 host，10-19 01:00–14:00 UTC 的每一轮都没有给任何机器下发新版（`setUpdate=0`）；
② 证明只认最新候选。
wecom-chat #2256 已合并并部署：加了 Winds 默认试点（电脑 C）和「已证明回退」（新版还没试够时，先把落后机器升到试点已跑稳的那一版）。部署后生产摘要里已出现这次新加的 `fallback=` 字段。检验结论：没有降级路径，也不会覆盖人工钉的更新目标。
