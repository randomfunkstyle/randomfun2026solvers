#!/usr/bin/env python3
"""`matmul` laid dense: one worker room, packed chains, rectangle loops.

:mod:`matmul_grid` compiles the same 32-block CFG into 83x98 and 31,553 ticks.
Rendered (``man_png``) that grid is one 56x83 void with block fragments scattered
through it -- **3.7% dense**, 8,624 cells holding 316 glyphs -- and both numbers
have the same cause: the pen gives every chain a full-width row of its own and
every routed edge a row of its own, so height counts *direction changes*, not
glyphs, and the man walks the blanks between the bands on every one of them.

Three changes, in the order they pay:

**Chains are boxes, and boxes share rows.**  A chain's ops only reach the bands
it actually uses, so its box is only as wide as that span -- `ROW` is three
columns, `MAC` is eight -- and two chains whose spans do not overlap sit on the
same rows.  The pen wraps inside its own box, so a wrap costs the box's width
instead of the room's.

**A counted loop is a rectangle, not a row plus a return corridor.**  A closed
grid walk has even length and four turns, so a 12-glyph body costs **16 ticks**
as a `w`x2 rectangle against 26 as a row whose return leg walks back empty --
and `MAC` runs 276 times on the mean public case, half of every tick spent.  The
`d` sits on a corner: BP>0 turns clockwise and stays in the loop, BP=0 goes
straight and leaves it.

**Every anchor is placed for the ops that use it, not paired with its ring.**  A
ring's two pipes are two independent nearest-sets, so `sx` can attach beside
`io` -- where ``LOADA_GO`` writes it -- while `rx` attaches beside `s`, where
``TBODY`` reads it.  The ring is then a long pipe between the two, which is
exactly what a ring wants: pipe cells *are* its storage.

## Landing on a block is not arriving at it

For three sessions this module emitted `19 22 43 50` on 2x2x2 and **nothing at
all** on every larger case: from about tick 20,000 the worker stood on ``MAC``'s
`rb` forever, `A` holding 2^21 where an entry of `A` belongs, the `k` ring
holding `D LANE LANE 1` instead of `LANE D LANE D D BIAS3` and two spare words
stranded in the one-word spill.  Six diagnoses were wrong before this one, so
the fault is worth naming precisely:

    ``BL2``'s `pos` lane landed on ``BL2_R``'s first cell **heading south**,
    and ``BL2_R`` is written to be read east.

The man executed the one glyph under him -- `rk`, which is why `LANE` ended up
loose in the rings -- and then walked *out of the block* through the blanks
below it and into ``HEAD``'s box, where he ran a fragment of the load-phase
preamble on live data.  Only `K >= 3` ever takes that lane (`BGRP_GO` peels one
entry, `BL1` a second, and `BL2`'s `d` sees `BP = 0` at `K = 2`), which is
exactly why the two public cases with `K = 2` passed and the other five hung.

``_route_edges`` skips drawing a corridor when the man already falls through to
the block he is meant to reach, and ``_follows_to`` answered *which block* while
saying nothing about **which way he was facing when he got there**.  Both now
return the heading, and the skip requires it to match.

Nothing else could see it.  The glyph run was complete and in order; the walk
from the block's own heading found every one of it; the pipes bound; the relays
turned.  Four plumbing faults were ruled out chasing it, and all four were
genuinely fine:

* **not a binding error** -- all 66 pipe ops put to the runtime's own
  ``lm.mjs route`` one cell at a time: 0 mismatches.  (Read the *last* cell of a
  receive's route and the *first* of a send's; both ends touch the north wall,
  and taking the wrong one makes every receive look unbound.)
* **not a misplaced op** -- every op walked from its block's first glyph and
  checked against *its own token's* band: 0 misplaced.
* **not a phantom pipe** -- 14 sources and 14 destinations.  There *were* two
  phantom sources (below); fixing them did not move the stall.
* **not a dead relay, and not the shared-relay deadlock** -- seven runners, none
  halted, and every ring has a turnaround room to itself.

The general lesson, and the reason the check went into ``matmul_grid`` where
every layout in the family goes through it: **a checker that walks each block in
isolation cannot see how the man got there.**  A block is a run of glyphs read
in one direction; an entry from any other direction is a different program, and
one that still passes every structural test.  ``walk_blocks`` now refuses a lane
whose arrival heading disagrees with its target's, and ``test_matmul_dense``
*executes* the finished `.man` against ``matmul_reference`` over seven shapes
rather than inspecting it -- including `K >= 3`, the family that was broken.

The two phantom pipe mouths, also real and also fixed: the coil's serpentine
turned at row `deep` while the relay was stamped at `deep + 1`, so the upward
leg's `^` had a room wall behind it and **was** a second pipe out of that relay.
An arrowhead with a wall behind it is a pipe mouth whether or not the layout
meant one; the relay's `s` would send to whichever of the two was nearer and
inject values into the middle of the coil.  One blank row between the coil and
the relay is the whole fix, and ``brackets_men.pipe_mouths`` counts them the way
the runtime does, which neither ``lm.mjs analyze`` nor ``route-check.mjs`` will.

## Where it stands, and what it costs

All seven public cases pass on the reference engine.  105x164, 67,105 ticks a
case, **1.80e9** -- against `matmul_packed`'s 2.746e8 on the same seven cases,
so the dense layout is 6.6x *worse* than the grid it was meant to replace, and
both of its two numbers say why:

* **the side is 164 and the room is 147 of it.**  The relay strip sits ~50
  columns east of the anchors, so the ring pipes are 650 (`b`) and 736 (`x`)
  cells long.  Rendered, the eastern half of the image is strip.
* **the ticks are 67,105 against a 27,593-cell model.**  The gap is ring
  *latency*, not layout: ``MAC`` re-reads the one-word spill every lap, and that
  word has to travel the whole 110-cell `s` ring between the `ss` that writes it
  and the `rs` that reads it back.  A ring the man re-reads every lap costs its
  whole length per lap, so the strip is charged twice -- once in the side and
  once in every tick.  (Fitting the north band from 20 rows to 15 took 24 cells
  off `s` and 9,249 ticks off the mean, which is the same lever at 1/6 scale.)

Both are the same decision.  Relays adjacent to the room, with a clear window
per ring, removes the strip and shortens every ring at once.

**And on the arithmetic it still does not pay, which is worth knowing before
the next session spends itself on it.**  The room is 147 rows and the measured
answer to widening the bands is that it does not shorten them -- gaps
`[1,1,1,4,1,2]` gave 75x141 against a 71x145 baseline, four rows for a much
wider room, because a box's height counts *direction changes within its chain*
and not band width.  So 147 is a floor on the side however the rings are
re-plumbed, and even at zero strip, zero band and the full 27,593-cell model
realised as ticks, 149^2 * 27,593 = **6.1e8**: still 2.2x behind
`matmul_packed`.  The dense layout's premise -- trade width for height by
wrapping every chain into a narrow box -- is what costs it: 311 glyphs over 147
rows is 2.1 a row, where `matmul_packed` gets 3.9 across 85 columns.  Beating
that needs the *room* halved, not the rings shortened.

## The old open problem: the rectangle's anchors make the north band unroutable

A ring's two pipes leave the worker's north wall, climb to a row of their own in
the north band, run east to the strip and drop into a turnaround room.  That is
crossing-free under exactly one rule, and it is the rule `matmul_grid` relies
on: **a pipe's row is its rank by column**, so a run east at row `r` only ever
passes columns whose risers stop at rows below `r`.  The rule needs a ring's two
rows to be *adjacent*, because a block in the strip owns one column range for
both of them.

Three of the seven rings here break it, and each for a reason the design chose
on purpose:

    q  rows  0/1   adjacent
    x  rows  2/13  split anchors: `sx` sits by `io`, `rx` by `s`
    io rows  4/3   adjacent
    k  rows  5/6   adjacent
    b  rows  7/9   interleaved with `c` by MAC's rectangle
    c  rows 10/8   interleaved with `b` by MAC's rectangle
    s  rows 11/12  adjacent

`MAC`'s 8x2 rectangle reads `s`, then `b`, then `c`, so its six anchors have to
run `S_b R_c R_b S_c S_s R_s` -- **`b` and `c` interleave, and that is precisely
what the band ordering forbids**.  Measured: with the strip ordered by earliest
row, `b`'s receive run at row 9 crosses `c`'s riser, which starts at row 8; with
the split `x`, its receive run at row 13 crosses every block west of it.  The
build stops at ``(64,10) holds '-', cannot place '|'``.

So the 16-tick MAC and a one-row-per-pipe north band are mutually exclusive.
The way out is a real two-dimensional channel router for the band -- rows *and*
columns allocated together, the way `Router` already does inside the room --
rather than a rank ordering.  That is the next thing to build, and it is the
only thing between this module and a `.man`.

## Five things that were measured and do not work, so they are not re-tried

**The box stack cannot be folded into two columns.**  The drawn room is 52x148
and `max(w, h)` charges the 148, so folding the stack into two columns of ~74
rows is the obvious move -- and it is impossible, not merely awkward.  Every
pipe attaches to the *north* wall, so "nearest pipe" collapses to nearest
column; a box shifted east by the width of the first column has all of its cells
east of every anchor, and every `r` in it therefore binds to whichever ring owns
the easternmost receive anchor and every `s` to the easternmost send anchor,
whatever the block wanted.  Measured at columns 24, 30, 40, 52 and 76: **every
`r` binds `x` and every `s` binds `s`.**  A second anchor set on the south wall
would fix it and cannot exist -- a ring has one send pipe and one receive pipe,
not two.  The legal way to spend width on height is to **spread the anchors**:
wider bands make every box wider, so the pen wraps less and the stack gets
shorter.  That is a change to :data:`ANCHORS`, not to the packer, and it has to
keep `MAC`'s six anchors inside seven consecutive columns or the 8x2 rectangle
stops closing.

**The three changes above only pay together.**  A rectangle is worth nothing in
`matmul_grid`'s geometry: there `s`, `b`, `c` span columns 26..44, so ``MAC``'s
twelve glyphs already occupy fifteen cells on one row and the rectangle would be
2x17 = 34 ticks against the 33 it costs today.  16 comes from the *tight*
anchors here -- six pipes in six columns -- and those in turn break
``matmul_grid``'s self-loop machinery, which requires a self-loop to lay on a
single row (``CFILL_GO`` raises "cannot be laid on one row" the moment the bands
tighten).  Neither half ships alone.

**Splitting the anchors cannot be retrofitted either.**  ``Bands`` already keeps
two independent orders, and an anneal over both orders and both width vectors
(2,500 steps) moved the score from 3.030e8 to 2.897e8 -- 1.05x, all of it one
row of height.  It never found the `sx`-beside-`io` placement because
``build_grid`` requires a stacked ring's two columns to be *adjacent* (it
straddles one turnaround room across both) and coils the rest in the east strip.
The split needs the ring geometry rebuilt, not the band order re-sorted.

**Two blocks cannot share a row, so the room's emptiness is not spendable.**  A
block entered by a man walking east from the left has an entry run that travels
*through* anything already on that row.  The shipped grid measures 10.4% dense
(543 glyphs in 5,234 interior cells) and yet every row is occupied: an anneal
over band widths 14..52 bottoms out at a 34x91 room, because height counts
direction changes -- 55 pen rows plus 28 lane rows -- not glyphs.  `max(w, h)`
charges the row, so packing boxes side by side buys nothing while entry runs are
row-shaped.  The escape is to make them column-shaped, which is what the
vertical strip below is: a column in ``recv ∩ send`` (q owns 1..3, k 11..13, s
19..20) hosts an unbounded ``r s r s ...`` run and collides with no eastward
entry.  ``Weave`` does not implement it yet; it is the next thing to build.

**A relay room cannot be smaller than 4x2 interior.**  A 3x2 has six cells, all
of them perimeter, and the man arrives back at the north-west corner heading
north -- so that corner has to turn him east and cannot also hold the ``@``.
Six rings at 6x4 outside is 144 cells, which does not fit beside a 24x28 room
inside side 32.  Sharing one relay across rings with plain `r`/`s` **deadlocks**:
the worker blocked on `rk` with every k-word still in the forward pipe, and the
relay blocked on `r_q` with every q-word already in the return pipe, is reachable
straight out of ``ROW``.  ``R``/``U`` cannot deadlock -- it takes from whichever
pipe has a value -- at the cost of coupling the rings' fairness, which is the
trade to take.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from randomfun2026solvers.circuit import CCW, CW, Circuit, Collision, E, N, S, W
from randomfun2026solvers.matmul_grid import (
    DEAD_LANES,
    ENTRY,
    LAID,
    SELF_LOOPS,
    band_of,
    chains_of,
    items,
)

__all__ = [
    "ANCHORS",
    "Bands",
    "Box",
    "Weave",
    "bands",
    "block_span",
    "chain_span",
    "lay_chain",
    "rect_loop",
]

# ── where the fourteen pipes attach ───────────────────────────────────────────
#
# All fourteen sit on the north wall, so the Manhattan distance from any cell to
# any of them is ``|x - col| + y + 1``: the `y` term is shared and "nearest pipe"
# collapses to *nearest column* at every row.  Ties are excluded rather than
# resolved -- they break by reading order over pipe *segments*, which is far too
# easy to get wrong by accident -- so consecutive sites in one partition are
# always an odd number of columns apart.
#
# The six columns 13..18 are `MAC`'s own: with `sb` at 13, `rc` at 14, `rb` at
# 16, `sc` at 17 and `s` straddling 18/19, the twelve-glyph body closes as an
# 8x2 rectangle whose every pipe op lands in its own band with nothing skipped.
IW = 25
ANCHORS: dict[tuple[str, bool], int] = {
    #  (ring, sending) -> north-wall column
    ("q", True): 1,   ("q", False): 2,
    ("io", True): 4,  ("io", False): 5,
    ("x", True): 8,   ("x", False): 7,
    ("k", True): 10,  ("k", False): 11,
    ("s", False): 14, ("s", True): 15,
    ("b", False): 17, ("b", True): 18,
    ("c", True): 21,  ("c", False): 22,
}

MARGIN_COLS = (0, IW - 1)


@dataclass(frozen=True)
class Bands:
    """Column -> ring, once for receives and once for sends."""

    recv: dict[int, str]
    send: dict[int, str]
    iw: int

    def ring_at(self, x: int, sending: bool) -> str | None:
        return (self.send if sending else self.recv).get(x)

    def span(self, ring: str, sending: bool) -> tuple[int, int]:
        cols = [x for x, r in (self.send if sending else self.recv).items() if r == ring]
        if not cols:
            raise Collision(f"{ring} {'send' if sending else 'recv'} owns no column")
        return min(cols), max(cols)


def bands(anchors: dict[tuple[str, bool], int] = None, iw: int = IW) -> Bands:
    """Voronoi over the attach columns, ties dropped, one partition per direction."""
    anchors = anchors or ANCHORS
    if len(set(anchors.values())) != len(anchors):
        raise Collision("two pipes want the same wall cell")
    out: list[dict[int, str]] = []
    for sending in (False, True):
        sites = {r: c for (r, s), c in anchors.items() if s == sending}
        cells: dict[int, str] = {}
        for x in range(iw):
            if x in MARGIN_COLS:
                continue
            d = sorted((abs(x - c), r) for r, c in sites.items())
            if len(d) < 2 or d[0][0] != d[1][0]:
                cells[x] = d[0][1]
        out.append(cells)
    return Bands(out[0], out[1], iw)


# ── one chain, laid inside its own box ────────────────────────────────────────
@dataclass
class Box:
    """A fall-through chain drawn into its own rectangle of the room."""

    blocks: list[str]
    lo: int                                    # westmost column it needs
    hi: int                                    # eastmost column it needs
    cells: dict[tuple[int, int], str] = field(default_factory=dict)
    start: dict[str, tuple[int, int]] = field(default_factory=dict)
    facing: dict[str, tuple[int, int]] = field(default_factory=dict)
    branch: dict[str, tuple[int, int, tuple[int, int]]] = field(default_factory=dict)
    axes: dict[tuple[int, int], set[int]] = field(default_factory=dict)
    entry: tuple[int, int] = (0, 0)            # cell the man must be steered onto
    entry_dir: tuple[int, int] = E
    exits: dict[str, tuple[tuple[int, int], tuple[int, int]]] = field(default_factory=dict)
    h: int = 0
    y: int = 0                                 # filled in by the packer

    @property
    def w(self) -> int:
        return self.hi - self.lo + 1


def block_span(name: str, b: Bands) -> tuple[int, int] | None:
    """The column range a block's pipe ops force it to reach, or None."""
    lo, hi = b.iw, -1
    for tok in LAID[name][0]:
        ring = band_of(tok)
        if ring is None:
            continue
        a, z = b.span(ring, tok[0] == "s")
        lo, hi = min(lo, a), max(hi, z)
    return None if hi < 0 else (lo, hi)


def chain_span(blocks: list[str], b: Bands) -> tuple[int, int]:
    """The column range a whole chain needs, widened to give the pen elbow room."""
    spans = [s for s in (block_span(n, b) for n in blocks) if s]
    if not spans:
        return 0, 3
    lo = min(a for a, _ in spans)
    hi = max(z for _, z in spans)
    # Two columns of slack east of the last band: a branch has to stand on an
    # east row with a cell to spare for its straight lane, and a block whose
    # last pipe op sits on the eastmost column of its span would have none.
    return lo, min(b.iw - 1, max(hi + 2, lo + 3))


# ── laying one chain ──────────────────────────────────────────────────────────
_TURN_GLYPH = {E: ">", W: "<", N: "^", S: "v"}


class Weave:
    """A pen that pours a chain into its own box, wrapping inside it.

    The box is only as wide as the bands the chain reaches, so a wrap costs the
    chain's own span rather than the room's -- which is the whole reason two
    chains can share rows at all.
    """

    def __init__(self, lo: int, hi: int, bands_: Bands) -> None:
        self.lo, self.hi, self.b = lo, hi, bands_
        # A wrap needs a cell to turn in, and the last glyph of a row may well
        # sit on the band at the very edge of the span -- so the turn columns
        # are one wider on each side than the glyph columns.
        self.tlo, self.thi = max(0, lo - 1), min(bands_.iw - 1, hi + 1)
        self.cells: dict[tuple[int, int], str] = {}
        self.x, self.y, self.d = lo, 0, E
        self.h = 1
        # Cell -> the axes a man walks it on (0 = east/west, 1 = north/south).
        # A cell the pen *skips* is still walked, and holds no glyph to say so:
        # a corridor that later turned there would silently re-steer the man.
        # Crossing one at right angles is safe, which is why the axis is kept
        # rather than a plain "used" flag.
        self.axes: dict[tuple[int, int], set[int]] = {}
        # A branch's clockwise lane leaves the cell *below* the branch glyph, so
        # the row under one has to stay clear: the pen steps two rows when it
        # wraps off a row that branched, and the connector runs through the gap.
        self.row_branch = False

    # -- primitives --------------------------------------------------------
    def _touch(self, x: int, y: int, axis: int | None = None) -> None:
        got = self.axes.setdefault((x, y), set())
        got.update({0, 1} if axis is None else {axis})
        self.h = max(self.h, y + 1)

    def _set(self, x: int, y: int, ch: str) -> None:
        if (x, y) in self.cells and self.cells[(x, y)] != ch:
            raise Collision(f"box cell ({x},{y}) holds {self.cells[(x,y)]!r}, not {ch!r}")
        self.cells[(x, y)] = ch
        self._touch(x, y)
        self.h = max(self.h, y + 1)

    def _skip_to(self, x: int) -> None:
        """Walk the pen east or west over cells it leaves blank, marking them.

        Nothing is marked before the box's first glyph: those cells are its
        *entry* run, which no man walks until a corridor delivers one along it,
        and reserving them would leave the box with no way in.
        """
        step = 1 if x > self.x else -1
        while self.x != x:
            if self.cells:
                self._touch(self.x, self.y, 0)
            self.x += step

    def put(self, ch: str) -> tuple[int, int]:
        at = (self.x, self.y)
        self._set(self.x, self.y, ch)
        self.x += self.d[0]
        self.y += self.d[1]
        return at

    def wrap(self) -> None:
        """Turn down onto the next row and reverse direction."""
        x = min(max(self.x, self.tlo), self.thi)
        self._set(x, self.y, "v")
        step = 2 if self.row_branch else 1
        for k in range(1, step):
            self.cells.setdefault((x, self.y + k), " ")
            self._touch(x, self.y + k, 1)
        self.d = W if self.d == E else E
        self.y += step
        self._set(x, self.y, _TURN_GLYPH[self.d])
        self.x = x + self.d[0]
        self.row_branch = False

    def room_ahead(self, need: int) -> bool:
        end = self.x + self.d[0] * (need - 1)
        return self.lo <= end <= self.hi

    def seek(self, ring: str, sending: bool) -> None:
        """Move the pen to the next column of `ring`'s band, wrapping if behind."""
        cols = [x for x in range(self.lo, self.hi + 1)
                if self.b.ring_at(x, sending) == ring]
        if not cols:
            raise Collision(f"{ring} has no column in {self.lo}..{self.hi}")
        for _ in range(3):
            ahead = [x for x in cols if (x >= self.x if self.d == E else x <= self.x)]
            if ahead:
                self._skip_to(min(ahead) if self.d == E else max(ahead))
                return
            self.wrap()
        raise Collision(f"cannot reach {ring} from column {self.x}")

    def approach(self, ring: str, sending: bool, lead: int) -> None:
        """Jump ahead so `lead` plain cells from here land just before `ring`.

        Pouring filler where the pen happens to stand and only then looking for
        the band is what makes a pen wrap once per constant: ``BLOAD_DONE``
        builds eight of them and every `sk` would be behind the pen.  Leading
        *into* the band costs nothing -- the cells between bands are blanks the
        man walks either way.
        """
        cols = [x for x in range(self.lo, self.hi + 1)
                if self.b.ring_at(x, sending) == ring]
        if not cols:
            return
        if self.d == E:
            want = min(cols) - lead
            if self.lo <= want and self.x < want:
                self._skip_to(want)
        else:
            want = max(cols) + lead
            if want <= self.hi and self.x > want:
                self._skip_to(want)

    def reaches(self, ring: str, sending: bool, lead: int) -> bool:
        """Would a band column still lie ahead after `lead` filler cells?"""
        cols = [x for x in range(self.lo, self.hi + 1)
                if self.b.ring_at(x, sending) == ring]
        end = self.x + self.d[0] * lead
        return any(x >= end for x in cols) if self.d == E else any(x <= end for x in cols)

    def go_east(self) -> None:
        """A branch's clockwise lane must leave *south*, which needs an east row."""
        if self.d != E:
            self.wrap()


# ── a counted loop as a rectangle ─────────────────────────────────────────────
def rect_loop(name: str, b: Bands, lo: int, hi: int
              ) -> tuple[dict[tuple[int, int], str], int, int, tuple[int, int], str]:
    """Lay a self-looping block as a `w`x2 rectangle; return cells and geometry.

    A closed grid walk has even length and four corners, so `n` glyphs need a
    perimeter of `2w >= n + 3` -- 16 cells for ``MAC``'s twelve against the 26 a
    row plus an empty return corridor costs.  The `d` sits on the west corner of
    the bottom row: arriving west it turns clockwise to north and stays in the
    loop, and goes straight on west to leave.  That puts the entry one cell west
    of the top row and the exit one cell west of the bottom row -- exactly where
    a pen walking east then wrapping west already is.

    Returns ``(cells, x0, w, first_glyph_cell, form)``; the loop occupies box
    rows 0 and 1 and is *not* written through :class:`Weave`, so the caller
    places it.  ``form`` is ``"west"`` (entry and exit one column west of the
    rectangle) or ``"east"``.
    """
    toks = LAID[name][0]
    slots: list[tuple[str, str | None, bool]] = []
    for tok in toks:
        ring = band_of(tok)
        for kind, payload in items(tok):
            if kind == "lit":
                raise Collision(f"{name}: a literal cannot ride a loop rectangle")
            slots.append((payload, ring, ring is not None and tok[0] == "s"))
    body, brancher = slots[:-1], slots[-1]
    if brancher[0] not in "dxX":
        raise Collision(f"{name} does not end in a branch")

    for w in range((len(slots) + 4) // 2, hi - lo + 2):
        for x0 in range(lo, hi - w + 2):
            for form in ("west", "east"):
                top = [(x, 0) for x in range(x0 + 1, x0 + w - 1)]
                bot = [(x, 1) for x in range(x0 + w - 2, x0, -1)]
                if form == "west":
                    # `d` on the south-west corner: arriving west it turns north
                    # and stays in, or goes straight on west and leaves.  Entry
                    # and exit both sit one column west, which is exactly where
                    # a pen walking east and then wrapping west already stands.
                    cells = {(x0, 0): ">", (x0 + w - 1, 0): "v",
                             (x0 + w - 1, 1): "<", (x0, 1): brancher[0]}
                    order = top + bot
                else:
                    # `d` on the north-east corner: the body then runs west along
                    # the bottom row first, which is the only way ``MAC`` closes
                    # -- it reads `s` before it reads `b` before it reads `c`,
                    # and those bands lie east to west in that order.
                    cells = {(x0, 0): ">", (x0 + w - 1, 0): brancher[0],
                             (x0 + w - 1, 1): "<", (x0, 1): "^"}
                    order = bot + top
                i = 0
                first: tuple[int, int] | None = None
                for cell in order:
                    if i >= len(body):
                        break
                    glyph, ring, send = body[i]
                    if ring is not None and b.ring_at(cell[0], send) != ring:
                        continue
                    cells[cell] = glyph
                    if first is None:
                        first = cell
                    i += 1
                if i == len(body):
                    return cells, x0, w, first or (x0, 1), form
    raise Collision(f"{name} does not close as a rectangle in {lo}..{hi}")


# ── a chain, block by block ───────────────────────────────────────────────────
@dataclass
class Lane:
    """One control edge that has to be routed: where it leaves and where to."""

    src: str
    key: str
    target: str
    cell: tuple[int, int]
    d: tuple[int, int]


def lay_chain(blocks: list[str], b: Bands) -> Box:
    """Pour a fall-through chain into its own box."""
    lo, hi = chain_span(blocks, b)
    box = Box(list(blocks), lo, hi)
    w = Weave(lo, hi, b)
    pending: list[Lane] = []

    for name in blocks:
        toks, succ = LAID[name][0], LAID[name][1]
        branch_i = len(toks) - 1 if isinstance(succ, dict) else None

        if name in SELF_LOOPS:
            cells, x0, rw, first, form = rect_loop(name, b, lo, hi)
            if form != "west":
                raise Collision(f"{name} needs an east-entry rectangle; "
                                "its chain has to be placed by hand")
            # The pen has to arrive at the rectangle's west entry *heading east*,
            # and a wrap flips the direction -- so if it is on the wrong side it
            # takes two of them: one down onto a west row, walked back past the
            # entry column, and one down again onto an east row in front of it.
            if not (w.d == E and w.x <= x0 - 1):
                if w.d == E:
                    w.wrap()
                w.x = max(w.tlo, min(w.x, x0 - 2))
                w.wrap()
            top = w.y
            w._skip_to(x0 - 1)
            for (cx, cy), ch in cells.items():
                w._set(cx, top + cy, ch)
            for yy in (top, top + 1):
                w._set(x0 - 1, yy, " ")
            box.start[name] = (first[0], top + first[1])
            box.facing[name] = E if first[1] == 0 else W
            box.branch[name] = (x0, top + 1, W)
            w.x, w.y, w.d = x0 - 2, top + 1, W
            w.h = max(w.h, top + 2)
            continue

        flat = [(i, tok, band_of(tok), kind, payload)
                for i, tok in enumerate(toks)
                for kind, payload in items(tok)]
        for j, (i, tok, ring, kind, payload) in enumerate(flat):
            if True:
                if ring is None:
                    lead, m = 0, j
                    while m < len(flat) and flat[m][2] is None:
                        lead += 1 if flat[m][3] == "g" else len(flat[m][4]) + 2
                        m += 1
                    if m < len(flat) and flat[m][0] != branch_i:
                        w.approach(flat[m][2], flat[m][1][0] == "s", lead)
                if kind == "lit":
                    need = len(payload) + 2
                    if not w.room_ahead(need):
                        w.wrap()
                    w.put("`")
                    for ch in payload:
                        w.put(ch)
                    at = w.put("`")
                elif i == branch_i:
                    # A branch has to stand on an **east** row with a cell to
                    # spare: its clockwise lane leaves south, which only exists
                    # below an east-walked row, and its straight lane runs on
                    # east past it.  Running out of row here is not a wrap --
                    # one wrap would turn the row west and put the lane above
                    # the block's own glyphs -- so it takes two.
                    for _ in range(4):
                        if w.d == E and w.room_ahead(2):
                            break
                        w.wrap()
                    else:
                        raise Collision(f"{name}: no east row for its branch")
                    at = w.put(payload)
                else:
                    if ring is not None:
                        w.seek(ring, tok[0] == "s")
                    elif not w.room_ahead(2):
                        w.wrap()
                    at = w.put(payload)
                if name not in box.start:
                    box.start[name] = at
                    box.facing[name] = w.d
            if i == branch_i:
                box.branch[name] = (at[0], at[1], w.d)
                w.row_branch = True
    box.cells = w.cells
    box.axes = w.axes
    box.h = w.h + (1 if w.row_branch else 0)
    box.entry = box.start[blocks[0]]
    box.entry_dir = box.facing[blocks[0]]
    box.exits["_pen"] = ((w.x, w.y), w.d)
    del pending
    return box


# ── the room: boxes stacked, edges routed ─────────────────────────────────────
#: Blank rows between two boxes.  One is enough for a corridor to *run* along;
#: two is what it takes for a corridor to reliably get *out* of a box, because
#: it may only turn on a cell no man already walks, and a box's own rows are
#: nearly all walked.  Measured: at one row the router strands
#: ``BL2_Z -> BGRP_END`` once the shorter edges have taken the gap.
GAP = 3

#: Channel columns west of the code.  A corridor prefers a free column inside
#: the room -- two walks cross at a blank without noticing each other -- but a
#: long span over a busy room may have none, and then it needs a fallback.
MARGIN = 18

#: Channel columns *east* of the code.  The room comes out far taller than it is
#: wide -- boxes stack, and `max(w, h)` is charged on the height -- so columns
#: are the one resource that is free here, and a corridor that cannot get out of
#: a box eastward is the commonest way the router strands an edge.
EAST = 10

_TURN_GLYPH_OF = {E: ">", W: "<", N: "^", S: "v"}


class Router:
    """Breadth-first corridors over the cells no man is already using.

    Two walks may **cross** at a blank -- one going north, one going east -- but
    they may not share it lengthwise, and neither may turn where the other
    passes.  A cell that is walked and holds no glyph is invisible to every
    other check, so both sets are tracked explicitly rather than inferred from
    what the grid looks like.

    A state is "the man stands on this cell with this heading, already
    resolved", so a turn is a property of the cell he arrives at and the search
    can turn on the last cell of a corridor as freely as on the first.
    """

    def __init__(self, c: Circuit, walked: dict[tuple[int, int], set[int]]) -> None:
        self.c = c
        self.walked = walked            # cell -> axes in use (0 = E/W, 1 = N/S)

    def _free(self, x: int, y: int, nd: tuple[int, int], turning: bool) -> bool:
        if not (0 <= x < self.c.w and 0 <= y < self.c.h):
            return False
        here = self.c.get(x, y)
        if here != " ":
            # a turn glyph already pointing the way we walk is a merge, not a
            # collision: two lanes into one entry share the last stretch of it
            return not turning and here == _TURN_GLYPH_OF[nd]
        used = self.walked.get((x, y))
        if not used:
            return True
        return not turning and (0 if nd[0] else 1) not in used

    def route(self, src: tuple[int, int], sd: tuple[int, int],
              dst: tuple[int, int], dd: tuple[int, int]) -> list[tuple[int, int]]:
        """Corridor from the glyph at `src` (leaving `sd`) onto `dst` (facing `dd`)."""
        from collections import deque

        start = (src[0], src[1], sd)
        goal = (dst[0] - dd[0], dst[1] - dd[1], dd)
        seen: dict[tuple, tuple | None] = {start: None}
        q = deque([start])
        while q and goal not in seen:
            x, y, d = q.popleft()
            nx, ny = x + d[0], y + d[1]
            if (nx, ny) == dst or (nx, ny) == src:
                continue
            for nd in (d, CW[d], CCW[d]):
                if not self._free(nx, ny, nd, nd != d):
                    continue
                st = (nx, ny, nd)
                if st not in seen:
                    seen[st] = (x, y, d)
                    q.append(st)
        if goal not in seen:
            raise Collision(f"no corridor {src} -> {dst}")
        path: list[tuple] = []
        st = goal
        while st is not None:
            path.append(st)
            st = seen[st]
        path.reverse()
        for i, (x, y, d) in enumerate(path):
            if not i:
                continue
            if path[i - 1][2] != d:
                self.c.set(x, y, _TURN_GLYPH_OF[d])
                self.walked.setdefault((x, y), set()).update({0, 1})
            else:
                self.walked.setdefault((x, y), set()).add(0 if d[0] else 1)
        return [(x, y) for x, y, _ in path[1:]]


@dataclass
class DenseRoom:
    """A drawn worker, in the shape `matmul_grid`'s checker already knows."""

    circuit: Circuit
    bands: Bands
    boxes: list[Box]
    iw: int
    ih: int
    margin: int = MARGIN
    _starts: dict[tuple[int, int], str] = field(default_factory=dict)
    _facing: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def starts(self) -> dict[tuple[int, int], str]:
        return dict(self._starts)

    def heading(self, name: str) -> tuple[tuple[int, int], tuple[int, int]]:
        for cell, who in self._starts.items():
            if who == name:
                return cell, self._facing[name]
        raise KeyError(name)

    def pipe_col(self, ring: str, sending: bool) -> int:
        return ANCHORS[(ring, sending)] + self.margin

    def regions(self):
        for box in self.boxes:
            yield (f"box:{box.blocks[0]}", self.margin + box.lo, box.y,
                   box.hi - box.lo + 1, box.h, " ".join(box.blocks))


#: Box order: the hot chain first, then the chains that talk to it, then the
#: load phases.  Only routing distance depends on it -- every edge between two
#: boxes is a corridor either way -- but a corridor is ticks, and `GEND`,
#: `GRP_GO` and `TNEXT` are on the emit path 34 times a case.
BOX_ORDER = ["TBODY", "TNEXT", "GRP_GO", "E1", "GG2", "E2", "GEND", "ROW", "ROW_GO",
             "BGRP_END", "BGRP_GO", "BL1_R", "BL2", "BL2_R", "BROW_GO",
             "HEAD", "BROW"]


def build_room(b: Bands | None = None) -> DenseRoom:
    """Stack the boxes, draw them, and route every edge that is not internal."""
    b = b or bands()
    made: dict[str, Box] = {c.blocks[0]: lay_chain(c.blocks, b)
                            for c in chains_of()}
    boxes = [made[n] for n in BOX_ORDER]
    if len(boxes) != len(made):
        raise Collision(f"box order lists {len(boxes)} of {len(made)} chains")

    y = 0
    for box in boxes:
        box.y = y
        y += box.h + GAP
    ih, iw = y, MARGIN + b.iw + EAST
    c = Circuit(iw, ih)
    walked: dict[tuple[int, int], set[int]] = {}
    starts: dict[tuple[int, int], str] = {}
    facing: dict[str, tuple[int, int]] = {}
    for box in boxes:
        for (x, yy), ch in box.cells.items():
            c.set(x + MARGIN, yy + box.y, ch)
        for (x, yy), ax in box.axes.items():
            walked.setdefault((x + MARGIN, yy + box.y), set()).update(ax)
        for name, (sx, sy) in box.start.items():
            starts[(sx + MARGIN, sy + box.y)] = name
            facing[name] = box.facing[name]

    room = DenseRoom(c, b, boxes, iw, ih, MARGIN, starts, facing)
    # The man spawns at `@` facing east and walks the blanks of the entry block's
    # own entry run into its first glyph.  Without this the grid loads, the six
    # relay men circulate, and nothing at all comes out.
    (ex, ey), ed = room.heading(ENTRY)
    if ed != E:
        raise Collision(f"{ENTRY} is entered heading {ed}, not east")
    c.set(ex - 1, ey, "@")
    walked.setdefault((ex - 1, ey), set()).update({0, 1})
    _route_edges(room, walked)
    return room


# ── which edges are already drawn, and where the rest have to go ──────────────
_TURN_OF = {">": E, "<": W, "^": N, "v": S}
_LANE_TURN = {
    "X": {"pos": CW, "neg": CCW, "zero": None},
    "d": {"pos": CW, "zero": None},
    "x": {"one": CW, "zero": CCW},
}


def _glyph_run(name: str) -> list[str]:
    return [g for tok in LAID[name][0] for _, g in items(tok)]


def lane_origins(room: DenseRoom) -> dict[tuple[str, str], tuple[tuple[int, int],
                                                                tuple[int, int]]]:
    """For every live lane, the glyph the man stands on and the way he leaves."""
    c = room.circuit
    out: dict[tuple[str, str], tuple[tuple[int, int], tuple[int, int]]] = {}
    for name in LAID:
        pos, d = room.heading(name)
        want, ops = len(_glyph_run(name)), 0
        for _ in range(4 * (room.iw + room.ih)):
            ch = c.get(*pos)
            if ch in _TURN_OF:
                d = _TURN_OF[ch]
            elif ch in _LANE_TURN:
                for lane, turn in _LANE_TURN[ch].items():
                    if (name, lane) in DEAD_LANES:
                        continue
                    out[(name, lane)] = (pos, turn[d] if turn else d)
                break
            elif ch == "H":
                break
            elif ch != " ":
                ops += 1
                if ops == want:
                    out[(name, "straight")] = (pos, d)
                    break
            pos = (pos[0] + d[0], pos[1] + d[1])
        else:                                       # pragma: no cover
            raise Collision(f"{name} never ends")
    return out


def _follows_to(room: DenseRoom, pos: tuple[int, int],
                d: tuple[int, int]) -> tuple[str | None, tuple[int, int]]:
    """Walk turns and blanks from `pos`; name the block reached *and the heading*.

    The heading is half the answer, and leaving it out cost six sessions.  A
    block is a run of glyphs read in one direction, so arriving on its first
    cell facing the wrong way executes that one glyph and then walks the man out
    of the block sideways -- through whatever blanks lie that way and into some
    other box's row.  Nothing else sees it: the grid loads, the pipes bind, the
    checker walks the block from its own heading and finds every glyph in place.
    ``BL2 -pos-> BL2_R`` was drawn exactly so, and only a case with ``K >= 3``
    ever takes that lane, which is why 2x2x2 passed and everything else hung.
    """
    starts = room.starts
    for _ in range(4 * (room.iw + room.ih)):
        if pos in starts:
            return starts[pos], d
        ch = room.circuit.get(*pos)
        if ch in _TURN_OF:
            d = _TURN_OF[ch]
        elif ch != " ":
            return None, d
        pos = (pos[0] + d[0], pos[1] + d[1])
        if not (0 <= pos[0] < room.iw and 0 <= pos[1] < room.ih):
            return None, d
    return None, d


def _route_edges(room: DenseRoom, walked: dict[tuple[int, int], set[int]]) -> int:
    """Draw every control edge the boxes did not already close.  Returns the count."""
    r = Router(room.circuit, walked)
    origins = lane_origins(room)
    todo = []
    for name in LAID:
        toks, succ = LAID[name]
        lanes = ({"straight": succ} if isinstance(succ, str)
                 else {k: v for k, v in succ.items() if (name, k) not in DEAD_LANES})
        for lane, target in lanes.items():
            if (name, lane) not in origins:
                continue
            pos, d = origins[(name, lane)]
            step = (pos[0] + d[0], pos[1] + d[1])
            dst, dd = room.heading(target)
            reached, arrive = _follows_to(room, step, d)
            # Reaching the block is not enough: the man has to arrive *facing*
            # the way its glyphs are written, or he reads one and walks out.
            if reached == target and arrive == dd:
                continue
            if dd == E:
                # A box's entry run -- the blanks between the west channels and
                # its first glyph -- belongs to nobody until a corridor delivers
                # a man along it, and boxes are stacked, so no two share a row.
                # Aiming every eastward entry at the channel end of that run,
                # instead of at the glyph, is what stops one corridor taking the
                # last cell another one had to turn on.
                for x in range(MARGIN, dst[0]):
                    if room.circuit.get(x, dst[1]) != " ":
                        raise Collision(f"{target}: entry run blocked at "
                                        f"({x},{dst[1]})")
                dst = (MARGIN, dst[1])
            todo.append((abs(pos[0] - dst[0]) + abs(pos[1] - dst[1]),
                         name, lane, pos, d, dst, dd))
    # Shortest first, then sweep again over whatever would not fit: a corridor
    # that fails is nearly always one whose only column a shorter edge has just
    # taken, and the shorter edge would have gone round.  Deferring is cheaper
    # than ripping up, and it terminates -- each pass either places one or stops.
    todo.sort()
    n = len(todo)
    while todo:
        rest, placed = [], False
        for job in todo:
            _, name, lane, pos, d, dst, dd = job
            try:
                cells = r.route(pos, d, dst, dd)
                del cells
                placed = True
            except Collision:
                rest.append(job)
        if not placed:
            _, name, lane, pos, d, dst, dd = rest[0]
            raise Collision(f"{name} -{lane}-> {dst}: no corridor from {pos}")
        todo = rest
    return n


# ── the whole machine: worker, six rings, and the two I/O rooms ───────────────
#: Words each ring has to hold, measured over every shape in 2..16.  A pipe's
#: capacity is its cell count, and a ring that cannot hold its contents
#: deadlocks *silently*.
RING_WORDS = {"x": 256, "b": 96, "c": 8, "k": 6, "q": 4, "s": 1}

#: No ring stacks.  A turnaround room is six columns wide and every ring's pipe
#: climbs straight up its own attach column, so a relay may span no column but
#: its ring's own two -- and `MAC`'s six anchors sit in seven consecutive
#: columns, which is exactly what makes the 8x2 rectangle close.  Enumerated,
#: every window for every ring holds somebody else's riser.  So all seven pairs
#: get the treatment the coiled ones already had: a north-band row apiece,
#: ordered west to east so no run ever passes over a riser that reaches higher.
LEVELS: dict[str, int] = {}

#: Rings long enough to need a serpentine in the strip rather than a straight
#: drop.  `x` holds 256 words and `b` 96; the rest are eight or fewer, and for
#: those the *depth* of the relay is chosen to make the ring only just long
#: enough -- a ring the man re-reads every lap costs its whole length in
#: latency, and `s` is read twice per MAC lap.
COILED = ("x", "b")

#: The smallest turnaround room there is: an eight-cell walk carrying one word.
#: A ring must have two rooms -- a pipe may not loop back into its own -- and
#: the man passes over `@` as a nop every lap, which is why a 3x2 interior
#: cannot do it: he would arrive back at the north-west corner heading north and
#: that corner has to turn him east.
RELAY = ["+----+", "|>@rv|", "|^ s<|", "+----+"]
RELAY_H = 4
STRIP_W = 7
NB = 20                                      # rows of north band above the wall
WX = 1


def _north_rows(off: int) -> tuple[dict[tuple[str, bool], int], list[str]]:
    """A north-band row and a strip order for every pipe that runs out east.

    Two crossing rules have to hold at once and they point opposite ways.  A
    pipe climbs from its column on the worker's wall to its own row and then
    runs east, so a run must never pass over a riser that reaches higher: rows
    go **west to east** on the worker side.  In the strip the same pipe drops
    from its row to its room, so a run must never pass over a drop that starts
    higher: strip columns go **east to west**.
    """
    pipes = sorted((ANCHORS[(r, s)] + off, (r, s))
                   for r in (*RING_WORDS, "io") for s in (True, False))
    rows = {key: row for row, (_, key) in enumerate(pipes)}
    order = [r for (r, s) in (k for _, k in pipes) if s]
    return rows, order[::-1]


def _relay_clear(box_x: int, ring: str, off: int) -> None:
    """Refuse a turnaround room that another ring's riser has to pass through.

    This is the wall the tight anchors run into, and it is worth stating
    exactly.  A relay is six columns wide and every other ring's pipe rises
    *vertically* from its own attach column to the north band, so a relay may
    not span any column but its own ring's two.  With eight anchors in eight
    consecutive columns -- which is precisely what makes ``MAC`` close as an 8x2
    rectangle -- no six-column window around `s`'s pair at 19/20 avoids `b`'s
    receive at 17, `c`'s send at 18 or `x`'s receive at 21.  Rotating the relay
    to a 2x4 interior narrows it to four columns and still does not fit.

    It is **not** only `s`.  Enumerated over the anchors here, every one of the
    four stacked rings is walled in the same way -- `q`'s only window holds
    `x`'s receive, `k`'s hold `io`'s send or `b`'s, `c`'s hold `b`'s and `s`'s.
    Moving `x`'s receive west was tried and measured: it frees nothing, because
    `b` and `c` block `s` on their own, and it costs 2,000 ticks by turning
    ``TBODY`` from a two-cell walk down one column into a seventeen-cell walk
    across the room (23,547 -> 25,502).

    So a *stacked* relay is out entirely, and the fix is not an anchor tweak: it
    is to give every ring the treatment the coiled ones already get -- its own
    north-band row and its own relay column range, per :func:`_north_rows` --
    with the jog kept short for `s`, whose latency the MAC pays every lap.  The
    room is 148 rows tall and 52 wide, so those columns are free.
    """
    mine = {ANCHORS[(ring, True)] + off, ANCHORS[(ring, False)] + off}
    for (other, send), c in ANCHORS.items():
        col = c + off
        if col in mine or other == "io" and False:
            continue
        if box_x <= col <= box_x + 5:
            raise Collision(
                f"{ring}'s relay spans columns {box_x}..{box_x + 5}, which "
                f"{other}'s {'send' if send else 'recv'} riser climbs at {col}")


def build_grid(room: DenseRoom | None = None, *, nb: int | None = None):
    """Worker room, six rings, and the input and output rooms.

    `nb` is the north band's height, and it is fitted rather than assumed: the
    band needs one row per pipe plus whatever the risers and the strip drops
    demand of each other, and where that floor lies depends on the band order.
    Here it is 14 rather than the 20 the first order needed, and the six rows
    come off the height *and* off every ring -- 24 cells off `s`, whose whole
    length the ``MAC`` pays as latency on every lap.
    """
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

    room = room or build_room()
    if nb is None:
        from randomfun2026solvers.matmul_grid import grid_loads

        for probe in range(6, NB + 1):
            try:
                made = build_grid(room, nb=probe)
            except Collision:
                continue
            # Drawing without a clash is not laying down a machine: the first
            # band height below the floor draws perfectly and then fails to
            # load, because a return pipe reaches back into its own turnaround
            # room.  Only the engine's own parse settles it.
            if grid_loads(made[0]):
                return made
        raise Collision("no north band lays this room")
    iw, ih = room.iw, room.ih
    wy = nb + 1
    off = WX + room.margin
    es = WX + iw + 3                          # first column of the east strip
    rows, blocks = _north_rows(off)
    g = Circuit(es + STRIP_W * len(blocks), wy + ih + 1)

    stamp(g, WX, wy, room.circuit.rows())
    walls(g, WX, wy, iw, ih)
    wall = wy - 1
    top, deep = wy + 1, wy + ih - 5

    def col(ring: str, send: bool) -> int:
        return ANCHORS[(ring, send)] + off

    lengths: dict[str, int] = {}

    # -- every ring, and the two I/O rooms, out in the strip -------------------
    for i, ring in enumerate(blocks):
        gx = es + i * STRIP_W
        sc, rc = col(ring, True), col(ring, False)
        r_out, r_in = rows[(ring, True)], rows[(ring, False)]
        if ring == "io":
            stamp(g, gx, top, ["+-+", "|I|", "+-+"])
            stamp(g, gx + 3, top, ["+-+", "|O|", "+-+"])
            draw_pipe(g, [(sc, wall - 1), (sc, r_out), (gx + 4, r_out),
                          (gx + 4, top - 1)])
            draw_pipe(g, [(gx + 1, top - 1), (gx + 1, r_in), (rc, r_in),
                          (rc, wall - 1)])
            continue
        need = RING_WORDS[ring] + 1
        if ring in COILED:
            # Same rule as the straight drop: the pipe whose row is higher has
            # to take the eastern column, or its own run cuts the other's riser.
            coil = (4, 3, 2) if r_out < r_in else (1, 2, 3)
            back = gx + (1 if r_out < r_in else 4)
            legs = [(sc, wall - 1), (sc, r_out), (gx + coil[0], r_out)]
            y = top
            for c_off in coil:
                legs += [(gx + c_off, y), (gx + c_off, deep if y == top else top)]
                y = deep if y == top else top
            # The serpentine turns at `deep`, so the relay may not start at
            # `deep + 1`: an arrowhead with a room wall behind it **is** a pipe
            # mouth, and the upward leg's `^` would then read as a second pipe
            # out of the relay.  The relay's `s` would send to whichever of the
            # two is nearer -- injecting values into the middle of the coil --
            # and the ring's order would quietly break.  One blank row is the
            # whole fix.
            legs.append((gx + coil[-1], deep + 1))
            fwd = draw_pipe(g, [q for j, q in enumerate(legs)
                                if j == 0 or q != legs[j - 1]])
            stamp(g, gx, deep + 2, RELAY)
            ret = draw_pipe(g, [(back, deep + 1), (back, r_in), (rc, r_in),
                                (rc, wall - 1)])
        else:
            # Straight down into a relay whose depth is chosen so the ring is
            # only just long enough: every extra cell is a tick the man waits.
            # Inside one block the two pipes cross unless the *higher* row
            # takes the *eastern* column: a run reaches its own column across
            # everything west of it, so the other riser has to start below it.
            cf, cr = (gx + 4, gx + 1) if r_out < r_in else (gx + 1, gx + 4)
            flat = ((wall - 1 - r_out) + abs(cf - sc)
                    + (wall - 1 - r_in) + abs(cr - rc) + 2)
            by = top + max(1, (need - flat + 1) // 2)
            stamp(g, gx, by, RELAY)
            fwd = draw_pipe(g, [(sc, wall - 1), (sc, r_out), (cf, r_out),
                                (cf, by - 1)])
            ret = draw_pipe(g, [(cr, by - 1), (cr, r_in), (rc, r_in),
                                (rc, wall - 1)])
        lengths[ring] = fwd + ret
        if fwd + ret < need:
            raise Collision(f"ring {ring} holds {fwd + ret}, needs {need}")

    art = [r.rstrip() for r in g.rows()]
    while art and not art[-1]:
        art.pop()

    d = DebugMap("matmul -- dense worker, rectangle loops")
    d.region("worker", WX, wy, iw, ih, color="#f59e0b",
             note=f"{len(room.boxes)} boxes")
    for label, x, y, w, h, note in room.regions():
        d.region(label, WX + x, wy + y, w, h, tags=["block"],
                 color="#22c55e", note=note)
    for ring, n in lengths.items():
        d.region(f"ring:{ring}", col(ring, True) - 2, 0, 6, wy, color="#0ea5e9",
                 note=f"{n} cells, holds {RING_WORDS[ring]} words")
    info = {"worker": (iw, ih), "rings": lengths, "boxes": len(room.boxes),
            "size": (max(len(r) for r in art), len(art))}
    return art, d, info


if __name__ == "__main__":  # pragma: no cover - the generator's CLI
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path)
    ap.add_argument("--html", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--png", type=Path)
    args = ap.parse_args()
    grid, dbg, meta = build_grid()
    if args.man:
        args.man.write_text("\n".join(grid) + "\n")
    if args.html:
        dbg.write_html(grid, args.html)
    if args.json:
        dbg.write_json(args.json)
    if args.png:
        from randomfun2026solvers.man_png import write_png
        meta["density"] = write_png(grid, args.png, scale=4, debug=dbg)
    print(meta)
