#!/usr/bin/env python3
"""`pathfinder` as a dataflow ring machine -- floorplan and pipe binding.

**Status: this module does not yet emit a grid.**  The binding schemes below
are worked out and testable, and :mod:`pathfinder_place` will pour most of the
program into a room, but it has not been driven to a complete placement.  What
is settled, and what is not, is spelled out at the bottom.

The machine itself is :mod:`pathfinder_prog`: 51 blocks of straight glyph runs
over one man, an 18-word ring, a spill FIFO ``F`` and a scratch FIFO ``G``.
That program is validated on all seven public cases at the op level: 596 glyph
cells, 554 tokens, 293 of them pipe ops.

Everything below is the *layout* problem, which for this program is dominated
by one thing.

## Nearest-pipe binding is the whole floorplan

`s`/`r` bind to the **nearest** pipe -- nearest, not nearest-ready -- so every
one of the 293 pipe ops has to be standing in the right place.  The engine's
rule (``fast_littleman._bind_pipe_ops``) measures Manhattan distance to a
*single cell*: an outgoing pipe's ``src_attach`` and an incoming pipe's
``dst_attach`` are the border cells the pipe joins, not segments.  So an anchor
is a point on a wall, and there is no way to give a pipe a long frontage.

### The counts that decide it

At the **op** level -- which is the level that matters, because ``sr`` and
``rr`` are different glyphs bound by different partitions -- the program has
293 pipe ops in 202 same-block transitions between *different* ops:

    rr 43  sr 49  rf 31  sf 31  rg 49  sg 46  sp 39  ri 5

    rr<->sr  47      rg<->sg  38      rf<->sf  27      <- 112 of the 202
    rg<->sr  14      rg->rf    7      sg->rr    6      sf->sp   6
    sr->sf    5      sf->rr    5      rr->sg    5      ... and a long tail

**112 of the 202 transitions are between a pipe's own send and its own
receive.**  Any scheme that puts a pipe's send zone and its receive zone in the
same place makes those free, and that is the single biggest lever in the
floorplan -- much bigger than the order of the zones.

## Three schemes, and why the third one wins on paper

**(a) Bands.**  All eight anchors on the north wall.  Distance is
``|x - col| + y + 1``, so binding depends only on the column, at every row --
the 1-D rule :mod:`tcp_ring` uses.  The send partition and the receive
partition are independent, so a pipe's ``s`` column and its ``r`` column may
coincide and the 112 free transitions are free here too.  Optimal arrangement
(brute-forced over all 4! x 4! column assignments) costs **108 band-steps**
with ``sR/rR, sG/rG, sF/rF, sP/rI`` in that order.

The trouble is not the band-steps, it is the *walk*.  A run like ``ROTB``'s
``rr sg rr rr sg rr`` alternates between two adjacent columns six times, and a
man who has just walked east into column ``c+1`` can only get back to column
``c`` by turning around, which costs a row.  With mean run length 1.22 the
whole program is that pattern, so a band layout burns a row per transition:
~200 rows for 596 glyphs, i.e. a room like 10x130 whose ``max(w,h)^2`` is
~17,000 before the panel is added.

**(b) Quadrants.**  Each pipe pair's two anchors on opposite walls at the same
column, so the ``x`` and ``y`` terms separate and the room splits into four
quadrants meeting at a point.  ``Binding`` implements it.  Near that point
every transition is short -- but only near that point: the region where all
four zones are within a couple of cells is a neighbourhood of a *single* cell,
and 596 glyphs do not fit in a neighbourhood.  Away from the centre a quadrant
scheme is strictly worse than bands, because now *both* coordinates can be
wrong.

**(c) Cross bands -- sends by column, receives by row.**  Put the four
**outgoing** anchors on the **north** wall and the four **incoming** anchors on
the **west** wall.  Then

    a send  constrains only x   (any row)
    a receive constrains only y (any column)

and the room becomes a 4x4 grid of rectangles, where rectangle
``(colband i, rowband j)`` accepts ``s_i`` and ``r_j`` at *every one of its
cells*.  ``ROTB``'s six-op alternation ``rr sg rr rr sg rr`` needs the man to
be somewhere in ``colband(G) x rowband(R)`` and nothing else -- he can wander
that whole rectangle placing them, with **no turnaround at all**.  That is the
thing neither (a) nor (b) can do, and it is why this is the scheme
:class:`GridBinding` implements.

Cost, measured the same way (brute force over 4! orders per axis, weighted by
the per-case execution counts ``Machine`` reports):

    columns (sends)    G, R, F, P    66 band-steps static, 4632 weighted
    rows    (receives) G, R, F, I    38 band-steps static, 4373 weighted

-- 104 band-steps against bands' 108, but spread over *two* axes, so half of
them are absorbed by a walk that was going to move anyway.  ``G`` and ``R``
adjacent on both axes is what the hot blocks want: ``ROTB`` (203 executions per
case) is pure R/G, and ``ITERB`` (420) walks F -> R -> G -> F -> R, which no
linear order can make all-adjacent but which costs only one two-band hop here.

## Numeric literals are a load-time hazard, not a layout detail

```nnn``` pairs backticks on rows **and columns independently**, and a
non-digit caught between a vertical pair is a *load* error.  Verified against
the reference interpreter::

    |@ 1  |
    | ` 5`|      -> error: expected a digit or a space between backticks,
    | M   |         but found 'M' (pos [2,3])
    | `7` |

The program has 20 multi-digit literals, i.e. 40 backticks, in a room of a few
dozen columns, so collisions are certain.  :mod:`pathfinder_place` handles it
by reserving **two columns that hold nothing but backticks and blanks** and
laying every multi-digit literal eastward from one to the other, space-padded:
each such row then has exactly two backticks around its own digits, and every
vertical pair in a backtick column encloses blanks only.  ``check_backticks``
re-derives the whole condition from the finished grid.

## What is measured, and what is not

Measured on partial placements (:mod:`pathfinder_place` driven over the real
program until it wedges):

* **travel cells outweigh glyph cells by 1.6x to 3.9x**, and the ratio tracks
  how far apart the anchors are: 1.64 with the four anchors packed into the
  middle half of each axis, 3.93 with them spread across 76 columns.  So the
  answer to "does travel dominate" is yes, but by less than 2x when the bands
  are tight -- which puts the worker room at roughly ``596 * 2.7 = 1600``
  cells, about 40x40, and a box around 62x42 once the 20x24 panel block is
  beside it.
* the ring, F and G high-water marks are 18 / 6 / 7 words
  (``test_pathfinder_prog.py``), so the loops need >= 19 / 7 / 8 cells of pipe.
* ``scratch/pf/relay.py``'s flat relay is engine-measured at 2.87 ticks/word
  against ``value_ring.RELAY_NORTH``'s 6.0.

**Not measured, because there is no grid:** ``w x h``, ``area2``,
``avg_ticks``, ``score``.  The placer wedges partway through -- it is greedy
with no backtracking, and every configuration eventually reaches a state where
the man's next legal cell is inside a pocket the earlier corridors sealed, or a
loop's entry bus has no free neighbour left to merge onto.  The guards that
prevent the first failure (an escape-region size test on every glyph, a merge
bus instead of a reserved dock, immediate depth-first descent into every branch
lane) each pushed the wedge later without removing it.  The next thing to try
is backtracking over the choice of goal cell, or abandoning free-form walking
for a discipline that cannot self-block.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

from randomfun2026solvers import pathfinder_prog as pf

__all__ = ["Binding", "GridBinding", "SEND_ZONE", "RECV_ZONE", "arrangements", "transitions"]

#: Which zone each pipe op belongs to, split by direction: a send is bound by
#: the outgoing partition and a receive by the incoming one, and the two are
#: independent, so a pipe's send and receive may share a place.
SEND_ZONE = {"sr": "R", "sf": "F", "sg": "G", "sp": "P"}
RECV_ZONE = {"rr": "R", "rf": "F", "rg": "G", "ri": "I"}


class Binding:
    """The quadrant partition, as an assertable predicate.

    ``send(x, y)`` and ``recv(x, y)`` name the pipe a glyph at that interior
    cell would bind to, computed from the same Manhattan rule the engine uses.
    Kept for comparison: see the module docstring for why scheme (c) beats it.
    """

    def __init__(self, iw: int, ih: int, xw: int, xe: int, yw: int, ye: int):
        if ih % 2:
            raise ValueError("IH must be even or the north/south split ties")
        if (xw + xe) % 2 == 0:
            raise ValueError("XW + XE must be odd or the east/west split ties")
        if (yw + ye) % 2 == 0:
            raise ValueError("YW + YE must be odd or the east/west split ties")
        if not (0 <= xw < xe < iw and 0 <= yw < ye < iw):
            raise ValueError("anchor columns must be inside the room, west < east")
        self.iw, self.ih = iw, ih
        self.xw, self.xe, self.yw, self.ye = xw, xe, yw, ye

    def send(self, x: int, y: int) -> str:
        west = abs(x - self.xw) < abs(x - self.xe)
        north = (y + 1) < (self.ih - y)
        return {(True, True): "sg", (False, True): "sr",
                (True, False): "sf", (False, False): "sp"}[(west, north)]

    def recv(self, x: int, y: int) -> str:
        west = abs(x - self.yw) < abs(x - self.ye)
        north = (y + 1) < (self.ih - y)
        return {(True, True): "rg", (False, True): "rr",
                (True, False): "rf", (False, False): "ri"}[(west, north)]

    def ok(self, x: int, y: int, tok: str) -> bool:
        if tok in ("sr", "sf", "sg", "sp"):
            return self.send(x, y) == tok
        if tok in ("rr", "rf", "rg", "ri"):
            return self.recv(x, y) == tok
        return True


class GridBinding:
    """Cross bands: outgoing anchors on the north wall, incoming on the west.

    A send at interior ``(x, y)`` is ``|x - col| + (y + 1)`` from a north-wall
    anchor, so the ``y`` term cancels and only the **column** decides; a receive
    is ``(x + 1) + |y - row|`` from a west-wall anchor, so only the **row**
    does.  The room is therefore a 4x4 grid of rectangles and, crucially, a
    rectangle accepts one send *and* one receive everywhere inside it.

    ``ok`` demands a **strict** minimum, never a tie resolved by reading order:
    a tie means a one-cell edit somewhere else in the grid could silently
    rebind the op.  Cells that tie are simply not legal for anybody.
    """

    def __init__(self, iw: int, ih: int, cols: dict[str, int], rows: dict[str, int]):
        if set(cols) != set("RFGP") or set(rows) != set("RFGI"):
            raise ValueError("need one column anchor per outgoing pipe, one row per incoming")
        if len(set(cols.values())) != 4 or len(set(rows.values())) != 4:
            raise ValueError("anchors must be distinct")
        if not all(0 <= c < iw for c in cols.values()):
            raise ValueError("column anchors must be inside the room")
        if not all(0 <= r < ih for r in rows.values()):
            raise ValueError("row anchors must be inside the room")
        self.iw, self.ih = iw, ih
        self.cols, self.rows = dict(cols), dict(rows)

    @staticmethod
    def _strict(value: int, anchors: dict[str, int], want: str) -> bool:
        d = {k: abs(value - v) for k, v in anchors.items()}
        return d[want] < min(v for k, v in d.items() if k != want)

    def send(self, x: int) -> str | None:
        """The pipe an ``s`` in column ``x`` binds to, or None if it ties."""
        return next((z for z in self.cols if self._strict(x, self.cols, z)), None)

    def recv(self, y: int) -> str | None:
        return next((z for z in self.rows if self._strict(y, self.rows, z)), None)

    def ok(self, x: int, y: int, tok: str) -> bool:
        if tok in SEND_ZONE:
            return self._strict(x, self.cols, SEND_ZONE[tok])
        if tok in RECV_ZONE:
            return self._strict(y, self.rows, RECV_ZONE[tok])
        return True

    def zones(self) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        """Which columns each send binds in, and which rows each receive does."""
        cols = {z: [x for x in range(self.iw) if self.send(x) == z] for z in self.cols}
        rows = {z: [y for y in range(self.ih) if self.recv(y) == z] for z in self.rows}
        return cols, rows


def probe_grid(binding: GridBinding) -> list[str]:
    """A runnable room wired exactly as :class:`GridBinding` describes it.

    Four outgoing pipes leave the north wall at the send anchors and four
    incoming pipes join the west wall at the receive anchors; every interior
    cell that binds cleanly carries the ``s`` or ``r`` it is supposed to.  The
    engine can then be asked which pipe each glyph actually reached, which is
    the only way to know the predicate matches the implementation rather than
    the prose.
    """
    iw, ih = binding.iw, binding.ih
    ox, oy = 6, 6
    w, h = ox + iw + 2, oy + ih + 2
    g = [[" "] * w for _ in range(h)]

    def put(x, y, ch):
        g[y][x] = ch

    def room(x0, y0, x1, y1):
        for x in range(x0, x1 + 1):
            put(x, y0, "-")
            put(x, y1, "-")
        for y in range(y0, y1 + 1):
            put(x0, y, "|")
            put(x1, y, "|")
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            put(x, y, "+")

    room(ox - 1, oy - 1, ox + iw, oy + ih)          # the worker
    room(ox - 1, 0, ox + iw, 2)                     # the sink, two rows up
    room(0, oy - 1, 2, oy + ih)                     # the source, three columns west
    put(ox, 1, "H")
    put(1, oy, "H")

    for col in binding.cols.values():
        put(ox + col, oy - 2, "^")
        put(ox + col, oy - 3, "^")
    for row in binding.rows.values():
        put(3, oy + row, ">")
        put(4, oy + row, ">")

    for y in range(ih):
        for x in range(iw):
            send, recv = binding.send(x), binding.recv(y)
            if (x + y) % 2 == 0 and send:
                put(ox + x, oy + y, "s")
            elif recv:
                put(ox + x, oy + y, "r")
            else:
                put(ox + x, oy + y, ".")
    return ["".join(r).rstrip() for r in g]


# ── measuring the program ─────────────────────────────────────────────────────
def _execution_counts(problem: Path | None = None) -> Counter:
    """Mean per-case execution count of every block, from the op-level model."""
    path = problem or Path(__file__).resolve().parents[3] / "tasks" / "problems" / "pathfinder.json"
    cases = json.loads(path.read_text())["publicTestData"]
    execs: Counter = Counter()
    prog = pf.build()

    class Counted(pf.Machine):
        def run(self, start="INIT", limit=4_000_000):
            block = start
            while block != "HALT":
                execs[block] += 1
                toks, succ = self.prog[block]
                lane = self.step_tokens(toks)
                if lane == "DRY":
                    return self.frames
                block = succ if isinstance(succ, str) else succ[lane]
            return self.frames

    for case in cases:
        Counted(prog, [int(v) for r in case["rounds"] for v in r["in"]]).run()
    for k in execs:
        execs[k] /= len(cases)
    return execs


def transitions(weights: Counter | None = None) -> tuple[Counter, Counter]:
    """Send-to-send and receive-to-receive zone changes, in program order.

    A send only constrains the column and a receive only the row, so the two
    axes are *independent* 1-D arrangement problems and this returns one
    transition table for each.  ``weights`` (block -> executions) turns the
    static count into the tick-weighted one.
    """
    sends: Counter = Counter()
    recvs: Counter = Counter()
    for name, (toks, _) in pf.build().items():
        w = weights[name] if weights else 1
        for table, out in ((SEND_ZONE, sends), (RECV_ZONE, recvs)):
            seq = [table[t] for t in toks if t in table]
            for a, b in zip(seq, seq[1:], strict=False):
                if a != b:
                    out[(a, b)] += w
    return sends, recvs


def arrangements(pairs: Counter, zones: str) -> list[tuple[float, int, tuple[str, ...]]]:
    """Every ordering of ``zones`` along one axis, cheapest first.

    Cost is ``sum(weight * |band index difference|)``; the second number counts
    the transitions that would have to cross a whole intervening band, which is
    what actually hurts -- a one-band step is free to a man already standing on
    the seam, a two-band step is not.
    """
    out = []
    for perm in itertools.permutations(zones):
        idx = {z: i for i, z in enumerate(perm)}
        cost = sum(v * abs(idx[a] - idx[b]) for (a, b), v in pairs.items())
        far = sum(v for (a, b), v in pairs.items() if abs(idx[a] - idx[b]) > 1)
        out.append((cost, far, perm))
    out.sort()
    return out


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--weighted", action="store_true",
                    help="weight transitions by measured per-case execution counts")
    args = ap.parse_args(argv)
    weights = _execution_counts() if args.weighted else None
    sends, recvs = transitions(weights)
    for label, pairs, zones in (("columns (sends)", sends, "RFGP"),
                                ("rows (receives)", recvs, "RFGI")):
        print(f"\n{label}: {sum(pairs.values()):.0f} zone changes")
        for k, v in pairs.most_common():
            print(f"    {k[0]} -> {k[1]}  {v:.0f}")
        print("  best orders:")
        for cost, far, perm in arrangements(pairs, zones)[:3]:
            print(f"    {''.join(perm)}  cost={cost:.0f}  two-band steps={far:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
