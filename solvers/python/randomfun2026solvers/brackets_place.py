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


def splice(worker: dict, entry: str, only: frozenset | None = None) -> dict:
    """Fold every unconditional edge into the block that takes it.

    :mod:`blockplace` routes **every** CFG edge as a corridor, and a corridor
    out of a bank costs that bank's whole width — the man walks back west to the
    successor's entry glyph.  A block that ends in a plain jump has no business
    paying that: its successor's glyphs could simply follow it along the row.

    The placer has no notion of a fall-through, so the fold is done here, on the
    tables, before it ever sees them: `A -> B` with `B` unconditional becomes one
    block holding `toks(A) + toks(B)` and `B`'s successor.  A `B` with several
    predecessors is *copied* into each, which costs its glyphs again and saves a
    corridor each time — always a win here, because every one of these blocks is
    one to five glyphs and every corridor is ten to twenty cells.

    The result is a table in which **every** block ends in a branch or a halt,
    and `brackets_stack.simulate` re-checks it against the same corpus.
    """
    out = {n: (list(t), s) for n, (t, s) in worker.items()}
    for _ in range(len(out) + 1):
        folded = False
        for name, (toks, succ) in list(out.items()):
            if not isinstance(succ, str) or succ == name:
                continue
            if only is not None and name not in only:
                continue
            btoks, bsucc = out[succ]
            out[name] = (toks + list(btoks), bsucc)
            folded = True
        if not folded:
            break
    else:  # pragma: no cover - a cycle of plain jumps is an infinite loop
        raise Collision("unconditional edges form a cycle")
    # whatever is no longer reachable was only ever a jump target
    seen, stack = set(), [entry]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        succ = out[n][1]
        if isinstance(succ, str):
            stack.append(succ)
        elif succ is not None:
            stack += list(succ.values())
    return {n: (t, s) for n, (t, s) in out.items() if n in seen}


def _close(worker):
    """A terminal block becomes a self-loop so the placer has an edge to route.

    The block ends in `H`, so the man is gone before the corridor is walked; it
    costs cells, never ticks.
    """
    return {n: (t, n if s is None else s) for n, (t, s) in worker.items()}


ENTRY = {"CLASS": "PINIT", "WORK": "QINIT", "COUNT": "CINIT"}
#: Which blocks' unconditional edges are folded away.  `()` keeps the tables as
#: `brackets_stack` writes them, `None` folds every one of them; a set folds just
#: those, which is how a hot edge is bought without paying for a cold one.
FOLD: dict[str, frozenset | None] = {"CLASS": None, "WORK": None, "COUNT": None}
#: man -> band geometry, which the fold never changes
BANDS = {
    "CLASS": ({"r": "IN", "s": "TOK"}, ("IN",), ("TOK",)),
    "WORK": ({"s": "ACK"}, (), ("ACK",)),
    "COUNT": ({"r": "AK", "sw": "TERM", "so": "OUT"}, ("AK",), ("TERM", "OUT")),
}


def tables(fold=None):
    """man -> (blocks, entry, token -> band, incoming bands, outgoing bands)."""
    fold = FOLD if fold is None else fold
    return {n: (_close(splice(S.MEN[n], ENTRY[n], fold[n])), ENTRY[n], *BANDS[n])
            for n in BANDS}


MEN = tables()
BLOCKS = {n: v[0] for n, v in MEN.items()}


#: The blocks each man walks **per character**.  Every CFG edge is a corridor the
#: man walks cell by cell, and a corridor out of a bank costs that bank's whole
#: width, so the hot blocks are given a narrow bank of their own and the cold
#: ones — init, end-of-string and the two verdicts — are exiled to a second bank
#: where their corridors are paid once per case instead of once per character.
HOT = {
    "CLASS": {"PSEND", "P1", "PNEG"},
    "WORK": {"QPUSH", "QPOP", "QZERO", "QDIV", "QQUOT"},
    "COUNT": {"CLOOP", "CINC"},
}


@dataclass(frozen=True)
class Shape:
    """One room's rectangle: a hot bank, then `banks - 1` cold ones."""

    banks: int
    channels: int
    code: int
    seed: int = 0
    attempts: int = 40
    #: Code columns of the cold banks; `None` reuses `code`.
    cold: int | None = None
    #: Rows and columns handed to the router alone, trimmed again if unused.
    pad: int = 4
    slack: int = 2
    #: How far above the previous block a block may float; see `blockplace.pack`.
    lookback: int | None = 6


def make_room(name: str, shape: Shape, order: list[str] | None = None,
              men=None) -> B.Room:
    """One man's room: banks of code side by side, corridors routed between them."""
    worker, entry, tz, zin, zout = (men or MEN)[name]
    banks, x = [], 0
    for i in range(shape.banks):
        cw = shape.code if i == 0 or shape.cold is None else shape.cold
        banks.append(B.Bank(chr(65 + i), x, shape.channels, x + shape.channels + cw))
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

    base = list(order or block_order(worker, entry))
    # Hot blocks first: they go in bank 0 and the pack gives the earliest rows to
    # the earliest blocks, so laying them down first is what keeps the vertical
    # half of a hot corridor short as well as the horizontal half.
    hot_first = sorted(base, key=lambda n: n not in HOT[name])
    assign = {}
    for nm in base:
        want = B.block_zones(worker, nm, tz)
        fit = [b for b in zoned if want <= set(b.zones)]
        if not fit:
            raise Collision(f"{name}: {nm} needs {sorted(want)}, no bank has it")
        k = 0 if nm in HOT[name] else shape.banks - 1
        pref = zoned[k] if zoned[k] in fit else fit[0]
        assign[nm] = tuple([pref] + [b for b in fit if b is not pref])
    why: Exception | None = None
    for cand in ([hot_first, base] if hot_first != base else [base]):
        try:
            return B.build(worker, entry, assign, geo, order=cand,
                           attempts=shape.attempts, seed=shape.seed,
                           pad=shape.pad, width=iw + shape.slack,
                           lookback=shape.lookback)
        except Exception as exc:                       # noqa: BLE001 - retried
            why = exc
    raise Collision(f"{name}: {why}")


#: The shapes the search settled on; re-derive with ``--shapes``.
SHAPES = {
    "CLASS": Shape(1, 1, 9),
    "WORK": Shape(2, 1, 9, cold=6),
    "COUNT": Shape(2, 1, 9, cold=6),
}
FLOOR = None
ORDERS: dict[str, list[str]] = {}


def build_rooms(shapes=None, orders=None, men=None) -> dict[str, B.Room]:
    shapes = shapes or SHAPES
    orders = orders if orders is not None else ORDERS
    return {n: make_room(n, shapes[n], orders.get(n), men)
            for n in ("CLASS", "WORK", "COUNT")}


# ── routing the pipes ─────────────────────────────────────────────────────────
DIRS = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}


def route_pipe(blocked: set, w: int, h: int, attach: tuple[int, int], out: tuple[int, int],
               goal: tuple[int, int], goal_in: tuple[int, int],
               borders: set | None = None) -> list[tuple[int, int]] | None:
    """A rectilinear pipe from one room wall to another, over free cells only.

    `attach` is the source room's wall cell and `out` the direction away from it;
    the pipe's **first** cell is `attach + out` and it must still be heading `out`
    when it leaves that cell, because a pipe is discovered by the room border
    behind its first arrowhead and one that turns immediately is not found at all
    — the grid loads with the pipe silently missing.

    `borders` is every room border cell in the grid, and it is what keeps the
    rule from working in reverse.  A pipe is found by looking *behind* each
    arrowhead: any cell whose outgoing direction has a room wall behind it is the
    start of a pipe leaving that room.  A corridor that merely turns south
    against the underside of a wall therefore mints a **second** pipe out of that
    room — the grid still loads, `analyze` still reports the pipes the author
    drew, and the room's `s` glyphs then split across two queues by nearest
    column.  That is what put `brackets`' classifier tokens into three different
    FIFOs and made the worker read them out of order.

    `goal` is the destination wall cell and `goal_in` the direction the pipe must
    be travelling when it enters it.  Cost is cells, with a small turn penalty so
    a tie is broken towards the straighter run; pipes never share a cell.
    """
    import heapq

    borders = borders or set()
    start = (attach[0] + out[0], attach[1] + out[1])
    second = (start[0] + out[0], start[1] + out[1])
    last = (goal[0] - goal_in[0], goal[1] - goal_in[1])
    for c in (start, second, last):
        if not (0 <= c[0] < w and 0 <= c[1] < h) or c in blocked:
            return None
    if second == last:
        return [start, second]

    def mints_a_pipe(cell, nd) -> bool:
        """Would an arrowhead at `cell` pointing `nd` be read as a pipe start?"""
        return (cell[0] - nd[0], cell[1] - nd[1]) in borders

    dist = {(second, out): 2.0}
    prev: dict = {(second, out): (start, out)}
    pq = [(2.0, second, out)]
    best = None
    while pq:
        cost, cell, d = heapq.heappop(pq)
        if cost > dist.get((cell, d), float("inf")) or (best and cost >= best[0]):
            continue
        for nd in DIRS.values():
            if nd == (-d[0], -d[1]):
                continue
            # `nd` is the direction `cell` is *left* with, so it is `cell` whose
            # arrowhead would be read backwards into a wall, not `nxt`.
            if mints_a_pipe(cell, nd):
                continue
            nxt = (cell[0] + nd[0], cell[1] + nd[1])
            if not (0 <= nxt[0] < w and 0 <= nxt[1] < h) or nxt in blocked:
                continue
            c = cost + 1.0 + (0.3 if nd != d else 0.0)
            if nxt == last:
                # The terminal arrowhead may itself be the final bend, so `last`
                # may be entered from any side; what it may not do is have a wall
                # behind the direction it points into the goal, which would make
                # it the mouth of a pipe out of *that* room as well.
                if not mints_a_pipe(last, goal_in) and (best is None or c < best[0]):
                    best = (c, (cell, d))
                continue
            if c < dist.get((nxt, nd), float("inf")):
                dist[(nxt, nd)] = c
                prev[(nxt, nd)] = (cell, d)
                heapq.heappush(pq, (c, nxt, nd))
    if best is None:
        return None
    cells, key = [last], best[1]
    while key in prev:
        cells.append(key[0])
        key = prev[key]
    cells.append(start)
    cells.reverse()
    # de-duplicate the start, which prev's chain reaches twice
    return [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1]]


#: The five pipes: name -> (source man, destination man).  `I` and `O` are the
#: input and output rooms, which the floor plan drops into the top margin.
WIRES = (("in", "I", "CLASS"), ("tok", "CLASS", "WORK"), ("ack", "WORK", "COUNT"),
         ("term", "COUNT", "WORK"), ("out", "COUNT", "O"))


@dataclass(frozen=True)
class Floor:
    """Where the rooms stand: a tuple of columns, each a stack of rooms.

    `COUNT` must be first in its column — its two outgoing pipes have to attach
    to the same wall for the nearest-pipe split to be a column comparison, and
    the wall the floor plan can always keep clear is the north one.
    """

    columns: tuple[tuple[str, ...], ...] = (("CLASS", "WORK"), ("COUNT",))
    top: int = 5
    side: int = 2
    chan: int = 4
    vgap: int = 4


def _place(rooms, floor: Floor):
    """Interior origins for every room, and the grid the whole plan needs."""
    at, x = {}, floor.side
    for column in floor.columns:
        y = floor.top
        for name in column:
            at[name] = (x + 1, y + 1)
            y += rooms[name].height + 2 + floor.vgap
        x += max(rooms[n].width for n in column) + 2 + floor.chan
    width = x - floor.chan + floor.side
    height = max(at[n][1] + rooms[n].height + 1 for n in at) + floor.side
    return at, width, height


def _faces(rooms, at, name, toward):
    """The wall of `name` that faces `toward`, as (cell, outward direction)."""
    ox, oy = at[name]
    w, h = rooms[name].width, rooms[name].height
    cx, cy = ox + w / 2, oy + h / 2
    tx, ty = toward
    if abs(tx - cx) >= abs(ty - cy):
        r = min(h - 1, max(0, int(ty - oy)))
        return ((ox + w, oy + r), DIRS["E"]) if tx > cx else ((ox - 1, oy + r), DIRS["W"])
    c = min(w - 1, max(0, int(tx - ox)))
    return ((ox + c, oy + h), DIRS["S"]) if ty > cy else ((ox + c, oy - 1), DIRS["N"])


def build_grid(shapes=None, orders=None, floor: Floor | None = None, seed: int = 0,
               men=None):
    """The whole machine: three rooms, five pipes, an input room and an output."""
    import random

    from randomfun2026solvers.man_debug import DebugMap

    rooms = build_rooms(shapes, orders, men)
    floor = floor or FLOOR or Floor()
    at, width, height = _place(rooms, floor)

    # the I/O rooms go in the top margin, over the columns they talk to
    io = {"I": (at["CLASS"][0], 1), "O": (at["COUNT"][0] + rooms["COUNT"].width - 1, 1)}
    boxes = {n: (at[n][0] - 1, at[n][1] - 1, rooms[n].width + 2, rooms[n].height + 2)
             for n in rooms}
    boxes.update({n: (x - 1, y - 1, 3, 3) for n, (x, y) in io.items()})

    blocked = {(x, y) for bx, by, bw, bh in boxes.values()
               for x in range(bx, bx + bw) for y in range(by, by + bh)}

    # ── the attach cells, and the one binding the layout has to police ───────
    cnt, ox, oy = rooms["COUNT"], *at["COUNT"]
    ends = {("term", "COUNT"): ((ox, oy - 1), DIRS["N"]),
            ("out", "COUNT"): ((ox + cnt.width - 1, oy - 1), DIRS["N"])}
    centre = {n: (bx + bw / 2, by + bh / 2) for n, (bx, by, bw, bh) in boxes.items()}
    used = {cell for cell, _d in ends.values()}
    for key, src, dst in WIRES:
        for me, other in ((src, dst), (dst, src)):
            if (key, me) in ends or me in io:
                continue
            cell, d = _faces(rooms, at, me, centre[other])
            # Two pipes on the same face want the same cell; walk one of them
            # along the wall rather than losing it, which is what a shared
            # attach cell would silently become.
            ox, oy = at[me]
            w, h = rooms[me].width, rooms[me].height
            span = ((ox, ox + w - 1), (cell[1], cell[1])) if d[0] == 0 else \
                   ((cell[0], cell[0]), (oy, oy + h - 1))
            while cell in used:
                cell = (min(cell[0] + (d[0] == 0), span[0][1]),
                        min(cell[1] + (d[0] != 0), span[1][1]))
                if cell in used and cell == (span[0][1], span[1][1]):
                    raise Collision(f"{me}: no free attach cell for {key}")
            used.add(cell)
            ends[(key, me)] = (cell, d)
    for n, (x, y) in io.items():
        key = "in" if n == "I" else "out"
        ends[(key, n)] = ((x, y + 1), DIRS["S"])

    # ── route, longest first, ripping the whole plan up on a failure ─────────
    rng = random.Random(seed)
    order = sorted(WIRES, key=lambda e: -abs(centre[e[1]][0] - centre[e[2]][0])
                   - abs(centre[e[1]][1] - centre[e[2]][1]))
    for _ in range(24):
        taken, paths = set(blocked), {}
        for key, src, dst in order:
            (sa, sd), (da, dd) = ends[(key, src)], ends[(key, dst)]
            path = route_pipe(taken, width, height, sa, sd, da,
                              (-dd[0], -dd[1]))
            if path is None:
                break
            paths[key] = (path, da)
            taken |= set(path)
        else:
            break
        rng.shuffle(order)
    else:
        raise Collision("the five pipes do not fit this floor plan")

    g = Circuit(width, height)
    for name, room in rooms.items():
        ox, oy = at[name]
        walls(g, ox, oy, room.width, room.height)
        for y, line in enumerate(room.rows()):
            for dx, ch in enumerate(line):
                if ch != " ":
                    g.set(ox + dx, oy + y, ch)
    for name, (x, y) in io.items():
        stamp(g, x - 1, y - 1, ["+-+", f"|{name}|", "+-+"])
    caps = {k: draw_pipe(g, p, into=into) for k, (p, into) in paths.items()}

    rows = [r.rstrip() for r in g.rows()]
    while rows and not rows[-1]:
        rows.pop()

    dbg = DebugMap("brackets — a three-man base-3 register stack")
    for name, room in rooms.items():
        bx, by, bw, bh = boxes[name]
        dbg.region(f"room:{name}", bx, by, bw, bh, color="#f59e0b",
                   note=f"{len(room.placed)} blocks, "
                        f"{room.corridor_cells} corridor cells")
    for key, (path, _into) in paths.items():
        dbg.lane(f"pipe:{key}", path, kind="pipe",
                 note=f"{caps[key]} cells of capacity")
    for name in io:
        bx, by, bw, bh = boxes[name]
        dbg.region(name, bx, by, bw, bh, color="#64748b")

    info = {
        "grid": (max(len(r) for r in rows), len(rows)),
        "rooms": {n: (r.width, r.height) for n, r in rooms.items()},
        "corridor_cells": {n: r.corridor_cells for n, r in rooms.items()},
        "pipe_capacity": caps,
        "origins": at,
    }
    return rows, dbg, info
