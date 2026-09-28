const fs = require('fs');
const path = require('path');

const publicDir = path.join(__dirname, '..', 'public');

const PROJECTS = [
  {
    name: 'Vivi',
    tagline: 'Telegram Mini App · AI 角色 RP 对话',
    href: '/vivi/',
  },
  {
    name: 'Softie',
    tagline: 'AI 陪伴聊天 · 美国市场（Web + Android）',
    href: '/softie/',
  },
  {
    name: 'Akke',
    tagline: '抖音全屋定制智能获客',
    href: '/akke/',
  },
  {
    name: 'Workflow',
    tagline: 'AI 内容生产流水线 · 短剧/视频/图文',
    href: '/workflow/',
  },
];

// 门店系统三大能力：首页主推区，文案取自 /akke/product-overview-2026-08-12 与各专题页
const STORE_PILLARS = [
  {
    no: '01',
    title: '自动化社媒运营',
    desc: '店长选题，系统完成改稿、配音、口型、B-roll、字幕和成片检查；抖音评论采集、意向分类与私信自动回复，把触达和回复放回同一条客户旅程。',
    tags: ['短视频成片', '评论采集', '意向分类', '私信回复'],
    links: [
      { text: '一条视频里发生了什么', href: '/workflow/replica-workflow-overview-2026-08' },
      { text: '抖音私信自动回复', href: '/akke/dm-autoreply-updates-0710-0713' },
      { text: '内容生产流水线', href: '/workflow/' },
    ],
  },
  {
    no: '02',
    title: '个人 / 企业微信自动化回复',
    desc: '客户一加微信就有人接：设备负责认人、读消息和发送，服务端负责上下文、画像、话术与落库；群聊和身份不明的会话不自动发，发送前再校验一次现场。',
    tags: ['个人微信 PC / 安卓', '企业微信接待', '沉默客户跟进'],
    links: [
      { text: '个人微信 · 功能与案例', href: '/akke/personal-wechat-autoreply-cases-20260916' },
      { text: '个人微信 · 完整逻辑', href: '/akke/personal-wechat-logic-workflow-complete' },
      { text: '企业微信 · 从加好友到成交', href: '/akke/reports/wecom-autoreply-topic' },
    ],
  },
  {
    no: '03',
    title: '智能数据库 · 企业大脑',
    desc: '门店资料解析成知识积木，行业参考与门店承诺分开存；冲突由负责人裁决，未确认内容暂停使用，机器说的每句话都能点回原件。',
    tags: ['资料入箱', '知识积木', '冲突裁决', '版本发布'],
    links: [
      { text: '门店知识怎么出话', href: '/akke/enterprise-brain-knowledge-reply' },
      { text: '资料中心现状', href: '/akke/enterprise-brain-kb-2026-07-31' },
    ],
  },
];

const GUIDES = [
  {
    file: 'anthropic-founder-handbook-zh.html',
    title: 'Anthropic 创始人手册（中译）',
    desc: 'Karpathy 翻译整理的 Anthropic 工作手册中文版。AI native 创业心法。',
  },
  {
    file: 'claude-code-windows-guide.html',
    title: 'Claude Code · Windows 上手攻略',
    desc: 'Windows 用户从 0 到能跑 Claude Code 的完整路径，含常见坑。',
  },
  {
    file: 'mac-windows-arm64-mirror-guide.html',
    title: 'Mac 装 Windows on ARM 镜像指南',
    desc: 'M 系列 Mac 通过 Parallels / UTM 装 Windows 11 ARM 的完整流程。',
  },
  {
    file: 'vpn-quick-start.html',
    title: 'VPN 快速上手',
    desc: '团队自建 VPN 节点配置，从客户端安装到分流规则。',
  },
  {
    file: 'design-system.html',
    title: 'upio.ai 设计系统',
    desc: '本站统一的暗色主题设计规范：色板、排版、组件样式，做新页面/子站时照此取值。',
  },
];

const missing = GUIDES.filter(g => !fs.existsSync(path.join(publicDir, g.file)));
if (missing.length) {
  console.warn(`[build-index] WARN: ${missing.length} curated guide(s) missing: ${missing.map(m => m.file).join(', ')}`);
}

const guideCards = GUIDES.filter(g => fs.existsSync(path.join(publicDir, g.file)))
  .map(g => {
    const href = '/' + g.file.replace(/\.html$/, '');
    return `        <a class="guide-card" href="${href}">
          <h3>${g.title}</h3>
          <p>${g.desc}</p>
        </a>`;
  })
  .join('\n');

const pillarCards = STORE_PILLARS.map(p => {
  const tags = p.tags.map(t => `<span class="chip">${t}</span>`).join('');
  const links = p.links.map(l => `<a href="${l.href}">${l.text} →</a>`).join('\n            ');
  return `        <article class="pillar">
          <span class="pillar__no">${p.no}</span>
          <h3>${p.title}</h3>
          <p>${p.desc}</p>
          <div class="chips">${tags}</div>
          <nav class="pillar__links">
            ${links}
          </nav>
        </article>`;
}).join('\n');

const projectCards = PROJECTS.map(p => {
  return `        <a class="project-card" href="${p.href}">
          <span class="badge badge--live">进入</span>
          <h3>${p.name}</h3>
          <p>${p.tagline}</p>
        </a>`;
}).join('\n');

const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>upio.ai · 团队知识库</title>
  <style>
    :root {
      --bg: #0b0d12;
      --bg-elevated: #141821;
      --bg-deep: #1a1f2b;
      --border: #242a38;
      --border-strong: #2f3646;
      --text: #e4e7ed;
      --text-dim: #9ba3b4;
      --text-muted: #6b7384;
      --accent: #8b5cf6;
      --accent-2: #60a5fa;
      --accent-soft: rgba(139, 92, 246, 0.15);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 16px;
      line-height: 1.7;
      -webkit-font-smoothing: antialiased;
      min-height: 100vh;
    }
    .container {
      max-width: 960px;
      margin: 0 auto;
      padding: 64px 24px 120px;
    }
    header.hero {
      padding: 32px 0 48px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 56px;
    }
    .hero h1 {
      margin: 0 0 14px;
      font-size: 44px;
      font-weight: 700;
      letter-spacing: -0.02em;
      background: linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .hero p {
      margin: 0;
      color: var(--text-dim);
      font-size: 16px;
      letter-spacing: 0.02em;
    }
    h2 {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      margin: 0 0 20px;
    }
    section { margin-bottom: 56px; }
    .projects {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 14px;
    }
    .guides {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 14px;
    }
    .project-card,
    .guide-card {
      position: relative;
      display: block;
      background: var(--bg-elevated);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 22px 22px 20px;
      transition: border-color 0.2s ease, transform 0.15s ease, background 0.2s ease;
      text-decoration: none;
      color: inherit;
    }
    .project-card:hover,
    .guide-card:hover {
      border-color: var(--accent);
      transform: translateY(-1px);
    }
    .project-card h3,
    .guide-card h3 {
      margin: 0 0 8px;
      font-size: 18px;
      font-weight: 600;
      color: var(--text);
      letter-spacing: -0.005em;
    }
    .project-card p,
    .guide-card p {
      margin: 0;
      font-size: 13.5px;
      color: var(--text-dim);
      line-height: 1.55;
    }
    .badge {
      position: absolute;
      top: 14px;
      right: 14px;
      font-size: 11px;
      font-weight: 500;
      padding: 3px 8px;
      border-radius: 999px;
      background: rgba(155, 163, 180, 0.1);
      color: var(--text-muted);
      letter-spacing: 0.04em;
    }
    .badge--live {
      background: var(--accent-soft);
      color: var(--accent);
    }
    .learn-card {
      display: flex;
      align-items: center;
      gap: 18px;
      background: linear-gradient(135deg, var(--accent-soft) 0%, var(--bg-elevated) 60%);
      border: 1px solid var(--border-strong);
      border-radius: 12px;
      padding: 22px 24px;
      text-decoration: none;
      color: inherit;
      transition: border-color 0.2s ease, transform 0.15s ease;
    }
    .learn-card:hover {
      border-color: var(--accent);
      transform: translateY(-1px);
    }
    .learn-card__icon {
      font-size: 30px;
      line-height: 1;
      flex-shrink: 0;
    }
    .learn-card__body h3 {
      margin: 0 0 6px;
      font-size: 18px;
      font-weight: 600;
      color: var(--text);
      letter-spacing: -0.005em;
    }
    .learn-card__body p {
      margin: 0;
      font-size: 13.5px;
      color: var(--text-dim);
      line-height: 1.55;
    }
    footer {
      margin-top: 32px;
      padding-top: 24px;
      border-top: 1px solid var(--border);
      color: var(--text-muted);
      font-size: 12.5px;
      text-align: center;
    }
    .store {
      position: relative;
      background: linear-gradient(160deg, rgba(139, 92, 246, 0.12) 0%, var(--bg-elevated) 45%, var(--bg) 100%);
      border: 1px solid var(--border-strong);
      border-radius: 16px;
      padding: 32px 28px 28px;
    }
    .store__head h2 { margin-bottom: 10px; color: var(--accent); }
    .store__title {
      margin: 0 0 10px;
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.01em;
      line-height: 1.35;
    }
    .store__lede {
      margin: 0 0 26px;
      color: var(--text-dim);
      font-size: 14.5px;
      max-width: 640px;
    }
    .pillars {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 14px;
    }
    .pillar {
      display: flex;
      flex-direction: column;
      background: var(--bg-elevated);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 22px 20px 18px;
      transition: border-color 0.2s ease;
    }
    .pillar:hover { border-color: var(--accent); }
    .pillar__no {
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.1em;
      color: var(--accent-2);
      margin-bottom: 8px;
    }
    .pillar h3 {
      margin: 0 0 10px;
      font-size: 18px;
      font-weight: 600;
      line-height: 1.4;
    }
    .pillar p {
      margin: 0 0 14px;
      font-size: 13.5px;
      color: var(--text-dim);
      line-height: 1.6;
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 16px;
    }
    .chip {
      font-size: 11.5px;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: #c4b5fd;
    }
    .pillar__links {
      margin-top: auto;
      padding-top: 12px;
      border-top: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .pillar__links a,
    .store__more a {
      color: var(--text);
      font-size: 13px;
      text-decoration: none;
    }
    .pillar__links a:hover,
    .store__more a:hover { color: var(--accent); }
    .store__more {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 20px;
      margin-top: 20px;
      font-size: 13px;
      color: var(--text-muted);
    }
    .store__more a { color: var(--accent-2); }
    @media (max-width: 720px) {
      .container { padding: 40px 20px 80px; }
      .hero h1 { font-size: 34px; }
      .projects,
      .guides,
      .pillars { grid-template-columns: 1fr; }
      .store { padding: 24px 18px 20px; }
      .store__title { font-size: 21px; }
    }
  </style>
</head>
<body>
  <div class="container">
    <header class="hero">
      <h1>upio.ai</h1>
      <p>门店 AI 获客与经营系统 · 团队知识库</p>
    </header>

    <section class="store">
      <div class="store__head">
        <h2>门店系统</h2>
        <p class="store__title">把大模型放进一家门店的获客与接待里，让它每天真的干活。</p>
        <p class="store__lede">从刷到视频、加上微信，到答疑和约到店：三块能力串成一条增长链，共用一个数据库，出站动作都有闸门把关。</p>
      </div>
      <div class="pillars">
${pillarCards}
      </div>
      <div class="store__more">
        <span>完整介绍：</span>
        <a href="/akke/product-overview-2026-08-12">产品完整介绍 →</a>
        <a href="/akke/business-tour">业务导览 · 三条业务线 →</a>
      </div>
    </section>

    <section>
      <h2>项目知识库</h2>
      <div class="projects">
${projectCards}
      </div>
    </section>

    <section>
      <h2>知识分享</h2>
      <a class="learn-card" href="/learn">
        <span class="learn-card__icon">📚</span>
        <div class="learn-card__body">
          <h3>AI · 大模型 · 工程化讲解合集</h3>
          <p>团队内部多次技术分享的沉淀：大模型基础、Agent / AI 工程、工具入门。点击进入知识分享中心 →</p>
        </div>
      </a>
      <a class="learn-card" href="/timeline" style="margin-top: 12px;">
        <span class="learn-card__icon">🗓️</span>
        <div class="learn-card__body">
          <h3>知识时间线</h3>
          <p>全站知识页按入库时间排列：知识分享、Akke、Workflow、Softie、Vivi 与通用指南，可按分区筛选、按标题搜索 →</p>
        </div>
      </a>
    </section>

    <section>
      <h2>通用指南</h2>
      <div class="guides">
${guideCards}
      </div>
    </section>

    <footer>upio.ai · 各项目的踩坑/案例/SOP 请进入对应子站</footer>
  </div>
</body>
</html>`;

fs.writeFileSync(path.join(publicDir, 'index.html'), html);
console.log(`Built index: ${STORE_PILLARS.length} store pillars, ${PROJECTS.length} projects, ${GUIDES.length - missing.length} guides`);
