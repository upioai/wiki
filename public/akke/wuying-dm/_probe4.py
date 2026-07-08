# -*- coding: utf-8 -*-
"""route-B web 评论发送键探针: 键入后 dump 输入框周边可点元素 + 试 Ctrl+Enter。"""
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
    page.wait_for_timeout(1000)
    print("--- 输入框容器内元素(找发送控件) ---")
    hits = box.evaluate("e => { let c = e; for (let i=0;i<4;i++) c = c.parentElement || c; return [...c.querySelectorAll('*')].filter(x => x.children.length<=1).slice(0,40).map(x => { const r = x.getBoundingClientRect(); return x.tagName+'|'+(x.className||'').toString().slice(0,40)+'|'+(x.getAttribute('data-e2e')||'')+'|'+(x.innerText||'').slice(0,10)+'|vis:'+(r.width>0&&r.height>0); }); }")
    for h in hits:
        print("  ", h)
    print("--- 试 Ctrl+Enter ---")
    box.click()
    page.keyboard.press("Control+Enter")
    page.wait_for_timeout(2500)
    body = page.locator('[data-e2e="comment-list"]').first.inner_text()
    print("SENT" if "测试评论123" in body else "NOT-SENT", "| comment-list:", body[:120].replace("\n", " "))
