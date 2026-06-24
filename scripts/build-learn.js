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
    ],
  },
  {
    label: 'AGENT · AI ENGINEERING',
    title: 'Agent · AI 工程',
    items: [
      { slug: 'claude-tag',       title: 'Claude Tag · Slack 队友', desc: '把 Claude 拉进 Slack 当常驻队友：@一下就接活、会主动盯事（Opus 4.8）。' },
      { slug: 'hermes-agent',     title: 'Hermes Agent 框架',  desc: '团队自研 Agent 系统讲解。' },
      { slug: 'loop-engineering', title: 'Loop Engineering',    desc: 'Agent 循环工程方法论。' },
      { slug: 'fde',              title: 'FDE 前线部署工程师',  desc: 'Anthropic 的 FDE 角色解读。' },
      { slug: 'ai-harness',       title: 'AI Harness 精要',     desc: 'Agent harness 框架要点。' },
      { slug: 'fable5',           title: 'Claude Fable 5',      desc: '新模型能力介绍。' },
      { slug: 'scheduling-terms', title: 'Cron / Routine / Schedule 辨析', desc: '定时·事件·常驻：一堆易混的自动化机制怎么选。' },
      { slug: 'autoreply-supabase-relay', title: '自动回复为什么"绕"数据库', desc: '客户回复→数据库中转→生成→发送：为什么不一步到位。' },
    ],
  },
  {
    label: 'TOOLS · GETTING STARTED',
    title: '工具入门',
    items: [
      { slug: 'sql-python-intro', title: 'SQL 与 Python 入门', desc: '数据分析基础速成。' },
    ],
  },
  {
    label: 'NETWORKING · 科学上网',
    title: '网络 · 科学上网',
    items: [
      { slug: 'self-hosted-vpn', title: '自建科学上网节点', desc: '协议·选 IP·客户端·排障：自建节点的完整脱敏科普。' },
    ],
  },
  {
    label: 'MENTAL MODELS · 思维模型',
    title: '思维 · 心智模型',
    items: [
      { slug: 'munger-inversion', title: '芒格逆向思维', desc: '「反过来想」——先问怎样会失败，再挨个避开；结合 Akke 举例。' },
      { slug: 'musk-five-step-algorithm', title: '马斯克工程五步法', desc: '质疑需求→删除→简化→加速→自动化：工程思维的操作系统，结合 Akke 举例。' },
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
