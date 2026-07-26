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

## Why the zone order is what it is

Band order is a free choice and it decides how wide the multi-band blocks have
to be.  ``RING IDS FILE IO`` is what :func:`tuned_zones` settles on: ``FILE`` is
the hub -- it shares a block with ``RING`` seven times and with ``IO`` seven
times -- so it wants a neighbour on each side, and ``IDS`` is the rarest band
(four blocks) so it pays least for being pushed to an edge of the interior.
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


def bands_of(pipe_in: dict[str, int], pipe_out: dict[str, int],
             lo: int, hi: int) -> dict[str, tuple[int, int]]:
    """The columns in ``[lo, hi]`` where an `r` and an `s` bind the same band.

    `Geometry.binds` is the authority on where a pipe op may stand, so the bands
    are read *out of it* rather than derived from the midpoints by hand.  A
    column where the incoming and outgoing rules disagree belongs to no band and
    is simply left out -- it is the dead gap the channel columns stand in.
    """
    probe = Geometry(TOKEN_ZONE, {}, pipe_in, pipe_out, lo, hi + 1)
    runs: dict[str, tuple[int, int]] = {}
    for x in range(lo, hi + 1):
        z = probe.binds(x, "rr")
        if z != probe.binds(x, "sr"):
            continue
        a, b = runs.get(z, (x, x))
        runs[z] = (min(a, x), max(b, x))
    return runs


#: Columns between a band's incoming and outgoing pipe.  Two, not one: the two
#: risers run side by side for most of the north band, and pipe glyphs are joined
#: by 4-adjacency, so touching risers would parse as **one** pipe — a failure the
#: grid still loads through.  Spreading them symmetrically about the bank centre
#: leaves both midpoints on the same column, so the bands do not move.
SPREAD = 2


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
    *k*'s pipe pair straddles the centre of bank *k*'s code window, which puts
    every split inside the channel columns of the next bank along: the gap
    between two code windows is ``nch + 1`` wide and a split lands in its middle
    whenever the two banks are within a gap's width of each other.  Both halves
    of that -- that the bands land where they should, and that the room's fixtures
    still fit above them -- are then *checked* rather than assumed, so a bank
    sweep can propose any shape it likes and be told no.
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
    cols = bands_of(pipe_in, pipe_out, made[0].code0, iw - 2)
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
#: **Width is free here and corridor cells are not.**  The room's height barely
#: moves -- 62 to 64 rows across every shape ``--sweep`` will accept -- because
#: what makes a block tall is how often its tokens cross a band, and that is the
#: program's business, not the layout's.  So the side that is squared is the
#: *height*, at 77, and any shape narrower than that scores the same 5,929.  The
#: tie is broken on corridor length, which is ticks: this shape walks 2,783 cells
#: of corridor where the square one walks 3,002.
BANKS = ((6, 8), (5, 8), (6, 10), (6, 13))


def build_room(banks=BANKS, zones=ZONES, order=None, home=None,
               seed: int = 0, attempts: int = 60) -> B.Room:
    geo, bk = layout(banks, zones)
    order = list(order or block_order(WORKER, "INIT"))
    return B.build(WORKER, "INIT", assign(bk, zones, home), geo,
                   order=order, attempts=attempts, seed=seed)


# ── the north band: three turnaround rooms, two I/O rooms, eight pipes ────────
#: Turnaround interior height.  3 is the shortest a perimeter walk may be, and
#: every row of the north band is charged against the room's height.
RELAY_H = 3

#: Rows above the worker room: the relays take the top ``RELAY_H + 2`` and the
#: eight risers have what is left.
#:
#: 11 is the floor.  A riser is a staircase (:func:`_riser`), so it gathers
#: ``2(BAND_H - RELAY_H - 3)`` cells, and a ring needs :data:`RING_WORDS` across
#: both of them plus the one its turnaround room holds -- which makes
#: ``4(BAND_H - 6) + 1 >= 17``, and 10 misses by a word.
BAND_H = 11

#: How far a riser's port stands from its band's centre: half the pipe spread,
#: plus one column for every row of staircase after the first.
_REACH = SPREAD // 2 + (BAND_H - RELAY_H - 4)

#: Turnaround interior width -- exactly what the two ports need and no more,
#: because the relay's width is what sets how close two bands may stand.
RELAY_W = 2 * _REACH + 1

#: Words a ring must hold at rest: ``N`` cells and a sentinel, at the largest
#: roster the rules allow.  An under-capacity ring deadlocks in *silence* -- the
#: grid loads, the roster goes in, and nothing ever comes out.
RING_WORDS = 17

#: **A turnaround room stores one word, not** :func:`~dataflow_relay.relay_words`
#: **of them.**  ``relay_words`` is throughput per lap; the room has a single
#: spawn, so one man walks it and can hold exactly one word between his ``r`` and
#: his ``s``.  Measured, not reasoned: with 13x3 relays and straight risers the
#: N=16 case deadlocked at 14 pipe cells a ring and passed at 16, which places
#: the true capacity at ``pipe cells + 1`` and nowhere near ``+ 11``.
RELAY_HOLDS = 1


def _riser(anchor: int, top: int, bot: int, east: bool) -> list[tuple[int, int]]:
    """One ring riser, as a staircase that fans out from its anchor going up.

    A straight riser needs ``RING_WORDS`` rows of north band and the band is
    charged against the room's height.  This buys the cells out of the width
    instead -- which is already spent -- without giving up the property that
    makes the whole band discipline sound.

    That property is that **the anchor is the nearest cell of its own pipe to
    every cell of the room**.  A cell one row further from the room may stand one
    column off the anchor and still lose to it, two rows two columns, and so on:
    the legal region is a cone, and a staircase is the longest rectilinear path
    that stays on its boundary.  A riser coiled *flat* -- the first version of
    this module -- leaves the cone at once, and :func:`audit_bindings` duly found
    an `st` whose nearest cell was the neighbouring band's coil.

    The first step is along the wall the pipe leaves, because a pipe whose first
    cell does not point away from its room fails to parse in silence; that costs
    the widest rank of the cone and is why the staircase is ``2(bot-top)`` cells
    rather than ``2(bot-top)+1``.
    """
    d, m = (1 if east else -1), bot - top
    if east:                                    # room -> relay, leaving northward
        pts = [(anchor, bot), (anchor, bot - 1)]
        for k in range(1, m):
            pts += [(anchor + d * k, bot - k), (anchor + d * k, bot - k - 1)]
    else:                                       # relay -> room, leaving southward
        pts = [(anchor + d * (m - 1), top), (anchor + d * (m - 1), top + 1)]
        for k in range(m - 2, -1, -1):
            pts += [(anchor + d * k, top + m - 1 - k), (anchor + d * k, top + m - k)]
    return pts


def _ring_legs(cin: int, cout: int):
    """Both risers of one ring, as `plotter_block.pipe` leg lists.

    The incoming one fans west of its anchor and the outgoing one east, so the
    two cones stand on opposite sides of the bank centre with the free column
    between them -- pipe glyphs join by 4-adjacency, and two touching risers
    would parse as one pipe.
    """
    top, bot = RELAY_H + 2, BAND_H - 1
    return {"in": _riser(cin, top, bot, east=False),
            "out": _riser(cout, top, bot, east=True)}


def build_grid(banks=BANKS, zones=ZONES, order=None, home=None, seed: int = 0
               ) -> tuple[list[str], DebugMap, dict[str, object]]:
    """The worker room, three turnaround rooms, both I/O rooms and eight pipes."""
    from randomfun2026solvers.circuit import Circuit
    from randomfun2026solvers.dataflow_relay import relay
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.plotter_block import pipe
    from randomfun2026solvers.value_ring import stamp, walls

    room = build_room(banks, zones, order, home, seed)
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

    def draw(key: str, legs, into) -> int:
        pts = [legs[0]]
        for a, b in zip(legs, legs[1:], strict=False):
            sx = (b[0] > a[0]) - (b[0] < a[0])
            sy = (b[1] > a[1]) - (b[1] < a[1])
            x, y = a
            while (x, y) != b:
                x, y = x + sx, y + sy
                pts.append((x, y))
        pipes[key] = pts
        return pipe(g, legs, into=into)

    for z in zones:
        cin, cout = wx + geo.pipe_in[z], wx + geo.pipe_out[z]
        if z == "IO":
            # No turnaround: `ri` drains the input room and `so` fills the output
            # room, and the two rooms cannot both stand over the pipe pair, so the
            # outgoing riser doglegs east to a room of its own.
            stamp(g, cin - 1, 0, ["+-+", "|I|", "+-+"])
            stamp(g, cout + 1, 0, ["+-+", "|O|", "+-+"])
            draw("in_IO", [(cin, 3), (cin, north - 1)], into=(cin, north))
            draw("out_IO", [(cout, north - 1), (cout, 3), (cout + 2, 3)],
                 into=(cout + 2, 2))
            continue
        legs = _ring_legs(cin, cout)
        rx = (cin + cout) // 2 - RELAY_W // 2 - 1
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
    anchors_are_the_nearest_cells(pipes, iw, north)
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
def anchors_are_the_nearest_cells(pipes, iw: int, north: int) -> None:
    """No cell of any pipe may beat its own anchor for any cell of the room.

    This is the premise the whole band discipline rests on: if every pipe's only
    cell within reach is the one on the north wall, then "nearest pipe" reduces
    to "nearest column" and `Geometry.binds` -- which the planner consults, one
    block at a time, before any of this is drawn -- is exactly right.  It is what
    :func:`_riser`'s cone is built to preserve, and it is cheap enough to check
    outright: every pipe cell against every column of the room's top row, which
    bounds every row below it because they all lie further south.
    """
    for key, cells in pipes.items():
        # An incoming pipe flows relay -> room, so the room end is its *last*
        # cell; an outgoing one flows room -> relay and the room end is its first.
        ax, ay = cells[-1] if key.startswith("in") else cells[0]
        if ay != north - 1:
            raise Collision(f"pipe {key} anchors at row {ay}, not {north - 1}")
        rest = [c for c in cells if c != (ax, ay)]
        for x in range(iw + 2):
            here = abs(x - ax) + 1                      # to the anchor, one row down
            far = min((abs(x - px) + abs(north - py) for px, py in rest),
                      default=here + 1)
            if far < here:
                raise Collision(f"pipe {key}: column {x} is nearer a bend than "
                                f"the anchor ({far} < {here})")



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
            best = min(TOKEN_ZONE.values(),
                       key=lambda z: min(abs(gx - px) + abs(gy - py)
                                         for px, py in pipes[f"{side}_{z}"]))
            if best != TOKEN_ZONE[tok]:
                raise Collision(f"{name}: {tok!r} at ({gx},{gy}) reaches {best}, "
                                f"not {TOKEN_ZONE[tok]}")


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
                    help="re-derive BANKS: try bank shapes, print the best few")
    args = ap.parse_args()
    if args.sweep:
        import random

        cands = [((n, wr), (5, wi), (n, wf), (n, wo))
                 for n in (5, 6, 7) for wr in (7, 9, 11, 13)
                 for wi in (8, 10, 12) for wf in (9, 11, 13, 15)
                 for wo in (13, 15, 17, 19, 21)]
        random.Random(1).shuffle(cands)
        seen = []
        for b in cands:
            try:
                r = build_room(b, attempts=30)
            except Exception:                               # noqa: BLE001 - skipped
                continue
            w, h = r.width + 3, r.height + BAND_H + 2
            seen.append((max(w, h) ** 2, r.corridor_cells, w, h, b))
        for a2, corr, w, h, b in sorted(seen)[:8]:
            print(f"area2 {a2:5d}  {w:3d}x{h:3d}  corridor {corr:5d}  {b}")
        raise SystemExit
    if args.zones:
        import itertools

        for zs in itertools.permutations(ZONES):
            try:
                r = build_room(BANKS, zs)
                w, h = r.width + 3, r.height + BAND_H + 2
                print(f"{' '.join(zs):22s} {w:3d}x{h:3d}  area2 {max(w, h) ** 2:5d}"
                      f"  corridor {r.corridor_cells}")
            except Collision as exc:
                print(f"{' '.join(zs):22s} does not lay out: {exc}")
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
