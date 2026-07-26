#!/usr/bin/env python3
"""Render a `.man` grid to a small PNG, so a layout can be *looked at*.

Reading a grid as text costs thousands of tokens and still leaves you tracking
coordinates in your head.  A 3-pixel-a-cell image of an 80x80 grid is a few
kilobytes and shows at a glance what a coordinate dump cannot: a bank that is
half empty, a corridor that meanders, a room taller than its contents.

No dependencies -- a PNG is a zlib stream of filtered scanlines wrapped in three
chunks, which is less code than parsing one would be.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

__all__ = ["CLASSES", "png_bytes", "render"]

#: Cell class -> RGB.  Walls and pipes are the skeleton, glyphs are the program,
#: and corridors are the cost -- so corridors get the colour that stands out.
CLASSES: dict[str, tuple[int, int, int]] = {
    "blank": (24, 24, 27),
    "wall": (82, 82, 91),
    "pipe": (14, 165, 233),
    "corridor": (245, 158, 11),
    "glyph": (250, 250, 250),
}


def _class_of(ch: str) -> str:
    if ch == " ":
        return "blank"
    if ch in "+-|":
        return "wall"
    if ch in "<>^v":
        # A turn glyph is a corridor cell in the room and a pipe cell outside it;
        # they are told apart by the caller, which knows where the walls are.
        return "corridor"
    return "glyph"


def png_bytes(pixels: list[list[tuple[int, int, int]]]) -> bytes:
    """Pack rows of RGB triples into a PNG byte string."""
    h, w = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in pixels)

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def render(grid: list[str] | Path | str, out: Path, scale: int = 3,
           pipe_rows: int = 0) -> tuple[int, int]:
    """Write `grid` to `out` as a PNG; returns the image size.

    `pipe_rows` names how many rows at the top are north band, where an arrow is
    a pipe rather than a corridor.
    """
    if isinstance(grid, (str, Path)) and Path(grid).exists():
        grid = Path(grid).read_text(encoding="utf-8").splitlines()
    elif isinstance(grid, str):
        grid = grid.splitlines()
    w = max(len(r) for r in grid)
    pixels: list[list[tuple[int, int, int]]] = []
    for y, line in enumerate(grid):
        row: list[tuple[int, int, int]] = []
        for x in range(w):
            ch = line[x] if x < len(line) else " "
            kind = _class_of(ch)
            if kind == "corridor" and y < pipe_rows:
                kind = "pipe"
            row += [CLASSES[kind]] * scale
        pixels += [row] * scale
    out.write_bytes(png_bytes(pixels))
    return len(pixels[0]), len(pixels)


if __name__ == "__main__":  # pragma: no cover - a viewer, not a build step
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--pipe-rows", type=int, default=0)
    args = ap.parse_args()
    print(render(args.grid, args.out, args.scale, args.pipe_rows))
