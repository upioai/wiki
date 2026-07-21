"""企微 PC 客户端 聚焦校准探针（方案B·补 probe_wecom_vl.py 两缺口，2026-06-29）。

probe_wecom_vl.py 验证了「截屏→VL 读消息正文」可行，但两点要补：
  (1) 客户身份：上次 VL 把会话标题读成公司名而非客户昵称 → 这里单独裁【顶部标题带】精读；
  (2) 坐标：把 VL 预测的【输入框 / 发送按钮】画标记 + 真实移动鼠标过去（不点击），
      你在云电脑屏幕上肉眼确认点没点准。

**只读不发、不点击，零封号动作（仅移动鼠标）。** 跑前请打开一个【真正的外部客户(@微信)】单聊。

    $env:ANTHROPIC_API_KEY = "sk-or-v1-真实key"
    python calib_wecom_vl.py

输出存 calib_output.txt + screenshots/_wecom_calib_marked.png，连同发回。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request

OUTPUT_FILE = "calib_output.txt"
WORK_DIR = os.path.dirname(os.path.abspath(__file__))


class _Tee:
    def __init__(self, console, logfile) -> None:
        self._console, self._log = console, logfile

    def write(self, s: str) -> int:
        self._console.write(s)
        self._log.write(s)
        return len(s)

    def flush(self) -> None:
        self._console.flush()
        self._log.flush()


def _setup_output() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.stdout = _Tee(sys.stdout, open(OUTPUT_FILE, "w", encoding="utf-8", errors="replace"))


try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(WORK_DIR, ".env"), override=True)
except Exception:  # noqa: BLE001
    pass

KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
BASE = os.environ.get("AKKE_OCR_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.environ.get("AKKE_OCR_MODEL", "qwen/qwen3-vl-30b-a3b-instruct")


def _vision(b64: str, prompt: str, mt: int = 700) -> str:
    payload = {"model": MODEL, "max_tokens": mt, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
        {"type": "text", "text": prompt}]}]}
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"]


def _pjson(txt: str):
    t = txt.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.lower().startswith("json"):
            t = t[4:]
    return json.loads(t.strip())


def _find_wework_window():
    try:
        import pygetwindow as gw
    except Exception:  # noqa: BLE001
        return None
    cands = []
    for w in gw.getAllWindows():
        t = (getattr(w, "title", "") or "")
        if "企业微信" not in t and "WeWork" not in t:
            continue
        if "菜单" in t or "menu" in t.lower():
            continue
        try:
            area = int(getattr(w, "width", 0) or 0) * int(getattr(w, "height", 0) or 0)
        except Exception:  # noqa: BLE001
            area = 0
        cands.append((area, w))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0], reverse=True)
    return cands[0][1]


def _b64(path: str) -> str:
    return base64.b64encode(open(path, "rb").read()).decode()


def main() -> None:
    _setup_output()
    print("=" * 70)
    print("=== 企微 聚焦校准探针（只读、只移动鼠标、不点击）")
    print("=" * 70)
    if not KEY:
        print("缺 ANTHROPIC_API_KEY / OPENROUTER_API_KEY。")
        sys.exit(1)
    try:
        import pyautogui
        from PIL import Image, ImageDraw
    except Exception as e:  # noqa: BLE001
        print(f"缺依赖: {e} → pip install pyautogui pygetwindow pillow")
        sys.exit(1)

    pyautogui.FAILSAFE = True  # 鼠标甩到左上角可紧急中止

    win = _find_wework_window()
    if win is None:
        print("未找到企微窗口，确认已打开/登录/未最小化。")
        sys.exit(1)
    try:
        if getattr(win, "isMinimized", False):
            win.restore()
        win.activate()
        win.maximize()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(1.2)

    input("\n请确认已打开【真正的外部客户(@微信)】单聊（不是测试号），然后按 Enter…")

    os.makedirs("screenshots", exist_ok=True)
    full = os.path.join("screenshots", "_wecom_calib_full.png")
    pyautogui.screenshot(full)
    with Image.open(full) as im:
        W, H = im.size
    print(f"\n截图 {full}  分辨率={W}x{H}")

    def _px(nx, ny):
        return int(float(nx) / 1000.0 * W), int(float(ny) / 1000.0 * H)

    # --- (1) 客户身份：单独裁顶部标题带精读 ---
    print("\n--- 缺口1：裁顶部标题带，精读客户昵称 ---")
    tx0, ty0, tx1, ty1 = int(0.08 * W), int(0.00 * H), int(0.70 * W), int(0.11 * H)
    tcrop = os.path.join("screenshots", "_wecom_title_crop.png")
    with Image.open(full) as im:
        im.crop((tx0, ty0, tx1, ty1)).save(tcrop)
    tp = ("这是企业微信聊天窗口最顶部的标题栏区域。读出【当前正在对话的那个人/客户的昵称】"
          "（通常在最左上，可能后面带 @微信 标记）。不要返回公司名/企业名。只回严格JSON："
          '{"customer_name":"客户昵称","has_wechat_tag":true或false,"ok":true或false}。读不出 ok=false。')
    try:
        td = _pjson(_vision(_b64(tcrop), tp))
        print(json.dumps(td, ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001
        print(f"标题带解析失败({e})，原始返回：")
        try:
            print(_vision(_b64(tcrop), tp))
        except Exception:  # noqa: BLE001
            pass
    print(f"（标题带裁剪图 {tcrop}，发回我核对真实标题位置）")

    # --- (2) 坐标：整屏 VL 取布局 → 画标记 → 移鼠标肉眼确认 ---
    print("\n--- 缺口2：取布局坐标 + 画标记 + 移鼠标确认 ---")
    lp = ("企业微信聊天窗整屏截图。只回严格JSON，坐标 0~1000 整数（x左→右,y顶→底）："
          '{"input_box":{"x":,"y":},"send_button":{"x":,"y":,"found":true或false},'
          '"message_area":{"x0":,"y0":,"x1":,"y1":},'
          '"session_list":{"x0":,"y0":,"x1":,"y1":}}')
    try:
        d = _pjson(_vision(_b64(full), lp))
        print(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001
        print(f"布局解析失败({e})，跳过坐标校准。")
        d = {}

    marked = os.path.join("screenshots", "_wecom_calib_marked.png")
    with Image.open(full).convert("RGB") as im:
        draw = ImageDraw.Draw(im)
        pts = []
        ib = d.get("input_box") or {}
        if "x" in ib:
            pts.append(("输入框", _px(ib["x"], ib["y"]), (0, 200, 0)))
        sb = d.get("send_button") or {}
        if sb.get("found") and "x" in sb:
            pts.append(("发送按钮", _px(sb["x"], sb["y"]), (0, 120, 255)))
        for label, (px, py), color in pts:
            draw.ellipse([px - 22, py - 22, px + 22, py + 22], outline=color, width=5)
            draw.line([px - 30, py, px + 30, py], fill=color, width=2)
            draw.line([px, py - 30, px, py + 30], fill=color, width=2)
        for box, color in ((d.get("message_area"), (255, 140, 0)), (d.get("session_list"), (200, 0, 200))):
            if box and "x0" in box:
                draw.rectangle([_px(box["x0"], box["y0"]), _px(box["x1"], box["y1"])], outline=color, width=4)
        im.save(marked)
    print(f"\n标记图存 {marked}（绿=输入框 蓝=发送 橙=消息区 紫=会话列表），发回我核对。")

    # 真实移动鼠标到预测点，肉眼确认（不点击）
    ib = d.get("input_box") or {}
    if "x" in ib:
        ipx, ipy = _px(ib["x"], ib["y"])
        print(f"\n>> 即将把鼠标移到【输入框】预测点 像素({ipx},{ipy})（不点击）。")
        time.sleep(1.0)
        try:
            pyautogui.moveTo(ipx, ipy, duration=0.6)
        except Exception as e:  # noqa: BLE001
            print(f"   移动失败: {e}")
        input("   看鼠标是否落在【文字输入框】里？记下 准/偏(往哪偏多少)，点回终端按 Enter…")
    sb = d.get("send_button") or {}
    if sb.get("found") and "x" in sb:
        spx, spy = _px(sb["x"], sb["y"])
        print(f">> 即将把鼠标移到【发送按钮】预测点 像素({spx},{spy})（不点击）。")
        time.sleep(1.0)
        try:
            pyautogui.moveTo(spx, spy, duration=0.6)
        except Exception as e:  # noqa: BLE001
            print(f"   移动失败: {e}")
        input("   看鼠标是否落在【发送按钮】上？记下 准/偏，点回终端按 Enter…")

    print("\n" + "=" * 70)
    print("校准结束。请发回：calib_output.txt + screenshots/_wecom_calib_marked.png")
    print("  + screenshots/_wecom_title_crop.png，并文字告诉我：客户昵称读对没、"
          "输入框/发送按钮鼠标准不准(偏的话往哪偏)。")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc(file=sys.stdout)
        sys.exit(1)
