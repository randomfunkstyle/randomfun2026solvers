"""A standalone DMA / memory-controller little man: a stack (RAM ring) behind a
command interface. Hand-laid as fixed hardware.

Design principle (why hand-laid, not composed): the engine routes s/r to the nearest
pipe, so the *data path* (pipe-ops) must be spatially zoned -- req reads top-left near
the West port, RAM ops low near the South ring, dout far East. The *control path* -- the
loop that cycles the man back to re-read -- is built from pure routing glyphs (>/</^/v)
with no pipe-ops, so it is routing-neutral and never disturbs the zoned data path. This
control/data split is what makes a multi-pipe man routable where generic composition
(forever_loop/if3) goes UNSAT.

Command frame: two ints (cmd, value):
  cmd < 0  -> ROTATE : move the head to the tail and emit it (peek-and-advance)
  cmd == 0 -> PUSH value
  cmd > 0  -> POP    : emit the head and remove it
The RAM ring is FIFO (dequeue returns the head); ROTATE lets a caller walk it. Dispatch
happens *after* the dip into the RAM zone, so all three arms sit near the South ring.
Standalone-testable: drive `req` from an input room and observe `dout`.
"""

from __future__ import annotations

from .blockspec import BlockGraph, E, Pipe, S, W
from .router import Canvas, solve_attachments
from .stores import RingStore
from .trail import PlacedCell, TrailLayout

RAM = RingStore("ram", S)


def dma_trail() -> TrailLayout:
    """The fixed DMA datapath + hardware looper (ROTATE / PUSH / POP).

    Read cmd,value on row 0, dip into the RAM zone, then dispatch with `X` so every arm
    (ROTATE north / PUSH straight / POP south) sits near the South ring. Emitting arms
    travel East to `dout` (away from the ring). The looper is control glyphs only.
    """
    cells: list[PlacedCell] = []

    def put(x, y, ch, pipe=None):
        cells.append(PlacedCell(x, y, ch, pipe))

    # read zone (row 0), req West
    put(0, 0, "@")
    put(1, 0, ">")          # loop re-entry
    put(2, 0, "r", "req")   # cmd
    put(3, 0, "M")          # B = cmd
    put(4, 0, "r", "req")   # value
    put(5, 0, "W")          # A = cmd, B = value
    put(6, 0, "v")          # dip into the RAM zone
    put(6, 1, "v")
    put(6, 2, ">")
    put(7, 2, "X")          # dispatch near the South ring

    # ROTATE arm (neg -> north -> row 1): head -> tail, emit it East
    put(7, 1, ">")
    put(8, 1, "r", "ram_down")
    put(9, 1, "s", "ram_up")
    put(10, 1, ".")
    put(11, 1, ".")
    put(12, 1, "s", "dout")
    put(13, 1, "v")
    # PUSH arm (zero -> straight east -> row 2): value(B) -> A, push
    put(8, 2, "W")
    put(9, 2, "s", "ram_up")
    put(10, 2, ">")
    put(11, 2, ">")
    put(12, 2, ">")
    put(13, 2, "v")
    # POP arm (pos -> south -> row 3): read head, emit East
    put(7, 3, ">")
    put(8, 3, "r", "ram_down")
    put(9, 3, ".")
    put(10, 3, ".")
    put(11, 3, "s", "dout")
    put(12, 3, ">")
    put(13, 3, "v")

    # hardware looper (control glyphs only): down col 13, west row 4, up col 1 -> re-entry
    put(13, 4, "<")
    for x in range(2, 13):
        put(x, 4, "<")
    put(1, 4, "^")
    put(1, 3, "^")
    put(1, 2, "^")
    put(1, 1, "^")

    return TrailLayout(width=14, height=5, cells=cells, spawn=(0, 0))


def render_dma_standalone(trail: TrailLayout | None = None, ram_len: int = 6) -> str:
    """Render the DMA as a standalone grid: `req` <- input (West), `dout` -> output
    (East), RAM ring on the South. For driving/observing the controller in isolation.
    """
    if trail is None:
        trail = dma_trail()
    dg = BlockGraph(cpu="DMA")
    dg.pipes = [
        Pipe("req", "I", E, "DMA", W),
        Pipe("dout", "DMA", E, "O", W),
        *RAM.pipes("DMA"),
    ]
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
        for y in range(Hi + 1, buf_top):
            c.put(col, y, ch)
    return c.render()
