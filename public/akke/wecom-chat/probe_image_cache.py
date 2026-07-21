#!/usr/bin/env python3
"""探测企微 PC 版收到的图片能不能从本地缓存直接读到原图。

B1（客户发户型图 → 读图出专属方案）卡在「拿不到那张图」。两条路：
  A. GUI 点开图片 → 截屏  —— 拿到的是屏幕渲染图，可能不够清晰读尺寸标注
  B. 读本地缓存         —— 拿到原图，画质最好

本脚本验证路线 B 是否可行。**只读不写、不改任何东西。**

用法（在云电脑上跑）：
    python probe_image_cache.py

跑之前先在企微里给自己（或测试号）发一张户型图，这样缓存里会有新文件。
"""
import os
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 企微 PC 版默认把文件落在「文档」下；换过存储路径的机器要自己改
CANDIDATE_ROOTS = [
    Path.home() / "Documents" / "WXWork",
    Path.home() / "Documents" / "企业微信",
    Path("C:/Users/Public/Documents/WXWork"),
]

IMAGE_MAGIC = {
    b"\xff\xd8\xff": "JPEG",
    b"\x89PNG\r\n\x1a\n": "PNG",
    b"GIF8": "GIF",
    b"BM": "BMP",
    b"RIFF": "WEBP(RIFF)",
}


def sniff(path: Path) -> str:
    """看文件头判断真实类型。加密的 .dat 头几个字节不会命中任何 magic。"""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except Exception as e:  # noqa: BLE001
        return f"读不了({e})"
    for magic, name in IMAGE_MAGIC.items():
        if head.startswith(magic):
            return name
    return "未知/可能加密 head=" + head[:8].hex()


def try_xor_recover(path: Path) -> str:
    """微信系 .dat 常见做法是整文件单字节异或。
    用已知的 JPEG/PNG 文件头反推密钥，能推出来说明可解。"""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except Exception:  # noqa: BLE001
        return ""
    if len(head) < 4:
        return ""
    for magic, name in ((b"\xff\xd8\xff", "JPEG"), (b"\x89PNG", "PNG")):
        key = head[0] ^ magic[0]
        if all((head[i] ^ key) == magic[i] for i in range(len(magic))):
            return f"单字节异或可解 key=0x{key:02x} → {name}"
    return ""


def jpeg_size(path: Path, key: int | None = None) -> str:
    """读 JPEG/PNG 的宽高，用来判断是不是原图分辨率（截屏图会明显偏小）。"""
    try:
        data = open(path, "rb").read()
        if key is not None:
            data = bytes(b ^ key for b in data[:64])
        if data.startswith(b"\x89PNG"):
            w, h = struct.unpack(">II", data[16:24])
            return f"{w}x{h}"
    except Exception:  # noqa: BLE001
        pass
    return ""


def main() -> int:
    roots = [p for p in CANDIDATE_ROOTS if p.exists()]
    if not roots:
        print("没找到企微数据目录，试过：")
        for p in CANDIDATE_ROOTS:
            print("  ", p)
        print("\n如果企微改过存储位置：企业微信 → 设置 → 通用 → 文件管理，看实际路径，")
        print("然后把路径加到脚本顶部的 CANDIDATE_ROOTS。")
        return 1

    cutoff = datetime.now() - timedelta(days=2)
    found = []
    for root in roots:
        print(f"\n扫描 {root}")
        for dirpath, _dirnames, filenames in os.walk(root):
            low = dirpath.lower()
            if "image" not in low and "cache" not in low and "file" not in low:
                continue
            for fn in filenames:
                p = Path(dirpath) / fn
                try:
                    st = p.stat()
                except Exception:  # noqa: BLE001
                    continue
                if st.st_size < 10_000:  # 缩略图/表情，太小的不看
                    continue
                if datetime.fromtimestamp(st.st_mtime) < cutoff:
                    continue
                found.append((st.st_mtime, st.st_size, p))

    if not found:
        print("\n近 2 天没有大于 10KB 的新文件。")
        print("先在企微里发一张图片，再跑一次。")
        return 1

    found.sort(reverse=True)
    print(f"\n近 2 天的候选文件 {len(found)} 个，最新 15 个：\n")
    print(f"{'修改时间':<20}{'大小':>10}  {'类型':<28}{'路径'}")
    print("-" * 110)
    for mtime, size, p in found[:15]:
        t = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M:%S")
        kind = sniff(p)
        if kind.startswith("未知"):
            rec = try_xor_recover(p)
            if rec:
                kind = rec
        dim = jpeg_size(p)
        print(f"{t:<20}{size/1024:>8.0f}KB  {kind:<28}{p}")
        if dim:
            print(f"{'':<20}{'':>10}  尺寸 {dim}")

    print("\n" + "=" * 110)
    print("怎么看这个结果：")
    print("  · 类型显示 JPEG/PNG        → 路线 B 可行，能直接读原图，最理想")
    print("  · 显示「单字节异或可解」    → 也可行，读的时候异或一下即可")
    print("  · 全是「未知/可能加密」     → 路线 B 走不通，改走 GUI 截屏（路线 A）")
    print("  · 完全找不到文件           → 缓存路径不对，或企微没落盘")
    print("\n把上面整段输出贴回来即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
