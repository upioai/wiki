const fs = require('fs');
const path = require('path');

const learnDir = path.join(__dirname, '..', 'public', 'learn');

// ────────────────────────────────────────────────────────────
// Manifest — 三个分类 × N 篇知识分享。每条 file = `<slug>.html`，
// 脚本会校验 public/learn/<file> 是否存在；缺失则 warn 并跳过该卡片。
// href 不带 .html（Vercel cleanUrls），形如 /learn/<slug>。
// ────────────────────────────────────────────────────────────
const CATEGORIES = [
  {
    label: 'LLM FOUNDATIONS',
    title: '大模型基础',
    items: [
      { slug: 'llm-glossary',              title: '大模型术语图鉴',              desc: '给运营与产品的大模型可视化术语手册。' },
      { slug: 'attention-is-all-you-need', title: 'Attention Is All You Need',   desc: 'Transformer 论文的极简三页讲解。' },
      { slug: 'scaling-law',               title: 'Scaling Law 缩放定律',        desc: '模型规模、数据与算力如何决定性能。' },
      { slug: 'kv-cache',                  title: 'KV Cache 讲解',               desc: '推理加速的核心机制与成本优化。' },
      { slug: 'world-models',              title: '世界模型',                    desc: '让 AI 在脑内模拟世界的范式。' },
      { slug: 'deep-learning-rl',          title: '深度学习与强化学习',          desc: '两大范式的通俗入门。' },
      { slug: 'recursive-self-improvement',title: '递归自我改进',                desc: 'AI 自我迭代的原理与边界。' },
      { slug: 'progressive-disclosure',    title: '渐进式披露',                  desc: '上下文工程中的记忆与信息分层。' },
      { slug: 'representation-learning',    title: '表征学习',                    desc: '让 AI 把万物「翻译成数字」——embedding 与向量搜索的地基，结合 Akke 举例。' },
      { slug: 'interpretability-alignment',title: '可解释性与对齐',              desc: '看懂 AI 在想什么、让它做我们想要的——结合 Akke 举例。' },
      { slug: 'generative-media',          title: 'AI 内容生成四件套',           desc: '文生图 · 文生视频 · 图生视频 · 视频配音——2026 年 7 月各厂商最新格局、底层原理、GitHub 高星开源选型，结合 Softie 与 Akke 的真实生产实践与合规红线。' },
    ],
  },
  {
    label: 'AGENT · AI ENGINEERING',
    title: 'Agent · AI 工程',
    items: [
      { slug: 'web-vs-desktop-client', title: '门店工作台该做网页版还是 Windows 客户端', desc: '把门店营销工作台原型（V4.1）的 JS bundle 解包，40 个界面逐屏枚举出来做形态判定：桌面客户端相对浏览器的独占能力只有 5 类（本地文件系统、常驻托盘、驱动本机 App、离线、本机硬件），逐类去找落点——命中 0 屏；反过来手机拍摄直传、企微扫码登录、扫码开通付款、一条链接即交付这四件事客户端做不了或做得更差。也替客户端说了两句话：日均 76–84 分钟的重度使用，以及唯一真站得住的那条——大陆可达性（2026-07-28 成都实测 akke.vercel.app 超时、*.supabase.co DNS 解析失败），并说明为什么客户端同样绕不过这堵墙。含与 T1/T2/T3 执行分级的对齐（三档没有一档落在店长电脑上）、选网页后真正要花钱的四件事、三条回头做客户端的触发线与「网页 + 薄 Agent」的正确形态（深色技术长页，2026-08-02 原型实测）。' },
      { slug: 'akke-tech-overview', title: 'Akke 项目技术全景 · 顾问速览', desc: '给第一次接触项目的技术顾问：一页滚动读懂 Akke 的部署三件套、评论→打分→触达数据管线、分通道 LLM 路由、绕抖音风控的真机/云电脑执行层、多租户 RLS，以及现阶段进展与最新技术栈版本（深色技术长页）。' },
      { slug: 'qwen-in-production', title: '千问在我们的生产链路里 · 团队项目与模型全景', desc: '为阿里千问团队来访准备的一页速览：六条产品线（Akke / 门店工作台 / Softie / Vivi / CozyUp / Workflow）各自在用哪个千问槽位，九个模型逐个列出角色、用途与逐项核验过的挂牌单价。重头是两轮去偏横评的实测结论——第一轮把 chat 槽切到 2507 快照，输入侧降本 5.1×、输出侧 3.3×，但诚实写明红利是「成本砍掉八成」而非「效果飞跃」；第二轮出了个反直觉结果（qwen3-max 4.77 反而低于 qwen3-235b-a22b 5.82），以及一次六天后的全量回滚——照评测第一名切了推理模型，生产实跑单条 131~171 秒击穿两道超时天花板，教训是「只测质量不测 wall-clock 延迟就会选出线上跑不动的冠军」。另含围着模型建的四道护栏（一行 env 回滚 / 推理 token 哨兵 / 评测额度隔离 / 逐条成本回填）、三类生产实测踩坑（VL 判小控件状态的假阴性、json_object 退化、长度合规回退），以及按卡点排序的九个待请教问题（深色技术长页，单价与评测数据截至 2026-08-07）。' },
      { slug: 'store-workbench-decision', title: '门店营销工作台 · 独立产品化的架构决策', desc: '一个 36 屏交互原型要变成能单独迭代的产品，架构该怎么切。先拆原型（22 主屏 + 14 详情屏、13 屏一次性配置 vs 9 屏每日任务），再复盘三个月前「不要独立」那版选型的四个候选与否决理由，以及它为什么在目标函数变了之后被推翻。核心决策是把「独立」拆成四个可分别取舍的层级，最终停在「独立仓 + 共享数据面」——切开发布节奏、不切开数据。含五条实读代码核过的硬约束（其中两条推翻了内部备忘录的既有说法）、三层架构与共享数据面的四条契约、执行面 T1/T2/T3 分级，以及逐屏对账后的能力缺口分布：59% 现有能力直接覆盖，剩下 41% 恰好集中在演示效果最强的几屏，另有三处「照做会破坏系统」必须改设计（深色技术长页）。' },
      { slug: 'akke-memory-system', title: 'Akke 团队记忆系统 · 现状与查漏', desc: 'AI 会话天生失忆，6 个人怎么共用一个脑子：三层渐进索引把 859 条经验、3.2MB 知识压成每次会话只花 5.7KB 的入口（约 560:1），一条记忆从写下到队友自动读到的 7 步生命周期，以及 pre-commit 体量硬闸、死链体检、未提交提醒、worktree 并发隔离四道自动闸——后半是逐条跑命令核实的 9 个缺口诊断与补洞清单（深色技术长页，数据实测 2026-07-29）。' },
      { slug: 'oltp-olap-lakehouse', title: 'OLTP · OLAP · Lakehouse — 我们要不要上 Snowflake / Databricks', desc: '起于一次真实告警：生产库 CPU 82%、当天 4 份资料写库超时，升配止血之后该问的是「是不是该上一套正经数据平台」。先把三个总被混着说的名词讲清楚——OLTP 一次改一行（行存＋事务）、OLAP 一次读一亿行（列存＋向量化，含同一问题两种存储读取量的量级对照）、Lakehouse 为什么是被数仓「贵且只收结构化」和数据湖「没事务变沼泽」两代坑逼出来的（Delta / Iceberg 那层元数据事务日志到底加了什么）。再用实测家底做判断：整库 1,356 MB、132 张表、最大表 44 万行，而两家的拐点在 TB~PB——差三个数量级，且它们不能替代业务库、上了是加一套不是换一套；附 Snowflake credit 与 Databricks DBU 双重计费的月度成本对照。真凶另有其人：一张 442 MB 的模型调用审计表吃掉三分之一的库、10 万行超 30 天且没有保留策略，邻居表全都有窗口只有它在裸奔。结论是不买但抄五个思想（存算分离冷热分层、bronze/silver/gold 分层、治理打标补上「能不能发给客户」的字段缺口、Time Travel 版本快照，以及一条反向结论——招牌向量检索恰恰最不用抄），并写死四条触发线与五级升级阶梯（深色技术长页，数据实测 2026-08-06）。' },
      { slug: 'douyin-replica-tech', title: '爆款视频复刻产线 · 换人换声技术方案', desc: '把一条跑出成绩的爆款换成自家主播的脸和声音重新出片：MiniMax 零样本音色克隆、sync-lipsync 扩散式嘴部重绘、真人底片表情保真、OpenCV 逐帧字幕擦除与「先剪后驱动」成本工法——单条 $1.8，附完整选型对比与团队 skill 用法（深色技术长页，2026-07）。' },
      { slug: 'video-pipeline-2026-08', title: '短视频复刻产线 · 现状全景', desc: '一年前只有「换脸换声」一条路，现在是四条：真人讲解、纯场景画外音、实景走位讲解，外加逐镜点菜（试验中，代码未合）。这一页讲整条产线当前长什么样——判型由解构端产出、复刻端只消费，九段主链路每一棒都落库交接，付费步骤的编号由服务端固定下发，断线重连不会产生第二笔费用；生产端只提交不可变阶段包、审核端只审当前包，两者都没有发布权，最后由店长完整播放一遍才解锁下载。成本拆成两本账：生成费有每单硬闸，而产线自己那个 AI 会话烧的「大脑费」曾经只记不拦——记的是遗照不是闸门，会话级美元硬闸已提交待合。另含 24 道确定性闸门按管什么分类（每道都对应一次真实翻车）、判死看心跳不看进度的原因、卡住／失败／判死三件事为什么必须分开，以及 13 类机器可读失败原因里只有一类值得自动重试（深色技术长页，口径 2026-08-10）。' },
      { slug: 'hdyx-progress-2026-07', title: '有大有小 · 获客四线实时进展与卡点', desc: '视频制作 → 广告投放 → 加微承接 → 企微 AI 自动化：四条业务线各自走到哪、哪段全绿、哪里在等谁——含 M1 图源对照实验、三生态投放评估排序、加微链路事实澄清、企微 AI 灰度真发数据与按「球在谁手里」排的卡点总表（甲方分享会深色长页，数据截至 2026-07-15）。' },
      { slug: 'hdyx-tech-blueprint-2026-07', title: '有大有小 · 获客四线技术方案蓝图', desc: '与进展页配套的工程口径：「想在云端、手在终端」三层总架构，视频产线 M1 图源对照实验设计与八工位流水线，三生态投放承接链路形态与归因埋点，抖音→企微 hard-gate 收口与短信桥，企微方案 A/B 对比与云电脑 GUI + VL 读屏数据链——含全栈选型表与两条贯穿性工程原则。' },
      { slug: 'langchain-langgraph-langfuse', title: 'LangChain · LangGraph · Langfuse', desc: '三个都带 lang 的名字怎么分？前两个帮 AI 干活、第三个帮你看清 AI——结合 Akke 讲我们用了哪个、没用哪个、为什么。' },
      { slug: 'claude-tag',       title: 'Claude Tag · Slack 队友', desc: '把 Claude 拉进 Slack 当常驻队友：@一下就接活、会主动盯事（Opus 4.8）。' },
      { slug: 'hermes-agent',     title: 'Hermes Agent 框架',  desc: '团队自研 Agent 系统讲解。' },
      { slug: 'loop-engineering', title: 'Loop Engineering',    desc: 'Agent 循环工程方法论。' },
      { slug: 'fde',              title: 'FDE 前线部署工程师',  desc: 'Anthropic 的 FDE 角色解读。' },
      { slug: 'ai-harness',       title: 'AI Harness 精要',     desc: 'Agent harness 框架要点。' },
      { slug: 'fable5',           title: 'Claude Fable 5',      desc: '新模型能力介绍。' },
      { slug: 'claude-model-picker', title: 'Sonnet 5 / Opus 4.8 / Fable 5 怎么选', desc: '在 Claude Code 里写 Akke 代码时，什么活儿配哪一档模型——一页决策图，含定价、速度与 Akke 任务对照。' },
      { slug: 'claude-code-model-effort', title: 'Opus 5 / Fable 5 / Effort 怎么拧', desc: '模型、effort、fast 是三个独立旋钮，不是同一条「更强」的滑杆：换模型治「想不明白」、调 effort 治「想得不够深或想太多」、开 fast 只治「等得烦」。判定线不按重要程度、按不确定性的位置——说得清的活留给 Opus 5，说不清、要无人值守跑很久、要一次产出可交付文档的才切 Fable 5；安全向分析是红线（Fable 会拒答且 CC 里没有自动兜底）。含两台机器的规格对照与真实账单倍数（Fable 约 3～4×，不止牌面上的 2 倍）、low→max 七档 effort 阶梯与各自的典型活儿、九个会真花钱的坑（删掉「再检查一遍」、子 agent 要反向调），以及 Akke cron 排查 / 支付鉴权审计 / 视频流水线选型 / 财务周报等九个场景可直接照抄的档位（深色技术长页，Claude Code 2.1.220 实测口径）。' },
      { slug: 'tencent-hy3', title: '腾讯 Hy3：新旗舰拆解，我们要换吗', desc: '腾讯混元刚发布的开源权重旗舰：架构、定价、优缺点，和生产在用的 Qwen3-2507 / DeepSeek V4 Flash 摆在一起算账——结论是两个槽都不换。' },
      { slug: 'scheduling-terms', title: 'Cron / Routine / Schedule 辨析', desc: '定时·事件·常驻：一堆易混的自动化机制怎么选。' },
      { slug: 'loop-goal-orchestration', title: '用 /loop × goal 编排任务', desc: '/loop 是让 prompt「反复醒来」的骨架，goal 是写进 prompt 的「何时停」判据——两者配起来让 Claude 自己盯一件事干到达标。结合 Akke 讲轮询/指标收敛/backlog 磨盘三种落法与四道护栏。' },
      { slug: 'usage-limit-auto-resume', title: '撞了 5 小时限额，让长任务自己等到点续跑', desc: '长任务跑到一半撞了 Claude Code 的 5 小时限额、人又不在？先破除一个普遍误解——撞限额不是进程被杀，是会话暂停等输入、上下文全在，于是「派个哨兵守着终端、到点自动续」就成立。梳理官方现状（至今未内置、一串 open issue）与社区四派方案，深讲首选工具 unsnooze 的双通道检测 / epoch 轮询扛睡眠 / tmux resume 机制与上手姿势，附 prompt cache 成本坑，以及 Akke/Workflow 长线程的实际落法（深色技术长页，2026-07）。' },
      { slug: 'dynamic-workflows', title: '动态工作流 · 让 Claude 调度一群 Agent', desc: '让 Claude 写一段脚本去编排几十上百个子代理并行干活、脚本本身不烧 token 的用法：两种触发（关键词 ultracode / 会话默认 /effort ultracode）、临时与默认两种限流（use at most N agents / config 四档规模）、以及全库审计·大规模迁移·交叉验证·循环到达标四类该用场景与「一下午能干完就别叫蜂群」的别用边界（深色技术长页）。' },
      { slug: 'autoreply-supabase-relay', title: '自动回复为什么"绕"数据库', desc: '客户回复→数据库中转→生成→发送：为什么不一步到位。' },
      { slug: 'wecom-vl-reading', title: '企微自动回复的"眼睛" · VL 读屏', desc: '系统不是"读"聊天记录，是"看"截图——为什么四条正经读法全是死路、VL 会怎么看错、diff 矫正为什么一度失灵（test-22 修法）、四条护栏原则与给运营的"卡顿"三步判断（深色技术长页）。' },
      { slug: 'enterprise-brain-distillation', title: '有大有小 · 企业 AI 大脑', desc: '从 41 页企业手册与金牌销售对话，蒸馏出一颗"管得住嘴"的 AI 大脑——搭建、训练与护栏全景（甲方分享版深色 Deck）。' },
      { slug: 'deconstruct-pipeline', title: '解构复刻产线 · 动态图解', desc: '一条高播放竞品视频链接进来，一条合规 AI 成片出去——六工位产线总控动画（解构→重写→生图→生视频→配音→成片），三个人工卡口、重 roll 熔断、单条成本账与 M1-M3 落地路线。' },
      { slug: 'ai-golden-salesperson', title: '让 AI 像金牌销售一样说话', desc: '"更有精气神"拆成 5 个可执行维度——人设密度、去客服腔、消息节奏、真实语料喂养、销售推进框架，每一维配开源研究依据，对照 Akke 企微 AI 代回的真实做法。' },
      { slug: 'claude-code-codex', title: 'Claude Code × Codex 双开一个项目', desc: '同一个项目同时用两家的编码 Agent 可行吗？可行，但要先迈两道坎——指令文件互不相认（CLAUDE.md vs AGENTS.md）、两个 agent 不能同时写一个工作区。以 Akke 仓库现状为例给出四种协作模式与落地清单。' },
      { slug: 'cloud-pc-fleet', title: '多台云电脑怎么统一管 · 机队版本治理方案', desc: '企业版与个人版云电脑混编共存，怎么避免「更新完各台跑的不是同一份程序」。先盘家底（八台在册，含一台没人登记过的企业版），再定位根因——中央能查到版本号的机器是零台，版本这个事实从没离开过机器本身。方案四层：版本自报（改动最小、必须排第一，它是后面每一步的验收尺子）、把三份手维护名单收敛成一份 manifest（更新工具现在只覆盖 25 个文件里的 11 个，还漏了它自己）、企业版推个人版拉但共用同一条哈希验收口径、漂移告警与按机器 pin 的灰度回滚。含分阶段路线、成本账（为什么不该为统一而全升企业版）与批量扩机红线（深色技术长页，2026-07）。' },
      { slug: 'claude-code-cloud-pc', title: '用 Claude Code 遥控一台云电脑', desc: '企业微信和抖音都没有可用的对外发消息接口，于是「发消息」这件事只能落到一台登录着真账号的 Windows 云电脑上用 GUI 去点。这篇讲清楚怎么把这台机器交给终端里的 Claude：控制链七层全景、阿里无影企业版 RunCommand 的官方口径（16KB 命令体 / 300 秒超时 / 24KB 输出截断）、会话 0 与会话 1 的硬边界与 InteractiveToken 跳板、把 88KB 脚本塞进 16KB 管子的分块校验法、SNI 阻断的判据与中转修法、十四条坑位速查——以及一次把自己三条「已知死路」实测推翻的记录（深色技术长页，2026-07）。' },
      { slug: 'aliyun-cli-runcommand', title: '阿里云 CLI × RunCommand 实战教程', desc: '不连远程桌面、不用人坐在机器前，怎么从 Mac 终端把脚本送进四台 Windows 云电脑再把结果读回来。手把手过一遍 aliyun CLI：RAM 凭据与 profile 的爆炸半径、「命令集 × API 版本 × 区域」三坐标（为什么 describe-users 会「不存在」、为什么 region 要写两遍且空列表比报错更坑）、run-command 与 describe-invocations 的两步走全流程（附本页写作时实跑的真实回执）、会话 0 与会话 1 的硬边界与 InteractiveToken 跳板、回执三条信道的可信度定论（其中两条曾被我们自己误判成平台 bug）、88KB 脚本分块灌入与 MD5 校验、开通机器与授权账号的完整序列，以及 18 条按症状查的坑位速查表——全部来自 Akke 生产机队（深色技术长页，2026-07）。' },
      { slug: 'cloudpc-wecom-evolution', title: '一台没人坐的电脑在替销售回消息', desc: '两台成都的 Windows 云电脑登录着真实企业微信账号，自己读屏、自己生成话术、自己打字发送，运维它们的是杭州一台 Mac 终端里的 Claude Code。上半篇讲遥控原理——为什么官方接口三堵墙全走不通、会话 0 与会话 1 的硬边界、InteractiveToken 跳板、SNI 阻断与 Fly 中转；下半篇讲企微 AI 代回七个月的迭代史——从 kf API 撞 ICP 备案墙，到 VL 读屏、50 轮记忆、7 天召回、到店漏斗、脚本自愈与看门狗、五层配置压成一个版本号，含当前线上版本快照与四条仍未解决的缺口（深色技术长页，2026-07）。' },
      { slug: 'i2v-quality-triage', title: '图生视频效果不好，怪谁？', desc: 'AI 生图 → 可灵/Seedance 图生视频的成片效果不理想，问题在提示词、图片还是模型？一次真实产线的三层归因排查：图片 > 模型参数 > 提示词，含嘴形破案实录（V3 无音频输入回退 v2.5）与可迁移的排查方法。' },
      { slug: 'omnihuman-short-drama', title: '抖音短剧 OmniHuman 生成技术方案', desc: 'AI 短剧口播视频怎么生成：从 v1 的 Kling+lipsync 多步级联，到 v2 的 OmniHuman 一步音频驱动（audio→video+face+body）。含 Seedream 真人锚点、反打单人架构（多人同框会两张脸同时张嘴）、fal/RunPod 调用地图澄清，以及逐环节的 RunPod 自建替代评估——空镜可换 Sulphur Pod 省 5×，口播真人的脸/口型/音色永远留 fal，单条 $14.82（深色技术长页，2026-07）。' },
    ],
  },
  {
    label: 'BUSINESS · MARKET INSIGHT',
    title: '商业 · 市场洞察',
    items: [
      { slug: 'enterprise-ai-business-models', title: '卖结果，不卖工位 · 2026 企服 AI 商业模式全景', desc: '2026 年跑通的企服 AI 没有一家靠「按人头订阅」赢——它们把收费口径从软件预算挪到了人力预算。拆解三种正在跑通的模式（按结果计费 / Service-as-Software / AI Rollup）、海外公司档案与真实增长基准（80+ 家过 $100M ARR、人均 ARR 差一个数量级、纯 SaaS 倍数跌 80%），中国现场的结构性差异（用友金蝶市值双双 -80%、北森 AI 新签已是传统 4 倍且要建 300 人 FDE 队伍），以及一套判断切入点的五条硬标准 + 可交互自查打分器与四个候选象限。全文数据标注 A/B/C 证据档位（深色技术长页，2026-07）。' },
      { slug: 'ai-growth-landscape', title: '谁在生成内容，谁在挖线索 · AI 获客赛道地图', desc: 'AI 营销视频与社媒获客两个赛道、国内外 60+ 家公司的全景扫描：Sora 为何关停、Icon 为何从 AI 退回真人拍摄、13 倍估值差从哪来，以及 Meta 与抖音的私信 API 如何决定了「谁能主动跟陌生人说第一句话」。每条结论标注信源与证据档位，查不到就写查不到（深色长页，2026-07）。' },
      { slug: 'tencent-ima-lke-landscape', title: '知识库正在白菜化 · 腾讯 IMA / LKE 拆解与企业大脑的差距', desc: '腾讯 IMA 全功能免费、LKE 已更名「智能体开发平台 ADP」且 188 元/月就送 100GB 知识库——「我们有个知识库」不再是卖点。逐条拆开腾讯这两个产品（含官方四档定价与配额），把国内竞品分成底座平台 / 办公入口 / 私域 SCRM 三层、海外分成企业搜索 / 客服 Agent / AI SDR 三个赛道，再对到我们企业大脑的家底上：五个料架 671 份资料，市面产品只覆盖得了其中两类，「该怎么说话」「遇到这种情况怎么办」和真实执行层是买不到的。含五条可直接落地的借鉴项（Glean 权限感知检索补「能不能发给客户」的字段缺口、Decagon AOP 治决策规则 35/40 天花板、OCR 救回 21 份扫描件）、两条明确不建议做的，以及整页数字的 A/B/C/D 证据分档。最扎心的一组数字来自微软自己：1.5 亿 Copilot 座席、约三成日活、6% 的试点走到规模化（深色技术长页，2026-08）。' },
    ],
  },
  {
    label: 'TOOLS · GETTING STARTED',
    title: '工具入门',
    items: [
      { slug: 'claude-code-skills', title: 'Claude Code 技能体系 · 团队上手手册', desc: '把反复要说的一长段话存成一个 /命令：技能怎么存怎么触发、常用技能逐个拆解（grill-me / brainstorming / deep-research / code-review / simplify）、开源生态该从哪装、worktree 与子智能体怎么并行不打架。含从分享会笔记到 2026-07 的现状更正——「think / megathink 三档思考」已被官方文档推翻，以及我们这台机器上实测在跑的 23 个插件与 32 个自建技能。' },
      { slug: 'new-mac-setup', title: '新 Mac 开荒手册 · 从拆箱到 Claude Code', desc: '新同学第一天的完整动线：Shadowrocket 隧道 → curl 出口验证 → Xcode CLT / Homebrew / Node 依赖链 → Claude Code 安装登录，每步可复制执行，附终检清单与真实踩坑速查（深色技术长页 + 动画终端演示）。' },
      { slug: 'mac-for-ai', title: '为什么 Mac 更适合做 AI', desc: 'AI 时代的日常 = 终端里的 Agent + 想跑就跑的本地模型：macOS 的 Unix 底子和 Apple Silicon 统一内存各接住一半，也诚实讲清 Mac 什么时候不是答案。' },
      { slug: 'vercel-supabase', title: 'Vercel × Supabase', desc: '我们几乎每个产品都在用的两件套——前端托管 + 后端数据库：来历、能力全景，以及我们用了哪些、没用哪些、为什么。' },
      { slug: 'database-storage-map', title: '数据库与存储全景 · 把一堆名字放回各自的货架', desc: 'MongoDB、Supabase、Neon、R2、S3、阿里云放在一起比，几乎总是错位比较——它们根本不在同一层。先用三刀分层（存的东西长什么样 / 谁替你运维 / 在谁的机房），再逐层展开：八种数据形状各自的擅长与不擅长、交易库与分析库这条被忽略的正交线、从自己装到 BaaS 的四档运维形态、四大供应商阵营与「国外托管库在国内打不开」这条排在所有技术对比之前的硬约束。重头在对象存储的账单真相——存储单价只差 4 倍，出网单价差到无穷，同一份用量（1TB 存 + 10TB/月 下载）最贵的 S3 约 $914、最便宜的 R2 约 $15，61 倍差额几乎全部来自出网流量。另含 Postgres 一族四种买法的横向对账、MongoDB 到底什么时候才是对的、一棵可照走的决策树、十条最贵的误解，以及我们自己「一个 Postgres + 一个 R2」的真实选型与代价。全文价格取自厂商官方定价页（深色技术长页，2026-08-05 核）。' },
      { slug: 'flyio', title: 'Fly.io 是什么', desc: '我们的「重活」都跑在 Fly.io 上：它到底是什么、和自己租一台 VPS 有什么本质区别、Render / Railway / Heroku / Cloud Run 这些竞品各自站哪，以及我们为什么选它、踩过哪些坑。' },
      { slug: 'falai-runpod', title: 'fal.ai × RunPod', desc: 'AI 生成的两种买法：fal.ai 按「结果」收费（一千多个模型的统一点菜口），RunPod 按「GPU 时间」收费（把带显卡的厨房租给你）。什么时候点菜、什么时候开火，以及我们两家都在用、分别用来干什么、踩过哪些坑。' },
      { slug: 'nextjs-nodejs-python', title: 'Next.js · Node.js · Python', desc: 'Akke 是用哪几种语言搭起来的？用「一条请求的旅程」串起 TypeScript / Next.js / Node.js / Python，全员不写代码也能看懂同事每天在改什么。' },
      { slug: 'sql-python-intro', title: 'SQL 与 Python 入门', desc: '数据分析基础速成。' },
      { slug: 'pr-git-worktree', title: 'PR 与 Git Worktree', desc: '合并前的检查点，和多会话并行开发的隔离工具——结合 Akke 最近 3 天的真实提交举例。' },
      { slug: 'git-commit-standards', title: '团队 Git 提交规范 · 从一周 193 次真实提交讲起', desc: '每次 git commit 之前该过哪几道题：把一条提交拆成 type / scope / 标题 / 正文 / 引用五块逐块讲透（含打字机对照演示），七个 type 怎么选、scope 为什么要前后一致、正文的三段式（什么问题逼你动手 / 为什么否决了另一条路 / 留了什么坑）。后半是代码进 main 的四条通道与两个组织的纪律差异（Akke 分层 PR 规则 + 高危路径双闸 vs Workflow 钩子直推 vs 新仓零 CI），以及三个逐条跑命令核出来的真问题——其中最要紧的一条是某仓 PR 自动评审因未装 GitHub App 一直 401、而 PR 平均 17 秒就被合掉，等于白开。全部举例取自同一人 2026-07-27→08-03 的 193 次真实提交（深色技术长页，Pantone 色板）。' },
    ],
  },
  {
    label: 'NETWORKING · 科学上网',
    title: '网络 · 科学上网',
    items: [
      { slug: 'self-hosted-vpn', title: '自建科学上网节点', desc: '协议·选 IP·客户端·排障：自建节点的完整脱敏科普。' },
      { slug: 'team-vpn-architecture', title: '团队跨境 VPN · 双层中转架构全景', desc: '给协作团队的架构脱敏速览：搬瓦工入口 VPS 终结加密隧道、透明中转到 10 个独占美国 ISP 出口，每人一 UUID 死绑一出口；协议被防火墙逼从 TCP 一路走到 Hysteria2 UDP（含 TCP 掐数据 vs UDP 放行动图与协议进化时间轴）、Clash / Shadowrocket 终端接入切换、双机热备与 15 分钟深度巡检——不含任何 IP 与凭据（深色技术长页，2026-07）。' },
      { slug: 'cloud-device-network', title: '云电脑 / 云手机连不上？先查代理', desc: '云电脑、云手机时不时打不开、连不上网络，九成是本机代理软件的 fake-IP 模式挡住了 UDP——一眼认出症状 + 三步修复。' },
    ],
  },
  {
    label: 'MENTAL MODELS · 思维模型',
    title: '思维 · 心智模型',
    items: [
      { slug: 'munger-inversion', title: '芒格逆向思维', desc: '「反过来想」——先问怎样会失败，再挨个避开；结合 Akke 举例。' },
      { slug: 'musk-five-step-algorithm', title: '马斯克工程五步法', desc: '质疑需求→删除→简化→加速→自动化：工程思维的操作系统，结合 Akke 举例。' },
      { slug: 'wbs-task-breakdown', title: 'WBS 工作分解结构', desc: '把大目标拆到「一人一天可验收」为止——用 Akke 近 3 天的真实工作演示任务拆解，结合 Akke 举例。' },
      { slug: 'yagni', title: 'YAGNI 你不会需要它', desc: '「以后可能用得上」是工程里最贵的一句话——为想象中的未来写的代码几乎都白写。什么该现在做、什么该等真需要时再做，以及 YAGNI 不适用的边界，结合团队真实决策。' },
    ],
  },
  {
    label: 'MATH & SCIENCE · 数学科学',
    title: '数学 · 科学前沿',
    items: [
      { slug: 'fields-medal-2026', title: '王虹 · 邓煜 与 2026 菲尔兹奖', desc: '数学最高奖 90 年来首次颁给中国数学家，一届来了两位：王虹终结 108 年的三维挂谷猜想（一根针转一圈最少要扫过多大地方），邓煜打通希尔伯特第六问题的核心链条（从分子碰撞严格推出流体方程）。零公式全比喻，含转针动画、三尺度粒子模拟、3D 奖牌与 Pantone 色卡版深色设计（深色技术长页，2026-07）。' },
    ],
  },
];

// 校验 + 统计 ──────────────────────────────────────────────────
const missing = [];
let rendered = 0;

const categoryBlocks = CATEGORIES.map(cat => {
  const cards = cat.items
    .filter(it => {
      const exists = fs.existsSync(path.join(learnDir, `${it.slug}.html`));
      if (!exists) missing.push(`${it.slug}.html`);
      return exists;
    })
    .map(it => {
      rendered++;
      return `        <a class="card" href="/learn/${it.slug}">
          <span class="card-tag">${it.slug}</span>
          <h3>${it.title}</h3>
          <p>${it.desc}</p>
        </a>`;
    })
    .join('\n');

  if (!cards) return ''; // 整个分类的卡片都缺失则不渲染该区块

  return `    <section class="cat">
      <h2 class="cat-label">${cat.label}<span class="cat-zh">${cat.title}</span></h2>
      <div class="grid">
${cards}
      </div>
    </section>`;
}).filter(Boolean).join('\n\n');

if (missing.length) {
  console.warn(`[build-learn] WARN: ${missing.length} entr${missing.length === 1 ? 'y' : 'ies'} missing, skipped: ${missing.join(', ')}`);
}

// HTML 模板 ────────────────────────────────────────────────────
const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>知识分享 · upio.ai</title>
<meta name="description" content="upio.ai 团队内部的 AI / 大模型 / 工程化讲解合集。">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;500;700;800&display=swap">
<style>
:root {
  --bg:           #0a0d14;
  --bg-elevated:  #131720;
  --bg-deep:      #1a1f2b;
  --border:        #232936;
  --border-strong: #2f3646;
  --text:       #e8eaef;
  --text-dim:   #9ba3b4;
  --text-muted: #6b7384;
  --accent:        #8b5cf6;
  --accent-soft:   rgba(139, 92, 246, 0.15);
  --accent-2:      #60a5fa;
  --accent-3:      #34d399;
  --font-display: 'Instrument Serif', 'Source Han Serif SC', Georgia, serif;
  --font-ui:      'Inter Tight', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
  --font-mono:    'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; padding: 0;
  background: var(--bg); color: var(--text);
  font-family: var(--font-ui);
  font-size: 15.5px; line-height: 1.7;
  font-feature-settings: 'cv11', 'ss01', 'tnum';
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}
.container { max-width: 1080px; margin: 0 auto; padding: 56px 24px 120px; }

/* Breadcrumb */
.breadcrumb { font-size: 12.5px; color: var(--text-muted); margin-bottom: 20px; font-family: var(--font-mono); }
.breadcrumb a { color: var(--text-dim); text-decoration: none; transition: color .2s; }
.breadcrumb a:hover { color: var(--accent); }
.breadcrumb span { margin: 0 8px; color: var(--border-strong); }

/* Hero */
header.hero { padding: 8px 0 36px; border-bottom: 1px solid var(--border); margin-bottom: 8px; }
.tag {
  display: inline-block; padding: 4px 12px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent);
  font-family: var(--font-mono); font-size: 11px; font-weight: 600;
  letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 18px;
}
h1 {
  font-family: var(--font-display); font-style: italic;
  font-size: 64px; font-weight: 400;
  margin: 0 0 18px; letter-spacing: -0.02em; line-height: 1.05;
  background: linear-gradient(135deg, #f4f5f8 30%, var(--accent) 75%, var(--accent-2) 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.subtitle { font-size: 17px; color: var(--text-dim); margin: 0; max-width: 760px; line-height: 1.6; }

/* Category section */
.cat { margin-top: 48px; }
.cat-label {
  font-family: var(--font-mono); font-size: 11px;
  color: var(--text-muted); letter-spacing: 0.14em;
  text-transform: uppercase; margin: 0 0 20px; font-weight: 700;
  display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
}
.cat-label::before {
  content: ''; width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent); box-shadow: 0 0 8px var(--accent);
  flex-shrink: 0; align-self: center;
}
.cat-zh {
  font-family: var(--font-display); font-style: italic;
  font-size: 24px; color: var(--text); letter-spacing: 0;
  text-transform: none; font-weight: 400;
}

/* Card grid */
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.card {
  position: relative; display: flex; flex-direction: column;
  background: var(--bg-elevated); border: 1px solid var(--border);
  border-radius: 14px; padding: 20px 20px 18px;
  text-decoration: none; color: inherit;
  transition: border-color .2s ease, transform .15s ease, box-shadow .2s ease;
}
.card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0;
  height: 3px; border-radius: 14px 14px 0 0;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  opacity: 0; transition: opacity .2s ease;
}
.card:hover {
  border-color: var(--accent); transform: translateY(-2px);
  box-shadow: 0 10px 26px rgba(0,0,0,0.32);
}
.card:hover::before { opacity: 1; }
.card-tag {
  font-family: var(--font-mono); font-size: 10.5px;
  color: var(--text-muted); letter-spacing: 0.04em;
  margin-bottom: 12px; display: inline-block;
}
.card:hover .card-tag { color: var(--accent-2); }
.card h3 {
  font-family: var(--font-ui); margin: 0 0 8px;
  font-size: 17px; font-weight: 600; color: var(--text);
  letter-spacing: -0.01em; line-height: 1.3;
}
.card p { margin: 0; font-size: 13px; color: var(--text-dim); line-height: 1.55; }

/* Footer */
footer {
  text-align: center; margin-top: 64px; padding-top: 28px;
  border-top: 1px solid var(--border);
  font-family: var(--font-mono); font-size: 11.5px; color: var(--text-muted);
  letter-spacing: 0.06em;
}

@media (max-width: 860px) { .grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 600px) {
  .container { padding: 40px 20px 80px; }
  h1 { font-size: 44px; }
  .grid { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; }
}
</style>
</head>
<body>
  <div class="container">

    <div class="breadcrumb">
      <a href="/">upio.ai</a><span>/</span>知识分享
    </div>

    <header class="hero">
      <span class="tag">KNOWLEDGE</span>
      <h1>知识分享</h1>
      <p class="subtitle">团队内部的 AI / 大模型 / 工程化讲解合集。</p>
    </header>

${categoryBlocks}

    <footer>upio.ai · 知识分享中心 · 共 ${rendered} 篇</footer>
  </div>
</body>
</html>`;

fs.writeFileSync(path.join(learnDir, 'index.html'), html);
console.log(`Built learn: ${CATEGORIES.length} categories, ${rendered} cards rendered, ${missing.length} missing`);
