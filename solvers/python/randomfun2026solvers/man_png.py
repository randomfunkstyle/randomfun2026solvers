#!/usr/bin/env python3
"""Render a `.man` grid as a PNG, so a layout can be *looked* at.

Dependency-free: `zlib` and `struct` are the whole toolchain.  A grid is easy to
read wrong from text -- 8,624 cells holding 316 glyphs looks the same as 900
cells holding 316 glyphs when both are printed as rows -- and the failure mode
that costs the most (a room that is mostly void, with the code scattered through
it) is invisible in a diff and obvious in an image.

What a cell's colour means:

* **walls / pipes** -- structure, in grey and blue; a pipe arrowhead is brighter
  than its body so the flow direction reads at a glance;
* **pipe ops** (`r`, `s`, `R`, `S`, `U`, `q`) -- magenta, the cells whose column
  has to bind to the right pipe;
* **turns** (`> < ^ v`) -- amber, the routing overhead;
* **branches** (`d`, `a`, `x`, `X`) -- red;
* **arithmetic / hands / literals** -- green, the cells that do the actual work;
* **blanks inside a room** -- near-black, so density is the first thing you see.

Regions from a :class:`man_debug.DebugMap` tint the background, block-to-block
edges are drawn as thin lines, and every pipe's attach column is marked on the
wall it attaches to.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

__all__ = ["Png", "classify", "density", "render", "write_png"]

# ── the palette ───────────────────────────────────────────────────────────────
BG = (14, 16, 22)
GRID = (30, 34, 44)

CLASS_COLOR: dict[str, tuple[int, int, int]] = {
    "void": (20, 22, 30),          # outside any room
    "blank": (34, 38, 50),         # a walked blank inside a room
    "wall": (110, 118, 135),
    "pipe": (37, 99, 235),
    "pipehead": (96, 165, 250),
    "io": (250, 250, 250),
    "man": (255, 255, 255),
    "pipeop": (217, 70, 239),      # r s R S U q -- column-bound
    "turn": (245, 158, 11),
    "branch": (239, 68, 68),
    "work": (34, 197, 94),         # arithmetic, hands, digits
    "halt": (148, 163, 184),
    "other": (203, 213, 225),
}

_PIPE_BODY = set("-|")
_HEADS = set("<>^v V")
_TURNS = set("<>^vV")
_BRANCH = set("daxX")
_PIPEOP = set("rsRSUq")
_WORK = set("0123456789`MWN+-*/%&|~{}")


def classify(ch: str, *, inside: bool) -> str:
    """Which colour class a glyph belongs to, given whether it is in a room."""
    if ch == "@":
        return "man"
    if ch in "IO":
        return "io"
    if ch == "+":
        return "wall"
    if not inside:
        if ch in _PIPE_BODY:
            return "pipe"
        if ch in _HEADS:
            return "pipehead"
        if ch in "=:":
            return "wall"
        if ch == " ":
            return "void"
        return "other"
    if ch == " ":
        return "blank"
    if ch == "H":
        return "halt"
    if ch in _PIPEOP:
        return "pipeop"
    if ch in _TURNS:
        return "turn"
    if ch in _BRANCH:
        return "branch"
    if ch in _WORK:
        return "work"
    return "other"


# ── the smallest PNG writer that does the job ─────────────────────────────────
class Png:
    """An RGB canvas with a handful of primitives and a `zlib`-only encoder."""

    def __init__(self, w: int, h: int, bg: tuple[int, int, int] = BG) -> None:
        self.w, self.h = w, h
        self.buf = bytearray(bg * (w * h))

    def px(self, x: int, y: int, c: tuple[int, int, int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i:i + 3] = bytes(c)

    def blend(self, x: int, y: int, c: tuple[int, int, int], a: float) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = (y * self.w + x) * 3
        for k in range(3):
            self.buf[i + k] = int(self.buf[i + k] * (1 - a) + c[k] * a)

    def rect(self, x: int, y: int, w: int, h: int, c: tuple[int, int, int]) -> None:
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.px(xx, yy, c)

    def rect_blend(self, x: int, y: int, w: int, h: int,
                   c: tuple[int, int, int], a: float) -> None:
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.blend(xx, yy, c, a)

    def frame(self, x: int, y: int, w: int, h: int, c: tuple[int, int, int]) -> None:
        for xx in range(x, x + w):
            self.px(xx, y, c)
            self.px(xx, y + h - 1, c)
        for yy in range(y, y + h):
            self.px(x, yy, c)
            self.px(x + w - 1, yy, c)

    def line(self, x0: int, y0: int, x1: int, y1: int,
             c: tuple[int, int, int], a: float = 1.0) -> None:
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.blend(x0, y0, c, a)
            if (x0, y0) == (x1, y1):
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def encode(self) -> bytes:
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):                       # filter 0 on every row
            raw.append(0)
            raw += self.buf[y * stride:(y + 1) * stride]

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        head = struct.pack(">2I5B", self.w, self.h, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head)
                + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                + chunk(b"IEND", b""))

    def write(self, path: str | Path) -> None:
        Path(path).write_bytes(self.encode())


# ── reading the grid ──────────────────────────────────────────────────────────
def rooms_of(rows: list[str]) -> list[tuple[int, int, int, int]]:
    """Every closed `+`-cornered rectangle, as `(x, y, w, h)` including walls.

    The same parse the interpreter does, and the only honest one: scan-filling
    rows between `|` glyphs would call the inside of a *pipe* bend a room, and
    the picture would then colour half the machine wrong.
    """
    h = len(rows)
    w = max((len(r) for r in rows), default=0)
    pad = [r.ljust(w) for r in rows]
    out = []
    for y in range(h):
        for x in range(w):
            if pad[y][x] != "+":
                continue
            for x1 in range(x + 2, w):
                if pad[y][x1] == "+" and all(pad[y][i] in "-=" for i in range(x + 1, x1)):
                    pass
                else:
                    continue
                for y1 in range(y + 2, h):
                    if pad[y1][x] != "+" or pad[y1][x1] != "+":
                        continue
                    if not all(pad[i][x] in "|:" for i in range(y + 1, y1)):
                        break
                    if not all(pad[i][x1] in "|:" for i in range(y + 1, y1)):
                        break
                    if all(pad[y1][i] in "-=" for i in range(x + 1, x1)):
                        out.append((x, y, x1 - x + 1, y1 - y + 1))
                        break
                break
    return out


def room_mask(rows: list[str]) -> list[list[bool]]:
    """True for every cell strictly inside some room."""
    h = len(rows)
    w = max((len(r) for r in rows), default=0)
    mask = [[False] * w for _ in range(h)]
    for x, y, rw, rh in rooms_of(rows):
        for yy in range(y + 1, y + rh - 1):
            for xx in range(x + 1, x + rw - 1):
                mask[yy][xx] = True
    return mask


def wall_mask(rows: list[str]) -> set[tuple[int, int]]:
    """Every cell on a room's border -- the glyphs that are structure, not pipe."""
    out: set[tuple[int, int]] = set()
    for x, y, rw, rh in rooms_of(rows):
        for xx in range(x, x + rw):
            out.add((xx, y))
            out.add((xx, y + rh - 1))
        for yy in range(y, y + rh):
            out.add((x, yy))
            out.add((x + rw - 1, yy))
    return out


def density(rows: list[str]) -> tuple[int, int, float]:
    """`(glyphs, interior cells, ratio)` -- how full the rooms actually are."""
    mask = room_mask(rows)
    pad = [r.ljust(max((len(q) for q in rows), default=0)) for r in rows]
    inside = live = 0
    for y, mrow in enumerate(mask):
        for x, m in enumerate(mrow):
            if not m:
                continue
            inside += 1
            if pad[y][x] != " ":
                live += 1
    return live, inside, (live / inside if inside else 0.0)


# ── the picture ───────────────────────────────────────────────────────────────
def _rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def render(
    rows: list[str],
    *,
    scale: int = 8,
    debug: object | None = None,
    edges: list[tuple[tuple[int, int], tuple[int, int]]] | None = None,
    attach: list[tuple[int, int, str]] | None = None,
    pad: int = 12,
) -> Png:
    """Draw the grid.  `edges` are block-to-block control edges in cell
    coordinates; `attach` are `(x, y, ring)` pipe attach points to mark."""
    h = len(rows)
    w = max((len(r) for r in rows), default=1)
    grid = [r.ljust(w) for r in rows]
    mask = room_mask(rows)
    walls = wall_mask(rows)
    img = Png(w * scale + 2 * pad, h * scale + 2 * pad)

    def at(x: int, y: int) -> tuple[int, int]:
        return pad + x * scale, pad + y * scale

    # region tints go down first, so glyphs stay legible on top
    if debug is not None:
        for reg in getattr(debug, "regions", []):
            px, py = at(reg.x, reg.y)
            img.rect_blend(px, py, reg.w * scale, reg.h * scale, _rgb(reg.color), 0.22)
            img.frame(px, py, reg.w * scale, reg.h * scale, _rgb(reg.color))

    for y in range(h):
        for x in range(w):
            ch = grid[y][x]
            cls = "wall" if (x, y) in walls else classify(ch, inside=mask[y][x])
            if cls == "void" and debug is None:
                continue
            px, py = at(x, y)
            c = CLASS_COLOR[cls]
            if cls in ("void", "blank"):
                img.rect_blend(px, py, scale, scale, c, 0.75)
                continue
            # a glyph is drawn as an inset block: the gap between two of them is
            # what makes a run of code read as a run
            img.rect(px + 1, py + 1, max(1, scale - 2), max(1, scale - 2), c)

    if edges:
        for (x0, y0), (x1, y1) in edges:
            ax, ay = at(x0, y0)
            bx, by = at(x1, y1)
            img.line(ax + scale // 2, ay + scale // 2,
                     bx + scale // 2, by + scale // 2, (250, 204, 21), 0.55)

    for x, y, _ring in attach or []:
        px, py = at(x, y)
        img.frame(px, py, scale, scale, (244, 114, 182))
        img.blend(px + scale // 2, py + scale // 2, (244, 114, 182), 1.0)
    return img


def write_png(rows: list[str], path: str | Path, **kw: object) -> tuple[int, int, float]:
    """Render and write; return the density triple so a caller can assert on it."""
    render(rows, **kw).write(path)          # type: ignore[arg-type]
    return density(rows)


if __name__ == "__main__":  # pragma: no cover - a CLI over a saved grid
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("man", type=Path)
    ap.add_argument("png", type=Path)
    ap.add_argument("--scale", type=int, default=8)
    args = ap.parse_args()
    art = args.man.read_text().splitlines()
    live, inside, ratio = write_png(art, args.png, scale=args.scale)
    print(f"{max(len(r) for r in art)}x{len(art)}: "
          f"{live} glyphs in {inside} interior cells -- {ratio:.1%} dense")
