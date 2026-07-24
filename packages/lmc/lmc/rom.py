"""ROM as a separate hardware block: holds a program (bytecode) and streams it.

The program lives here, NOT embedded in the CPU -- so one CPU + RAM run any program by
swapping the ROM. A ROM block is a forwarder ring seeded with the bytecode at spawn; it
forever pops the head, re-appends it (keeps cycling), and emits it East to the CPU. So the
CPU fetches instructions in order by reading the ROM pipe. Hand-laid + zoned like the other
store men (ring South, emit East); part of a horizontal pipeline ROM -> CPU -> RAM.
"""

from __future__ import annotations

from .blockspec import BlockGraph, E, Instr, Pipe, S, W
from .router import Canvas, solve_attachments
from .stores import RingStore
from .trail import PlacedCell, TrailLayout

Op = Instr
ROM = RingStore("rom", S)


def _lit(n: int) -> list[Instr]:
    s = str(abs(n))
    return [Op("`"), *[Op(d) for d in s], Op("`")] + ([Op("N")] if n < 0 else [])


def rom_trail(bytecode: list[int]) -> TrailLayout:
    """Seed the ring with `bytecode`, then forever emit the next word to `romout` (East)."""
    cells: list[PlacedCell] = []

    def put(x, y, ch, pipe=None):
        cells.append(PlacedCell(x, y, ch, pipe))

    put(0, 0, "@")
    x = 1
    for b in bytecode:
        for ch in _lit(b):
            put(x, 0, ch.char)
            x += 1
        put(x, 0, "s", "rom_up")
        x += 1
    re = x
    for ch, pipe in [(">", None), ("r", "rom_down"), ("s", "rom_up"), (".", None),
                     ("s", "romout"), ("v", None)]:
        put(x, 0, ch, pipe)   # fetch head, re-append (cycle), emit to CPU, dip
        x += 1
    end = x - 1
    put(end, 1, "<")
    for xx in range(re, end):
        put(xx, 1, "<")
    put(re, 1, "^")
    return TrailLayout(width=x, height=2, cells=cells, spawn=(0, 0))


def render_rom_standalone(bytecode: list[int], rom_len: int = 8) -> str:
    """ROM block alone: streams the bytecode out the East `romout` port (-> O for testing)."""
    trail = rom_trail(bytecode)
    g = BlockGraph(cpu="ROM")
    g.pipes = [Pipe("romout", "ROM", E, "O", W), *ROM.pipes("ROM")]
    seg = solve_attachments(g, trail)
    Wi, Hi = trail.width, trail.height
    c = Canvas()
    c.rect(-1, -1, Wi, Hi)
    for cc in trail.cells:
        c.put(cc.x, cc.y, cc.char)
    _, orow = seg["romout"]
    c.put(Wi + 1, orow, ">")
    c.put(Wi + 2, orow, ">")
    c.rect(Wi + 3, orow - 1, Wi + 5, orow + 1)
    c.put(Wi + 4, orow, "O")
    buf_top = Hi + rom_len + 1
    c.rect(-1, buf_top, max(Wi, 5), buf_top + 3)
    c.text("@>rsv", 0, buf_top + 1)
    c.text(".^..<", 0, buf_top + 2)
    for p in ROM.pipes("ROM"):
        col, _ = seg[p.id]
        ch = "v" if p.cpu_dir("ROM") == "out" else "^"
        for y in range(Hi + 1, buf_top):
            c.put(col, y, ch)
    return c.render()
