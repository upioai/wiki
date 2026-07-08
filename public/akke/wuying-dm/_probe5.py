# -*- coding: utf-8 -*-
"""全页找评论发送控件: 键入后扫 发送/send/submit/svg, hover 输入框后再扫一遍。"""
import sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
JS = """() => [...document.querySelectorAll('*')].filter(e => {
  const cls=(e.className||'').toString(), t=(e.innerText||'').slice(0,8), de=e.getAttribute('data-e2e')||'';
  return /发送|send|submit|publish/i.test(cls+t+de) && e.children.length<=2;
}).slice(0,25).map(e => { const r=e.getBoundingClientRect();
  return e.tagName+'|'+(e.className||'').toString().slice(0,50)+'|'+(e.getAttribute('data-e2e')||'')+'|'+(e.innerText||'').slice(0,8)+'|vis:'+(r.width>0&&r.height>0)+'|xy:'+Math.round(r.x)+','+Math.round(r.y); })"""
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
    print("--- 键入后全页 发送/send 扫描 ---")
    for h in page.evaluate(JS):
        print("  ", h)
    box.hover()
    page.wait_for_timeout(800)
    print("--- hover 输入框后 ---")
    for h in page.evaluate(JS):
        print("  ", h)
    print("--- 输入框 box ---")
    bb = box.bounding_box()
    print("  ", bb)
