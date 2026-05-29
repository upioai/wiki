/**
 * build-characters.js — generate Google-indexable, SFW character landing pages
 * for Vivi (the Telegram AI-companion Mini App).
 *
 * Source of truth: data/vivi-characters.json (vendored, SFW-only projection
 * produced by the Vivi repo's scripts/export_seo_characters.py — never contains
 * system prompts or explicit content).
 *
 * Emits:
 *   public/vivi/characters/index.html        — character directory (grid)
 *   public/vivi/characters/<slug>.html        — one landing page per character
 *   public/sitemap.xml                        — all indexable URLs
 *   public/robots.txt                         — allow all + sitemap pointer
 *
 * Each landing page carries a "Chat on Telegram" CTA whose deeplink reuses
 * Vivi's existing ref- attribution format (ref-seo__<char>__<story>), so SEO
 * traffic is measurable in the backend's /admin/attribution report.
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const DATA = path.join(ROOT, 'data', 'vivi-characters.json');
const OUT_DIR = path.join(ROOT, 'public', 'vivi', 'characters');
const PUBLIC_DIR = path.join(ROOT, 'public');

const SITE = 'https://upio.ai';
const BOT = 'VividDreamsBot';
const TODAY = process.env.BUILD_DATE || new Date().toISOString().slice(0, 10);

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
// For text inside attributes that must also be JSON/JS-safe we only need esc().
function clip(s, n) {
  s = String(s || '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
}

const SHARED_CSS = `
  :root{--bg:#0b0d12;--bg-elevated:#141821;--bg-deep:#1a1f2b;--border:#242a38;--border-strong:#2f3646;--text:#e4e7ed;--text-dim:#9ba3b4;--text-muted:#6b7384;--accent:#a78bfa;--accent-soft:rgba(167,139,250,.15);--accent-2:#22d3ee;--accent-3:#f472b6;--accent-3-soft:rgba(244,114,182,.12)}
  *{box-sizing:border-box}html{scroll-behavior:smooth}
  body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Helvetica,Arial,sans-serif;font-size:16px;line-height:1.7;-webkit-font-smoothing:antialiased}
  a{color:inherit}
  .container{max-width:1080px;margin:0 auto;padding:48px 24px 120px}
  .breadcrumb{font-size:13px;color:var(--text-muted);margin-bottom:24px}
  .breadcrumb a{color:var(--text-dim);text-decoration:none}.breadcrumb a:hover{color:var(--accent)}
  .breadcrumb span{margin:0 8px;color:var(--border-strong)}
  .tag{display:inline-block;padding:4px 12px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:13px;font-weight:500;margin-bottom:14px}
  h1{font-size:44px;font-weight:700;margin:0 0 12px;letter-spacing:-.02em;line-height:1.15;background:linear-gradient(135deg,#e4e7ed 20%,var(--accent) 80%,var(--accent-2) 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
  .subtitle{font-size:18px;color:var(--text-dim);margin:0 0 24px;max-width:760px}
  h2{font-size:13px;font-weight:600;color:var(--text-muted);letter-spacing:.12em;text-transform:uppercase;margin:48px 0 18px}
  .cta{display:inline-flex;align-items:center;gap:10px;background:linear-gradient(135deg,var(--accent),var(--accent-3));color:#fff;text-decoration:none;font-weight:600;font-size:16px;padding:14px 26px;border-radius:999px;transition:transform .15s,box-shadow .2s;box-shadow:0 6px 24px rgba(167,139,250,.25)}
  .cta:hover{transform:translateY(-1px);box-shadow:0 8px 30px rgba(167,139,250,.4)}
  .facts{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:8px 0 16px}
  @media(max-width:720px){.facts{grid-template-columns:repeat(2,1fr)}}
  .fact{background:var(--bg-elevated);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
  .fact .label{font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}
  .fact .val{font-size:15px;color:var(--text);font-weight:500;text-transform:capitalize}
  .hero-row{display:flex;gap:28px;align-items:flex-start;flex-wrap:wrap}
  .portrait{width:200px;height:200px;border-radius:20px;object-fit:cover;border:1px solid var(--border-strong);flex-shrink:0;background:var(--bg-elevated)}
  .hero-body{flex:1;min-width:280px}
  .story{background:var(--bg-elevated);border:1px solid var(--border);border-radius:14px;padding:18px 20px;margin-bottom:12px}
  .story h3{margin:0 0 6px;font-size:17px;font-weight:600}
  .story p{margin:0;font-size:14px;color:var(--text-dim);line-height:1.6}
  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  @media(max-width:860px){.grid{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:560px){.grid{grid-template-columns:1fr}}
  .card{background:var(--bg-elevated);border:1px solid var(--border);border-radius:16px;overflow:hidden;text-decoration:none;color:inherit;display:flex;flex-direction:column;transition:border-color .2s,transform .15s}
  .card:hover{border-color:var(--accent);transform:translateY(-2px)}
  .card img{width:100%;aspect-ratio:1/1;object-fit:cover;background:var(--bg-deep)}
  .card .body{padding:14px 16px 16px}
  .card h3{margin:0 0 4px;font-size:16px;font-weight:600}
  .card p{margin:0;font-size:13px;color:var(--text-dim);line-height:1.5}
  footer{margin-top:64px;padding-top:24px;border-top:1px solid var(--border);font-size:13px;color:var(--text-muted)}
  footer a{color:var(--text-dim);text-decoration:none}footer a:hover{color:var(--accent)}
`;

function deeplink(c) {
  const tail = c.primary_story_id ? `__${c.id}__${c.primary_story_id}` : '';
  return `https://t.me/${BOT}?start=ref-seo${tail}`;
}

function metaHead(opts) {
  const { title, desc, url, image } = opts;
  return `<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${esc(title)}</title>
<meta name="description" content="${esc(desc)}">
<link rel="canonical" href="${esc(url)}">
<meta property="og:type" content="profile">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(desc)}">
<meta property="og:url" content="${esc(url)}">
<meta property="og:image" content="${esc(image)}">
<meta property="og:site_name" content="Vivi Dreams">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(title)}">
<meta name="twitter:description" content="${esc(desc)}">
<meta name="twitter:image" content="${esc(image)}">`;
}

function relatedCharacters(c, allChars, n) {
  // Deterministic "more like this" strip — rotates through the catalog from
  // the current character's position so every page cross-links to others
  // (internal links spread crawl equity and keep visitors browsing).
  const others = allChars.filter(x => x.slug !== c.slug);
  const start = allChars.findIndex(x => x.slug === c.slug);
  const out = [];
  for (let i = 0; i < others.length && out.length < n; i++) {
    out.push(others[(start + i) % others.length]);
  }
  return out;
}

function buildFaq(c) {
  // SFW Q&A that doubles as visible content + FAQPage structured data. Google
  // requires the structured data to match what's on the page, so the same
  // array drives both the JSON-LD and the rendered section.
  const who = c.name;
  return [
    {
      q: `Is ${who} free to chat with?`,
      a: `Yes. You can start chatting with ${who} for free inside Telegram — no install, no sign-up. New users also get free gems to unlock voice replies and gifts.`,
    },
    {
      q: `What is ${who}?`,
      a: `${who} is an AI companion on Vivi Dreams${c.tagline ? ` — ${clip(c.tagline, 120)}` : ''}. You chat in real time across story scenarios, and ${who} remembers you and grows closer over time.`,
    },
    {
      q: `How do I chat with ${who} on Telegram?`,
      a: `Open the Vivi Dreams Mini App on Telegram and tap ${who}, or use the button on this page. It runs right inside Telegram — there's nothing to download.`,
    },
  ];
}

function characterPage(c, allChars) {
  const url = `${SITE}/vivi/characters/${c.slug}`;
  const title = `${c.name} — AI Companion Chat on Telegram · Vivi`;
  const desc = clip(c.tagline || `Chat with ${c.name}, an AI companion on Vivi Dreams.`, 155);
  const dl = deeplink(c);
  const appearance = c.appearance || {};
  const factHtml = Object.entries(appearance)
    .map(([k, v]) => `        <div class="fact"><div class="label">${esc(k)}</div><div class="val">${esc(clip(v, 60))}</div></div>`)
    .join('\n');
  const storyHtml = (c.stories || [])
    .map(s => `      <div class="story"><h3>${esc(s.title)}</h3><p>${esc(clip(s.description || '', 240))}</p></div>`)
    .join('\n');

  const faq = buildFaq(c);
  const faqHtml = faq
    .map(f => `      <div class="story"><h3>${esc(f.q)}</h3><p>${esc(f.a)}</p></div>`)
    .join('\n');

  const related = relatedCharacters(c, allChars, 6);
  const relatedHtml = related.map(r => `      <a class="card" href="/vivi/characters/${esc(r.slug)}">
        <img src="${esc(r.avatar)}" alt="${esc(r.name)}" loading="lazy" width="200" height="200">
        <div class="body"><h3>${esc(r.name)}</h3><p>${esc(clip(r.tagline || '', 70))}</p></div>
      </a>`).join('\n');

  // Unique long-form intro — gives the page enough original content to rank,
  // weaving in the character's traits naturally.
  const looks = Object.values(appearance).filter(Boolean).map(v => clip(v, 50));
  const introBits = [];
  if (c.tagline) introBits.push(clip(c.tagline, 160));
  if (looks.length) introBits.push(`${c.name} has ${looks.slice(0, 3).join(', ')}.`);
  const intro = introBits.join(' ');

  // schema.org: ProfilePage about the character + an FAQPage for rich results.
  const jsonld = [
    {
      '@context': 'https://schema.org',
      '@type': 'ProfilePage',
      mainEntity: { '@type': 'Person', name: c.name, description: desc, image: c.avatar, url },
    },
    {
      '@context': 'https://schema.org',
      '@type': 'FAQPage',
      mainEntity: faq.map(f => ({
        '@type': 'Question',
        name: f.q,
        acceptedAnswer: { '@type': 'Answer', text: f.a },
      })),
    },
  ];

  return `<!DOCTYPE html>
<html lang="en">
<head>
${metaHead({ title, desc, url, image: c.avatar })}
<script type="application/ld+json">${JSON.stringify(jsonld)}</script>
<style>${SHARED_CSS}</style>
</head>
<body>
  <div class="container">
    <nav class="breadcrumb">
      <a href="/">upio.ai</a><span>/</span>
      <a href="/vivi/">Vivi</a><span>/</span>
      <a href="/vivi/characters">Characters</a><span>/</span>
      <span style="color:var(--text-dim)">${esc(c.name)}</span>
    </nav>
    <div class="hero-row">
      <img class="portrait" src="${esc(c.avatar)}" alt="${esc(c.name)} portrait" loading="eager" width="200" height="200">
      <div class="hero-body">
        <span class="tag">${esc(c.category || 'AI Companion')}</span>
        <h1>${esc(c.name)}</h1>
        <p class="subtitle">${esc(c.tagline || '')}</p>
        <a class="cta" href="${esc(dl)}" rel="nofollow">💬 Chat with ${esc(c.name)} on Telegram</a>
      </div>
    </div>
    <p style="color:var(--text-dim);max-width:760px;margin-top:20px">Chat with <strong>${esc(c.name)}</strong>, an AI companion on Vivi Dreams. ${esc(intro)} Talk in real time, build a relationship that deepens across story scenarios, and unlock voice replies and gifts — all inside Telegram.</p>
${factHtml ? `    <h2>Appearance</h2>\n    <div class="facts">\n${factHtml}\n    </div>` : ''}
${storyHtml ? `    <h2>Storylines</h2>\n${storyHtml}` : ''}
    <h2>FAQ</h2>
${faqHtml}
    <h2>Meet ${esc(c.name)} on Vivi Dreams</h2>
    <p style="color:var(--text-dim);max-width:760px">Vivi Dreams is a Telegram Mini App where you chat with AI companions who remember you and grow with you across story scenarios. Open ${esc(c.name)} in one tap — no install, runs right inside Telegram.</p>
    <p style="margin-top:20px"><a class="cta" href="${esc(dl)}" rel="nofollow">💬 Start chatting — it's free</a></p>
${relatedHtml ? `    <h2>More companions</h2>\n    <div class="grid">\n${relatedHtml}\n    </div>` : ''}
    <footer>
      <a href="/vivi/characters">← All characters</a> · <a href="/vivi/">Vivi knowledge base</a> · Powered by Vivi Dreams on Telegram
    </footer>
  </div>
</body>
</html>
`;
}

function indexPage(chars) {
  const url = `${SITE}/vivi/characters`;
  const title = `${chars.length} AI Companions to Chat With on Telegram · Vivi Dreams`;
  const desc = `Browse ${chars.length} AI companions on Vivi Dreams — each with their own personality and story. Chat free inside Telegram, no install required.`;
  const ogImage = chars[0] ? chars[0].avatar : `${SITE}/og-default.png`;
  const cards = chars.map(c => `      <a class="card" href="/vivi/characters/${esc(c.slug)}">
        <img src="${esc(c.avatar)}" alt="${esc(c.name)}" loading="lazy" width="300" height="300">
        <div class="body"><h3>${esc(c.name)}</h3><p>${esc(clip(c.tagline || '', 90))}</p></div>
      </a>`).join('\n');

  const jsonld = {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: title,
    description: desc,
    url,
    hasPart: chars.map(c => ({
      '@type': 'Person', name: c.name, url: `${SITE}/vivi/characters/${c.slug}`,
    })),
  };

  return `<!DOCTYPE html>
<html lang="en">
<head>
${metaHead({ title, desc, url, image: ogImage })}
<script type="application/ld+json">${JSON.stringify(jsonld)}</script>
<style>${SHARED_CSS}</style>
</head>
<body>
  <div class="container">
    <nav class="breadcrumb">
      <a href="/">upio.ai</a><span>/</span>
      <a href="/vivi/">Vivi</a><span>/</span>
      <span style="color:var(--text-dim)">Characters</span>
    </nav>
    <span class="tag">Vivi Dreams</span>
    <h1>Meet your AI companion</h1>
    <p class="subtitle">${chars.length} characters with their own personalities and stories. Chat free inside Telegram — open any of them in one tap, no install.</p>
    <p style="margin:0 0 32px"><a class="cta" href="https://t.me/${BOT}?start=ref-seo" rel="nofollow">💬 Open Vivi on Telegram</a></p>
    <div class="grid">
${cards}
    </div>
    <footer>
      <a href="/vivi/">← Vivi knowledge base</a> · Powered by Vivi Dreams on Telegram
    </footer>
  </div>
</body>
</html>
`;
}

function buildSitemap(chars) {
  const urls = [
    { loc: `${SITE}/`, pr: '1.0' },
    { loc: `${SITE}/vivi/`, pr: '0.8' },
    { loc: `${SITE}/vivi/characters`, pr: '0.9' },
    ...chars.map(c => ({ loc: `${SITE}/vivi/characters/${c.slug}`, pr: '0.7' })),
  ];
  const body = urls.map(u =>
    `  <url><loc>${u.loc}</loc><lastmod>${TODAY}</lastmod><priority>${u.pr}</priority></url>`
  ).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${body}
</urlset>
`;
}

function main() {
  const data = JSON.parse(fs.readFileSync(DATA, 'utf-8'));
  const chars = data.characters || [];
  if (!chars.length) {
    console.error('[build-characters] no characters in data file — aborting');
    process.exit(1);
  }

  fs.mkdirSync(OUT_DIR, { recursive: true });
  let n = 0;
  for (const c of chars) {
    if (!c.slug || !c.avatar) continue;
    fs.writeFileSync(path.join(OUT_DIR, `${c.slug}.html`), characterPage(c, chars));
    n++;
  }
  fs.writeFileSync(path.join(OUT_DIR, 'index.html'), indexPage(chars));
  fs.writeFileSync(path.join(PUBLIC_DIR, 'sitemap.xml'), buildSitemap(chars));
  fs.writeFileSync(path.join(PUBLIC_DIR, 'robots.txt'),
    `User-agent: *\nAllow: /\nSitemap: ${SITE}/sitemap.xml\n`);

  console.log(`✅ built ${n} character pages + index + sitemap (${chars.length} urls) + robots.txt`);
}

main();
