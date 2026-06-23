# -*- coding: utf-8 -*-
"""坐标捕获:倒数5秒,把鼠标移到目标上不动,自动打印 px + 千分比(脚本用千分比)。
用法: python _capture_pos.py 连播开关"""
import sys, time
import pyautogui
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
target = sys.argv[1] if len(sys.argv) > 1 else "目标"
print("5 秒内把鼠标移到【%s】上,停住别动..." % target, flush=True)
for i in range(5, 0, -1):
    print("  %d..." % i, flush=True)
    time.sleep(1)
x, y = pyautogui.position()
w, h = pyautogui.size()
print("\n>>> %s : px=(%d,%d)  千分比=(%d,%d)  屏幕=%dx%d"
      % (target, x, y, round(x * 1000 / w), round(y * 1000 / h), w, h), flush=True)
