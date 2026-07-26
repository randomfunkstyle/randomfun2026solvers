#!/usr/bin/env python3
"""A reusable 2D block placer for littleman CFG machines.

`lllm_layout.plan_blocks` turns a token CFG into glyph rows; `lllm_layout` and
`snake_layout` then lay **one block per row band** down a single column of code
with one channel bank west of it.  That policy makes height the binding
dimension — snake's 44 blocks became 118 rows in a 44-column room that is 5%
full — and the score squares `max(w, h)`, so every wasted row is paid twice.

This module keeps the parts of that design that are load-bearing and drops the
part that is not:

* **A block's columns are decided by its pipe ops, not by its row.**  Pipe
  binding is nearest-column (every pipe stands on the north wall, so the `y`
  term of the Manhattan distance is common), which fixes *one* split column per
  pipe pair.  It does **not** say there may be only one bank of code: any number
  of banks may stand side by side inside the same zone, and a block with no pipe
  op at all is free to stand in any of them.
* **Corridors cross at a blank, never at a turn glyph.**  :class:`Field` records
  what each cell *does* — nothing, a heading a man passes with, or a turn glyph
  that rebinds every man who steps on it — so only turns are exclusive, and a
  corridor may cut straight through another one.  Routing is Dijkstra over
  ``(x, y, heading)`` with a turn penalty, and a failed edge rips up and retries.

The objective is the contest's: ``max(w, h)^2 x ticks``.  Corridor length *is*
tick count, so the router minimises cells walked, and the placer balances bank
heights against room width.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

__all__ = [
    "Bank",
    "Collision",
    "Field",
    "Route",
    "route_edges",
]

E, W, N, S = (1, 0), (-1, 0), (0, -1), (0, 1)
GLYPH = {E: ">", W: "<", N: "^", S: "v"}
HEADING = {v: k for k, v in GLYPH.items()}
TURNS = {E: (N, S), W: (N, S), N: (E, W), S: (E, W)}


class Collision(RuntimeError):
    pass


# ── the field ─────────────────────────────────────────────────────────────────
class Field:
    """Every cell of one room, and what it does to a man who steps on it.

    Three things can be true of a cell:

    * **nothing** — a man keeps his heading; any number of corridors may cross
      it from any directions, because they are never there at the same time;
    * **a turn glyph** — *every* man who steps on it leaves with that one
      heading, so it may be shared only by corridors that all wanted that
      heading next.  This is the one exclusive resource in the room;
    * **an op** — the man executes it.  A corridor may not touch it at all.

    `ins` is what makes the second rule checkable: a blank that two corridors
    already cross heading east can still take a `>`, but the same blank crossed
    once heading south cannot, and neither fact is visible in the glyph grid.
    """

    #: glyphs that steer rather than compute
    TURN = set(GLYPH.values())

    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        self.glyph: dict[tuple[int, int], str] = {}
        self.ins: dict[tuple[int, int], set[tuple[int, int]]] = {}

    # -- queries -------------------------------------------------------------
    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def get(self, x: int, y: int) -> str:
        return self.glyph.get((x, y), " ")

    def free(self, x: int, y: int) -> bool:
        return self.inside(x, y) and (x, y) not in self.glyph

    def can_step(self, cell: tuple[int, int], din, dout) -> bool:
        """May a man enter `cell` heading `din` and leave heading `dout`?"""
        if not self.inside(*cell):
            return False
        g = self.glyph.get(cell)
        if g is None:
            # a bare cell only redirects if we put a turn on it, and that turn
            # rebinds everyone already crossing it
            return din == dout or self.ins.get(cell, frozenset()) <= {dout}
        if g not in self.TURN:
            return False              # an op: stepping on it would execute it
        return HEADING[g] == dout     # the glyph already says where he leaves

    def step(self, cell: tuple[int, int], din, dout) -> None:
        if not self.can_step(cell, din, dout):
            raise Collision(
                f"{cell} holds {self.get(*cell)!r} with ins "
                f"{sorted(self.ins.get(cell, ()))}; cannot enter {din} leave {dout}"
            )
        if din != dout:
            self.glyph[cell] = GLYPH[dout]
        self.ins.setdefault(cell, set()).add(din)

    # -- writing code --------------------------------------------------------
    def op(self, x: int, y: int, glyph: str, d=E) -> None:
        """Place an op the man walks over heading `d`."""
        if not self.inside(x, y):
            raise Collision(f"({x},{y}) outside the {self.w}x{self.h} room")
        old = self.glyph.get((x, y))
        if old is not None and old != glyph:
            raise Collision(f"({x},{y}) holds {old!r}, cannot place {glyph!r}")
        self.glyph[(x, y)] = glyph
        self.ins.setdefault((x, y), set()).add(d)

    def walk(self, x: int, y: int, n: int, d=E) -> None:
        """Mark `n` cells the man walks over without an op on them."""
        for _ in range(n):
            if self.glyph.get((x, y)) not in (None, GLYPH.get(d)):
                raise Collision(f"({x},{y}) holds {self.get(x, y)!r} on a walked run")
            self.ins.setdefault((x, y), set()).add(d)
            x, y = x + d[0], y + d[1]

    def rows(self) -> list[str]:
        return ["".join(self.get(x, y) for x in range(self.w)) for y in range(self.h)]


# ── routing one corridor ──────────────────────────────────────────────────────
@dataclass
class Route:
    """A routed corridor: the cells walked and the heading held on each."""

    steps: list[tuple[tuple[int, int], tuple[int, int], tuple[int, int]]]

    @property
    def cells(self) -> list[tuple[int, int]]:
        return [c for c, _, _ in self.steps]

    def __len__(self) -> int:
        return len(self.steps)


#: A turn is not more expensive to *walk* than a straight cell, but it burns the
#: room's only exclusive resource, so the router breaks ties away from it.
TURN_COST = 0.25
#: Likewise a cell somebody already walks: it is still free to cross, but every
#: corridor that piles onto the same channel column cuts that column into more
#: pieces, and the edge that finds none left is rarely the one that caused it.
BUSY_COST = 0.2


def shortest(fld: Field, start: tuple[int, int], din, goal: tuple[int, int],
             *, forbid=()) -> Route | None:
    """Cheapest corridor from `start` (entered heading `din`) onto `goal`.

    `start` is the first cell outside the block — the man is already standing on
    it holding `din`.  `goal` is the target's entry glyph, which turns him east
    whatever heading he arrives with, so any approach but a head-on one works.
    Cost is cells walked, which is exactly what the edge costs in ticks.
    """
    # (cost, cell, heading leaving that cell); the start cell may itself turn
    dist: dict[tuple[tuple[int, int], tuple[int, int]], float] = {}
    prev: dict[tuple[tuple[int, int], tuple[int, int]], tuple] = {}
    pq: list[tuple[float, tuple[int, int], tuple[int, int]]] = []
    for dout in (din, *TURNS[din]):
        if start == goal:
            continue
        if not fld.can_step(start, din, dout):
            continue
        c = 1.0 + (TURN_COST if dout != din else 0.0)
        key = (start, dout)
        if c < dist.get(key, float("inf")):
            dist[key] = c
            prev[key] = (None, din)
            heapq.heappush(pq, (c, start, dout))

    best: tuple[float, tuple] | None = None
    while pq:
        cost, cell, d = heapq.heappop(pq)
        if cost > dist.get((cell, d), float("inf")):
            continue
        if best is not None and cost >= best[0]:
            break
        nxt = (cell[0] + d[0], cell[1] + d[1])
        if not fld.inside(*nxt) or nxt in forbid:
            continue
        if nxt == goal:
            # the entry glyph rebinds him east; only a head-on approach fails
            if d != W and fld.can_step(goal, d, E):
                cand = (cost + 1.0, (cell, d))
                if best is None or cand[0] < best[0]:
                    best = cand
            continue
        busy = BUSY_COST if nxt in fld.ins else 0.0
        for dout in (d, *TURNS[d]):
            if not fld.can_step(nxt, d, dout):
                continue
            c = cost + 1.0 + busy + (TURN_COST if dout != d else 0.0)
            key = (nxt, dout)
            if c < dist.get(key, float("inf")):
                dist[key] = c
                prev[key] = (cell, d)
                heapq.heappush(pq, (c, nxt, dout))
    if best is None:
        return None

    # unwind: rebuild (cell, din, dout) per step
    chain: list[tuple[tuple[int, int], tuple[int, int]]] = []
    cell, d = best[1]
    while cell is not None:
        chain.append((cell, d))
        cell, d = prev[(cell, d)]
    chain.reverse()
    steps = []
    din_i = din
    for cell, dout in chain:
        steps.append((cell, din_i, dout))
        din_i = dout
    steps.append((goal, din_i, E))
    return Route(steps)


def apply(fld: Field, route: Route) -> None:
    for cell, din, dout in route.steps:
        fld.step(cell, din, dout)


def snapshot(fld: Field) -> tuple[dict, dict]:
    return dict(fld.glyph), {k: set(v) for k, v in fld.ins.items()}


def restore(fld: Field, snap: tuple[dict, dict]) -> None:
    fld.glyph, fld.ins = dict(snap[0]), {k: set(v) for k, v in snap[1].items()}


# ── banks ─────────────────────────────────────────────────────────────────────
CW = {E: S, S: W, W: N, N: E}
CCW = {v: k for k, v in CW.items()}


@dataclass(frozen=True)
class Bank:
    """One column window of code plus the channel columns it is entered from.

    Every block in a bank is entered at ``(entry, first glyph row)`` and walks
    east from ``code0``, exactly as in the single-bank layouts — the difference
    is that several banks stand side by side, so the rows of one are the rows of
    all of them and height is divided rather than summed.

    `zones` names the pipe bands this bank's columns bind.  A bank west of the
    RING/IO midpoint binds RING and may only hold blocks whose pipe ops are
    RING ops; a block that needs both bands needs a window that spans the
    midpoint, which is what `wide` banks are for.
    """

    name: str
    ch0: int                     # first channel column
    nch: int                     # channel columns
    code_hi: int                 # last usable code column, inclusive
    zones: tuple[str, ...] = ()

    @property
    def entry(self) -> int:
        return self.ch0 + self.nch

    @property
    def code0(self) -> int:
        return self.entry + 1

    @property
    def iw(self) -> int:
        return self.code_hi + 1

    @property
    def width(self) -> int:
        return self.iw - self.ch0


def bank_geometry(bank: Bank, base):
    """`base` narrowed to one bank: its own columns, everybody's pipes.

    The pipe columns stay global on purpose — `Geometry.check_binding` then
    measures a glyph against *every* pipe in the room, so a bank whose window
    strayed over a midpoint is caught while planning rather than at run time.
    """
    from randomfun2026solvers.lllm_layout import Geometry

    cols = {}
    for z in bank.zones:
        lo, hi = base.zone_cols[z]
        lo, hi = max(lo, bank.code0), min(hi, bank.code_hi)
        if lo > hi:
            raise Collision(f"bank {bank.name} has no room for zone {z}")
        cols[z] = (lo, hi)
    return Geometry(
        token_zone=base.token_zone,     # full: `rr` is a pipe op in every bank
        zone_cols=cols,
        pipe_in=base.pipe_in,
        pipe_out=base.pipe_out,
        code0=bank.code0,
        iw=bank.iw,
    )


def block_zones(worker, name: str, token_zone: dict[str, str]) -> set[str]:
    return {token_zone[t] for t in worker[name][0] if t in token_zone}


# ── one block's rows and exits ────────────────────────────────────────────────
def lanes_of(worker, name: str, plan):
    """(lane kind, target) for every edge leaving a block, in glyph order."""
    from randomfun2026solvers.lllm_layout import _straight_key, _turn_keys

    succ = worker[name][1]
    if isinstance(succ, str):
        return [("straight", succ)]
    out = []
    key = _straight_key(plan.branch)
    if key is not None:
        out.append(("straight", succ[key]))
    for lane, turn in _turn_keys(plan.branch).items():
        out.append((turn, succ[lane]))
    return out


@dataclass
class Placed:
    """One block, once it knows which rows it owns."""

    name: str
    bank: Bank
    plan: object                     # lllm_layout.Plan
    ys: list[int] = field(default_factory=list)
    lanes: dict[str, list[tuple[int, int, int]]] = field(default_factory=dict)

    @property
    def entry_cell(self) -> tuple[int, int]:
        return (self.bank.entry, self.ys[0])


def _row_span(plan, i: int) -> tuple[int, int]:
    """The columns row `i` of a plan occupies, wrap link included."""
    cols = [c for c, _ in plan.rows[i].cells]
    lo, hi = min(cols + [plan.rows[i].start]), max(cols + [plan.rows[i].start])
    if i + 1 < len(plan.rows):
        lo, hi = min(lo, plan.rows[i].end), max(hi, plan.rows[i].end)
    return lo, hi


def _shape(worker, name: str, plan, entry: int) -> tuple[list[int], list[tuple[int, int, int]]]:
    """Row offsets of the glyph rows, and every (offset, lo, hi) the block claims.

    A branch needs the cells directly above and below its glyph free — that is
    where its two turning lanes step out — so its last glyph row is pushed down
    by one and a second free row follows it.  Those two rows are claimed for one
    *cell* each, not for their whole width, which is what lets a narrow branch
    share them with its neighbours.
    """
    k = len(plan.rows)
    if plan.branch is None:
        ys = list(range(k))
    else:
        ys = [*range(k - 1), k]
    claims = [(ys[i], *_row_span(plan, i)) for i in range(k)]
    for i in range(k - 1):          # the wrap link crosses any row in between
        col = plan.rows[i].end
        claims += [(y, col, col) for y in range(ys[i] + 1, ys[i + 1])]
    for run in lane_claims(worker, name, plan, ys, entry).values():
        claims += run
    return ys, claims


def lane_claims(worker, name: str, plan, ys: list[int], entry: int):
    """The run each lane needs to get clear of the code and into the channels.

    A lane that is only given its first cell can be walled in by a corridor that
    merely *crosses* it — the crossing pins the heading the waiting man may leave
    with, and the failure then surfaces as an unroutable edge on the far side of
    the room.  Reserving the whole run west costs a row per lane inside its own
    bank, which is what the one-block-per-band layouts spent anyway; the saving
    here comes from banks standing side by side, not from crowding the lanes.
    """
    out, last = {}, ys[-1]
    kinds = [k for k, _t in lanes_of(worker, name, plan)]
    taken = {last + _exit_offset(plan, k)[0][1] for k in kinds if k != "straight"}
    for kind in kinds:
        (cx, dy), _d = _exit_offset(plan, kind)
        if kind != "straight" or not plan.rows[-1].east:
            # a turning lane already steps off the glyph row; a west-walking
            # straight lane simply keeps going, into the channels behind it
            out[kind] = [(last + dy, min(entry, cx), max(entry, cx))]
            continue
        # walking east he must first drop past the rows his own turning lanes
        # own, so the drop column is reserved down to a row of his own
        row = last + 1
        while row in taken:
            row += 1
        out[kind] = [(y, cx, cx) for y in range(last, row)]
        out[kind].append((row, min(entry, cx), max(entry, cx)))
    return out


def _exit_offset(plan, kind: str):
    """Exit cell as (column, row offset from the last glyph row) and its heading."""
    row = plan.rows[-1]
    d = E if row.east else W
    if kind == "straight":
        return (row.end, 0), d
    turn = CW[d] if kind == "cw" else CCW[d]
    return (plan.branch_col + turn[0], turn[1]), turn


def exit_of(worker, name: str, plan, ys: list[int], kind: str):
    """The first corridor cell of a lane, and the heading the man holds on it."""
    (cx, dy), d = _exit_offset(plan, kind)
    return (cx, ys[-1] + dy), d


# ── packing blocks into rows ──────────────────────────────────────────────────
class RowMap:
    """Which column ranges of each row are already spoken for.

    This is the whole of the packing rule: a block's columns are decided by its
    pipe ops, only its rows are free, and two blocks whose columns are disjoint
    may therefore share a row.  Nothing here knows about bands.
    """

    def __init__(self) -> None:
        self.used: dict[int, list[tuple[int, int]]] = {}

    def fits(self, claims) -> bool:
        for y, lo, hi in claims:
            if y < 0:
                return False
            for a, b in self.used.get(y, ()):
                if lo <= b and a <= hi:
                    return False
        return True

    def take(self, claims) -> None:
        for y, lo, hi in claims:
            self.used.setdefault(y, []).append((lo, hi))

    @property
    def height(self) -> int:
        return max(self.used, default=-1) + 1


def pack(worker, order: list[str], plans, assign: dict[str, Bank],
         *, hints: dict[str, str] | None = None) -> tuple[dict[str, Placed], int]:
    """Give every block the topmost rows its columns are free on.

    `hints` names, per block, the block that reaches it: the search starts at
    that block's row rather than at the top of the room, so a corridor stays as
    short as the packing allows.  Corridor length is tick count, and a placer
    that only minimised height would spend every row it saved on the walk.
    """
    rows, out = RowMap(), {}
    for name in order:
        bank, plan = assign[name], plans[name]
        offs, claims = _shape(worker, name, plan, bank.entry)
        lo0, hi0 = claims[0][1], claims[0][2]
        claims[0] = (claims[0][0], min(lo0, bank.entry), hi0)
        start = 0
        if hints and (h := hints.get(name)) in out:
            start = max(0, out[h].ys[0])
        y = start
        while not rows.fits([(y + o, lo, hi) for o, lo, hi in claims]):
            y += 1
        placed = [(y + o, lo, hi) for o, lo, hi in claims]
        rows.take(placed)
        ys = [y + o for o in offs]
        out[name] = Placed(name, bank, plan, ys,
                           lane_claims(worker, name, plan, ys, bank.entry))
    return out, rows.height


# ── the room ──────────────────────────────────────────────────────────────────
@dataclass
class Room:
    field: Field
    placed: dict[str, Placed]
    order: list[str]
    edges: list[tuple[str, str, str, Route]] = field(default_factory=list)

    @property
    def height(self) -> int:
        return self.field.h

    @property
    def width(self) -> int:
        return self.field.w

    def rows(self) -> list[str]:
        return self.field.rows()

    @property
    def corridor_cells(self) -> int:
        return sum(len(r) for *_x, r in self.edges)


def stamp(fld: Field, placed: dict[str, Placed], entry: str) -> None:
    """Write every block's glyphs, its entry and its wrap links into the field."""
    for name, p in placed.items():
        plan, ys, bank = p.plan, p.ys, p.bank
        fld.op(bank.entry, ys[0], "@" if name == entry else ">", E)
        first = min(c for c, _ in plan.rows[0].cells)
        fld.walk(bank.entry + 1, ys[0], first - bank.entry - 1, E)
        for i, row in enumerate(plan.rows):
            d = E if row.east else W
            cols = [c for c, _ in row.cells]
            for c, glyph in row.cells:
                fld.op(c, ys[i], glyph, d)
            # the gaps a `seek` jumped over are walked, not free
            lo, hi = min(cols), max(cols)
            for x in range(lo, hi + 1):
                fld.ins.setdefault((x, ys[i]), set()).add(d)
            if i + 1 < len(plan.rows):
                col = row.end
                fld.op(col, ys[i], "v", d)
                for y in range(ys[i] + 1, ys[i + 1]):
                    fld.ins.setdefault((col, y), set()).add(S)
                fld.op(col, ys[i + 1], ">" if plan.rows[i + 1].east else "<", S)


def route_edges(fld: Field, worker, placed: dict[str, Placed], order: list[str],
                *, attempts: int = 24, seed: int = 0) -> list[tuple[str, str, str, Route]]:
    """Route every CFG edge, ripping the whole bank up and retrying on a failure.

    Edges are routed longest-first: a short hop can nearly always find another
    way round, a long one cannot, and a bank that is full by the time the long
    hop is asked for has to be torn down anyway.
    """
    import random

    todo = []
    for name in order:
        p = placed[name]
        for kind, target in lanes_of(worker, name, p.plan):
            cell, d = exit_of(worker, name, p.plan, p.ys, kind)
            todo.append((name, target, kind, cell, d, placed[target].entry_cell))
    # A lane's first cell belongs to that lane.  Another corridor merely
    # *crossing* it pins the heading the waiting man may leave with, and a lane
    # boxed in like that has nowhere to go — the failure looks like an
    # unroutable edge halfway across the room from the corridor that caused it.
    lane_cells = {
        (n, k): {(x, y) for y, lo, hi in run for x in range(lo, hi + 1)}
        for n, p in placed.items() for k, run in p.lanes.items()
    }
    owned: dict[tuple[int, int], tuple[str, str]] = {}
    for key, cells in lane_cells.items():
        for c in cells:
            owned[c] = key

    def span(e):
        return abs(e[5][0] - e[3][0]) + abs(e[5][1] - e[3][1])

    snap = snapshot(fld)
    rng = random.Random(seed)
    # An edge that failed once is asked for first next time; that is the whole of
    # rip-up-and-reroute, and it converges much faster than reshuffling, because
    # the edges that cannot find a way round are exactly the ones that keep failing.
    urgency: dict[tuple[str, str], int] = {}
    last: str | None = None
    for _ in range(attempts):
        restore(fld, snap)
        jitter = {(e[0], e[2]): rng.random() for e in todo}
        order_ = sorted(todo, key=lambda e: (-urgency.get((e[0], e[2]), 0),
                                             -span(e), jitter[(e[0], e[2])]))
        done: list[tuple[str, str, str, Route]] = []
        for src, dst, kind, cell, d, goal in order_:
            mine = lane_cells[(src, kind)]
            block = {c for c in owned if c not in mine and c != goal}
            r = shortest(fld, cell, d, goal, forbid=block)
            if r is None:
                last = f"{src} -{kind}-> {dst}: no corridor from {cell} to {goal}"
                urgency[(src, kind)] = urgency.get((src, kind), 0) + 1
                break
            apply(fld, r)
            done.append((src, dst, kind, r))
        else:
            return done
    raise Collision(f"routing never fit after {attempts} attempts: {last}")


def build(worker, entry: str, banks: dict[str, Bank], base_geo, *,
          order: list[str] | None = None, hints: dict[str, str] | None = None,
          attempts: int = 24, seed: int = 0) -> Room:
    """Plan, pack, stamp and route one room.  `banks` maps block name -> bank."""
    from randomfun2026solvers.lllm_layout import block_order, plan_blocks

    order = order or block_order(worker, entry)
    for name in order:
        want = block_zones(worker, name, base_geo.token_zone)
        if not want <= set(banks[name].zones):
            raise Collision(f"{name} needs {sorted(want)}, bank "
                            f"{banks[name].name} binds {sorted(banks[name].zones)}")
    # backtick columns pair down the grid as well as along a row, so the set is
    # shared across every bank rather than restarted for each
    backticks: set[int] = set()
    plans = {}
    for name in order:
        geo = bank_geometry(banks[name], base_geo)
        plans.update(plan_blocks([name], worker, geo, backticks))
    placed, height = pack(worker, order, plans, banks, hints=hints)
    width = max(b.iw for b in banks.values())
    fld = Field(width, height)
    stamp(fld, placed, entry)
    edges = route_edges(fld, worker, placed, order, attempts=attempts, seed=seed)
    used = max(y for (_x, y) in fld.glyph) + 1
    if used < height:                       # trim the slack the router did not need
        fld.h = used
    return Room(fld, placed, order, edges)
