#!/usr/bin/env python3
"""A dataflow ring machine for `snake` — no CPU, no ISA, no ROM.

`snake` is scored `max(w,h)^2 x avg_ticks` on a 16x16 display, so the 18x18 panel
room is most of the bounding box and the panel harness is the dominant term.  The
game state is tiny (the longest snake across the public cases is **6** cells), so
the body lives as a handful of values circulating in a pipe ring rather than in a
tape or a bitmap: a ring *is* a FIFO, which is exactly what a snake body is.

This module is built bottom-up and each stage is a runnable, engine-checked grid:

* :func:`build_panel_probe` — display + painter + the three port pipes, driven
  straight from the input room.  Pins the panel footprint floor and proves the
  port geometry and pipe-length timing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, W
from randomfun2026solvers.plotter_block import build_display, pipe
from randomfun2026solvers.value_ring import stamp, walls

__all__ = ["PANEL_H", "PANEL_W", "build_panel_probe", "painter"]

PANEL_W = PANEL_H = 16

# ── the painter ───────────────────────────────────────────────────────────────
#
# Protocol on its single incoming pipe: `n, (addr, colour) x n`, then it commits
# the frame itself with `SWAP 1`.  `SWAP 1` copies next -> current *preserving*
# both buffers and the cursor (`lm1/display.py`), so a frame is a **delta**: after
# the first paint the worker only ever repaints the pixels that changed — two per
# tick (tail black, head green), one per fruit spawn, and the body on death.
#
# Interior 11x3.  The pixel loop is `counted_loop_horizontal(0, 0, "rsrs")`,
# entered heading west at (5,0):
#
#       > . . . m v . b r < <
#       ^ s r s r d . . @ . ^
#       . . . . . > . 1 s ^ .
#
# 12 cells per pixel.  The two loop sends must sit in **different columns**: all
# three port pipes leave the south wall and `s` binds by Manhattan distance to the
# pipe's source segment, so two sends in one column would bind to the same pipe.
# Walking the body westward puts `s@ADDR` at column 3 and `s@DATA` at column 1,
# and `s@SWAP` is pushed out to column 8 on the row below.
#
# The spawn sits at (8,1) heading east — into `^` at (10,1), up and back west into
# the `r b` preamble — so the man's first act is to read `n`, not to commit a black
# frame (which would fail the streaming compare on its first frame).
PAINTER_IW, PAINTER_IH = 11, 3
P_DATA, P_ADDR, P_SWAP = 1, 3, 8  # interior columns of the three sends


def painter() -> Circuit:
    c = Circuit(PAINTER_IW, PAINTER_IH)
    exit_ = c.counted_loop_horizontal(0, 0, "rsrs")
    assert exit_ == (5, 2), exit_

    # commit: `1` then s@SWAP, far east of both loop sends so it binds SWAP
    c.set(5, 2, ">")
    c.run(7, 2, "1s")
    c.set(9, 2, "^")
    c.set(9, 1, " ")

    # preamble, walked west into the loop entry at (5,0)
    c.set(9, 0, "<")
    c.run(8, 0, "rb", d=W)
    c.set(6, 0, " ")

    # spawn: east into the riser, then west over itself into `r b`
    c.set(8, 1, "@")
    c.set(10, 1, "^")
    c.set(10, 0, "<")
    return c


# ── the panel probe ───────────────────────────────────────────────────────────
#
# Geometry, and why it is what it is:
#
# * the display's **top** wall is ADDR, its **left** wall DATA, its **bottom**
#   SWAP; the right wall and every corner are load errors.  So the panel needs a
#   free row above, a free column west and a free row below.
# * a pipe leaving a room's south wall has a *forced* first cell pointing south,
#   so it can only bend on the second row below the wall — hence the two-row band
#   between the painter and the panel.
# * ADDR must not arrive after its own DATA, and SWAP must not overtake the DATA
#   writes still in flight, so ADDR is the shortest pipe and SWAP the longest.
#   With ADDR = 2 and the sends 2 ticks apart that is satisfied with slack.
PROBE_PAINTER = (2, 1)  # painter interior origin
PROBE_PANEL = (3, 7)    # panel *wall* origin


def build_panel_probe() -> tuple[list[str], dict[str, int]]:
    """Display + painter + the three port pipes, fed by the input room.

    The input pipe stands in for the worker, so the probe speaks the painter's
    exact protocol and a correct frame here means the panel harness is right.
    """
    g = Circuit(22, 26)
    px, py = PROBE_PAINTER
    dx, dy = PROBE_PANEL

    stamp(g, px, py, painter().rows())
    walls(g, px, py, PAINTER_IW, PAINTER_IH)
    build_display(g, dx, dy, panel_w=PANEL_W, panel_h=PANEL_H)

    south = py + PAINTER_IH + 1  # first free row below the painter's south wall
    l_addr = pipe(g, [(px + P_ADDR, south), (px + P_ADDR, south + 1)],
                  into=(px + P_ADDR, dy))
    l_data = pipe(g, [(px + P_DATA, south), (px + P_DATA, south + 1),
                      (dx - 1, south + 1), (dx - 1, dy + 2)], into=(dx, dy + 2))
    l_swap = pipe(g, [(px + P_SWAP, south), (px + P_SWAP, south + 1),
                      (dx + PANEL_W + 2, south + 1),
                      (dx + PANEL_W + 2, dy + PANEL_H + 2),
                      (px + P_SWAP, dy + PANEL_H + 2)],
                  into=(px + P_SWAP, dy + PANEL_H + 1))

    stamp(g, 16, 0, ["+-+", "|I|", "+-+"])
    pipe(g, [(15, 1), (14, 1)], into=(px + PAINTER_IW, 1))

    lens = {"addr": l_addr, "data": l_data, "swap": l_swap}
    if not (l_addr - 2 <= l_data and l_swap > l_data - 12):
        raise ValueError(f"pipe lengths deliver out of order: {lens}")
    return [r.rstrip() for r in g.rows()], lens


def _cli() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", nargs="?", default="panel-probe",
                    choices=["panel-probe", "painter"])
    ap.add_argument("--out", type=Path, help="write the grid here")
    args = ap.parse_args()
    if args.what == "painter":
        print(painter().ruler())
        return
    rows, lens = build_panel_probe()
    print(f"# ADDR {lens['addr']} / DATA {lens['data']} / SWAP {lens['swap']} cells",
          file=sys.stderr)
    text = "\n".join(rows) + "\n"
    if args.out:
        args.out.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    _cli()
