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
      grid-template-columns: repeat(3, 1fr);
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
    footer {
      margin-top: 32px;
      padding-top: 24px;
      border-top: 1px solid var(--border);
      color: var(--text-muted);
      font-size: 12.5px;
      text-align: center;
    }
    @media (max-width: 720px) {
      .container { padding: 40px 20px 80px; }
      .hero h1 { font-size: 34px; }
      .projects,
      .guides { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <header class="hero">
      <h1>upio.ai</h1>
      <p>团队知识库 · Team Knowledge Base</p>
    </header>

    <section>
      <h2>项目知识库</h2>
      <div class="projects">
${projectCards}
      </div>
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
console.log(`Built index: ${PROJECTS.length} projects, ${GUIDES.length - missing.length} guides`);
