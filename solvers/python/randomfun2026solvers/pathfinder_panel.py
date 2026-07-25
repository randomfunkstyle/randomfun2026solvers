#!/usr/bin/env python3
"""The `pathfinder` display block: painter room + 16x16 LM-75 + the three ports.

`pathfinder` is scored ``max(w,h)^2 x avg_ticks`` on a 16x16 panel, so the panel
harness is the floor of the bounding box and every row it wastes is squared.  This
module is that harness, as a stampable block plus a standalone probe grid.

Two things make it different from `snake_ring`'s equivalent block (22x26):

**A run-length protocol.**  ``snake``'s painter speaks ``n, (addr, colour) x n``
and commits itself.  `pathfinder`'s setup frame paints all 256 pixels, and making
the worker emit 256 *addresses* costs it six glyphs a pixel.  So the painter here
speaks runs — the display's DATA write auto-advances the cursor, so a run of *n*
consecutive pixels needs one address and *n* colours::

    loop forever:
        r -> n
        n == 0 :  send 1 to SWAP                      # commit; SWAP 1 preserves
        n > 0  :  r -> addr ; send addr to ADDR       # both buffers *and* the
                  n x { r -> colour ; send to DATA }  # cursor, so frames are deltas

The full-board setup frame is therefore the stream ``256, 0, c0..c255, 0`` and a
two-pixel delta is ``1, a1, col1, 1, a2, col2, 0``.

``r b a`` is the whole dispatch: ``b`` loads the count, ``a`` turns
counter-clockwise when BP > 0 and goes straight when it is 0, so ``n == 0`` walks
on into the commit lane and ``n > 0`` peels off into the run lane.  The counted
loop that follows *re-uses the same BP* — it tests before its body and decrements
on the return leg, so the count is loaded once and consumed once.

**Three walls instead of one.**  ``snake`` hangs all three port pipes off the
painter's south wall, which costs a two-row band between the painter and the panel
(a pipe leaving a south wall has a forced first cell pointing south, so it can only
bend on the *second* row below the wall).  Here the painter's south wall is flush
against the panel's top wall and the ports leave sideways:

===== ============ ==================================================
port  painter wall route
===== ============ ==================================================
ADDR  east, row 1  two cells east, then straight down into the top wall
DATA  west, row 2  two cells west, then down the west gutter into the left wall
SWAP  east, row 0  east over ADDR's descent, down the east gutter, west under the panel
in    west, row 0  the worker's pipe terminates one cell west of the wall
===== ============ ==================================================

That removes the band entirely: ``5 (painter room) + 18 (panel) + 1 (the row the
SWAP pipe needs under the panel) = 24`` rows, against ``snake``'s
``5 + 2 + 18 + 1 = 26``.  Width is ``1 (DATA gutter) + 18 (panel) + 1 (SWAP
gutter) = 20``.

Geometry rules this layout is pinned by, each of which is a load error if broken:

1. The display's **top** wall is ADDR, its **left** wall DATA, its **bottom**
   SWAP.  The right wall and every corner are load errors — hence the DATA gutter
   west of the panel, the SWAP gutter east of it, and terminals that stop one
   column short of a corner.
2. A pipe's first cell points **away** from the wall it leaves and can only bend
   on the second cell.  An east-wall pipe therefore descends two columns east of
   the wall, which is what caps the painter interior at 11 columns: the descent
   column ``px + 11 + 3`` must still be inside the panel's top wall.
3. ADDR and SWAP share the east wall, and SWAP has to *cross* ADDR's descent
   column — so SWAP leaves on the row **above** ADDR's.  There is no ordering of
   those two rows that lets a third pipe out to the east, which is why the
   worker's pipe comes in on the west wall.
4. All three sends compete by Manhattan distance to their pipe's source cell, so
   the sends are placed to win by at least one: ``s@DATA`` west and low,
   ``s@ADDR`` mid and on ADDR's own row, ``s@SWAP`` east and on the top row.
   :func:`send_bindings` recomputes the whole table from the geometry and the
   fast tests assert every margin is strictly positive.
5. The spawn sits on the eastbound street two cells before ``r``, so the man's
   first act is a **read**.  A painter that committed before its first read would
   put a black frame at the head of the stream and fail the compare instantly.

**Why north and not west.**  Putting the painter in the strip *west* of the panel
buys the last row back — the panel then needs only one row above (ADDR) and one
below (SWAP), so 20 rows — but it pays for it in columns: one worker lane, the
painter room, the two-column DATA gutter and the 18-column panel.  Even a painter
turned on its side (interior 3x11, room 5x13) lands at 26x20, and this 11x3 one at
34x20.  Both lose to 20x24 on ``max(w,h)``, which is the term that is squared, so
the west variant is not built here.

Pipe-length ordering is re-derived rather than inherited — the protocol is
different, so ``snake``'s ``l_addr - 2 <= l_data`` does not carry over.  See
:func:`delivery_ok` for the four inequalities and the tick gaps they are measured
against.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, W
from randomfun2026solvers.plotter_block import build_display, pipe
from randomfun2026solvers.value_ring import stamp, walls

__all__ = [
    "BLOCK_H",
    "BLOCK_W",
    "GAP_ADDR_DATA",
    "GAP_DATA_ADDR",
    "GAP_DATA_SWAP",
    "GAP_SWAP_DATA",
    "PAINTER_IH",
    "PAINTER_IW",
    "PANEL_H",
    "PANEL_W",
    "P_ADDR",
    "P_DATA",
    "P_SWAP",
    "build_block",
    "build_probe",
    "delivery_ok",
    "expected_frames",
    "free_cells",
    "painter",
    "send_bindings",
    "stream_for",
]

PANEL_W = PANEL_H = 16

# ── the painter ───────────────────────────────────────────────────────────────
#
# Interior 11x3.  Row 2 is the eastbound street, row 0 the westbound return, and
# the counted loop lives in the four west columns of rows 0-1:
#
#       > . m v < . . . . s <
#       ^ s r d ^ . s r < . .
#       . . . > . @ r b a 1 ^
#
# One lap of the pixel loop is 8 cells, so a run costs 8 ticks a pixel plus 15 to
# turn the run around.  Both lanes rejoin the westbound row at ``(4,0)``, which is
# west of ``s@SWAP`` — the run lane must never walk over the commit send.
PAINTER_IW, PAINTER_IH = 11, 3
P_DATA = (1, 1)   # interior cell of the send into DATA (inside the counted loop)
P_ADDR = (6, 1)   # interior cell of the send into ADDR
P_SWAP = (9, 0)   # interior cell of the send into SWAP

# Interior rows the four pipes attach at, and which wall they use.
IN_ROW, SWAP_ROW = 0, 0   # west wall / east wall
ADDR_ROW = 1              # east wall
DATA_ROW = 2              # west wall

# Ticks the man spends between consecutive sends.  Measured off the walk above;
# `test_pathfinder_panel.py` re-derives them by walking the grid, so they cannot
# drift away from the glyphs.
GAP_ADDR_DATA = 7    # s@ADDR   -> the run's first s@DATA
GAP_DATA_DATA = 8    # s@DATA   -> s@DATA (one lap of the pixel loop)
GAP_DATA_ADDR = 15   # last s@DATA of a run -> the next run's s@ADDR
GAP_DATA_SWAP = 17   # last s@DATA of a frame -> s@SWAP
GAP_SWAP_ADDR = 16   # s@SWAP   -> the next frame's s@ADDR
GAP_SWAP_DATA = GAP_SWAP_ADDR + GAP_ADDR_DATA


def painter() -> Circuit:
    """The 11x3 run-length painter.  Entered nowhere: it owns its own spawn."""
    c = Circuit(PAINTER_IW, PAINTER_IH)

    # the pixel loop: `d` tests BP before the body and `m` decrements on the way
    # back, so a run of n costs exactly n laps and leaves BP at 0.
    exit_ = c.counted_loop_horizontal(0, 0, "rs")
    assert exit_ == (3, 2), exit_

    # eastbound street: spawn, read n, load BP, dispatch on it
    c.set(3, 2, ">")
    c.set(4, 2, " ")
    c.set(5, 2, "@")
    c.run(6, 2, "rba")

    # run lane (BP > 0): read the address, ship it, walk into the loop's entry
    c.set(8, 1, "<")
    c.run(7, 1, "rs", d=W)
    c.set(5, 1, " ")
    c.set(4, 1, "^")
    c.set(4, 0, "<")

    # commit lane (BP == 0): `1` then s@SWAP, rejoining the westbound row east of
    # where the run lane joins it, so the run lane never walks over this send.
    c.set(9, 2, "1")
    c.set(10, 2, "^")
    c.set(10, 1, " ")
    c.set(10, 0, "<")
    c.set(9, 0, "s")
    c.blanks(5, 0, 4)
    return c


# ── the block ─────────────────────────────────────────────────────────────────
#
# Block-relative geometry, all derived from these four numbers.  The panel's west
# wall is column 1 because the DATA pipe needs exactly one gutter column west of
# it; the SWAP gutter is column 19 for the same reason on the far side.
BLOCK_W, BLOCK_H = 20, 24
PANEL_AT = (1, 5)        # panel *wall* origin, block-relative
PAINTER_AT = (3, 1)      # painter *interior* origin, block-relative
DATA_ENTRY = 8           # DATA joins the panel's left wall at dy + DATA_ENTRY

IN_SIDE = "west"
IN_CELL = (1, 1)         # block-relative; the worker's pipe must END here


def send_bindings(ox: int = 0, oy: int = 0) -> dict[str, dict[str, int]]:
    """Manhattan distance from each send to each outgoing pipe's source cell.

    The engine binds ``s`` to the nearest source segment (ties by reading order),
    so this table *is* the correctness argument for the layout.  Returned keyed by
    send, then by port; ``build_block`` and the fast tests both check that the
    intended port is a strict minimum.
    """
    px, py = ox + PAINTER_AT[0], oy + PAINTER_AT[1]
    east, west = px + PAINTER_IW, px - 1
    src = {
        "addr": (east + 1, py + ADDR_ROW),
        "swap": (east + 1, py + SWAP_ROW),
        "data": (west - 1, py + DATA_ROW),
    }
    sends = {"addr": P_ADDR, "data": P_DATA, "swap": P_SWAP}
    return {
        name: {
            port: abs(px + sx - cx) + abs(py + sy - cy) for port, (cx, cy) in src.items()
        }
        for name, (sx, sy) in sends.items()
    }


def delivery_ok(l_addr: int, l_data: int, l_swap: int) -> bool:
    """Do the three pipe lengths deliver the protocol in order?

    A value sent on tick ``T`` into a pipe of ``L`` cells is processed by the
    display on tick ``T + L - 1``, and within one tick the display processes
    **ADDR, then DATA, then SWAP**.  Four things can go wrong, and the run-length
    protocol makes them different from ``snake``'s:

    * a run's ADDR arriving *after* its own first colour — same tick is fine,
      ADDR is processed first: ``l_addr - l_data <= GAP_ADDR_DATA``;
    * SWAP overtaking colours still in flight — same tick is fine again, DATA is
      processed before SWAP: ``l_data - l_swap <= GAP_DATA_SWAP``;
    * the **next** run's ADDR overtaking this run's colours, which would move the
      cursor out from under them.  Here the same tick is *not* fine (ADDR runs
      first), so the bound is strict: ``l_data - l_addr < GAP_DATA_ADDR``;
    * the next *frame's* colours overtaking the SWAP, which would fold them into
      the frame being committed.  Strict for the same reason, with DATA processed
      before SWAP: ``l_swap - l_data < GAP_SWAP_DATA``.

    An early ADDR relative to a SWAP is harmless: ``SWAP 1`` preserves the cursor.
    Blocking only ever pushes a *later* send later, so every violation is
    structural and this test is sufficient.
    """
    return (
        l_addr - l_data <= GAP_ADDR_DATA
        and l_data - l_swap <= GAP_DATA_SWAP
        and l_data - l_addr < GAP_DATA_ADDR
        and l_swap - l_data < GAP_SWAP_DATA
    )


def build_block(g: Circuit, ox: int, oy: int) -> dict:
    """Stamp painter + walls + panel + the three port pipes at ``(ox, oy)``.

    Returns the block's contract: its bounding box, where the worker's pipe has to
    terminate, and the three pipe lengths.
    """
    dx, dy = ox + PANEL_AT[0], oy + PANEL_AT[1]
    px, py = ox + PAINTER_AT[0], oy + PAINTER_AT[1]
    east, west = px + PAINTER_IW, px - 1

    stamp(g, px, py, painter().rows())
    walls(g, px, py, PAINTER_IW, PAINTER_IH)
    build_display(g, dx, dy, panel_w=PANEL_W, panel_h=PANEL_H)

    # ADDR: east wall, then straight down the first column it is allowed to bend
    # in, into the panel's top wall.
    addr_col = east + 2
    l_addr = pipe(
        g,
        [(east + 1, py + ADDR_ROW), (addr_col, py + ADDR_ROW), (addr_col, dy - 1)],
        into=(addr_col, dy),
    )
    # DATA: west wall, down the gutter, east into the panel's left wall.  The
    # entry row is a free parameter and is chosen to land l_data in the middle of
    # the window `delivery_ok` allows.
    l_data = pipe(
        g,
        [(west - 1, py + DATA_ROW), (west - 2, py + DATA_ROW), (west - 2, dy + DATA_ENTRY)],
        into=(dx, dy + DATA_ENTRY),
    )
    # SWAP: east wall one row *above* ADDR's, over ADDR's descent, down the east
    # gutter, then west under the panel to the last column before its corner.
    gutter = ox + BLOCK_W - 1
    under = dy + PANEL_H + 2
    swap_col = dx + PANEL_W
    l_swap = pipe(
        g,
        [
            (east + 1, py + SWAP_ROW),
            (gutter, py + SWAP_ROW),
            (gutter, under),
            (swap_col, under),
        ],
        into=(swap_col, under - 1),
    )

    bindings = send_bindings(ox, oy)
    for name, dists in bindings.items():
        best = min(dists, key=lambda p: (dists[p], p))
        if best != name or sorted(dists.values())[1] == dists[name]:
            raise ValueError(f"s@{name.upper()} binds {best}, distances {dists}")
    if not delivery_ok(l_addr, l_data, l_swap):
        raise ValueError(
            f"pipe lengths deliver out of order: addr={l_addr} data={l_data} swap={l_swap}"
        )
    return {
        "w": BLOCK_W,
        "h": BLOCK_H,
        "in_cell": (ox + IN_CELL[0], oy + IN_CELL[1]),
        "in_side": IN_SIDE,
        "l_addr": l_addr,
        "l_data": l_data,
        "l_swap": l_swap,
        "panel_at": (dx, dy),
        "painter_at": (px, py),
        "bindings": bindings,
    }


def free_cells(ox: int = 0, oy: int = 0) -> list[tuple[int, int]]:
    """Cells inside the bounding box a parent may still route a pipe through.

    Blank *interiors* do not count: a blank inside the painter is a cell its man
    walks, and a blank inside the panel is display memory.  What is left is the
    genuinely spare space — the west gutter above and below the DATA pipe, the
    strip between the painter's east wall and the panel's top-right corner, and
    the row under the panel either side of the SWAP run.
    """
    g = Circuit(ox + BLOCK_W, oy + BLOCK_H)
    info = build_block(g, ox, oy)
    dx, dy = info["panel_at"]
    px, py = info["painter_at"]
    inside = {
        (x, y)
        for y in range(dy + 1, dy + PANEL_H + 1)
        for x in range(dx + 1, dx + PANEL_W + 1)
    } | {
        (x, y)
        for y in range(py, py + PAINTER_IH)
        for x in range(px, px + PAINTER_IW)
    }
    return [
        (x, y)
        for y in range(oy, oy + BLOCK_H)
        for x in range(ox, ox + BLOCK_W)
        if g.get(x, y) == " " and (x, y) not in inside
    ]


# ── the probe ─────────────────────────────────────────────────────────────────
#
# The input room stands in for the worker, so the probe speaks the painter's exact
# protocol.  It sits west of the block because that is the wall the worker's pipe
# has to use (rule 3 above), and its pipe terminates on `in_cell`.
PROBE_OX, PROBE_OY = 4, 0


def build_probe() -> list[str]:
    """A complete runnable grid: the block plus an input room driving the painter."""
    g = Circuit(PROBE_OX + BLOCK_W, BLOCK_H)
    info = build_block(g, PROBE_OX, PROBE_OY)
    stamp(g, 0, 0, ["+-+", "|I|", "+-+"])
    pipe(g, [(3, 1), (info["in_cell"][0] - 1, 1), info["in_cell"]],
         into=(info["in_cell"][0] + 1, 1))
    return [r.rstrip() for r in g.rows()]


# ── driving it ────────────────────────────────────────────────────────────────
def stream_for(runs: list[list[tuple[int, list[int]]]]) -> list[int]:
    """Encode frames-of-runs into the painter's input stream.

    ``runs[f]`` is frame *f* as a list of ``(addr, colours)`` runs; each frame ends
    with the ``0`` that commits it.
    """
    out: list[int] = []
    for frame in runs:
        for addr, colours in frame:
            out += [len(colours), addr, *colours]
        out.append(0)
    return out


def expected_frames(runs: list[list[tuple[int, list[int]]]]) -> list[list[str]]:
    """The front buffer after each commit, as the engine reports it (hex per pixel).

    ``SWAP 1`` preserves the next buffer, so frames accumulate: this models the
    panel exactly the way ``lm1/display.py`` does, one frame per committing ``0``.
    """
    buf = [0] * (PANEL_W * PANEL_H)
    frames: list[list[str]] = []
    for frame in runs:
        for addr, colours in frame:
            for i, colour in enumerate(colours):
                buf[(addr + i) % len(buf)] = colour
        frames.append(
            ["".join(f"{p:x}" for p in buf[y * PANEL_W : (y + 1) * PANEL_W])
             for y in range(PANEL_H)]
        )
    return frames


def _cli() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", type=Path, help="write the probe grid here")
    ap.add_argument("--painter", action="store_true", help="print the painter interior")
    args = ap.parse_args()
    if args.painter:
        print(painter().ruler())
        return
    g = Circuit(PROBE_OX + BLOCK_W, BLOCK_H)
    info = build_block(g, PROBE_OX, PROBE_OY)
    print(
        f"# block {info['w']}x{info['h']}  in_cell={info['in_cell']} ({info['in_side']})"
        f"  ADDR {info['l_addr']} / DATA {info['l_data']} / SWAP {info['l_swap']} cells",
        file=sys.stderr,
    )
    text = "\n".join(build_probe()) + "\n"
    if args.man:
        args.man.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    _cli()
