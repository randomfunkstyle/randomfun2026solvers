#!/usr/bin/env python3
"""Render a ``.man`` grid to PNG, so layout can be *looked at* instead of inferred.

A generated grid has no comments and ~10k cells, and `-`, `|`, `<`, `>`, `^`, `v`
are each three different things depending on where they sit — a room wall, a pipe
body or bend, or a direction opcode. Reading that off an ASCII dump means holding
a classification in your head for every cell, which is exactly the kind of
bookkeeping that goes wrong silently.

So the classification comes from the engine, not from the glyph: ``lm.mjs
analyze`` reports every room box and every pipe's cell path, and this colours
each cell by what the loader actually decided it was. What the picture then shows
at a glance, and an index-by-index reading does not:

* how much of each room is **empty** — the thing that sets the footprint;
* where the pipes run and how much of the box they occupy;
* where the *code* actually is, and how sparse it is.

Usage::

    python -m randomfun2026solvers.manpng grid.man --out grid.png [--cell 12]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

__all__ = ["classify", "render"]

REPO = Path(__file__).resolve().parents[3]
LM = REPO / "littleman"

MONO = "/System/Library/Fonts/Menlo.ttc"

#: Cell classes, in the order a cell is tested against them.
BG = (250, 250, 252)
COLOURS: dict[str, tuple[int, int, int]] = {
    "outside": (250, 250, 252),
    "wall": (70, 78, 92),
    "empty": (232, 236, 242),      # room interior, nothing in it
    "pipe": (120, 170, 235),       # pipe body
    "arrow": (40, 110, 200),       # pipe bend or endpoint
    "io": (250, 210, 110),         # the I / O cell
    "spawn": (232, 90, 90),        # @
    "split": (200, 40, 40),        # Y
    "pipeop": (245, 150, 60),      # r s S R U q
    "arith": (110, 190, 120),      # + - * / % & | ~ { } N
    "hands": (90, 200, 200),       # M W
    "const": (185, 140, 225),      # digits, backticks
    "flow": (240, 225, 110),       # < > ^ v V X x d a
    "pack": (235, 140, 190),       # b m ]
    "halt": (40, 40, 40),          # H
    "nop": (215, 219, 226),        # . inside a room
}

_KIND = [
    ("pipeop", set("rsSRUq")),
    ("arith", set("+-*/%&|~{}N")),
    ("hands", set("MW")),
    ("const", set("0123456789`")),
    ("flow", set("<>^vVXxda")),
    ("pack", set("bm]")),
    ("halt", set("H")),
    ("split", set("Y")),
    ("spawn", set("@")),
    ("io", set("IO")),
    ("nop", set(".")),
]


def _analyze(path: Path) -> dict:
    out = subprocess.run(
        ["node", "lm.mjs", "analyze", str(path.resolve()), "--json"],
        cwd=LM, capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def classify(rows: list[str], info: dict) -> dict[tuple[int, int], str]:
    """Cell -> class name, using the loader's own room and pipe decisions."""
    kind: dict[tuple[int, int], str] = {}

    for room in info.get("rooms", []):
        (x0, y0), (x1, y1) = room["min"], room["max"]
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                edge = x in (x0, x1) or y in (y0, y1)
                kind[(x, y)] = "wall" if edge else "empty"

    for pipe in info.get("pipes", []):
        cells = pipe["path"]
        for i, step in enumerate(cells):
            x, y = step["pos"]
            ends = i in (0, len(cells) - 1)
            turn = not ends and step["dir"] != cells[i - 1]["dir"]
            kind[(x, y)] = "arrow" if ends or turn else "pipe"

    # Live glyphs win over "empty": a room's interior is only empty where the
    # grid really has nothing in it.
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == " ":
                continue
            if kind.get((x, y)) in ("wall", "pipe", "arrow"):
                continue
            for name, glyphs in _KIND:
                if ch in glyphs:
                    kind[(x, y)] = name
                    break
    return kind


def render(path: Path, out: Path, cell: int = 12, glyphs: bool = True) -> tuple[int, int, dict]:
    rows = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    w = max(len(r) for r in rows)
    h = len(rows)
    info = _analyze(path)
    kind = classify(rows, info)

    pad = 28
    img = Image.new("RGB", (w * cell + 2 * pad, h * cell + 2 * pad), BG)
    d = ImageDraw.Draw(img)
    font = None
    if glyphs and cell >= 8 and Path(MONO).exists():
        try:
            font = ImageFont.truetype(MONO, max(7, int(cell * 0.78)))
        except OSError:
            font = None

    for y in range(h):
        row = rows[y]
        for x in range(w):
            k = kind.get((x, y), "outside")
            if k == "outside":
                continue
            px, py = pad + x * cell, pad + y * cell
            d.rectangle([px, py, px + cell - 1, py + cell - 1], fill=COLOURS[k])
            ch = row[x] if x < len(row) else " "
            if font and ch not in (" ", "") and k not in ("empty", "outside"):
                d.text((px + cell * 0.18, py + cell * 0.04), ch, fill=(20, 20, 24), font=font)

    # a ruler every ten cells, so a coordinate can be read straight off the image
    tick = ImageFont.truetype(MONO, 9) if Path(MONO).exists() else None
    for x in range(0, w, 10):
        d.text((pad + x * cell + 1, 4), str(x), fill=(120, 128, 140), font=tick)
        d.line([pad + x * cell, pad - 3, pad + x * cell, pad], fill=(180, 186, 196))
    for y in range(0, h, 10):
        d.text((2, pad + y * cell), str(y), fill=(120, 128, 140), font=tick)
        d.line([pad - 3, pad + y * cell, pad, pad + y * cell], fill=(180, 186, 196))

    img.save(out)
    tally: dict[str, int] = {}
    for k in kind.values():
        tally[k] = tally.get(k, 0) + 1
    return w, h, tally


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--cell", type=int, default=12)
    ap.add_argument("--no-glyphs", action="store_true")
    args = ap.parse_args()
    out = args.out or args.grid.with_suffix(".png")
    w, h, tally = render(args.grid, out, args.cell, not args.no_glyphs)
    box = w * h
    print(f"{args.grid.name}: {w}x{h}  footprint {max(w, h) ** 2:,}  box {box:,} cells")
    for k, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<8} {n:>6}  {n / box:6.1%}")
    live = box - tally.get("empty", 0) - tally.get("outside", 0)
    print(f"  -> {live:,} cells carry anything at all ({live / box:.1%} of the box)")
    print(f"  wrote {out}")


if __name__ == "__main__":
    main()
