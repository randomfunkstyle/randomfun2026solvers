"""Two-man System-on-Chip layout: a CPU core + a DMA/memory man on one bus.

Why two men: the engine routes s/r to the *nearest* pipe, so one man touching several
same-direction pipes (ROM+RAM+I/O) goes UNSAT under any placement. The fix:

- **CORE** talks only over a bus -- `req` (its single outgoing pipe -> s routes there
  trivially) and `resp` (read with `R`, which takes from ANY ready incoming pipe and is
  exempt from the nearest rule). So the core is always routable regardless of program.
- **DMA** owns memory-mapped I/O (and, later, RAM/ROM). It is a fixed dispatch man; its
  request-reads use nearest `r` kept spatially near the `req` pipe (fixed 2-word command
  frames keep both reads adjacent at the top), and its per-command sends live in separate
  `if3` arms -> zoned -> routable, and solved once.

`render_soc` places the CORE room on top, the DMA below, straight bus pipes in the gap
(the core's req/resp columns are free, so we align them to the DMA's solved columns), and
the DMA's I west / O east. Only the DMA needs a routing solve.
"""

from __future__ import annotations

from .blockspec import BlockGraph, E, N, Pipe, S, W
from .router import Canvas, solve_attachments
from .trail import TrailLayout

# bus command opcodes (fixed 2-word frames: cmd, value; value is a dummy when unused)
IN_CMD = 0
OUT_CMD = 1


def _place(canvas: Canvas, trail: TrailLayout, oy: int) -> None:
    for c in trail.cells:
        canvas.put(c.x, c.y + oy, c.char)


def render_soc(core_trail: TrailLayout, dma_trail: TrailLayout, bus_gap: int = 3) -> str:
    """Render CORE (top) + DMA (bottom) joined by a req/resp bus.

    The CORE's pipes are `req` (out, S) and `resp` (in, S); the DMA's are `req` (in, N),
    `resp` (out, N), `din` (in, W = input), `dout` (out, E = output).
    """
    CWi, CHi = core_trail.width, core_trail.height
    DWi, DHi = dma_trail.width, dma_trail.height

    dg = BlockGraph(cpu="DMA")
    dg.pipes = [
        Pipe("req", "CORE", S, "DMA", N),
        Pipe("resp", "DMA", N, "CORE", S),
        Pipe("din", "I", E, "DMA", W),
        Pipe("dout", "DMA", E, "O", W),
    ]
    dseg = solve_attachments(dg, dma_trail)  # DMA-relative attach cells

    dtop = CHi + bus_gap + 1  # DMA interior top row; gap rows are CHi+1 .. dtop-2
    c = Canvas()
    c.rect(-1, -1, CWi, CHi)                 # CORE room
    _place(c, core_trail, 0)
    c.rect(-1, dtop - 1, DWi, dtop + DHi)    # DMA room
    _place(c, dma_trail, dtop)

    # straight bus pipes in the gap: req down, resp up (walls stay intact)
    reqx = dseg["req"][0]
    respx = dseg["resp"][0]
    for y in range(CHi + 1, dtop - 1):
        c.put(reqx, y, "v")   # CORE -> DMA
        c.put(respx, y, "^")  # DMA -> CORE

    # DMA I room (west) + O room (east) on the DMA's solved attach rows
    _, inrow = dseg["din"]
    _, outrow = dseg["dout"]
    iy = dtop + inrow
    c.rect(-6, iy - 1, -4, iy + 1)
    c.put(-5, iy, "I")
    c.put(-3, iy, ">")
    c.put(-2, iy, ">")
    oy = dtop + outrow
    c.rect(DWi + 3, oy - 1, DWi + 5, oy + 1)
    c.put(DWi + 4, oy, "O")
    c.put(DWi + 1, oy, ">")
    c.put(DWi + 2, oy, ">")
    return c.render()
