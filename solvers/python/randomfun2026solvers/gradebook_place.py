#!/usr/bin/env python3
"""`gradebook` laid out by :mod:`blockplace`: four pipe bands, banks side by side.

:mod:`gradebook_cfg` is the machine — 37 blocks, 322 glyph cells, three rings and
an I/O pair.  This module only *places* it, and the placing is the whole score:
the judge charges ``max(w,h)^2 x ticks``, so the side of the room is squared and
every column saved is paid back twice.

## Four bands, not two

`snake` had one ring and one I/O pair, so there was a single split column and the
only question was where to put it.  Here there are four pipe pairs -- ``RING``
(the packed cells), ``IDS`` (the bare ids), ``FILE`` (scratch) and ``IO`` -- so
there are *three* splits, and a block that needs two bands needs a window that
spans both.  Fourteen of the thirty-seven blocks do.

The bands are therefore not chosen; they are *derived*.  Banks are laid out west
to east first, each pipe pair is dropped on the centre of its own bank's code
window, and :func:`bands_of` then reads back, column by column, which pipe an
``r`` and an ``s`` standing there would actually bind.  A band is the run of
columns where the two agree.  Nothing about the split is asserted in a comment
and hoped for: `blockplace.bank_geometry` measures every glyph against every
pipe in the room while it plans, and :func:`audit_bindings` measures the built
grid against every *cell* of every pipe afterwards.

## Why the band order is what it is

Band order is a free choice and it decides how wide the multi-band blocks have
to be.  ``RING IDS FILE IO`` is what ``--zones`` settles on: ``FILE`` is the hub
-- it shares a block with ``RING`` seven times and with ``IO`` seven times -- so
it wants a neighbour on each side, and ``IDS`` is the rarest band (four blocks)
so it pays least for being pushed to an edge of the interior.  Most of the other
twenty-three orders do not lay out at all, and the one that reaches a shorter
room -- ``IO IDS FILE RING``, 59 rows against 64 -- pays 27% more ticks for it,
because it puts ``IO`` and ``RING`` at opposite walls and every operation's hot
path crosses between them.

## What this machine actually costs

**It is corridor-bound, not code-bound.**  322 cells of program sit in ~2,900
cells of corridor, and the corridor is what the ticks are: measured across the
shortlist, a case runs 6.2 to 6.8 ticks for every corridor cell in the room.
That is worth stating plainly because it sets which knobs matter -- the ones
that move corridor length -- and because it makes the op model's 1,726 ticks a
case a *floor* rather than an estimate.  On the grid it measures near 18,000.

Two things this machine is *not* vulnerable to, both checked rather than assumed:

* **No fall-through chains.**  `blockplace` routes every CFG edge as a corridor,
  so a machine whose blocks fall through into each other pays for edges that
  ought to be free -- the trap that cost `matmul` 21% of its ticks.  Twenty-three
  of gradebook's blocks do fall through, and **not one** of their targets has a
  single predecessor: every loop head and every operation's return point is
  re-entered from two or three places.  There is nothing to merge.
* **No op stands on a tie.**  See :func:`bands_of`.

## What it scores, measured rather than projected

On the seven public cases, against the LM-1 CPU build in ``gradebook_cpu.man``
run on the same data::

                     side     area^2      avg ticks        score
    CPU            93x92       8,649        286,287     2.476e9
    this           70x74       5,476         16,496     9.033e7    27.4x

The area is only 1.58x; the 17.4x is ticks, which is what moving every variable
out of a tape and into a pipe buys.  The submitted CPU score was 6.39e9 on the
judge's data against 2.476e9 here, a factor of 2.58; at the same factor this
grid lands near **2.3e8**, which is the *pessimistic* end of ``gradebook_cfg``'s
own projection table and not the middle of it.

**Rows are the only thing that costs.**  The grid is taller than it is wide, so
every row is ~2.7% of the score and a column is free until the room turns
square.  That is the rule the north band is built around and the reason
:data:`REACH` exists at all -- and it cuts the other way too, which is why a
narrower relay beat a safer one and why ``--sweep`` will not take a smaller room
that walks further to get around itself.

The reason to state that plainly is that the op model is not a forecast of grid
ticks and should not be read as one: it charges 1,726 ticks for the average
public case and the grid measures 15,646, a factor of nine, because the model
charges glyph cells and the grid also walks 2,432 cells of corridor between
them.  A layout is chosen on measured ticks here for exactly that reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from randomfun2026solvers import blockplace as B
from randomfun2026solvers.circuit import Collision
from randomfun2026solvers.gradebook_cfg import WORKER
from randomfun2026solvers.lllm_layout import Geometry, block_order

if TYPE_CHECKING:  # pragma: no cover
    from randomfun2026solvers.man_debug import DebugMap

__all__ = ["TOKEN_ZONE", "ZONES", "bands_of", "build_room", "layout"]

#: Which band each pipe token binds.  `rr`/`sr` and friends all compile to a
#: bare `r` or `s`; the band is a *column* discipline, not a glyph.
TOKEN_ZONE = {
    "rr": "RING", "sr": "RING",
    "rq": "IDS", "sq": "IDS",
    "rt": "FILE", "st": "FILE",
    "ri": "IO", "so": "IO",
}

#: Bands west to east.  See the module docstring; re-derive with ``--zones``.
ZONES = ("RING", "IDS", "FILE", "IO")


def cells_of(legs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Every cell a `plotter_block.pipe` leg list passes through, in flow order."""
    pts = [legs[0]]
    for (x0, y0), (x1, y1) in zip(legs, legs[1:], strict=False):
        sx, sy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x, y = x + sx, y + sy
            pts.append((x, y))
    return pts


def bands_of(cells_in: dict[str, list[tuple[int, int]]],
             cells_out: dict[str, list[tuple[int, int]]],
             lo: int, hi: int) -> dict[str, tuple[int, int]]:
    """The columns in ``[lo, hi]`` where an `r` and an `s` reach the same band.

    Measured over the pipes' **actual cells**, which is exact, and exact is
    cheaper than it sounds:

        A room cell ``(x, y)`` is ``|x-px| + (y-py)`` from a pipe cell, and every
        pipe cell is above every room cell, so ``y`` appears in that sum once per
        pipe and cancels out of the comparison.  **Which pipe is nearest depends
        on the column and not on the row.**

    That is what lets a band be a column range at all, and it holds *however the
    risers are bent*.  The first version of this module did not use it: it kept
    the risers inside a cone one column wide per row so that each anchor would
    provably beat its own pipe's other cells, and so paid a row of north band for
    every column of riser.  The band is charged against the room's height and the
    height is the side being squared -- so that was the expensive axis buying the
    free one.  Reading the bands off the cells instead frees the riser to be
    folded for capacity, and the bands simply move; :func:`layout` then checks
    they still cover their banks.

    A column **equidistant from two pipes** is left out even though a rule could
    name one: the tie-break is not something the engine promises, and the op that
    stands there reads a plausible number off the wrong ring.
    """
    def reach(cells: list[tuple[int, int]], x: int) -> int:
        # `- py` rather than `BAND_H - py`: the constant is common to every pipe.
        return min(abs(x - px) - py for px, py in cells)

    runs: dict[str, tuple[int, int]] = {}
    for x in range(lo, hi + 1):
        near_in = sorted((reach(c, x), z) for z, c in cells_in.items())
        near_out = sorted((reach(c, x), z) for z, c in cells_out.items())
        if near_in[0][1] != near_out[0][1]:
            continue
        if near_in[0][0] == near_in[1][0] or near_out[0][0] == near_out[1][0]:
            continue
        z = near_in[0][1]
        a, b = runs.get(z, (x, x))
        runs[z] = (min(a, x), max(b, x))
    return runs


#: Columns between a band's incoming and outgoing pipe.  Two, not one: pipe
#: glyphs are joined by 4-adjacency, so two touching risers parse as **one** pipe
#: -- a failure the grid still loads through.
SPREAD = 2


def pipes_never_touch(cells: dict[str, dict[str, list[tuple[int, int]]]]) -> None:
    """No cell of one pipe may be 4-adjacent to a cell of another.

    Pipe glyphs join by adjacency, so two risers that brush past each other parse
    as a **single** pipe -- and a grid whose eight pipes are seven still loads,
    still runs, and reads the wrong ring.  Folding the risers sideways is exactly
    what makes this reachable, so it is checked on the cells rather than argued
    from the column spacing.
    """
    owned: dict[tuple[int, int], str] = {}
    for d, per_zone in cells.items():
        for z, cs in per_zone.items():
            for c in cs:
                owned[c] = f"{d}_{z}"
    for (x, y), who in owned.items():
        for nb in ((x + 1, y), (x, y + 1)):
            other = owned.get(nb)
            if other is not None and other != who:
                raise Collision(f"pipes {who} and {other} touch at "
                                f"{(x, y)}/{nb}; they would parse as one")


def _rooms_span(zone: str) -> tuple[int, int]:
    """Columns a band's rooms occupy, relative to its bank centre.

    A ring gets one turnaround room centred on the band; ``IO`` gets an input
    room over the incoming riser and an output room beside the outgoing one.
    """
    if zone == "IO":
        return (-(SPREAD // 2) - 2, SPREAD // 2 + 3)
    return (-(RELAY_W // 2) - 1, RELAY_W // 2 + 1)


def layout(banks: tuple[tuple[int, int], ...], zones: tuple[str, ...] = ZONES):
    """Bank columns, the eight pipe columns and the four bands, from bank widths.

    `banks` is ``(channel columns, code columns)`` per band, west to east.  Band
    *k*'s pipe pair straddles the centre of bank *k*'s code window, and the bands
    are then read back off the pipes' **actual cells** by :func:`bands_of` -- the
    risers are folded sideways for capacity, so where a band ends is a property
    of the shape they took, not of the two anchor columns.  Both halves of that
    -- that the bands still cover their banks, and that the room's fixtures fit
    above them -- are *checked* rather than assumed, so a bank sweep can propose
    any shape it likes and be told no.
    """
    if len(banks) != len(zones):
        raise ValueError(f"{len(banks)} banks for {len(zones)} bands")
    made, x = [], 0
    for (nch, wcode), z in zip(banks, zones, strict=True):
        bk = B.Bank(z[0], x, nch, x + nch + wcode, (z,))
        made.append(bk)
        x = bk.code_hi + 1
    iw = made[-1].code_hi + 2
    centre = {z: (bk.code0 + bk.code_hi) // 2 for bk, z in zip(made, zones, strict=True)}
    pipe_in = {z: c - SPREAD // 2 for z, c in centre.items()}
    pipe_out = {z: c + SPREAD // 2 for z, c in centre.items()}
    legs = legs_for(centre)
    cells = {d: {z: cells_of(legs[z][d]) for z in zones} for d in ("in", "out")}
    cols = bands_of(cells["in"], cells["out"], made[0].code0, iw - 2)
    pipes_never_touch(cells)
    for bk, z in zip(made, zones, strict=True):
        if z not in cols:
            raise Collision(f"band {z} binds no column at all")
        lo, hi = cols[z]
        if lo > bk.code0 or hi < bk.code_hi:
            raise Collision(f"band {z} is [{lo},{hi}], bank {bk.name} wants "
                            f"[{bk.code0},{bk.code_hi}]")
    # Each band's rooms stand over its own centre -- a turnaround room for a
    # ring, the input and output rooms for `IO` -- so two bands whose centres are
    # too close cannot both have theirs.  Caught here rather than at stamping
    # time, so a bank sweep skips such shapes instead of crashing on one.
    for a, b in zip(zones, zones[1:], strict=False):
        need = _rooms_span(a)[1] - _rooms_span(b)[0]
        if centre[b] - centre[a] <= need:
            raise Collision(f"bands {a},{b} are {centre[b] - centre[a]} columns "
                            f"apart; their rooms need more than {need}")
    geo = Geometry(TOKEN_ZONE, cols, pipe_in, pipe_out, made[0].code0, iw,
                   lit_slack=0)
    return geo, spans(made, zones)


def spans(made: list[B.Bank], zones: tuple[str, ...]) -> dict[str, B.Bank]:
    """Every bank over a *contiguous* run of bands, keyed ``"RING:FILE"``.

    A block may only stand in a window that binds all the bands it uses, so a
    block using two bands needs a window spanning them and everything between.
    Fourteen of the thirty-seven blocks do, which is why the wide windows are
    enumerated rather than hand-written: the run ``i..j`` starts at bank *i*'s
    channels and ends at bank *j*'s last code column, so it swallows the channel
    columns in between and the packer keeps its rows clear of them for us.
    """
    out = {}
    for i, a in enumerate(made):
        for j in range(i, len(made)):
            b = made[j]
            key = f"{zones[i]}:{zones[j]}"
            out[key] = B.Bank(key, a.ch0, a.nch, b.code_hi, zones[i:j + 1])
    return out


def assign(banks: dict[str, B.Bank], zones: tuple[str, ...],
           home: dict[str, str] | None = None) -> dict[str, tuple[B.Bank, ...]]:
    """Candidate windows per block: the narrowest that binds it, then wider ones.

    A block with no pipe op at all binds nothing and could stand anywhere; it is
    given a *home* band so that the row it costs is charged to one bank instead
    of to the whole room, and the fallbacks let it spill east if its literals do
    not fit.
    """
    home = home or {}
    idx = {z: i for i, z in enumerate(zones)}
    out = {}
    for name in WORKER:
        z = B.block_zones(WORKER, name, TOKEN_ZONE)
        if not z:
            z = {home.get(name, zones[0])}
        i, j = min(idx[k] for k in z), max(idx[k] for k in z)
        # Widening westward first: the entry column moves west with the window,
        # and an entry further west is reachable from more of the room.
        cands = [banks[f"{zones[a]}:{zones[b]}"]
                 for a in range(i, -1, -1) for b in range(j, len(zones))]
        out[name] = tuple(sorted(cands, key=lambda b: b.width))
    return out


#: Bank shapes, ``(channel columns, code columns)`` per band, west to east.
#:
#: They are deliberately unequal, because the bands are.  ``RING`` and ``IDS``
#: hold nothing but one- to three-cell blocks and want almost no width; ``IO``
#: holds ``TOP`` (33 cells) and ``T_END`` (21) and wants all it can get.
#:
#: **The two sides trade against each other and neither is free.**  A narrower
#: room does not get shorter -- what makes a block tall is how often its tokens
#: cross a band, which is the program's business -- but it does make every
#: corridor that crosses it shorter, and this machine is corridor-bound: 322
#: cells of program against ~2,900 cells of corridor.  So a shape that saves a
#: row usually spends it on ticks and the choice cannot be made on either number
#: alone.  ``--sweep`` prices candidates on ``area2 x corridor``, which is close
#: enough to shortlist and *not* close enough to decide -- ticks a corridor cell
#: ran 6.2 to 6.8 across the shortlist -- so the engine picks the winner, on
#: ``LM_VALIDATOR=reference``: the fast engine is a re-implementation and is known
#: to disagree with the reference on at least one grid in this repo.
BANKS = ((6, 8), (6, 8), (6, 10), (6, 13))


#: Rip-up-and-retry budget for the router.  Not a knob to leave low: the router
#: tears a whole bank down on a failure, and the difference between 60 and 150
#: here was two rows of room -- the retries are what find the packing, not just
#: a legal routing of the one it was handed.
ATTEMPTS = 150


#: The order blocks are laid in, pinned so the build is deterministic and pays
#: for no search.  An unweighted minimum-linear-arrangement anneal, from
#: `blockorder.anneal`; re-derive with ``--sweep``.
#:
#: **Swept jointly with** :data:`BANKS`, which is the whole point.  The anneal
#: trades rows for corridor cells, so bolted onto widths already tuned under a
#: DFS order it reads as a regression -- that shape has absorbed the rows
#: already, and the first attempt here duly measured 6,084 against DFS's 5,625
#: and was thrown away.  Swept together it is the largest single lever on the
#: machine: against the DFS order at the same shape it is 2,789 corridor cells
#: down to 2,446 and 18,303 ticks down to 16,496 -- 1.00e8 down to 9.03e7.
#:
#: It is written out rather than recomputed because it cannot be recomputed:
#: `anneal` prices a move in rows, the rows come from a room built under some
#: bank shape, and this order was found under a shape the sweep no longer starts
#: from.  ``--sweep`` therefore measures it against fresh candidates instead of
#: reproducing it, which is the useful question anyway.
ORDER = [
    "INIT", "A_END", "ROUND", "OP", "REST_E", "OP_GO", "SET", "GET",
    "S_SKIP", "S_L", "S_TEST", "FOUND", "G_HIT", "REST_B", "REST", "S_HIT",
    "A_ADD", "A_L", "AVG", "D34", "TOP", "T_END", "T_MSK", "T_L", "T_MID",
    "T_X", "T_SET", "T_CMP", "PHASE", "ROSTER", "CELL", "STU", "HORN_B",
    "HORN", "PAD_B", "PAD", "PADSET",
]


def block_rows(room: B.Room) -> dict[str, int]:
    """Rows each block costs, the number `blockorder.anneal` prices its moves by."""
    return {n: len(p.plan.rows) + (2 if p.plan.branch else 0)
            for n, p in room.placed.items()}


def build_room(banks=BANKS, zones=ZONES, order=None, home=None,
               seed: int = 0, attempts: int = ATTEMPTS) -> B.Room:
    geo, bk = layout(banks, zones)
    order = list(order or ORDER or block_order(WORKER, "INIT"))
    return B.build(WORKER, "INIT", assign(bk, zones, home), geo,
                   order=order, attempts=attempts, seed=seed)


# ── the north band: three turnaround rooms, two I/O rooms, eight pipes ────────
#: Turnaround interior height.  3 is the shortest a perimeter walk may be, and
#: every row of the north band is charged against the room's height.
RELAY_H = 3

#: First row a riser may use: the relays occupy ``0 .. RELAY_H + 1``.
RISER_TOP = RELAY_H + 2

#: Rows above the worker room.
#:
#: **8 is the floor, and the floor is where to sit**, because the grid is 69 wide
#: and 78 tall: the side being squared is the *height*, so a row of north band
#: costs ~2.6% of the score and a column costs nothing at all until the room is
#: wider than it is tall.  The first version of this module had ``BAND_H = 11``
#: -- six rows of one-column-per-row staircase -- because its risers had to stay
#: inside a cone (see :func:`bands_of`).  Reading the bands off the pipe cells
#: instead lets a riser cross :data:`REACH` columns in a single row, and then the
#: only floors left are the relay's five rows and the three a riser needs to
#: leave the relay, turn along a row, and drop to its anchor.
BAND_H = 8

#: How far a riser reaches sideways from its band's centre.  Columns are free and
#: rows are not, so the capacity a ring needs is bought here rather than in
#: :data:`BAND_H`: the pair holds ``2(BAND_H - RISER_TOP - 1) + 2*REACH + 2``
#: cells, so ``REACH = 5`` puts exactly the **16 pipe cells that were measured to
#: pass** the N=16 case into each ring, and the turnaround room's one word on top.
#:
#: 5 rather than 6, even though 6 would hold 19, because the relay is
#: ``2*REACH + 3`` wide and its width is what sets how close two bands may stand:
#: 6 forces every bank out to 17 columns; swept over its own orders and shapes it
#: bottomed out at 1.012e8 against this one's 9.033e7.
#: That is not a margin worth 13% -- ``N <= 16`` is a rule of the problem, not a
#: distribution, so 17 words is the true worst case and not a typical one.
REACH = 5

#: Turnaround interior width -- exactly what the two ports need and no more,
#: because the relay's width is what sets how close two bands may stand.
RELAY_W = 2 * REACH + 3

#: Words a ring must hold at rest: ``N`` cells and a sentinel, at the largest
#: roster the rules allow.  An under-capacity ring deadlocks in *silence* -- the
#: grid loads, the roster goes in, and nothing ever comes out.
RING_WORDS = 17

#: **A turnaround room stores one word, not** :func:`~dataflow_relay.relay_words`
#: **of them.**  ``relay_words`` is throughput per lap; the room has a single
#: spawn, so one man walks it and can hold exactly one word between his ``r`` and
#: his ``s``.  Measured, not reasoned: with straight risers the N=16 case
#: deadlocked at 14 pipe cells a ring and passed at 16, which places the true
#: capacity at ``pipe cells + 1`` and nowhere near ``+ relay_words``.
RELAY_HOLDS = 1


def ring_legs(centre: int) -> dict[str, list[tuple[int, int]]]:
    """One ring's two risers, as `plotter_block.pipe` leg lists.

    Each is an L: out from the wall it leaves, along a row, then to its port.  The
    row is where the capacity comes from -- ``REACH`` cells for one row of band,
    against the single cell a row of staircase used to buy.

    The incoming riser turns on ``bot - 1`` and the outgoing one on ``top``, and
    they sit on opposite sides of the centre, so no cell of one is ever
    4-adjacent to a cell of the other -- which would make the two parse as a
    single pipe, silently.
    """
    top, bot, c = RISER_TOP, BAND_H - 1, centre
    cin, cout = c - SPREAD // 2, c + SPREAD // 2
    return {
        # relay -> room: south off the relay's wall, east along `bot - 1`, down
        "in": [(cin - REACH, top), (cin - REACH, bot - 1), (cin, bot - 1),
               (cin, bot)],
        # room -> relay: north off the worker's wall, east along `top`
        "out": [(cout, bot), (cout, top), (cout + REACH, top)],
    }


def io_legs(centre: int) -> dict[str, list[tuple[int, int]]]:
    """``IO``'s two pipes.

    No turnaround room, so no capacity to find: the input room feeds ``ri`` and
    ``so`` fills the output room, and the two rooms cannot both stand over the
    pair, so the outgoing pipe doglegs east to one of its own.
    """
    bot, c = BAND_H - 1, centre
    cin, cout = c - SPREAD // 2, c + SPREAD // 2
    return {"in": [(cin, 3), (cin, bot)],
            "out": [(cout, bot), (cout, 3), (cout + 2, 3)]}


def legs_for(centre: dict[str, int]) -> dict[str, dict[str, list]]:
    """Every pipe in the room, keyed by band and then by direction."""
    return {z: (io_legs(c) if z == "IO" else ring_legs(c))
            for z, c in centre.items()}


def build_grid(banks=BANKS, zones=ZONES, order=None, home=None, seed: int = 0,
               attempts: int = ATTEMPTS
               ) -> tuple[list[str], DebugMap, dict[str, object]]:
    """The worker room, three turnaround rooms, both I/O rooms and eight pipes."""
    from randomfun2026solvers.circuit import Circuit
    from randomfun2026solvers.dataflow_relay import relay
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.plotter_block import pipe
    from randomfun2026solvers.value_ring import stamp, walls

    room = build_room(banks, zones, order, home, seed, attempts)
    walked_cells_all_hold_a_glyph(room)
    geo, _banks = layout(banks, zones)
    iw, ih = geo.iw, room.height
    wx, wy = 1, BAND_H + 1
    g = Circuit(wx + iw + 1, wy + ih + 1)
    for y, line in enumerate(room.rows()):
        for x, ch in enumerate(line):
            if ch != " ":
                g.set(wx + x, wy + y, ch)
    walls(g, wx, wy, iw, ih)
    north, cap = wy - 1, {}
    pipes: dict[str, list[tuple[int, int]]] = {}
    # The very legs `layout` derived the bands from, shifted into grid columns:
    # drawing anything else would make the bands a description of a different
    # room than the one that gets stamped.
    centre = {z: wx + (geo.pipe_in[z] + geo.pipe_out[z]) // 2 for z in zones}
    plan = legs_for(centre)

    def draw(key: str, legs, into) -> int:
        pipes[key] = cells_of(legs)
        return pipe(g, legs, into=into)

    for z in zones:
        cin, cout = wx + geo.pipe_in[z], wx + geo.pipe_out[z]
        legs = plan[z]
        if z == "IO":
            # No turnaround: `ri` drains the input room and `so` fills the output
            # room, and the two rooms cannot both stand over the pipe pair, so the
            # outgoing pipe doglegs east to a room of its own.
            stamp(g, cin - 1, 0, ["+-+", "|I|", "+-+"])
            stamp(g, cout + 1, 0, ["+-+", "|O|", "+-+"])
            draw("in_IO", legs["in"], into=(cin, north))
            draw("out_IO", legs["out"], into=(legs["out"][-1][0], 2))
            continue
        rx = centre[z] - RELAY_W // 2 - 1
        ports = (legs["in"][0][0], legs["out"][-1][0])
        if not all(rx < p < rx + RELAY_W + 1 for p in ports):
            raise Collision(f"relay {z} spans {rx}..{rx + RELAY_W + 1}, "
                            f"the risers want ports at {ports}")
        stamp(g, rx, 0, relay(RELAY_W, RELAY_H))
        cap[z] = RELAY_HOLDS \
            + draw(f"in_{z}", legs["in"], into=(cin, north)) \
            + draw(f"out_{z}", legs["out"], into=(legs["out"][-1][0], RELAY_H + 1))
        if cap[z] < RING_WORDS:
            raise Collision(f"ring {z} holds {cap[z]} words, wanted {RING_WORDS}")

    rows = [r.rstrip() for r in g.rows()]
    while rows and not rows[-1]:
        rows.pop()
    every_pipe_cell_is_above_the_room(pipes, north)
    audit_bindings(room, wx, wy, pipes)

    d = DebugMap("gradebook - a three-ring machine, banked")
    for z in zones:
        lo, hi = geo.zone_cols[z]
        d.region(f"band:{z}", wx + lo, wy, hi - lo + 1, ih, color="#1f2937",
                 note=f"{z} pipe ops stand here; nearest column binds")
        if z in cap:
            d.region(f"relay:{z}", wx + geo.pipe_in[z] - 2, 0, RELAY_W + 2,
                     RELAY_H + 2, color="#0ea5e9",
                     note=f"turnaround of the {z} ring, {cap[z]} words")
    d.region("input", wx + geo.pipe_in["IO"] - 1, 0, 3, 3, color="#64748b")
    d.region("output", wx + geo.pipe_out["IO"] + 1, 0, 3, 3, color="#64748b")
    for name, p in room.placed.items():
        d.region(f"block:{name}", wx + p.bank.entry, wy + p.ys[0],
                 p.bank.iw - p.bank.entry, p.ys[-1] - p.ys[0] + 1,
                 note=f"{p.bank.name}: " + " ".join(WORKER[name][0]),
                 color="#f59e0b", tags=["block"])
    w, h = max(len(r) for r in rows), len(rows)
    info = {
        "worker": (iw, ih),
        "grid": (w, h),
        "area2": max(w, h) ** 2,
        "rings": cap,
        "blocks": len(room.placed),
        "corridor_cells": room.corridor_cells,
        "banks": {n: p.bank.name for n, p in room.placed.items()},
    }
    return rows, d, info


# ── the three static checks a wrong grid still loads through ─────────────────
def every_pipe_cell_is_above_the_room(pipes, north: int) -> None:
    """Every pipe cell must lie strictly north of the worker room's wall.

    This one line is what the whole band discipline rests on.  A room cell
    ``(x, y)`` is ``|x-px| + (y-py)`` from a pipe cell; if every ``py < y`` then
    the ``y`` cancels out of the comparison between pipes, "nearest pipe" is a
    function of the column alone, and a band can be a column range at all.  Let a
    single pipe cell down beside the room and the bands become row-dependent
    without anything failing to load.

    It replaces the cone check the staircase risers needed.  The cone was a
    *sufficient* condition for the same conclusion and a far more expensive one --
    it cost a row of north band per column of riser, on the axis being squared.
    """
    for key, cells in pipes.items():
        for px, py in cells:
            if py >= north:
                raise Collision(f"pipe {key} has a cell at ({px},{py}), on or "
                                f"below the room's north wall at row {north}")


def audit_bindings(room: B.Room, wx: int, wy: int,
                   pipes: dict[str, list[tuple[int, int]]]) -> None:
    """Every `r`/`s` glyph must reach the pipe its band promised it.

    Measured over *every cell* of every pipe, which is stricter than the spec's
    "segment attached to the room" and stricter than the column rule the planner
    used: if this passes, no reading of "nearest" can make the machine bind the
    wrong ring.  It is the check that caught the flat coil.
    """
    for name, p in room.placed.items():
        toks = [t for t in WORKER[name][0] if t in TOKEN_ZONE]
        placed = [(c, p.ys[i]) for i, r in enumerate(p.plan.rows)
                  for c, gl in r.cells if gl in ("r", "s")]
        if len(placed) != len(toks):
            raise Collision(f"{name}: {len(placed)} r/s glyphs, {len(toks)} tokens")
        for (cx, cy), tok in zip(placed, toks, strict=True):
            side = "in" if tok[0] == "r" else "out"
            gx, gy = wx + cx, wy + cy
            reach = sorted(
                (min(abs(gx - px) + abs(gy - py) for px, py in pipes[f"{side}_{z}"]), z)
                for z in dict.fromkeys(TOKEN_ZONE.values()))
            if reach[0][1] != TOKEN_ZONE[tok]:
                raise Collision(f"{name}: {tok!r} at ({gx},{gy}) reaches "
                                f"{reach[0][1]}, not {TOKEN_ZONE[tok]}")
            # A tie is not a win.  `Geometry.binds` breaks one westward and the
            # engine need not agree, so an op standing on one is a coin toss
            # between two rings that the grid loads through either way.
            if reach[0][0] == reach[1][0]:
                raise Collision(f"{name}: {tok!r} at ({gx},{gy}) is {reach[0][0]} "
                                f"from both {reach[0][1]} and {reach[1][1]}")


def walked_cells_all_hold_a_glyph(room: B.Room) -> None:
    """Every cell a block walks must still hold the op it was compiled from.

    A block whose row lost a glyph to a corridor still loads and still runs; it
    just computes something else.  `subset_sum_grid`'s static check, restated.
    """
    for name, p in room.placed.items():
        for i, row in enumerate(p.plan.rows):
            for col, glyph in row.cells:
                got = room.field.get(col, p.ys[i])
                if got != glyph:
                    raise Collision(f"{name} row {i}: ({col},{p.ys[i]}) holds "
                                    f"{got!r}, compiled {glyph!r}")


if __name__ == "__main__":  # pragma: no cover - the generator's CLI
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path, help="the grid")
    ap.add_argument("--html", type=Path, help="a labelled debug overlay")
    ap.add_argument("--json", type=Path, help="the debug region sidecar")
    ap.add_argument("--zones", action="store_true",
                    help="re-derive the band order and print the table")
    ap.add_argument("--sweep", action="store_true",
                    help="re-derive BANKS and ORDER jointly, print the best few")
    args = ap.parse_args()
    if args.sweep:
        from randomfun2026solvers.blockorder import anneal, edges_of

        base = block_order(WORKER, "INIT")
        rows = block_rows(build_room(BANKS, order=base))
        weight = dict.fromkeys(edges_of(WORKER), 1)
        # The pinned order rides along so the sweep can be asked whether it is
        # still the best rather than only asked for a fresh one.  It will not be
        # re-derived exactly: `anneal` prices a move in rows, the rows come from a
        # room built under some shape, and a different shape gives a different
        # cost surface -- which is the same reason the two are swept together.
        orders = {"pinned": ORDER, "dfs": base}
        for i, seeds in enumerate([(1, 5, 11), (23, 42, 99), (7,), (13,)]):
            orders[f"anneal{i}"] = anneal(base, rows, weight, steps=20_000,
                                          seeds=seeds)
        shapes = [((6, wr), (5, wi), (6, wf), (6, wo))
                  for wr in (8, 9, 10, 11) for wi in (8, 9)
                  for wf in (10, 11, 12) for wo in (13, 14)]
        # `area2 x corridor cells` is the proxy the sweep is priced on -- it is
        # not exact (ticks a corridor cell ran 6.2 to 6.8 across these shapes),
        # so it shortlists and the engine chooses.
        out = []
        for tag, o in orders.items():
            for b in shapes:
                try:
                    r = build_room(b, order=o)
                except Exception:                           # noqa: BLE001 - skipped
                    continue
                w, h = r.width + 3, r.height + BAND_H + 2
                a2 = max(w, h) ** 2
                out.append((a2 * r.corridor_cells, a2, r.corridor_cells, w, h, tag, b))
        for proxy, a2, corr, w, h, tag, b in sorted(out)[:10]:
            print(f"{tag:9s} {b}  {w:3d}x{h:3d}  area2 {a2:5d}  corridor {corr:5d}"
                  f"  proxy {proxy / 1e6:.2f}M")
        raise SystemExit
    if args.zones:
        import itertools

        for zs in itertools.permutations(ZONES):
            try:
                r = build_room(BANKS, zs)
                w, h = r.width + 3, r.height + BAND_H + 2
                print(f"{' '.join(zs):22s} {w:3d}x{h:3d}  area2 {max(w, h) ** 2:5d}"
                      f"  corridor {r.corridor_cells}")
            except Exception as exc:                        # noqa: BLE001 - reported
                print(f"{' '.join(zs):22s} does not lay out: {str(exc)[:60]}")
        raise SystemExit
    grid, dbg, meta = build_grid()
    if args.man:
        args.man.write_text("\n".join(grid) + "\n")
    if args.html:
        dbg.write_html(grid, args.html)
    if args.json:
        dbg.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(grid))
    else:
        print(meta)
