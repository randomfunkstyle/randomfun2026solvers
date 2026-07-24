"""`memory` as a chain of sub-blocks (see ARCHITECTURE.md).

Block 1 -- **DMA-mem**: a hand-laid ring man exposing the low-level memory commands a
Driver composes into READ/WRITE. Commands (cmd, value):

  cmd == 0  ADVANCE  : rotate the ring by 1 (no output)
  cmd  > 0  PEEK     : emit the head, then rotate by 1
  cmd  < 0  REPLACE  : pop the head, push `value` (rotate by 1)

All three advance the ring by exactly one slot, so a Driver positions to index `i` with
ADVANCE×i, does its op, then ADVANCE×(N-i-1) to complete a lap back to canonical. The ring
is seeded at spawn (memory starts at 0). Hand-laid + zoned like `dma.py`: read at the top
(near the West command port), ring ops dip near the South ring, PEEK emits East; the loop
is control glyphs only.
"""

from __future__ import annotations

from .blockspec import BlockGraph, E, Instr, Pipe, S, W
from .router import Canvas, solve_attachments
from .stores import RingStore
from .trail import PlacedCell, TrailLayout

Op = Instr
RAM = RingStore("ram", S)


def _lit(n: int) -> list[Instr]:
    s = str(abs(n))
    return [Op("`"), *[Op(d) for d in s], Op("`")] + ([Op("N")] if n < 0 else [])


def memdma_trail(seed_vals: list[int]) -> TrailLayout:
    """DMA-mem datapath: seed the ring, then loop reading (cmd,value) commands."""
    cells: list[PlacedCell] = []

    def put(x, y, ch, pipe=None):
        cells.append(PlacedCell(x, y, ch, pipe))

    # spawn + seed the ring
    put(0, 0, "@")
    x = 1
    for v in seed_vals:
        for ch in _lit(v):
            put(x, 0, ch.char)
            x += 1
        put(x, 0, "s", "ram_up")
        x += 1

    re = x                          # loop re-entry column
    for ch, pipe in [(">", None), ("r", "req"), ("M", None), ("r", "req"), ("W", None)]:
        put(x, 0, ch, pipe)         # >, read cmd, B=cmd, read value, A=cmd/B=value
        x += 1
    dipx = x
    put(x, 0, "v")
    x += 1

    put(dipx, 1, "v")
    put(dipx, 2, ">")
    put(dipx + 1, 2, "X")           # dispatch near the South ring
    xr = dipx + 1
    # REPLACE (neg -> north row 1): pop head (discard), push value(B)
    put(xr, 1, ">")
    put(xr + 1, 1, "r", "ram_down")
    put(xr + 2, 1, "W")
    put(xr + 3, 1, "s", "ram_up")
    put(xr + 4, 1, ">")
    put(xr + 5, 1, ">")
    put(xr + 6, 1, "v")
    # ADVANCE (zero -> east row 2): rotate, discard, no emit
    put(xr + 1, 2, "r", "ram_down")
    put(xr + 2, 2, "s", "ram_up")
    put(xr + 3, 2, ">")
    put(xr + 4, 2, ">")
    put(xr + 5, 2, ">")
    put(xr + 6, 2, "v")
    # PEEK (pos -> south row 3): read head, re-append, emit East
    put(xr, 3, ">")
    put(xr + 1, 3, "r", "ram_down")
    put(xr + 2, 3, "s", "ram_up")
    put(xr + 3, 3, ".")
    put(xr + 4, 3, ".")
    put(xr + 5, 3, "s", "dout")
    put(xr + 6, 3, "v")

    # looper (control only): down col xr+6, west row 4, up col `re` -> re-entry
    end = xr + 6
    put(end, 4, "<")
    for xx in range(re, end):
        put(xx, 4, "<")
    put(re, 4, "^")
    put(re, 3, "^")
    put(re, 2, "^")
    put(re, 1, "^")
    return TrailLayout(width=end + 2, height=5, cells=cells, spawn=(0, 0))


def render_memdma_standalone(seed_vals: list[int], ram_len: int = 6) -> str:
    """Standalone grid: command stream <- West input, results -> East output."""
    trail = memdma_trail(seed_vals)
    dg = BlockGraph(cpu="DMA")
    dg.pipes = [Pipe("req", "I", E, "DMA", W), Pipe("dout", "DMA", E, "O", W), *RAM.pipes("DMA")]
    seg = solve_attachments(dg, trail)
    Wi, Hi = trail.width, trail.height
    c = Canvas()
    c.rect(-1, -1, Wi, Hi)
    for cc in trail.cells:
        c.put(cc.x, cc.y, cc.char)
    _, irow = seg["req"]
    c.rect(-6, irow - 1, -4, irow + 1)
    c.put(-5, irow, "I")
    c.put(-3, irow, ">")
    c.put(-2, irow, ">")
    _, orow = seg["dout"]
    c.rect(Wi + 3, orow - 1, Wi + 5, orow + 1)
    c.put(Wi + 4, orow, "O")
    c.put(Wi + 1, orow, ">")
    c.put(Wi + 2, orow, ">")
    buf_top = Hi + ram_len + 1
    c.rect(-1, buf_top, max(Wi, 5), buf_top + 3)
    c.text("@>rsv", 0, buf_top + 1)
    c.text(".^..<", 0, buf_top + 2)
    for p in RAM.pipes("DMA"):
        col, _ = seg[p.id]
        ch = "v" if p.cpu_dir("DMA") == "out" else "^"
        for y in range(Hi + 1, buf_top + 1):
            c.put(col, y, ch)
    return c.render()
