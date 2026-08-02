#!/usr/bin/env python3
"""The CPU's dispatch+return: the floor :mod:`place.table` had to leave blank.

    cpu dispatch+return  travel/instr    floor --   actual 25.0   execs 880,332

That dash is the last one in the table, and it is 25.7 % of the run.  It is a
dash because dispatch+return is not a loop, so :func:`place.route.loop_floor`
does not apply, and because it is not *one* leg either: it is **twenty-two
overlapping closed circuits sharing a basepoint**, one per opcode, whose lengths
are decided by where every lane sits.  Its floor is a placement result.

Three results, in the order they are proved.

1.  The bounding-box theorem (:func:`bbox_floor`)
-------------------------------------------------
A closed walk from the trie root that visits an op cell ``dx`` columns away must
cross each of the ``dx`` vertical lines between them **at least twice** -- once
outward, once back -- and the same on the ``dy`` horizontal ones.  Every crossing
is a distinct step, and a step is a tick.  So

    any closed walk visiting S  >=  2 * (dx + dy)  of  bbox(S u {root})

with no assumption about shape, glyph set or op order.  It is automatically even,
which the bipartite-grid argument in :mod:`place.circuit` independently demands.
This is the open-leg analogue of ``lap_floor`` and it is what the framework was
missing.

2.  The shipped circuits, walked exactly (:func:`trace_trie`, :func:`circuit`)
------------------------------------------------------------------------------
The decode trie is a deterministic router, so its paths can be *walked* rather
than modelled: :func:`trace_trie` enumerates both exits of every ``x``/``d`` from
the root and reaches all 22 leaves.  Composing that with the lane run, the drop,
the corridor or collector and the riser gives each opcode's whole
per-instruction circuit, cell by cell, off the shipped grid.  Summed against
measured execution counts it reproduces the profiler.

The circuits come out **4.011 cells/instr above their own bounding boxes**, and
that whole excess is one thing: the trie's *vertical overshoot*.  A binary
``x`` fork sends the man north or south, so a node on the way to a southern leaf
may sit north of it and the man walks back.  Everything else -- lane run, drop,
corridor, collector, riser -- is monotone, which is why ``IN`` and ``SND`` sit
exactly on their boxes and the rest do not.

3.  The structural floor, and where the shipped machine misses it
------------------------------------------------------------------
Decompose the box.  With the root on row ``centre``:

    dy(m) >= |row(m) - centre|      the box must contain both rows
    dx(m) >= drop_x(m) - west

``dy``'s floor is reached only if a return bus exists on the lane's own side of
the root.  :data:`machine.HIGH_COLLECTOR` opens one at ``centre - 1`` and every
lane **above** the root reaches its floor.  **There is no mirror below.**
``machine._stop`` reads

    if hi_row is not None and r < hi_row: return hi_row
    return collector

so a lane below the root falls past the whole band to the collector and climbs
back, paying a flat ``2 * (collector - centre)`` -- 20 cells -- whatever its row,
when its floor is as little as 2.  That asymmetry is the gap this module prices,
and :data:`SHAPES` proposes the mirror.

**Two corridors are provably enough.**  With one at ``centre - 1`` and one at
``centre + 1`` every lane attains ``dy = |row - centre|``, which no layout can
beat, so a third bus cannot help and neither can moving the fetch: ``dx`` works
out to ``trie_columns + own_width`` however far the whole rigid body is
translated.  The only levers left after that are the trie's column count and its
overshoot, which trade against each other.

What comes out
--------------
::

    shipped circuits          35.440 cells/instr    <- the 18 simple lanes
    their bounding boxes      31.429                <- routing floor, ATTAINED
    trie vertical overshoot    4.011                <- the whole difference

    vertical term, shipped    11.730
    vertical term, floor       8.937
    gap                        2.793 cells/instr = 2.04 % of the run

    LOW_COLLECTOR             -2.793 cells/instr = -2.04 %
      + legal row search      -4.025 cells/instr = -2.94 %

ARCH 7.1 is applied here as a **constraint on the search**, not a check after
it: :func:`legal_rows` refuses a row that would rebind a lane's pipe.  It costs
0.605 cells/instr of the row search's freedom, and it is not decoration --
``LD``, 17.4 % of all instructions, may sit only on ten of the band's
twenty-two rows, because one row lower its ``mem_resp`` ``r`` starts binding
``rom``.

    python3 dispatch.py
"""

from __future__ import annotations

import pickle
import random
import sys
from dataclasses import dataclass
from pathlib import Path

PKL = Path("/tmp/compactor/heat-1-21.pkl")
#: Whole-run ticks over the 21-round tour.  MEASURED (``prof.py 1 21``,
#: ``passed=True fatal=None``, 614x403).
TOTAL = 85_522_204
#: Instructions retired.  MEASURED.
INSTR = 880_332

#: Cells the man stands on that perform no operation.
STEER = set("<>^vV. @")
#: Branch/jump lanes: their drop runs **past** the collector into a slab, so the
#: band's return buses cannot catch them and their ``dy`` is the slab's depth,
#: not their own row.  Read off the grid would be nicer; this is the plan's own
#: ``_JUMP_SEMS | _BRANCH_SEMS`` and does not drift.
STRUCTURED = {"BRN", "BRZ", "JMPF", "JMPS"}

CW = {"N": "E", "E": "S", "S": "W", "W": "N"}
CCW = {v: k for k, v in CW.items()}
DIR = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}
HEAD = {">": "E", "<": "W", "^": "N", "v": "S"}

__all__ = [
    "Lane", "Anatomy", "read_cpu", "trace_trie", "bbox_floor", "circuit",
    "shape", "SHAPES",
]


# ── the shipped anatomy ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class Lane:
    """One opcode's lane, read off the built grid."""

    name: str
    row: int
    x0: int
    #: column of its **last operation** -- its intrinsic extent, not the padded
    #: one.  The drop rule floors a lane at the extent of everything below it, so
    #: a shipped width can be a neighbour's padding.
    end: int
    #: column its ``v`` stands on
    drop: int
    #: executions over the tour: ``heat - wait`` at the entry cell, so a blocked
    #: tick is never miscounted as an execution.  MEASURED.
    execs: int
    #: cells of the trie descent that reaches it, walked
    trie: int
    #: cells of the trie descent a *monotone* descent would have used
    trie_floor: int

    @property
    def structured(self) -> bool:
        return self.name in STRUCTURED

    @property
    def own_width(self) -> int:
        return self.end - self.x0 + 1

    @property
    def overshoot(self) -> int:
        """Cells the trie walks away from the leaf before walking back."""
        return self.trie - self.trie_floor


@dataclass(frozen=True)
class Anatomy:
    """The CPU's fixed points, all read off the grid rather than assumed."""

    centre: int          # the trie root's row
    hi_row: int          # HIGH_COLLECTOR's corridor, centre - 1
    collector: int       # the collector under the band
    root_x: int          # the root cell's column
    riser_x: int         # column the collector's riser stands in
    hi_x: int            # column the corridor drops in
    lane_x0: int         # where every lane's micro-program starts
    band_top: int        # northmost lane row


def read_cpu(pkl: Path = PKL):
    with pkl.open("rb") as fh:
        d = pickle.load(fh)
    rows, heat, wait, regs = d["rows"], d["heat"], d["wait"], d["regions"]
    a = Anatomy(
        centre=regs["cpu:fetch"][1],
        hi_row=regs["cpu:return:high"][1],
        collector=regs["cpu:return:collector"][1],
        root_x=regs["cpu:trie"][0],
        riser_x=regs["cpu:return:riser"][0],
        hi_x=regs["cpu:return:high"][0],
        lane_x0=min(v[0] for k, v in regs.items() if k.startswith("cpu:lane:")),
        band_top=min(v[1] for k, v in regs.items() if k.startswith("cpu:lane:")),
    )
    trie = trace_trie(rows, regs, a)
    lanes = []
    for name, (x, y, w, _h) in regs.items():
        if not name.startswith("cpu:lane:"):
            continue
        nm = name.rsplit(":", 1)[1]
        line = rows[y]
        ops = [xx for xx in range(x, x + w) if line[xx] not in STEER]
        drops = [xx for xx in range(x, x + w) if line[xx] == "v"]
        # A monotone descent is |row - centre| rows plus the columns from the root
        # to the last trie column, inclusive of the root cell.
        floor = abs(y - a.centre) + (a.lane_x0 - a.root_x)
        lanes.append(Lane(
            nm, y, x, max(ops) if ops else x - 1,
            drops[-1] if drops else x + w - 1,
            heat.get((x, y), 0) - wait.get((x, y), 0),
            trie[nm], floor,
        ))
    lanes.sort(key=lambda l: l.row)
    return a, lanes, d


def trace_trie(rows, regs, a: Anatomy) -> dict[str, int]:
    """Walk the decode trie from the root and count the cells to every leaf.

    The trie is a deterministic router: ``x`` always turns (clockwise on BP's low
    bit, counter-clockwise otherwise), ``d`` turns clockwise iff BP > 0, ``]``
    shifts without turning, and the steers set the heading outright.  So both
    exits of every branch can be enumerated without knowing a single BP value,
    and the walk terminates at the lane cells.  Reaching all 22 leaves is the
    check that the tracer agrees with the grid.
    """
    lanes = {(v[0], v[1]): k.rsplit(":", 1)[1]
             for k, v in regs.items() if k.startswith("cpu:lane:")}
    out: dict[str, int] = {}

    def walk(x, y, h, n):
        if n > 200 or not (0 <= y < len(rows) and 0 <= x < len(rows[y])):
            return
        g = rows[y][x]
        if (x, y) in lanes and n > 1:
            out.setdefault(lanes[(x, y)], n - 1)
            return
        if g == "x" or g in "da":
            outs = ((CW[h], CCW[h]) if g == "x"
                    else ((CW[h] if g == "d" else CCW[h]), h))
            for nh in outs:
                dx, dy = DIR[nh]
                walk(x + dx, y + dy, nh, n + 1)
            return
        if g in HEAD:
            h = HEAD[g]
        elif g == " ":
            return
        dx, dy = DIR[h]
        walk(x + dx, y + dy, h, n + 1)

    walk(a.root_x, a.centre, "E", 1)
    want = set(lanes.values())
    if set(out) != want:
        raise SystemExit(f"trie tracer missed {sorted(want - set(out))}")
    return out


# ── 1. the bounding-box theorem ──────────────────────────────────────────────
def bbox_floor(dx: int, dy: int) -> int:
    """Ticks of **any** closed walk whose bounding box is ``dx`` by ``dy``.

    See the module docstring: ``2 * (dx + dy)``, by cut-line crossings.  No
    layout can beat it and no glyph set changes it.
    """
    return 2 * (dx + dy)


def circuit(a: Anatomy, ln: Lane) -> tuple[int, int, int, int]:
    """``(cells, dx, dy, overshoot)`` of ``ln``'s shipped per-instruction circuit.

    Above the corridor: root -> trie -> lane -> drop to ``hi_row`` -> west to
    ``hi_x`` -> one row down -> root.  Below it: -> drop to the collector -> west
    to ``riser_x`` -> up the riser -> root.  Both returns are monotone, so the
    only slack in the circuit is the trie's, which is ``overshoot``.
    """
    if ln.row < a.hi_row:
        dx, dy = ln.drop - a.hi_x, a.centre - ln.row
    else:
        dx, dy = ln.drop - a.riser_x, a.collector - a.centre
    return bbox_floor(dx, dy) + ln.overshoot, dx, dy, ln.overshoot


# ── 3. the structural floor: search over shapes ──────────────────────────────
def rows_of(a: Anatomy, low_corridor: bool):
    """``(rows, dy, west)`` for a shape: what each band row costs a lane on it.

    **Shipped.**  Lanes ``band_top .. hi_row-1`` drop to the corridor at
    ``hi_row`` and turn at ``hi_x``: ``dy = centre - r``, its floor.  Lanes
    ``centre .. collector-1`` fall to the collector and climb the riser at
    ``riser_x``: ``dy = collector - centre`` **flat**, whatever the row.

    **LOW_COLLECTOR.**  One blank row is opened at the fetch row's south side and
    the whole block above it -- upper lanes, corridor, fetch row -- shifts up one
    row into the band's stagger slack (:data:`machine.SQUASH_BAND` is 12 of 20
    here, so eight rows sit blank above the band already).  The collector, every
    slab, the seek tail and the room's height are therefore **unmoved**, and only
    the CPU's own band re-flows.  Now:

    * ``band_top-1 .. hi_row-1`` -- twelve upper lanes, ``dy = centre - r``,
      unchanged;
    * the fetch row itself keeps a lane, which returns over either corridor:
      ``dy = 1``;
    * ``lo_row = centre + 1`` is the new corridor;
    * ``lo_row+1 .. collector-1`` -- nine lanes that **rise** to ``lo_row`` and
      turn north at ``hi_x``, landing on the fetch row's own ``>`` from the south
      exactly as the high corridor's man lands on it from the north:
      ``dy = r - centre``, its floor, and ``dx`` one column **shorter** than the
      collector route because the corridor stops a column east of the riser.
    """
    if not low_corridor:
        up = list(range(a.band_top, a.hi_row))
        lo = list(range(a.centre, a.collector))
        dy = {r: a.centre - r for r in up}
        dy.update({r: a.collector - a.centre for r in lo})
        west = {r: a.hi_x for r in up}
        west.update({r: a.riser_x for r in lo})
        return up, lo, dy, west
    c = a.centre - 1                       # the fetch row, shifted up one
    up = list(range(a.band_top - 1, c - 1))   # 12 rows, corridor at c - 1
    lo = [c] + list(range(c + 2, a.collector))  # the fetch row + 9 below lo_row
    dy = {r: c - r for r in up}
    dy[c] = 1
    dy.update({r: r - c for r in lo if r != c})
    west = {r: a.hi_x for r in up + lo}
    return up, lo, dy, west


# ── ARCH 7.1, as a *constraint* rather than a check ──────────────────────────
#: Pipe touch cells, captured from the shipped build's own ``check_bindings``
#: call (``/tmp/compactor/touches.json``).  MEASURED, not assumed.
TOUCH = {
    "rom": (7, 186), "in": (9, 137), "mem_resp": (43, 153),
    "mem_req": (43, 158), "stream_cmd": (19, 208), "cmd": (43, 203),
}
#: ``machine.INCOMING`` verbatim: the pool an ``r`` chooses from.
INCOMING = {"rom", "in", "mem_resp"}
#: The band names ``build_cpu`` tags a lane's pipe glyph with, mapped to the pipe
#: they have to bind.  ``mem`` is a band, not a pipe: an ``s`` in it wants
#: ``mem_req`` and an ``r`` wants ``mem_resp``.
BAND_PIPE = {("mem", "s"): "mem_req", ("mem", "r"): "mem_resp",
             ("in", "r"): "in", ("rom", "r"): "rom",
             ("stream_cmd", "s"): "stream_cmd", ("cmd", "s"): "cmd"}


def binds(x: int, y: int, glyph: str, want: str) -> bool:
    """Does a ``glyph`` at ``(x, y)`` bind ``want`` under ARCH 7.1?

    Nearest touch by Manhattan distance within the glyph's direction pool, ties
    broken by **reading order** -- top to bottom, left to right (``SPEC.md:183``).
    This is ``z3/bind.decide``'s rule; what is new here is that the search calls
    it as a *filter* on the rows a lane may occupy, instead of the placement
    being emitted first and checked afterwards.
    """
    pool = INCOMING if glyph == "r" else set(TOUCH) - INCOMING
    def key(n):
        tx, ty = TOUCH[n]
        return (abs(tx - x) + abs(ty - y), ty, tx)
    return min(pool, key=key) == want


def legal_rows(a: Anatomy, ln: Lane, glyphs, rows: list[int]) -> set[int]:
    """Which of ``rows`` this lane may sit on without rebinding a pipe.

    ``glyphs`` is ``{(x, y): (glyph, band)}`` from the build.  A lane translates
    as a rigid body, so a glyph at column ``x`` on row ``y`` moves to ``(x, r)``.
    """
    # only the lane's own cells: the row also carries the riser's ROM reads and
    # the trie, which are not part of the body that translates.
    mine = [(x, g, BAND_PIPE[(b, g)])
            for (x, y), (g, b) in glyphs.items()
            if y == ln.row and ln.x0 <= x <= ln.drop]
    return {r for r in rows if all(binds(x, r, g, w) for x, g, w in mine)}


def shape(a: Anatomy, lanes: list[Lane], *, low_corridor: bool,
          reorder: bool, glyphs=None, seeds: int = 200, rnd=None):
    """Weighted cells/instr for a shape, and the assignment that achieves it.

    Only the eighteen **simple** lanes are scored and permuted.  The four
    structured ones are pinned to the band's deepest rows and held at their
    shipped cost, for two independent reasons: ``machine.build_cpu``'s
    ``hi_free`` refuses a slab lane above the corridor outright, and a slab
    lane's box is the *slab's* depth rather than its own row's, so a row nearer
    the root is wasted on it.  Pinning them is therefore both legal and optimal,
    and it keeps every delta below attributable to the simple lanes alone.

    ``reorder`` searches the lane-to-row assignment under the drop rule's suffix
    maximum -- and, for a **rising** lane, its *prefix* maximum, which is the
    mirror discipline ``build_cpu`` already implements as ``asc_x``.  A dropping
    band therefore wants its long lanes north; a rising band wants them south.
    """
    simple = sorted((l for l in lanes if not l.structured), key=lambda l: l.row)
    struct = sorted((l for l in lanes if l.structured), key=lambda l: l.row)
    rnd = rnd or random.Random(20260802)
    up, lo, dy, west = rows_of(a, low_corridor)

    # The structured lanes keep their shipped rank inside the lower band: their
    # box is the slab's depth, so a row nearer the root is wasted on them, but
    # they cannot simply take the deepest rows either -- ``SND``'s ``s`` has to
    # stay south of y=171 to keep binding ``stream_cmd`` (see :func:`legal_rows`),
    # so the deepest row is spoken for.  Ranking them as shipped is what a
    # rank-preserving relabelling would produce.
    rank = {l.name: i for i, l in enumerate(sorted(lanes, key=lambda l: l.row))}
    order = sorted(up + lo)
    pinned = [order[rank[l.name]] for l in struct]
    free = [r for r in order if r not in pinned]
    assert len(free) == len(simple), (len(free), len(simple))
    ends = {r: l.end for r, l in zip(pinned, struct, strict=True)}
    #: The trie's overshoot is a property of the **row**, not of the opcode: a
    #: rank-preserving relabelling (``opt/build_taped.relabel``) keeps the slot
    #: set, so ``_uneven_trie`` draws the same trie and only the ROM encoding
    #: moves.  Carrying it with the lane would credit a reorder with a trie it
    #: would not get.  For a shape that adds a row the trie is redrawn, so the
    #: shipped profile is re-laid onto the new rows in north-south rank order --
    #: MODELLED, and the one assumption in this module that a build would test.
    ship_over = [l.overshoot for l in sorted(lanes, key=lambda l: l.row)]
    over = dict(zip(sorted(up + lo), ship_over, strict=True))

    #: ARCH 7.1 as a constraint: which rows each lane may legally occupy.
    ok = ({l.name: legal_rows(a, l, glyphs, free) for l in simple}
          if glyphs else {l.name: set(free) for l in simple})

    def cost(assign: dict[int, Lane]) -> float:
        """Weighted cells/instr, ``inf`` if any lane sits where 7.1 refuses it.

        A **dropping** lane's column is a suffix maximum -- its drop crosses the
        rows below it -- and a **rising** lane's is a prefix maximum, the mirror
        discipline ``build_cpu`` implements as ``asc_x``.  With a low corridor the
        two live in the same band: the simple lanes rise, the pinned slab lanes
        still fall past the collector, so both walks are kept and each lane reads
        the one that applies to it.
        """
        tot = 0.0
        for rows_ in (up, lo):
            end_of = {r: (ends[r] if r in ends else assign[r].end) + 1
                      for r in rows_ if r in ends or assign.get(r)}
            suff, pref, run = {}, {}, 0
            for r in sorted(rows_, reverse=True):
                run = max(run, end_of.get(r, 0))
                suff[r] = run
            run = 0
            for r in sorted(rows_):
                run = max(run, end_of.get(r, 0))
                pref[r] = run
            for r in rows_:
                l = assign.get(r)
                if l is None:
                    continue
                if r not in ok[l.name]:
                    return float("inf")
                # the fetch row's own lane touches neither corridor's column run
                rise = low_corridor and rows_ is lo and not l.structured
                col = (end_of[r] if low_corridor and r == a.centre - 1
                       else pref[r] if rise else suff[r])
                tot += l.execs * (bbox_floor(col - west[r], dy[r]) + over[r])
        return tot / INSTR

    ship = dict(zip(free, simple, strict=True))
    best, bestv = dict(ship), cost(ship)
    if not reorder:
        return bestv, best, pinned, ok

    for s in range(seeds):
        cur = dict(ship)
        if s:
            p = list(simple)
            rnd.shuffle(p)
            cur = dict(zip(free, p, strict=True))
        v = cost(cur)
        improved = True
        while improved:  # 2-opt to a local minimum under the exact objective
            improved = False
            for i in range(len(free)):
                for j in range(i + 1, len(free)):
                    ri, rj = free[i], free[j]
                    cur[ri], cur[rj] = cur[rj], cur[ri]
                    v2 = cost(cur)
                    if v2 < v - 1e-9:
                        v, improved = v2, True
                    else:
                        cur[ri], cur[rj] = cur[rj], cur[ri]
        if v < bestv:
            bestv, best = v, dict(cur)
    return bestv, best, pinned, ok


SHAPES = [
    ("shipped", dict(low_corridor=False, reorder=False)),
    ("shipped + row search", dict(low_corridor=False, reorder=True)),
    ("LOW_COLLECTOR", dict(low_corridor=True, reorder=False)),
    ("LOW_COLLECTOR + row search", dict(low_corridor=True, reorder=True)),
]


#: A saved CPU cell is NOT a saved tick: the man is 37.4 % blocked on the store,
#: so shortening his walk partly converts to waiting instead.  The exact
#: row-assignment solve measured the conversion at **0.71 ticks per cell**
#: (0.92 % in cells came out as 0.66 % in ticks).  Every tick figure below is a
#: cell figure times this, and is therefore MODELLED.
TICKS_PER_CELL = 0.71


def load_glyphs(path=Path("/tmp/compactor/touches.json")):
    """``{(x, y): (glyph, band)}`` from the shipped build's own bindings call."""
    import json
    if not path.exists():
        return None
    d = json.loads(path.read_text())
    return {(x, y): (g, b) for x, y, g, b in d["glyphs"]}


def reconcile(a: Anatomy, lanes: list[Lane], d) -> tuple[float, float]:
    """Predicted vs measured ticks over exactly the cells the circuits touch.

    The gate on everything above.  Every circuit is *drawn* -- the traced trie
    path, the lane run, the drop, the corridor or collector, the riser, the fetch
    -- and each cell is charged its lane's measured execution count.  Summed over
    the cells that construction touches, against the profiler's own walk counts
    on the same cells.  ``JMPS`` is left out: it exits into the seek drum rather
    than a slab, and its return is the drum's business, not the band's.
    """
    rows, heat, wait, regs = d["rows"], d["heat"], d["wait"], d["regions"]
    riser_of = {n.rsplit(":", 1)[1]: v[0]
                for n, v in regs.items() if n.startswith("cpu:riser:")}
    entry_of = {n.rsplit(":", 1)[1]: v[1]
                for n, v in regs.items() if n.startswith("cpu:entry:")}
    trie_path = _trie_paths(rows, regs, a)
    pred: dict[tuple[int, int], int] = {}
    for ln in lanes:
        if ln.name == "JMPS":
            continue
        cells = list(trie_path[ln.name])
        cells += [(x, ln.row) for x in range(ln.x0, ln.drop + 1)]
        if ln.structured:
            ex = riser_of[ln.name]
            cells += [(ln.drop, y) for y in range(ln.row + 1, entry_of[ln.name])]
            cells += [(x, a.collector) for x in range(a.riser_x, ex)]
            cells += [(a.riser_x, y) for y in range(a.centre, a.collector + 1)]
            cells += [(a.hi_x, a.centre)]
        elif ln.row < a.hi_row:
            cells += [(ln.drop, y) for y in range(ln.row + 1, a.hi_row + 1)]
            cells += [(x, a.hi_row) for x in range(a.hi_x, ln.drop)]
            cells += [(a.hi_x, a.centre)]
        else:
            cells += [(ln.drop, y) for y in range(ln.row + 1, a.collector + 1)]
            cells += [(x, a.collector) for x in range(a.riser_x, ln.drop)]
            cells += [(a.riser_x, y) for y in range(a.centre, a.collector)]
            cells += [(a.riser_x, a.centre), (a.hi_x, a.centre)]
        for c in cells:
            pred[c] = pred.get(c, 0) + ln.execs
    tp = sum(pred.values())
    tm = sum(heat.get(c, 0) - wait.get(c, 0) for c in pred)
    return tp / INSTR, tm / INSTR


def _trie_paths(rows, regs, a: Anatomy) -> dict[str, list]:
    """:func:`trace_trie`, keeping the cells rather than only counting them."""
    lanes = {(v[0], v[1]): k.rsplit(":", 1)[1]
             for k, v in regs.items() if k.startswith("cpu:lane:")}
    out: dict[str, list] = {}

    def walk(x, y, h, cells):
        if len(cells) > 200:
            return
        g = rows[y][x]
        if (x, y) in lanes and cells:
            out.setdefault(lanes[(x, y)], list(cells))
            return
        if g == "x" or g in "da":
            outs = ((CW[h], CCW[h]) if g == "x"
                    else ((CW[h] if g == "d" else CCW[h]), h))
            for nh in outs:
                dx, dy = DIR[nh]
                walk(x + dx, y + dy, nh, cells + [(x, y)])
            return
        if g in HEAD:
            h = HEAD[g]
        elif g == " ":
            return
        dx, dy = DIR[h]
        walk(x + dx, y + dy, h, cells + [(x, y)])

    walk(a.root_x, a.centre, "E", [])
    return out


def main() -> int:
    a, lanes, _d = read_cpu()
    glyphs = load_glyphs()
    tp, tm = reconcile(a, lanes, _d)
    print(f"gate: the drawn circuits predict {tp:.3f} t/instr over the cells they "
          f"touch;\n      the profiler MEASURED {tm:.3f} on the same cells "
          f"(ratio {tp / tm:.4f}).", flush=True)
    print(f"anatomy, MEASURED off the shipped grid: root ({a.root_x},{a.centre}) "
          f"corridor y={a.hi_row} x={a.hi_x}  collector y={a.collector} "
          f"riser x={a.riser_x}  lane_x0={a.lane_x0}  band top y={a.band_top}",
          flush=True)
    print()
    print(f"{'op':6s} {'row':>4s} {'end':>4s} {'drop':>4s} {'trie':>5s} "
          f"{'|Δy|':>5s} {'over':>5s} {'dx':>3s} {'dy':>3s} {'cells':>6s} "
          f"{'box':>5s} {'sh%':>6s} {'t/instr':>8s}  bus", flush=True)
    tot = box = over = 0.0
    for ln in lanes:
        c, dx, dy, ov = circuit(a, ln)
        bus = ("slab" if ln.structured
               else "corridor" if ln.row < a.hi_row else "collector")
        if not ln.structured:
            tot += ln.execs * c
            box += ln.execs * bbox_floor(dx, dy)
            over += ln.execs * ov
        print(f"{ln.name:6s} {ln.row:4d} {ln.end:4d} {ln.drop:4d} {ln.trie:5d} "
              f"{abs(ln.row - a.centre):5d} {ln.overshoot:5d} {dx:3d} {dy:3d} "
              f"{c:6d} {bbox_floor(dx, dy):5d} {100 * ln.execs / INSTR:6.3f} "
              f"{ln.execs * c / INSTR:8.3f}  {bus}", flush=True)

    print(f"\n  18 simple lanes, MODELLED from the walked geometry:", flush=True)
    print(f"    shipped circuits        {tot / INSTR:8.3f} cells/instr "
          f"({tot:,.0f} ticks, {100 * tot / TOTAL:5.2f} % of the run)", flush=True)
    print(f"    their bounding boxes    {box / INSTR:8.3f}   <- the routing floor "
          f"for these boxes", flush=True)
    print(f"    trie vertical overshoot {over / INSTR:8.3f}   <- the whole "
          f"difference; every other leg is monotone", flush=True)

    # ── the dy term on its own: exact, and independent of the trie ───────────
    _up, _lo, dyf, _w = rows_of(a, True)
    dy_ship = sum(l.execs * circuit(a, l)[2] for l in lanes if not l.structured)
    dy_flr = sum(l.execs * abs(l.row - a.centre) for l in lanes
                 if not l.structured)
    print(f"\n  the vertical term alone (exact -- the box has to contain the "
          f"root's row and the lane's):", flush=True)
    print(f"    shipped  2*sum p*dy   {2 * dy_ship / INSTR:8.3f} cells/instr",
          flush=True)
    print(f"    floor    2*sum p*|row-centre| {2 * dy_flr / INSTR:8.3f}   "
          f"attained above the root, missed below", flush=True)
    print(f"    gap                   {2 * (dy_ship - dy_flr) / INSTR:8.3f} "
          f"cells/instr = {2 * (dy_ship - dy_flr) * TICKS_PER_CELL / TOTAL * 100:.2f} "
          f"% of the run at {TICKS_PER_CELL} ticks/cell", flush=True)

    # ── the drop rule's suffix maximum: what the padding actually costs ─────
    pad = sum(l.execs * 2 * (l.drop - l.end - 1) for l in lanes
              if not l.structured)
    print(f"\n  drop-rule padding (a lane paying a longer neighbour's columns): "
          f"{pad / INSTR:.3f} cells/instr\n    -- the suffix maximum is still "
          f"active, but the shipped band is very nearly length-descending, so it "
          f"is worth\n    only {100 * pad * TICKS_PER_CELL / TOTAL:.2f} % of the "
          f"run and most of it is ADDI/MULI/LDI paying for a band column, not for "
          f"a long lane.", flush=True)

    # ── ARCH 7.1 as a constraint ────────────────────────────────────────────
    if glyphs:  # self-check: every shipped pipe glyph must bind what it declares
        bad = [(x, y, g, b) for (x, y), (g, b) in glyphs.items()
               if (b, g) in BAND_PIPE and not binds(x, y, g, BAND_PIPE[(b, g)])]
        if bad:
            raise SystemExit(f"binds() disagrees with the shipped grid: {bad[:5]}")
        print(f"\n  binds() self-check: all "
              f"{sum(1 for k in glyphs if (glyphs[k][1], glyphs[k][0]) in BAND_PIPE)}"
              f" declared pipe glyphs on the shipped grid bind what build_cpu "
              f"tagged them with.", flush=True)
    up, lo, _dy, _w = rows_of(a, True)
    allrows = sorted(up + lo)
    print(f"\n  ARCH 7.1, applied as a *constraint* (touches captured from the "
          f"build):", flush=True)
    for l in sorted(lanes, key=lambda l: -l.execs):
        if l.structured or not glyphs:
            continue
        ok = sorted(legal_rows(a, l, glyphs, allrows))
        if len(ok) == len(allrows):
            continue
        print(f"    {l.name:6s} ({100 * l.execs / INSTR:5.2f} %) may only sit on "
              f"y={ok[0]}..{ok[-1]} -- {len(allrows) - len(ok)} of "
              f"{len(allrows)} rows refused", flush=True)

    print("\n  shapes, 18 simple lanes only (MODELLED; the four structured lanes "
          "are pinned to the deepest\n  band rows and held at their shipped "
          "cost, so every delta is the simple lanes' alone):", flush=True)
    base = None
    for name, kw in SHAPES:
        for tag, gl in (("", glyphs), (" [7.1 ignored]", None)):
            if tag and not kw["reorder"]:
                continue
            v, _as, _p, _ok = shape(a, lanes, glyphs=gl, **kw)
            if base is None:
                base = v
            print(f"    {name + tag:38s} {v:8.3f} cells/instr  Δ {v - base:+7.3f}"
                  f"  = {(v - base) * TICKS_PER_CELL * INSTR / TOTAL * 100:+6.2f} % "
                  f"of the run", flush=True)

    print("\n  the best legal shape, row by row:", flush=True)
    v, assign, pinned, _ok = shape(a, lanes, low_corridor=True, reorder=True,
                                   glyphs=glyphs)
    for r in allrows:
        l = assign.get(r)
        if l is None:
            print(f"        y={r:>4d} {'PINNED slab' if r in pinned else ''}",
                  flush=True)
            continue
        mark = "" if l.row == r else f"  <- was y={l.row}"
        print(f"        y={r:>4d} {l.name:6s} end={l.end:3d} dy={dyf[r]:>2d} "
              f"exec={100 * l.execs / INSTR:5.2f}%{mark}", flush=True)
    print(f"        y={a.centre:>4d} <- the new LOW corridor row "
          f"(the fetch row moves to {a.centre - 1})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
