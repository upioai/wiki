"""企微 PC 客户端 坐标量取（方案B·点击坐标定死，2026-06-29）。

VL 实时定位点击点不稳（输入框两次预测 fx=499 vs ≈105，飘 ±一大截），按抖音家族惯例
「导航/点击用量准的固定坐标，VL 只做读消息+核身」。本工具让你把鼠标【悬停】在真实控件上，
脚本读鼠标像素位置 → 换算成 0~1000 归一化坐标 → 打印 .env 行。

**纯本地、不调 API、不点击、不发消息**，只读鼠标坐标。零封号动作。

跑法（先把企微最大化、打开任意一个会话，让输入框可见）：

    python measure_wecom_coords.py

按提示：每个目标给你 6 秒，把鼠标移到那个控件上【停住别动】，倒计时结束自动记录。
跑完把打印的 AKKE_WECOM_* 几行发回，我写进这台机器的企微 .env。
"""

from __future__ import annotations

import sys
import time


def main() -> None:
    try:
        import pyautogui
    except Exception as e:  # noqa: BLE001
        print(f"缺依赖: {e} → pip install pyautogui")
        sys.exit(1)

    W, H = pyautogui.size()
    print("=" * 64)
    print("=== 企微 坐标量取（只读鼠标，不点击）")
    print("=" * 64)
    print(f"屏幕分辨率: {W}x{H}")
    print("请先确认企业微信【已最大化】并打开了一个会话（输入框可见）。\n")

    # (key, 提示, 是否必量)
    targets = [
        ("AKKE_WECOM_C_INPUT", "文字输入框（底部那个能打字的空白区中心）", True),
        ("AKKE_WECOM_C_SEND", "发送按钮（右下角「发送」；若没有/靠 Enter 发，随便停哪都行，标 optional）", False),
        ("AKKE_WECOM_C_SESSION1", "会话列表里【第一个会话】的行中心（左侧）", True),
        # 7 天召回·主动发起腿要用：顶部搜索框（搜联系人那个）。不跑召回可不量（标 optional）。
        ("AKKE_WECOM_C_SEARCH", "顶部搜索框（左上角「搜索」联系人的输入框中心）", False),
    ]

    results = {}
    for key, desc, _required in targets:
        print(f"\n>> 目标: {desc}")
        for n in range(6, 0, -1):
            x, y = pyautogui.position()
            print(f"   {n}… 当前鼠标 像素({x},{y})    ", end="\r")
            time.sleep(1.0)
        x, y = pyautogui.position()
        fx, fy = round(x / W * 1000), round(y / H * 1000)
        results[key] = (x, y, fx, fy)
        print(f"\n   记录 {key}: 像素({x},{y})  归一化 fx={fx} fy={fy}        ")

    print("\n" + "=" * 64)
    print("量取完成。把下面这几行发回（我写进企微 .env，发送适配器按归一化坐标点击）：")
    print("=" * 64)
    for key, (x, y, fx, fy) in results.items():
        print(f"{key}={fx},{fy}        # 像素({x},{y}) @ {W}x{H}")
    print("\n（发送优先走 Enter，AKKE_WECOM_C_SEND 仅在 Enter 不发/插入换行时做兜底点击。）")


if __name__ == "__main__":
    main()
