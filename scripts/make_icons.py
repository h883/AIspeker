"""PWA 用アイコンを生成する（外部ライブラリ不要）。

frontend/assets/icon-192.png と icon-512.png を作り直す。
デザインを変えたいときはこのファイルを編集して python scripts/make_icons.py を実行する。
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "frontend" / "assets"

BG_TOP = (27, 42, 99)
BG_BOTTOM = (11, 16, 32)
ORB_LIGHT = (111, 156, 255)
ORB_DARK = (47, 75, 181)


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _pixels(size: int) -> bytearray:
    rows = bytearray()
    center = size / 2
    orb_radius = size * 0.31
    corner_radius = size * 0.22

    for y in range(size):
        rows.append(0)  # PNG filter type: None
        for x in range(size):
            # 角丸の外側は透明にする
            dx = max(corner_radius - x, x - (size - corner_radius), 0)
            dy = max(corner_radius - y, y - (size - corner_radius), 0)
            if math.hypot(dx, dy) > corner_radius:
                rows.extend((0, 0, 0, 0))
                continue

            color = _blend(BG_TOP, BG_BOTTOM, y / size)
            distance = math.hypot(x - center, y - center)
            if distance < orb_radius:
                # 左上を明るくした球体風グラデーション
                light = math.hypot(x - center * 0.72, y - center * 0.66) / (orb_radius * 1.7)
                color = _blend(ORB_LIGHT, ORB_DARK, min(light, 1.0))
                edge = orb_radius - distance
                if edge < 1.5:
                    color = _blend(color, _blend(BG_TOP, BG_BOTTOM, y / size), 1 - edge / 1.5)
            rows.extend((color[0], color[1], color[2], 255))
    return rows


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, size: int) -> None:
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8bit RGBA
    body = zlib.compress(bytes(_pixels(size)), 9)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", body) + _chunk(b"IEND", b"")
    )
    print(f"生成しました: {path} ({size}x{size})")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        write_png(ASSETS / f"icon-{size}.png", size)


if __name__ == "__main__":
    main()
