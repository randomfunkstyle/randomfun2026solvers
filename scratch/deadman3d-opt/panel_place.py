"""Where do the four DOOM blocks go around one 2x2 panel cluster?

``panel_pack.py`` answered the question about the *panels*: how close can four
LM-75s stand, and what sets the floor (one free column, and a band of free rows
between the panel rows whose width is a counting argument about arrowheads).
This one answers the question about everything else — the router, the four
panel-less blocks and the twelve port pipes — because that is what the wall's
bounding box is actually made of.  The panels are 134x106 of it; the blocks are
four 166x85s; the wall was 493x305.

    python scratch/deadman3d-opt/panel_place.py

What the search space is
------------------------

An arrangement is a choice of

* where the four 166x85 logic blocks sit relative to the cluster (which side,
  which rows),
* which tile each block drives — free, because the composition is by index and
  nothing on the grid says which panel is which,
* which side of the cluster each block's three pipes arrive from, and
* the gutter ``gy``, the channel widths and the cluster's row.

The last four are searched numerically in ``d3_router`` itself (the cluster's
row and the twelve channel columns' order are solved on every build).  The first
two are a handful of shapes, and this file enumerates them and scores each by
the bounding box its parts force — before any pipe is drawn, because the two
*structural* rules below rule most of them out on paper and the ones they do not
rule out are worth building.

The two rules that decide it
----------------------------

**1. Depth ordering.**  ``len(addr) == len(data)`` and ``len(swap) >=
len(data) + 20`` are statements about where a panel's three terminals *are*.
Coming at a panel from the north-west they run ADDR (the row above the top
wall), DATA (the left wall), SWAP (below the bottom wall) — shallowest to
deepest, exactly the order the invariants want, and the 64-column freedom in
each terminal absorbs the rest.  Come at the same panel along the band between
the panel rows and it inverts: a north panel's SWAP arrowhead is the first thing
a pipe entering the band meets and its ADDR arrowhead the last, because ADDR has
to climb the gutter's tunnel to the north edge.  Measured on the real geometry
that is ADDR ~345 against SWAP ~246 — a 100-cell deficit against a 63-cell
freedom.  **A block cannot feed a panel through the band.**

**2. The leg owns the strip above its block.**  A router leg ends on the cell
above its block's north wall pointing south, so it holds that column and every
row it descends through.  A block east of the cluster would have to send its
pipes west across exactly that strip, and every row it could use is either the
leg's own or below it.  Combined with rule 1 (the band is out), **a block east
of the cluster cannot feed it at all.**

So every block goes west of the cluster, and the only question left is how to
stack four 166x85s and what that costs in rows.

    shape                                  parts bbox        wall     note
    2x2 blocks, cluster east of all four    466 x 170     499 x 223   is
    1x4 blocks in a row, cluster east       798 x 106     ~825 x 115  short, huge
    4x1 blocks stacked, cluster east        300 x 340     ~330 x 352  narrow, tall
    3+1 blocks, cluster east                466 x 255     ~500 x 290
    2x2 blocks, cluster between the rows    466 x 276     493 x 305   was

The 2x2 with the cluster *east* is the smallest by area and by height at once,
and height is the one that matters twice over: the wall hangs below a CPU that
is already 493 columns wide, so the wall's width is not the machine's, and its
height adds in full.

Why the winner is not smaller
-----------------------------

Its 170 rows of parts sit in a 223-row wall.  Thirteen of the rest are the
router and its lane, and the remaining forty are the cluster hanging below the
block rows.  The cluster's row is searched, and the smallest
one that closes every length invariant is what sets it: the south-west block's
three pipes cross the middle column along three rows *below* the whole wall, so
they reach the cluster from the south — rule 1's inversion again, in miniature —
and the cluster has to hang low enough that its south edge sits below that
return band for the order to right itself.

Routing that block through the gutter instead (climbing, so it arrives from the
north) removes the inversion and would give a 191-row wall.  It does not close:
the gutter then holds three crossings for each west block, and the west
channel's three columns are busy over every row between them, so the leg to the
*middle* south block — which has to cross that channel to reach a command cell
at ``mx + 16`` — has no free row to cross on.  Two spare rows can be made for
it, but then the leg's own descent crosses the second block's crossings instead.
That is the floor, and it is set by the west channel, not by the panels.

And it is a tick win as well as a smaller box
---------------------------------------------

``packed_probe.py``'s scene — 50 commands, two frames, every corner of every
tile — bisected for the first tick at which all four panels hold frame two, on
the reference engine:

    old packed wall  493x305   13,563 ticks
    new packed wall  499x223   13,014 ticks   -4.0%
    build_wall       (panels inside their blocks, 15-35 cell port pipes)
                     572x228   12,988 ticks   the floor

So the packing penalty — what it costs to move four panels out of four blocks
and into one screen — was 575 ticks and is now 26.  It is the pipes: the four
command legs went 14/308/237/531 to 9/166/123/280 and the twelve port pipes came
down with them, and a pipe's length is its latency.  This is the subsystem under
a probe workload, not the demo: the demo's own frame time is dominated by
per-tile backlog, which is a driver property and unchanged here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1 import d3_router as R  # noqa: E402
from randomfun2026solvers.lm1 import d3_unit  # noqa: E402

#: block width, block height, cluster width, cluster height
BW, BH = 166, 85
CW, CH = 2 * (d3_unit.PANEL_W + 2) + R.GUTTER_X, 2 * (d3_unit.PANEL_H + 2) + R.GUTTER_Y


def shapes() -> list[tuple[str, int, int, str]]:
    """Every arrangement's *parts* bounding box — blocks plus cluster, no slack.

    A lower bound rather than a wall: it charges nothing for the router, the
    channels or the return bands, which is the point — it says which shapes are
    even worth routing.
    """
    return [
        ("2x2 blocks, cluster between the block rows", 2 * BW + CW, 2 * BH + CH,
         "the old wall: the cluster's rows add to the blocks' in full"),
        ("2x2 blocks, cluster east of all four", 2 * BW + CW, max(2 * BH, CH),
         "the cluster overlaps both block rows"),
        ("4x1 blocks stacked, cluster east", BW + CW, max(4 * BH, CH),
         "narrowest, but four block heights add"),
        ("1x4 blocks in a row, cluster east", 4 * BW + CW, max(BH, CH),
         "shortest, but the width is the machine's then"),
        ("3+1 blocks, cluster east of the second column", 2 * BW + CW,
         max(3 * BH, BH + CH), "unbalanced, and no better than the 2x2"),
    ]


def main() -> int:
    print(f"block {BW}x{BH}, cluster {CW}x{CH}, four blocks + cluster = "
          f"{4 * BW * BH + CW * CH} cells\n")
    print(f"{'shape':<46} {'parts bbox':>12} {'area':>8}")
    for name, w, h, note in sorted(shapes(), key=lambda s: s[1] * s[2]):
        print(f"{name:<46} {w:>5} x {h:<4} {w * h:>8}   {note}")

    wall = R.build_packed_wall()
    print(f"\nbuilt wall {wall.width}x{wall.height} = {wall.width * wall.height}, "
          f"legs {wall.legs}")
    print(f"cluster at {wall.regions['cluster']}, panels {wall.panels}")
    slack = wall.width * wall.height - (4 * BW * BH + CW * CH)
    print(f"slack over the parts: {slack} cells "
          f"({100 * slack / (wall.width * wall.height):.0f}% of the wall)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
