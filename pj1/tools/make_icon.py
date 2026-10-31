"""生成应用图标（不依赖任何第三方库）。

画一个和界面同一套配色的图标：深灰圆角方块 + 米白对话气泡 + 打字三点。

    python tools/make_icon.py

产出：
    assets/chat.ico            多尺寸图标（16/32/48/64/128/256），给快捷方式用
    assets/preview-large.png   256px 预览（用来肉眼确认设计）
    assets/preview-small.png   小尺寸实际大小对照（确认 16px 下还认得出）
"""

from __future__ import annotations

import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

BG = (0x33, 0x33, 0x33)       # 深灰
FG = (0xF9, 0xF8, 0xF6)       # 米白
SS = 4                        # 每个像素的超采样倍数（抗锯齿）

# ── 归一化几何（0~1 坐标系，y 向下）────────────────────────────────
BG_RECT = (0.50, 0.50, 0.47, 0.47, 0.235)          # cx, cy, hw, hh, r
BUBBLE = (0.50, 0.415, 0.27, 0.16, 0.075)          # cx, cy, hw, hh, r
TAIL = ((0.32, 0.53), (0.48, 0.53), (0.335, 0.775))  # 三角形
DOTS = [(0.40, 0.415), (0.50, 0.415), (0.60, 0.415)]
DOT_R = 0.037
DOTS_MIN_SIZE = 48            # 太小的尺寸画三个点会糊成一团，就不画了


def sd_round_rect(px, py, cx, cy, hw, hh, r):
    """圆角矩形的有符号距离，<0 表示在内部。"""
    qx = abs(px - cx) - (hw - r)
    qy = abs(py - cy) - (hh - r)
    ax, ay = max(qx, 0.0), max(qy, 0.0)
    return math.hypot(ax, ay) + min(max(qx, qy), 0.0) - r


def in_triangle(px, py, a, b, c):
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

    d1, d2, d3 = cross(a, b, (px, py)), cross(b, c, (px, py)), cross(c, a, (px, py))
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def render(size: int) -> bytearray:
    """返回 size×size 的 RGBA 字节。"""
    with_dots = size >= DOTS_MIN_SIZE
    out = bytearray(size * size * 4)
    step = 1.0 / (size * SS)
    bub_cx, bub_cy, bub_hw, bub_hh, bub_r = BUBBLE
    bg_cx, bg_cy, bg_hw, bg_hh, bg_r = BG_RECT

    for py in range(size):
        for px in range(size):
            r_acc = g_acc = b_acc = a_acc = 0.0
            for sy in range(SS):
                y = (py * SS + sy + 0.5) * step
                for sx in range(SS):
                    x = (px * SS + sx + 0.5) * step
                    col = (0, 0, 0, 0)
                    if sd_round_rect(x, y, bg_cx, bg_cy, bg_hw, bg_hh, bg_r) < 0:
                        col = (*BG, 255)
                        if sd_round_rect(x, y, bub_cx, bub_cy, bub_hw, bub_hh, bub_r) < 0 or in_triangle(
                            x, y, *TAIL
                        ):
                            col = (*FG, 255)
                            if with_dots and any(
                                math.hypot(x - dx, y - dy) < DOT_R for dx, dy in DOTS
                            ):
                                col = (*BG, 255)
                    r_acc += col[0]
                    g_acc += col[1]
                    b_acc += col[2]
                    a_acc += col[3]
            n = SS * SS
            i = (py * size + px) * 4
            a = a_acc / n
            if a <= 0:
                continue
            # 颜色按覆盖率归一化（避免边缘发黑）
            out[i] = int(r_acc / a_acc * 255 + 0.5)
            out[i + 1] = int(g_acc / a_acc * 255 + 0.5)
            out[i + 2] = int(b_acc / a_acc * 255 + 0.5)
            out[i + 3] = int(a + 0.5)
    return out


# ── 输出 PNG ────────────────────────────────────────────────────────
def write_png(path: str, w: int, h: int, rgba: bytes) -> None:
    raw = b"".join(b"\x00" + rgba[y * w * 4 : (y + 1) * w * 4] for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as fh:
        fh.write(png)


# ── 输出 ICO ────────────────────────────────────────────────────────
def write_ico(path: str, images: list[tuple[int, bytearray]]) -> None:
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    blobs = b""
    offset = 6 + 16 * len(images)
    for size, rgba in images:
        bih = struct.pack(
            "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0
        )
        rows = []
        for y in range(size - 1, -1, -1):  # BMP 自下而上
            row = bytearray()
            for x in range(size):
                i = (y * size + x) * 4
                row += bytes((rgba[i + 2], rgba[i + 1], rgba[i], rgba[i + 3]))  # BGRA
            rows.append(bytes(row))
        xor = b"".join(rows)
        mask = b"\x00" * (size * ((size + 31) // 32) * 4)  # 32 位带 alpha，掩码留空
        blob = bih + xor + mask
        dim = 0 if size == 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    with open(path, "wb") as fh:
        fh.write(header + entries + blobs)


def composite_strip(sizes: list[int], pad: int = 8, bg=(0xF9, 0xF8, 0xF6)) -> tuple[int, int, bytearray]:
    """把若干尺寸按实际像素画到一条浅色背景上，用来确认小尺寸下是否还认得出。"""
    w = sum(s + pad for s in sizes) + pad
    h = max(sizes) + pad * 2
    canvas = bytearray()
    for _ in range(w * h):
        canvas += bytes((*bg, 255))
    x0 = pad
    for size in sizes:
        img = render(size)
        y0 = (h - size) // 2
        for y in range(size):
            for x in range(size):
                i = (y * size + x) * 4
                a = img[i + 3] / 255.0
                if a <= 0:
                    continue
                j = ((y0 + y) * w + (x0 + x)) * 4
                for k in range(3):
                    canvas[j + k] = int(img[i + k] * a + canvas[j + k] * (1 - a) + 0.5)
        x0 += size + pad
    return w, h, canvas


def main() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    images = [(s, render(s)) for s in sizes]
    ico = os.path.join(ASSETS, "chat.ico")
    write_ico(ico, images)
    print(f"写出 {ico}  ({os.path.getsize(ico)} bytes, {len(sizes)} 个尺寸)")

    big = dict(images)[256]
    write_png(os.path.join(ASSETS, "preview-large.png"), 256, 256, big)
    w, h, strip = composite_strip(sizes)
    write_png(os.path.join(ASSETS, "preview-small.png"), w, h, strip)
    print("写出预览图 assets/preview-large.png / preview-small.png")


if __name__ == "__main__":
    main()
