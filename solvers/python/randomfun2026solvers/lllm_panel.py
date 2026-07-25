#!/usr/bin/env python3
"""The LM-75 panel harness for `little-little-little-man` (LLLM).

LLLM is a 16x16 display problem scored ``footprint-tick`` — ``max(w,h)^2 x
avg_ticks`` — so the panel room (18x18 with its walls) plus the pipes that drive
it *is* most of the bounding box before a single instruction of the interpreter
is written.  This module is that harness, and nothing else: a painter room that
speaks a tiny frame protocol, the three port pipes, and a standalone probe that
drives it straight from the input room so the whole thing can be proven on the
engine before any LLLM semantics exist.

It is a port of `snake_ring`'s harness (branch ``snake-finish``), which is
engine-proven against all 129 frames of the five public `snake` cases.  The
geometry is reproduced unchanged and re-verified here against LLLM's frames.

The painter's protocol
----------------------
One incoming pipe, carrying ``n, (addr, colour) x n`` per frame; the painter then
commits the frame itself with ``SWAP 1``.  ``SWAP 1`` copies next -> current
*preserving* both buffers and the cursor (SPEC.md, "The LM-75 display"), so a
frame is a **delta**: only the pixels that changed since the previous frame are
sent.  For LLLM that is a very good deal — after the first frame, a round usually
moves one little man, so the delta is two pixels (the vacated cell repainted in
its static colour, the new cell in 9) no matter how big the program is.

:func:`delta_stream` computes that stream from a case's frames, so the probe can
replay a case without interpreting anything.

Why the geometry is what it is
------------------------------
Five rules pin the layout; each one is a load error or a wrong frame if broken.

1. **The side a pipe connects to sets its function.**  Top wall = ADDR, left wall
   = DATA, bottom wall = SWAP.  The right wall and every corner are load errors,
   and two pipes on one side is a load error.  So the panel needs a free row
   above it, a free column to its west, and a free row below it.

2. **A pipe leaving a south wall has a forced first cell.**  That cell points
   south, so the pipe can only bend on the *second* row below the wall — which is
   why there is a two-row band between the painter's south wall and anything the
   pipes route around.

3. **ADDR must not arrive after its own DATA, and SWAP must not overtake the DATA
   writes still in flight.**  The display processes ADDR, then DATA, then SWAP
   within a tick, but only for values that have already arrived.  The painter's
   pixel loop sends ADDR two ticks before DATA and the commit ``SWAP`` a further
   12 cells (one loop lap) after the last DATA, so the pipe lengths must satisfy
   ``l_addr - 2 <= l_data`` and ``l_swap > l_data - 12``.  ADDR is therefore the
   shortest pipe and SWAP the longest.  :func:`attach_panel` asserts it.

4. **All three port pipes leave the painter's south wall, and ``s`` binds by
   Manhattan distance** to the nearest pipe source segment.  Two sends in the same
   interior column would bind to the same pipe, so the three sends must sit in
   three different columns — :data:`P_DATA`, :data:`P_ADDR`, :data:`P_SWAP`.
   That constraint, not the instruction count, is what shapes the painter room.

5. **The spawn is placed so the man's first act is the ``r`` of ``n``.**  Judging
   is a streaming compare of committed frames, so a spurious all-black frame
   before the first real one fails the case on frame 1.  The ``@`` therefore sits
   downstream of the commit and walks *into* the preamble rather than through it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, W
from randomfun2026solvers.plotter_block import build_display, pipe
from randomfun2026solvers.value_ring import stamp, walls

__all__ = [
    "PAINTER_IH",
    "PAINTER_IW",
    "PANEL_H",
    "PANEL_W",
    "P_ADDR",
    "P_DATA",
    "P_SWAP",
    "PROBE_PAINTER",
    "PROBE_PANEL",
    "attach_panel",
    "build_panel_probe",
    "delta_stream",
    "painter",
]

PANEL_W = PANEL_H = 16

# ── the painter ───────────────────────────────────────────────────────────────
#
# Interior 11x3.  The pixel loop is `counted_loop_horizontal(0, 0, "rsrs")`,
# entered heading west at (5,0):
#
#       > . . . m v . b r < <
#       ^ s r s r d . . @ . ^
#       . . . . . > . 1 s ^ .
#
# 12 cells per lap, so 12 painter ticks per (addr, colour) pair.  Walking the
# loop body westward from (4,1) executes `r` (addr) `s`@col3 `r` (colour)
# `s`@col1 — ADDR two ticks ahead of its DATA, which is rule 3's `- 2`.
#
# The two loop sends must sit in **different columns** (rule 4), and the commit's
# `s` is pushed out to column 8 on the row below so that it, too, binds its own
# pipe.  `1` then `s` is `SWAP 1`: preserve, so the next frame is a delta.
#
# The spawn sits at (8,1) heading east — into `^` at (10,1), up and back west
# into the `r b` preamble — so the man's first act is to read `n` rather than to
# commit a black frame (rule 5).
PAINTER_IW, PAINTER_IH = 11, 3
P_DATA, P_ADDR, P_SWAP = 1, 3, 8  # interior columns of the three sends


def painter() -> Circuit:
    """The 11x3 painter room: `n, (addr, colour) x n`, then `SWAP 1`."""
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


# ── the panel and its three port pipes ────────────────────────────────────────
def _stamp_panel(g: Circuit, dx: int, dy: int) -> None:
    """Stamp the 16x16 LM-75 wall box with its top-left *wall* cell at (dx, dy).

    `plotter_block.build_display` grew ``panel_w`` / ``panel_h`` keywords on the
    ``snake-finish`` branch; on trunk it is still hard-wired to `plotter`'s own
    32x24 panel.  Prefer the shared builder whenever it can size itself, and fall
    back to the four-line stamp otherwise — SPEC.md fixes the glyphs either way
    ('+' corners, '=' horizontal walls, ':' vertical walls).
    """
    try:
        build_display(g, dx, dy, panel_w=PANEL_W, panel_h=PANEL_H)
        return
    except TypeError:
        pass
    w, h = PANEL_W + 2, PANEL_H + 2
    for x in range(dx, dx + w):
        for y in (dy, dy + h - 1):
            g.set(x, y, "+" if x in (dx, dx + w - 1) else "=")
    for y in range(dy + 1, dy + h - 1):
        g.set(dx, y, ":")
        g.set(dx + w - 1, y, ":")


def attach_panel(
    g: Circuit, painter_x: int, painter_y: int, panel_x: int, panel_y: int
) -> dict[str, int]:
    """Stamp the panel and wire the painter's three ports to it.

    ``(painter_x, painter_y)`` is the painter's **interior** origin — the cell its
    room's north-west interior corner occupies, i.e. what :func:`stamp` was given.
    ``(panel_x, panel_y)`` is the panel's **wall** origin, its '+' corner; the
    panel occupies ``18x18`` cells from there.

    Draws, from the painter's south wall (rules 1, 2 and 4):

    * ADDR — straight down from interior column :data:`P_ADDR` into the panel's
      top wall, which must therefore be reachable in that column;
    * DATA — down, then west in the second row below the wall, then south down the
      free column ``panel_x - 1`` into the left wall at ``panel_y + 2``;
    * SWAP — down, then east in that same row, round the panel's east side in
      column ``panel_x + 18`` and back west along the row ``panel_y + 18`` into
      the bottom wall under interior column :data:`P_SWAP`.

    Returns ``{"addr": …, "data": …, "swap": …}``, the pipes' capacities in cells,
    and raises :class:`ValueError` if those lengths would deliver out of order
    (rule 3).  Everything is drawn through :class:`Circuit`, so an overlap with a
    worker already on the grid raises :class:`~randomfun2026solvers.circuit.Collision`.
    """
    px, py, dx, dy = painter_x, painter_y, panel_x, panel_y
    _stamp_panel(g, dx, dy)

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

    lens = {"addr": l_addr, "data": l_data, "swap": l_swap}
    if not (l_addr - 2 <= l_data and l_swap > l_data - 12):
        raise ValueError(f"pipe lengths deliver out of order: {lens}")
    return lens


# ── the standalone probe ──────────────────────────────────────────────────────
PROBE_PAINTER = (2, 1)  # painter interior origin
PROBE_PANEL = (3, 7)    # panel *wall* origin
PROBE_INPUT = (16, 0)   # input room's north-west *wall* cell


def build_panel_probe() -> tuple[list[str], dict[str, int]]:
    """Display + painter + the three port pipes, fed by the input room.

    The input pipe stands in for the interpreter, so the probe speaks the
    painter's exact protocol and a correct frame here means the panel harness is
    right — 22x26, whatever the worker eventually turns out to be.
    """
    g = Circuit(22, 26)
    px, py = PROBE_PAINTER
    dx, dy = PROBE_PANEL
    ix, iy = PROBE_INPUT

    stamp(g, px, py, painter().rows())
    walls(g, px, py, PAINTER_IW, PAINTER_IH)
    lens = attach_panel(g, px, py, dx, dy)

    # the input room, feeding the painter's single incoming pipe on its east wall
    stamp(g, ix, iy, ["+-+", "|I|", "+-+"])
    pipe(g, [(ix - 1, iy + 1), (ix - 2, iy + 1)], into=(px + PAINTER_IW, py))

    return [r.rstrip() for r in g.rows()], lens


# ── the delta stream the painter is driven with ───────────────────────────────
def delta_stream(frames: list[list[str]]) -> list[int]:
    """Flatten a list of 16x16 frames into the painter's input stream.

    Each frame is 16 strings of 16 hex digits (the shape a problem JSON's
    ``frames`` uses).  Emits ``n`` then ``n`` ``(addr, colour)`` pairs per frame,
    the pairs being exactly the pixels that differ from the previous frame — the
    previous frame being all-black before the first one, since both LM-75 buffers
    start filled with colour 0.  Addresses are ``row * 16 + column``, pairs in
    row-major order.
    """
    prev = [[0] * PANEL_W for _ in range(PANEL_H)]
    out: list[int] = []
    for f in frames:
        if len(f) != PANEL_H or any(len(r) != PANEL_W for r in f):
            raise ValueError(f"frame is not {PANEL_W}x{PANEL_H}: {f}")
        pairs: list[tuple[int, int]] = []
        for y, row in enumerate(f):
            for x, ch in enumerate(row):
                c = int(ch, 16)
                if c != prev[y][x]:
                    pairs.append((y * PANEL_W + x, c))
                    prev[y][x] = c
        out.append(len(pairs))
        for addr, colour in pairs:
            out += [addr, colour]
    return out


def replay(stream: list[int]) -> list[list[str]]:
    """Inverse of :func:`delta_stream`: the frames a ``SWAP 1`` painter commits.

    The next buffer starts black and is never cleared (``SWAP 1`` preserves it),
    so this is the model the round-trip test checks the stream against.
    """
    buf = [[0] * PANEL_W for _ in range(PANEL_H)]
    frames: list[list[str]] = []
    i = 0
    while i < len(stream):
        n = stream[i]
        i += 1
        for _ in range(n):
            addr, colour = stream[i], stream[i + 1]
            i += 2
            buf[addr // PANEL_W][addr % PANEL_W] = colour
        frames.append(["".join(f"{p:x}" for p in row) for row in buf])
    return frames


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
