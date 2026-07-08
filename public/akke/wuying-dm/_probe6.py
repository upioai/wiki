# -*- coding: utf-8 -*-
"""定位评论发送红圆↑: dump 输入框大容器内全部小图标, 点最右侧那个, 验证是否发送。"""
import sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    page = next((pg for c in b.contexts for pg in c.pages if "douyin.com" in (pg.url or "")), None)
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(3000)
    page.evaluate("() => { const els=[...document.querySelectorAll('*')].filter(e => e.children.length===0 && (e.innerText||'')==='留下你的精彩评论吧'); for (const el of els){ const r=el.getBoundingClientRect(); if(r.width>0&&r.height>0){ el.click(); return } } }")
    page.wait_for_timeout(1500)
    box = page.locator("div.public-DraftEditor-content").first
    box.click()
    box.press_sequentially("测试评论123", delay=40)
    page.wait_for_timeout(1200)
    bb = box.bounding_box()
    cy = bb["y"] + bb["height"] / 2
    icons = box.evaluate("""(e, cy) => { let c = e; for (let i=0;i<7;i++) c = c.parentElement || c;
      return [...c.querySelectorAll('*')].filter(x => {
        const r = x.getBoundingClientRect();
        return r.width>4 && r.width<70 && r.height>4 && r.height<70 && Math.abs((r.y+r.height/2)-cy)<45 && x.children.length<=2;
      }).map(x => { const r=x.getBoundingClientRect();
        return {tag:x.tagName, cls:(x.className||'').toString().slice(0,45), de:x.getAttribute('data-e2e')||'', x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width)};
      }).sort((a,b)=>a.x-b.x); }""", cy)
    print("--- 输入框同排小图标(x 升序) ---")
    for ic in icons: print("  ", ic)
    if not icons:
        print("NO_ICONS"); sys.exit(1)
    tgt = icons[-1]
    print(f"--- 点击最右图标 x={tgt['x']} cls={tgt['cls'][:30]} ---")
    page.mouse.click(tgt["x"] + tgt["w"]/2, cy)
    page.wait_for_timeout(2500)
    body = page.locator('[data-e2e="comment-list"]').first.inner_text()
    print("SENT" if "测试评论123" in body else "NOT-SENT", "| comment-list:", body[:100].replace("\n", " "))
