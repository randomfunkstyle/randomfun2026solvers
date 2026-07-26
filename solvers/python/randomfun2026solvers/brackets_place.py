#!/usr/bin/env python3
"""`brackets` as three men in three rooms, laid out by :mod:`blockplace`.

:mod:`brackets_stack` is the machine: a one-register base-3 stack, verified at
the op level against every string through length seven.  This module is only its
**layout** — it turns each man's token CFG into a room of glyph rows and routed
corridors, stands the three rooms side by side, and threads the five pipes
through a band above them.

## Why three rooms, and what each one binds

`r`/`s` bind the **nearest** pipe by Manhattan distance to the wall cell that
pipe attaches to, so a room with one incoming and one outgoing pipe has no
binding question to answer at all.  That is true of `CLASS` (in -> tok) and of
`WORK`, whose two incoming pipes are read with `R`, which takes from *any* ready
pipe rather than the nearest one.  Only `COUNT` has to be careful: it sends on
two pipes, `sw` to the worker's terminator and `so` to the output room, so its
two outgoing attach cells stand on the **same wall row** at opposite ends of it,
which makes the split purely columnar and lets :class:`Geometry` police it while
the blocks are still being planned.

## The band

Five pipes and two I/O rooms share the rows above the three rooms.  Pipes may
not cross, so the horizontals are stacked in the order that makes their spans
**nest**: every pipe's two vertical legs must miss every horizontal below it.
Reading the wall columns west to east that comes out as

    CLASS:  in            tok-out
    WORK:   tok-in  ack-out  term-in
    COUNT:  term-out  ack-in  out

with `ack` on the topmost horizontal, then `tok`, then `term` — see
:func:`band_rows` for the four crossings that pins.

A pipe's first cell must point *away* from its room, so every horizontal sits at
least two rows above the wall it leaves; a pipe that turns immediately does not
parse, and the grid still loads with that pipe silently missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from randomfun2026solvers import blockplace as B
from randomfun2026solvers import brackets_stack as S
from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.lllm_layout import Geometry, block_order
from randomfun2026solvers.plotter_block import pipe as draw_pipe
from randomfun2026solvers.value_ring import stamp, walls

if TYPE_CHECKING:  # pragma: no cover
    from randomfun2026solvers.man_debug import DebugMap

__all__ = ["MEN", "Shape", "build_grid", "build_rooms", "make_room"]


def _close(worker):
    """A terminal block becomes a self-loop so the placer has an edge to route.

    The block ends in `H`, so the man is gone before the corridor is walked; it
    costs cells, never ticks.
    """
    return {n: (t, n if s is None else s) for n, (t, s) in worker.items()}


#: man -> (blocks, entry, token -> band, incoming bands, outgoing bands)
MEN = {
    "CLASS": (_close(S.CLASS), "PINIT", {"r": "IN", "s": "TOK"}, ("IN",), ("TOK",)),
    "WORK": (_close(S.WORK), "QINIT", {"s": "ACK"}, (), ("ACK",)),
    "COUNT": (_close(S.COUNT), "CINIT", {"r": "AK", "sw": "TERM", "so": "OUT"},
              ("AK",), ("TERM", "OUT")),
}


@dataclass(frozen=True)
class Shape:
    """The four knobs that decide one room's rectangle."""

    banks: int
    channels: int
    code: int
    seed: int = 0
    attempts: int = 40


def make_room(name: str, shape: Shape, order: list[str] | None = None) -> B.Room:
    """One man's room: banks of code side by side, corridors routed between them."""
    worker, entry, tz, zin, zout = MEN[name]
    banks, x = [], 0
    for i in range(shape.banks):
        banks.append(B.Bank(chr(65 + i), x, shape.channels,
                            x + shape.channels + shape.code))
        x = banks[-1].code_hi + 1
    iw = banks[-1].code_hi + 1
    lo, hi = banks[0].code0, iw - 2

    zone_cols = {z: (lo, hi) for z in zin}
    pipe_in = dict.fromkeys(zin, 0)
    if len(zout) == 1:
        pipe_out = {zout[0]: 0}
        zone_cols[zout[0]] = (lo, hi)
    else:
        # The two outgoing attach cells stand at the ends of the same wall, so
        # the nearest-pipe split is one column and `Geometry` can check it.
        pipe_out = {"TERM": 0, "OUT": iw - 1}
        mid = (iw - 1) // 2
        zone_cols["TERM"], zone_cols["OUT"] = (lo, mid - 1), (mid + 1, hi)
        for z in ("TERM", "OUT"):
            if zone_cols[z][0] > zone_cols[z][1]:
                raise Collision(f"{name}: band {z} has no columns at width {iw}")
    geo = Geometry(tz, zone_cols, pipe_in, pipe_out, lo, iw, lit_slack=0)

    # A bank carries only the bands its own window covers, so a block that needs
    # one it cannot reach falls through to a bank that can rather than failing.
    zoned = [B.Bank(b.name, b.ch0, b.nch, b.code_hi,
                    tuple(z for z, (a, c) in zone_cols.items()
                          if max(a, b.code0) <= min(c, b.code_hi)))
             for b in banks]

    order = list(order or block_order(worker, entry))
    assign = {}
    for i, nm in enumerate(order):
        want = B.block_zones(worker, nm, tz)
        fit = [b for b in zoned if want <= set(b.zones)]
        if not fit:
            raise Collision(f"{name}: {nm} needs {sorted(want)}, no bank has it")
        k = min(shape.banks - 1, i * shape.banks // len(order))
        pref = zoned[k] if zoned[k] in fit else fit[0]
        assign[nm] = tuple([pref] + [b for b in fit if b is not pref])
    return B.build(worker, entry, assign, geo, order=order,
                   attempts=shape.attempts, seed=shape.seed)


#: The shapes the search settled on; re-derive with ``--shapes``.
SHAPES = {
    "CLASS": Shape(1, 1, 6),
    "WORK": Shape(2, 1, 7),
    "COUNT": Shape(2, 1, 6),
}
ORDERS: dict[str, list[str]] = {}


def build_rooms(shapes=None, orders=None) -> dict[str, B.Room]:
    shapes = shapes or SHAPES
    orders = orders if orders is not None else ORDERS
    return {n: make_room(n, shapes[n], orders.get(n)) for n in ("CLASS", "WORK", "COUNT")}


# ── the band above the rooms ──────────────────────────────────────────────────
#: Rows 0-2 hold the two I/O rooms; 3, 4 and 5 the three horizontals; 6 is the
#: stub row every pipe leaves its wall into; 7 is the rooms' north wall.
R_ACK, R_TOK, R_TERM, R_STUB, NORTH = 3, 4, 5, 6, 7
GAP = 1


def band_rows() -> dict[str, int]:
    """Which row each horizontal runs on, and why that order and no other.

    A pipe's two vertical legs run from its horizontal down to the stub row, so
    they cut every horizontal below them.  Four crossings have to miss:

    * `ack` over `tok`   — its wall columns lie east of `tok`'s;
    * `ack` over `term`  — `ack` leaves the worker west of where `term` arrives,
      and reaches the counter east of where `term` leaves it;
    * `tok` over `term`  — `tok` arrives at the worker west of `term`;
    * `in` and `out` over all three — the input drops west of every span, the
      output rises east of every span.
    """
    return {"ack": R_ACK, "tok": R_TOK, "term": R_TERM}


def build_grid(shapes=None, orders=None):
    """The whole machine: three rooms, five pipes, an input room and an output."""
    from randomfun2026solvers.man_debug import DebugMap

    rooms = build_rooms(shapes, orders)
    cls, wrk, cnt = rooms["CLASS"], rooms["WORK"], rooms["COUNT"]

    # ── floor plan: three interiors in a row, north walls on the same line ────
    xs, x = {}, 1
    for name in ("CLASS", "WORK", "COUNT"):
        xs[name] = x
        x += rooms[name].width + 1 + GAP
    width = x - GAP + 1
    height = NORTH + 1 + max(r.height for r in rooms.values()) + 1

    def col(name: str, c: int) -> int:
        return xs[name] + c

    ci = col("CLASS", 0)                      # input arrives here
    t1 = col("CLASS", cls.width - 1)          # tokens leave here
    t2 = col("WORK", 0)                       # ... and arrive here
    a1 = col("WORK", wrk.width // 2)          # acks leave the worker
    bt = col("WORK", wrk.width - 1)           # the terminator arrives
    ct = col("COUNT", 0)                      # ... having left here
    a2 = col("COUNT", cnt.width // 2)         # acks arrive here
    co = col("COUNT", cnt.width - 1)          # the answer leaves here

    g = Circuit(width, height)
    for name, room in rooms.items():
        ox, oy = xs[name], NORTH + 1
        walls(g, ox, oy, room.width, room.height)
        for y, line in enumerate(room.rows()):
            for dx, ch in enumerate(line):
                if ch != " ":
                    g.set(ox + dx, oy + y, ch)

    stamp(g, ci - 1, 0, ["+-+", "|I|", "+-+"])
    stamp(g, co - 1, 0, ["+-+", "|O|", "+-+"])

    legs = {
        "in": [(ci, 3), (ci, R_STUB)],
        "tok": [(t1, R_STUB), (t1, R_TOK), (t2, R_TOK), (t2, R_STUB)],
        "ack": [(a1, R_STUB), (a1, R_ACK), (a2, R_ACK), (a2, R_STUB)],
        "term": [(ct, R_STUB), (ct, R_TERM), (bt, R_TERM), (bt, R_STUB)],
        "out": [(co, R_STUB), (co, 3)],
    }
    into = {"in": (ci, NORTH), "tok": (t2, NORTH), "ack": (a2, NORTH),
            "term": (bt, NORTH), "out": (co, 2)}
    caps = {k: draw_pipe(g, v, into=into[k]) for k, v in legs.items()}

    rows = [r.rstrip() for r in g.rows()]
    while rows and not rows[-1]:
        rows.pop()

    dbg = DebugMap("brackets — a three-man base-3 register stack")
    for name, room in rooms.items():
        dbg.region(f"room:{name}", xs[name] - 1, NORTH, room.width + 2,
                   room.height + 2, color="#f59e0b",
                   note=f"{len(room.placed)} blocks, "
                        f"{room.corridor_cells} corridor cells")
    for key, path in legs.items():
        dbg.lane(f"pipe:{key}", path, kind="pipe",
                 note=f"{caps[key]} cells of capacity")
    dbg.region("input", ci - 1, 0, 3, 3, color="#64748b")
    dbg.region("output", co - 1, 0, 3, 3, color="#64748b")

    info = {
        "grid": (max(len(r) for r in rows), len(rows)),
        "rooms": {n: (r.width, r.height) for n, r in rooms.items()},
        "corridor_cells": {n: r.corridor_cells for n, r in rooms.items()},
        "pipe_capacity": caps,
        "attach": {"in": ci, "tok": (t1, t2), "ack": (a1, a2),
                   "term": (ct, bt), "out": co},
    }
    return rows, dbg, info
