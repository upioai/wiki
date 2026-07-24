# /learn/fields-medal-2026 — 王虹 · 邓煜 2026 菲尔兹奖讲解页 设计 spec

日期：2026-07-24 ｜ 模式：/goal 自主执行（brainstorming 以自主对齐替代逐题问答）

## 目标与受众

- 讲清王虹（三维挂谷猜想）与邓煜（希尔伯特第六问题）2026-07-23 费城 ICM 获菲尔兹奖的数学成果
- 受众：团队全员（数学小白）。零公式讲解，全部走「故事 + 比喻 + 动画」；公式只作装饰性符号出现
- 产出：upio.ai/learn 讲解页，slug `fields-medal-2026`

## 落位与分类

- 文件：`public/learn/fields-medal-2026.html`（自包含单文件，仅 Google Fonts 外链）
- `scripts/build-learn.js` CATEGORIES **新增分类**（站规：非 AI 内容禁止强塞现有 AI 分类，先例：思维·心智模型）：
  - `label: 'MATH & SCIENCE · 数学科学'`，`title: '数学 · 科学前沿'`，追加在数组末尾

## 视觉方案

- 基底走 dark-tech-page skill 骨架（reference.html：网格辉光背景/rail 导航/进度条/reveal 含三坑修复/pipe 流光/spec 表/宋体大标题 + IBM Plex Mono）
- **Pantone 高级色卡**（用户显式要求，覆盖 skill 的"别改配色"约束；底色仍保站点 #06080c 保证站内一致）：
  | 语义 | Pantone | 基准 hex | 暗底亮阶（正文/线条用） |
  |---|---|---|---|
  | 主信号 · 数学之光 | PANTONE 333 C | #3CDBC0 | #3fe0c5（≈站点 teal，天然对齐） |
  | 王虹篇 · 几何 | PANTONE 18-3838 Ultra Violet | #5F4B8B | #b48cff |
  | 邓煜篇 · 物理 | PANTONE 19-4052 Classic Blue | #0F4C81 | #6fa8ff |
  | 人物 · 热点 | PANTONE 18-1750 Viva Magenta | #BB2649 | #ff7d97 |
  | 菲尔兹奖 · 荣誉金 | PANTONE 7555 C | #D29F13 | #e3b341 |
  - 页内设「PANTONE 色卡带」组件：五张色片（名称/编号/hex），既满足要求又当设计元素
  - 亮阶五色作图表 categorical 集，须过 dataviz validate_palette.js --mode dark
- 图标遵 skill 新规：禁 emoji，一律 SVG 线条图标

## 章节结构（rail）

1. `hero` — 90 年来第一次：两位中国数学家同届摘得菲尔兹奖（金色 gradient 重点词 + chips + 背景漂浮暗淡数学符号）
2. `medal` — 菲尔兹奖是什么：四年一届/40 岁红线/每届 2–4 人/无数学诺奖的由来；**CSS 3D 奖牌旋转** + statband count-up
3. `laureates` — 本届四人：王虹/邓煜大卡 + Pardon/Tsimerman 简卡 + 打破的纪录清单
4. `wang` — 王虹 · 一根针的百年难题：
   - 转针问题（SVG 动画：针在圆内转 vs 在三尖瓣线内转，面积对比）
   - Besicovitch 反转：面积可任意小（三角平移重叠动画 + 面积数字递减）
   - 但「维度」偷不走：维度谱条（点0/线1/科赫1.26/挂谷集须=3）
   - 时间轴 pipe：1917 挂谷 → 1919 Besicovitch → 1971 二维解决 → 1995 Wolff 5/2 → 2025-02 王虹&Zahl 三维全解
   - 为什么重要：调和分析塔（塔基图）+ 名家评价 quote
   - 人物卡（履历时间线）
5. `deng` — 邓煜 · 从原子到风暴的桥：
   - 1900 希尔伯特 23 问故事 → 第六问
   - **三层世界阶梯**（canvas 粒子对撞 → 分布曲线 → 流体流线，三栏动画）
   - Lanford 1975「只证明了电影的前 0.1 秒」→ 卡住 50 年
   - 2024–2025 Deng–Hani–Ma 长时间推导 + 打通流体方程；时间轴 pipe
   - 人物卡（IMO 满分金牌等）
6. `echo` — 共同点与回声：北大同门/纪录清单/对做 AI 的我们意味着什么（傅里叶分析与信号处理、统计力学与扩散模型的隐秘联系，克制表述）
7. `cheat` — 一分钟转述版：三句话卡片 + 两人成果对照 spec 表 + Pantone 色卡带 + 信源列表（A/B 证据档）

## 事实纪律

- 两个后台研究 agent 产出经溯源的事实清单（IMU/ICM 官方、arXiv、Quanta 优先）；正文所有客观陈述以 agent 核实结果为准，未核实处按证据分级 hedge 或删除
- 页脚标注数据截至 2026-07-24 与主要信源

## 动效与性能

- 全部纯 CSS/SVG/canvas，零外部 JS 库；canvas 粒子 ≤150 个、单页 ≤2 个 rAF 循环
- `prefers-reduced-motion` 全量兜底（关动画）
- skill 三必修坑全带：reveal 滚动兜底 + hero relative + 锚点直达 revealPast()

## 验证与发布

1. `node scripts/build-learn.js` 本地重建 index 验 manifest
2. `python3 -m http.server` + Claude in Chrome：computer scroll 到底断言 `.rv:not(.in)==0`、进度条 100%、`hOverflow=false`、console 0 报错；390px 移动端复查
3. 脱敏 grep（本页纯公开信息，仍走流程）
4. commit `feat(learn): ...` → rebase origin/main → push 到 main（沿用本仓 36 篇先例的直推流程）→ Vercel 部署核对（`vercel ls` 时间戳晚于 push，落后则 `npx vercel --prod --yes` 兜底）→ curl 线上验证 title/正文/learn 卡片三处命中
