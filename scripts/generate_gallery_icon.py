#!/usr/bin/env python3

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


SIZE = 1024
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT = PROJECT_DIR / "assets" / "gallery-icon.png"


def clamp(value: float, minimum: float = 0.0, maximum: float = 255.0) -> int:
    return int(max(minimum, min(maximum, round(value))))


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(clamp(a[i] + (b[i] - a[i]) * t) for i in range(3))


def inside_round_rect(x: int, y: int, left: int, top: int, width: int, height: int, radius: int) -> bool:
    right = left + width
    bottom = top + height
    if left + radius <= x < right - radius or top + radius <= y < bottom - radius:
        return True
    cx = left + radius if x < left + radius else right - radius - 1
    cy = top + radius if y < top + radius else bottom - radius - 1
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def alpha_blend(dst: tuple[int, int, int, int], src: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    sr, sg, sb, sa = src
    dr, dg, db, da = dst
    sa_n = sa / 255.0
    da_n = da / 255.0
    out_a = sa_n + da_n * (1 - sa_n)
    if out_a == 0:
      return (0, 0, 0, 0)
    out_r = (sr * sa_n + dr * da_n * (1 - sa_n)) / out_a
    out_g = (sg * sa_n + dg * da_n * (1 - sa_n)) / out_a
    out_b = (sb * sa_n + db * da_n * (1 - sa_n)) / out_a
    return (clamp(out_r), clamp(out_g), clamp(out_b), clamp(out_a * 255))


def draw_circle(canvas: list[list[tuple[int, int, int, int]]], cx: int, cy: int, radius: int, color: tuple[int, int, int, int]) -> None:
    x0 = max(0, cx - radius)
    x1 = min(SIZE, cx + radius)
    y0 = max(0, cy - radius)
    y1 = min(SIZE, cy + radius)
    radius_sq = radius * radius
    for y in range(y0, y1):
        row = canvas[y]
        for x in range(x0, x1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius_sq:
                row[x] = alpha_blend(row[x], color)


def draw_round_rect(
    canvas: list[list[tuple[int, int, int, int]]],
    left: int,
    top: int,
    width: int,
    height: int,
    radius: int,
    fill_top: tuple[int, int, int],
    fill_bottom: tuple[int, int, int],
    alpha: int = 255,
) -> None:
    for y in range(top, top + height):
        t = (y - top) / max(height - 1, 1)
        color = mix(fill_top, fill_bottom, t)
        row = canvas[y]
        for x in range(left, left + width):
            if inside_round_rect(x, y, left, top, width, height, radius):
                row[x] = alpha_blend(row[x], (*color, alpha))


def draw_rect(canvas: list[list[tuple[int, int, int, int]]], left: int, top: int, width: int, height: int, color: tuple[int, int, int, int]) -> None:
    for y in range(top, top + height):
        row = canvas[y]
        for x in range(left, left + width):
            row[x] = alpha_blend(row[x], color)


def draw_landscape(canvas: list[list[tuple[int, int, int, int]]]) -> None:
    left, top, width, height, radius = 300, 286, 424, 322, 58
    draw_round_rect(canvas, left, top, width, height, radius, (8, 32, 38), (13, 59, 67))

    for y in range(top + 138, top + height):
        row = canvas[y]
        for x in range(left, left + width):
            if not inside_round_rect(x, y, left, top, width, height, radius):
                continue
            if y > (-0.72 * (x - 344) + 572) and x < 530:
                row[x] = alpha_blend(row[x], (44, 181, 176, 255))
            if y > (0.66 * (x - 474) + 444) and x > 430:
                row[x] = alpha_blend(row[x], (109, 235, 226, 255))
            if y > (-0.85 * (x - 596) + 552) and x > 560:
                row[x] = alpha_blend(row[x], (159, 245, 236, 255))

    draw_circle(canvas, 618, 382, 40, (209, 255, 249, 255))


def write_png(path: Path, canvas: list[list[tuple[int, int, int, int]]]) -> None:
    raw = bytearray()
    for row in canvas:
        raw.append(0)
        for r, g, b, a in row:
            raw.extend((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    canvas = [[(0, 0, 0, 0) for _ in range(SIZE)] for _ in range(SIZE)]

    draw_round_rect(canvas, 84, 84, 856, 856, 220, (11, 79, 86), (8, 27, 31))
    draw_circle(canvas, 766, 260, 160, (60, 214, 202, 28))
    draw_circle(canvas, 304, 224, 124, (243, 170, 108, 28))
    draw_round_rect(canvas, 256, 218, 512, 588, 112, (248, 255, 254), (124, 230, 221), 42)
    draw_landscape(canvas)
    draw_rect(canvas, 344, 648, 336, 22, (220, 250, 245, 235))
    draw_rect(canvas, 344, 692, 242, 18, (166, 220, 214, 176))

    write_png(OUTPUT, canvas)


if __name__ == "__main__":
    main()
