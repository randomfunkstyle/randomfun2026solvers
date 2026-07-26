#!/usr/bin/env python3
"""Compile :data:`lllm_ring.WORKER` — a token CFG — into one little-man room.

Deliberately naive. `footprint-tick` squares the bounding box, but the field on
this problem tops out at 1.27e9 with a 25x cliff to second place, so anything
comfortably under 3e10 collects the same points a hand-folded 22x26 would. This
lays one block to a row band and spends rows freely.

## The band

A block owns a horizontal run walked **east**, wrapping to the next row walked
west when it has to come back for a pipe op (see zones). Its rows are adjacent
except where a free row is needed:

    non-branching   k glyph rows, then one straight-lane row
    branching       k glyph rows with a free row above the last, then a
                    south-lane row, then a straight-lane row

`X` entered heading east turns clockwise (south) on positive, counter-clockwise
(north) on negative and goes straight on zero; `x` always turns; `d` turns
clockwise or goes straight. So a branch's lanes leave from the cells directly
above and below the branch glyph, which is why the last glyph row is the one
that needs free rows around it.

Every lane — north, south and straight — runs **west** along its own row into a
channel bank west of the code, turns into a free column there, runs vertically
to its target's glyph row and turns east. Targets are all entered at the same
cell, `(NC, first glyph row)`, holding `>`: a fall-through arrives there from
above heading south and a channel arrival heading east, and both leave east.
That one convention is what makes fall-through and routed edges the same thing.

## Zones

Six pipes attach to the room's **north** wall, so the distance from any cell to
any of them is `|x - c| + y + 1` — the `y` term is common and "nearest pipe"
collapses to "nearest column" at every row (`tcp_ring`'s rule). Each pipe op
must therefore sit in its own column band, and a block that revisits a band it
has already passed has to wrap onto the next row.

Store first, then file, then I/O. Of 285 pipe ops only 13 are store ops — but
they are the ones inside `SEEK` and `REST`, which turn the whole ring once per
interpreted tick, so they are the ops that must be one column from the entry.
The 246 file ops each run about once a tick and can afford the walk.

## Literals

A backtick pairs on rows *and* columns independently, and a column holding two
of them makes a vertical literal out of whatever lies between — a load error the
moment that includes a turn glyph. Rows are cheap here and columns are cheaper,
so every backtick in the room gets a **column of its own**; the digits between a
pair may be padded with spaces, which literals ignore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.lllm_ring import WORKER

if TYPE_CHECKING:  # pragma: no cover
    from randomfun2026solvers.man_debug import DebugMap

__all__ = ["Plan", "Room", "build_room", "plan_blocks"]

#: Which pipe band each pipe op has to stand in.
TOKEN_ZONE: dict[str, str] = {
    "rr": "ST",
    "sr": "ST",
    "rq": "FI",
    "sq": "FI",
    "ri": "IO",
    "sp": "IO",
}
ZONE_ORDER = ("ST", "FI", "IO")

#: Column widths of the three bands, west to east.  `ST` is narrow and first
#: because `SEEK`/`REST` are the only hot loops; `FI` is wide because the file
#: rotations are long straight runs; `IO` needs room for `MOVE`'s four sends.
ZONE_WIDTH = {"ST": 6, "FI": 46, "IO": 26}
#: Columns of slack east of the last band, for literals and long arithmetic.
TAIL_WIDTH = 60

BRANCH_GLYPHS = ("X", "x", "d")


def _straight_key(glyph: str) -> str | None:
    """Which lane of a branch continues straight ahead, if any."""
    return {"X": "zero", "d": "zero", "x": None}[glyph]


def _straight_target(plan: Plan, succ: object) -> str | None:
    """The block this one continues straight into, or ``None`` if it never does.

    An `x` branch has no straight lane, so it needs no straight-lane row — and a
    row is the charged dimension on this machine.
    """
    if isinstance(succ, str):
        return succ
    key = _straight_key(plan.branch)
    return None if key is None else succ[key]  # type: ignore[index]


def _turn_keys(glyph: str) -> dict[str, str]:
    """lane name -> "cw" | "ccw" for the lanes of a branch that turn."""
    if glyph == "X":
        return {"pos": "cw", "neg": "ccw"}
    if glyph == "x":
        return {"one": "cw", "zero": "ccw"}
    return {"pos": "cw"}


@dataclass
class Row:
    """One walked row of a block: a direction and the glyphs poured along it."""

    east: bool
    start: int
    cells: list[tuple[int, str]] = field(default_factory=list)
    end: int = 0  # the cell *after* the last glyph, in the walking direction


@dataclass
class Plan:
    """A block laid out relative to its own first glyph row."""

    name: str
    rows: list[Row]
    branch: str | None  # the branch glyph, if the block ends in one
    branch_col: int = 0


# ── column geometry ───────────────────────────────────────────────────────────
#: Channel columns west of the code.  Sized generously; `build_room` asserts.
NC = 26
CODE0 = NC + 1

#: Pipe columns on the north wall.  The store pair is deliberately *spread* —
#: its ring is the long one and its two pipes have to pass each other in the
#: band above the room, which they cannot do if they start next to each other.
PIPE_COL = {
    "store_out": CODE0,
    "store_in": CODE0 + 13,
    "file_in": CODE0 + 28,
    "file_out": CODE0 + 29,
    "input": CODE0 + 64,
    "painter": CODE0 + 65,
}
#: Usable column range per band, kept clear of the midpoints so that the
#: incoming and outgoing nearest-column rules agree everywhere inside it.
ZONE_COLS = {
    "ST": (CODE0, CODE0 + 14),
    "FI": (CODE0 + 21, CODE0 + 45),
    "IO": (CODE0 + 48, CODE0 + 124),
}
IW = CODE0 + 130


def _nearest(cols: dict[str, int], x: int) -> str:
    """Which pipe an op at column `x` binds to: nearest column, ties westward."""
    return min(cols, key=lambda k: (abs(x - cols[k]), cols[k]))


def check_binding(x: int, token: str) -> None:
    want = TOKEN_ZONE[token]
    if token in ("rr", "rq", "ri"):
        cols = {"ST": PIPE_COL["store_in"], "FI": PIPE_COL["file_in"], "IO": PIPE_COL["input"]}
    else:
        cols = {"ST": PIPE_COL["store_out"], "FI": PIPE_COL["file_out"], "IO": PIPE_COL["painter"]}
    got = _nearest(cols, x)
    if got != want:
        raise Collision(f"{token!r} at column {x} binds {got}, wanted {want}")


# ── planning one block ────────────────────────────────────────────────────────
def _items(tok: str) -> list[tuple[str, object]]:
    """A token as placeable items: ('g', glyph) or ('lit', digits).

    The pipe tokens name *which* pipe, which is a column discipline rather than
    a glyph: they all compile to a bare `r` or `s`.
    """
    if tok.startswith("L"):
        v = int(tok[1:])
        return [("g", str(v))] if 0 <= v <= 9 else [("lit", str(v))]
    if tok in TOKEN_ZONE:
        return [("g", "r" if tok[0] == "r" else "s")]
    return [("g", tok)]


class _Pen:
    """Pours glyphs along a row, wrapping when a band lies behind the pen."""

    def __init__(self, backticks: set[int]) -> None:
        self.backticks = backticks
        self.rows: list[Row] = [Row(east=True, start=CODE0)]
        self.x = CODE0

    @property
    def row(self) -> Row:
        return self.rows[-1]

    def _step(self) -> int:
        return 1 if self.row.east else -1

    def wrap(self) -> None:
        self.row.end = self.x
        self.rows.append(Row(east=not self.row.east, start=self.x))
        self.x += 1 if self.rows[-1].east else -1

    def put(self, glyph: str) -> int:
        if not (CODE0 <= self.x < IW - 1):
            raise Collision(f"row ran off the room at column {self.x}")
        at = self.x
        self.row.cells.append((at, glyph))
        self.x += self._step()
        return at

    def ensure(self, need: int) -> None:
        """Wrap if `need` cells do not fit ahead of the pen on this row."""
        if self.row.east:
            if self.x + need > IW - 2:
                self.wrap()
        elif self.x - need < CODE0:
            self.wrap()

    def seek(self, lo: int, hi: int) -> None:
        """Move the pen into [lo, hi], wrapping if that band is behind it."""
        for _ in range(3):
            if lo <= self.x <= hi:
                return
            if self.row.east:
                if self.x < lo:
                    self.x = lo
                    return
            elif self.x > hi:
                self.x = hi
                return
            self.wrap()
        raise Collision(f"cannot reach band [{lo},{hi}] from column {self.x}")

    def literal(self, digits: str) -> None:
        step = self._step()
        while self.x in self.backticks:
            self.x += step
        open_col = self.put("`")
        for ch in digits:
            self.put(ch)
        while self.x in self.backticks:
            self.x += step
        close_col = self.put("`")
        self.backticks.update((open_col, close_col))


def plan_blocks(order: list[str], worker=WORKER) -> dict[str, Plan]:
    backticks: set[int] = set()
    plans: dict[str, Plan] = {}
    for name in order:
        toks, succ = worker[name]
        branch = toks[-1] if isinstance(succ, dict) else None
        pen = _Pen(backticks)
        for i, tok in enumerate(toks):
            zone = TOKEN_ZONE.get(tok)
            if zone is not None:
                pen.seek(*ZONE_COLS[zone])
            if branch is not None and i == len(toks) - 1:
                # the branch glyph must not be the row's first cell: its lanes
                # leave from the cells above and below it, and the entry walk
                # has to have room to reach it.
                if not pen.row.cells:
                    pen.put(" ")
            for kind, payload in _items(tok):
                if kind == "lit":
                    # +8 of slack: the backtick columns are globally unique, so
                    # both ends may have to step past columns already spent.
                    pen.ensure(len(str(payload)) + 10)
                    pen.literal(str(payload))
                else:
                    pen.ensure(2)
                    col = pen.put(str(payload))
                    if zone is not None:
                        check_binding(col, tok)
        pen.row.end = pen.x
        plans[name] = Plan(name, pen.rows, branch, pen.rows[-1].cells[-1][0] if branch else 0)
    return plans


# ── block order: chain each block to its straight-lane successor ──────────────
def block_order(worker=WORKER, entry: str = "INIT") -> list[str]:
    """Depth-first, straight lane first, so most edges become fall-throughs."""
    order: list[str] = []
    seen: set[str] = set()
    stack = [entry]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        order.append(name)
        toks, succ = worker[name]
        if isinstance(succ, str):
            nxt = [succ]
        else:
            straight = _straight_key(toks[-1])
            nxt = [succ[straight]] if straight and succ.get(straight) else []
            nxt += [t for k, t in succ.items() if k != straight]
        for target in reversed(nxt):
            if target not in seen:
                stack.append(target)
        # keep the straight successor on top of the stack
        if nxt and nxt[0] not in seen:
            stack.append(nxt[0])
    for name in worker:  # anything unreachable would be a bug, but be safe
        if name not in seen:
            order.append(name)
    return order


# ── the room ──────────────────────────────────────────────────────────────────
@dataclass
class Room:
    circuit: Circuit
    order: list[str]
    plans: dict[str, Plan]
    glyph_ys: dict[str, list[int]]
    straight_y: dict[str, int]
    channels: int


def _first_col(plan: Plan) -> int:
    """The column a block's entry walk has to reach before it does anything."""
    return plan.rows[0].cells[0][0] if plan.rows[0].cells else CODE0


def _droppable(order: list[str], plans: dict[str, Plan], worker) -> dict[str, str]:
    """Fall-throughs that need no straight-lane row, because they fall *east*.

    A straight edge normally costs a whole row: the man walks west from the end of
    the block to the channel bank, and a west leg reserves the row from the bank
    out to wherever he started. That is 57 of this room's 101 overhead rows.

    But the detour only exists to get him back to the target's entry at ``NC``. When
    the target is the very next block *and* its first glyph stands east of where he
    stopped, he does not need the entry at all — he drops one row at his own column
    and keeps walking east into it. The row is never claimed, so the successor moves
    up into it.

    Both conditions matter. "Next in order" is what makes the target's first glyph
    row adjacent once the row is skipped; "east of" is what lets him reach the
    glyphs by continuing rather than doubling back.
    """
    drop: dict[str, str] = {}
    for i, name in enumerate(order[:-1]):
        target = _straight_target(plans[name], worker[name][1])
        if target == order[i + 1] and plans[name].rows[-1].end < _first_col(plans[target]):
            drop[name] = target
    return drop


def build_room(worker=WORKER) -> Room:  # noqa: PLR0912, PLR0915 - one pass, read top to bottom
    order = block_order(worker)
    plans = plan_blocks(order, worker)
    dropped = _droppable(order, plans, worker)

    glyph_ys: dict[str, list[int]] = {}
    south_y: dict[str, int] = {}
    straight_y: dict[str, int] = {}
    y = 0
    for name in order:
        p = plans[name]
        n = len(p.rows)
        # Only allocate a lane row some edge can actually leave on. A branch's
        # three lanes are not all reachable: `d` declares `pos`/`zero` only and `x`
        # has no straight lane at all (`_straight_key` -> None), so allocating all
        # three for every branch buries rows no man can ever stand on — and rows
        # are the charged dimension. Which of the north/south pair a turn uses
        # depends on the walk direction of the last glyph row, so decide both here
        # exactly as the edge loop below will.
        needs_n = needs_s = False
        needs_st = _straight_target(p, worker[name][1]) is not None and name not in dropped
        if p.branch:
            turns = set(_turn_keys(p.branch).values())
            east = p.rows[-1].east
            has_cw, has_ccw = "cw" in turns, "ccw" in turns
            # `cw` leaves south when the last row walks east and north when it walks
            # west; `ccw` is the mirror. Same rule the edge loop applies below.
            needs_n = (has_ccw and east) or (has_cw and not east)
            needs_s = (has_cw and east) or (has_ccw and not east)
        if p.branch and needs_n:
            if n == 1:
                y += 1  # free row: the north lane
                ys = [y]
                y += 1
            else:
                ys = list(range(y, y + n - 1))
                y += n - 1
                y += 1  # free row: the north lane
                ys.append(y)
                y += 1
        else:
            ys = list(range(y, y + n))
            y += n
        if needs_s:
            south_y[name] = y
            y += 1
        if needs_st:
            straight_y[name] = y
            y += 1
        glyph_ys[name] = ys
    ih = y + 1

    # ── edges, and the channel column each routed one gets ────────────────────
    routed: list[tuple[str, str, int, str]] = []  # (src, dst, lane row, kind)
    chained: dict[str, str] = {}
    for name in order:
        toks, succ = worker[name]
        p = plans[name]
        last_y, last_row = glyph_ys[name][-1], p.rows[-1]
        lanes: list[tuple[str, str]] = []
        if isinstance(succ, str):
            lanes.append(("straight", succ))
        else:
            key = _straight_key(p.branch)
            if key is not None:
                lanes.append(("straight", succ[key]))
            for lane, turn in _turn_keys(p.branch).items():
                lanes.append((turn, succ[lane]))
        for kind, target in lanes:
            if kind == "straight":
                if dropped.get(name) == target:
                    continue  # falls east into the next block; no lane, no channel
                row = straight_y[name]
                if glyph_ys[target][0] == row + 1 and target not in chained.values():
                    chained[name] = target
                    continue
            elif kind == "cw":
                row = south_y[name] if last_row.east else last_y - 1
            else:
                row = last_y - 1 if last_row.east else south_y[name]
            routed.append((name, target, row, kind))

    spans: list[list[tuple[int, int]]] = []
    channel: dict[int, int] = {}
    for i, (_src, dst, row, _kind) in enumerate(routed):
        lo, hi = sorted((row, glyph_ys[dst][0]))
        for col, taken in enumerate(spans):
            if all(hi + 1 < a or b + 1 < lo for a, b in taken):
                taken.append((lo, hi))
                channel[i] = col
                break
        else:
            spans.append([(lo, hi)])
            channel[i] = len(spans) - 1
    if len(spans) > NC:
        raise Collision(f"{len(spans)} channels needed, only {NC} columns")

    # ── draw ──────────────────────────────────────────────────────────────────
    c = Circuit(IW, ih)
    for name in order:
        p, ys = plans[name], glyph_ys[name]
        c.set(NC, ys[0], "@" if name == order[0] else ">")
        for i, row in enumerate(p.rows):
            for col, glyph in row.cells:
                c.set(col, ys[i], glyph)
            if i + 1 < len(p.rows):  # wrap link, straight down
                c.set(row.end, ys[i], "v")
                c.set(row.end, ys[i + 1], ">" if p.rows[i + 1].east else "<")

    def west_leg(col_from: int, row: int, col_to: int) -> None:
        for x in range(col_to + 1, col_from):
            if not c.free(x, row):
                raise Collision(f"west leg on row {row} hits {c.get(x, row)!r} at {x}")

    has_straight = set(chained) | {s for s, _, _, k in routed if k == "straight"}
    for name in order:
        p, ys = plans[name], glyph_ys[name]
        last_y, last_row = ys[-1], p.rows[-1]
        if name in dropped:  # falls east: straight down its own column, then on
            target_y = glyph_ys[dropped[name]][0]
            c.set(last_row.end, last_y, "v")
            for yy in range(last_y + 1, target_y):
                if not c.free(last_row.end, yy):
                    raise Collision(
                        f"{name}: drop to {dropped[name]} blocked at "
                        f"({last_row.end},{yy}) by {c.get(last_row.end, yy)!r}"
                    )
            c.set(last_row.end, target_y, ">")
        elif name in has_straight:  # drop out of the row onto the straight lane
            c.set(last_row.end, last_y, "v")
            if last_y + 1 != straight_y[name] and not c.free(last_row.end, last_y + 1):
                raise Collision(f"{name}: straight lane blocked below the run")
            c.set(last_row.end, straight_y[name], "<")
        if p.branch:
            for _s, _d, row, kind in routed:
                if _s == name and kind in ("cw", "ccw"):
                    c.set(p.branch_col, row, "<")

    for name, target in chained.items():
        row = straight_y[name]
        west_leg(plans[name].rows[-1].end, row, NC)
        c.set(NC, row, "v")
        assert glyph_ys[target][0] == row + 1

    for i, (src, dst, row, kind) in enumerate(routed):
        ch = channel[i]
        target_y = glyph_ys[dst][0]
        start = plans[src].rows[-1].end if kind == "straight" else plans[src].branch_col
        west_leg(start, row, ch)
        c.set(ch, row, "v" if target_y > row else "^")
        step = 1 if target_y > row else -1
        for yy in range(row + step, target_y, step):
            if not c.free(ch, yy):
                raise Collision(f"channel {ch} blocked at row {yy} ({src}->{dst})")
        c.set(ch, target_y, ">")
        for x in range(ch + 1, NC):
            if not c.free(x, target_y):
                raise Collision(f"entry run to {dst} blocked at ({x},{target_y})")

    return Room(c, order, plans, glyph_ys, straight_y, len(spans))


# ── the whole machine ─────────────────────────────────────────────────────────
#: A turnaround room: `>@rv` over `^.s<`, an eight-cell cycle with one receive
#: and one send.  The spawn cannot sit in a corner — `@` is a nop, so a man who
#: entered it heading north would walk straight out through the wall.
RELAY = ["+----+", "|>@rv|", "|^.s<|", "+----+"]
RELAY_IW, RELAY_IH = 4, 2

BAND_H = 30  # rows above the worker, for the panel, rings and I/O
WX, WY = 1, BAND_H + 1


def _serpentine(x0: int, x1: int, y_start: int, y_end: int) -> list[tuple[int, int]]:
    """A boustrophedon sweep between two columns, walking north two rows a pass."""
    pts: list[tuple[int, int]] = []
    y, at_x1 = y_start, False
    while y - 2 >= y_end:
        pts.append((x1 if at_x1 else x0, y))
        y -= 2
        pts.append((x1 if at_x1 else x0, y))
        at_x1 = not at_x1
    return pts


def build_grid() -> tuple[list[str], DebugMap, dict[str, object]]:
    """Worker + register-file ring + store ring + input room + painter + panel."""
    from randomfun2026solvers import lllm_panel as panel
    from randomfun2026solvers.lllm_ring import FILE_WORDS, STORE_WORDS
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.plotter_block import pipe
    from randomfun2026solvers.value_ring import stamp, walls

    room = build_room()
    iw, ih = IW, room.circuit.h
    g = Circuit(WX + iw + 1, WY + ih + 1)

    for y, line in enumerate(room.circuit.rows()):
        for x, ch in enumerate(line):
            if ch != " ":
                g.set(WX + x, WY + y, ch)
    walls(g, WX, WY, iw, ih)
    north = WY - 1  # the worker's north wall row
    col = {k: WX + v for k, v in PIPE_COL.items()}

    # ── the panel, top right ─────────────────────────────────────────────────
    px, py = 100, 1
    stamp(g, px, py, panel.painter().rows())
    walls(g, px, py, panel.PAINTER_IW, panel.PAINTER_IH)
    lens = panel.attach_panel(g, px, py, px + 1, py + 6)
    # painter feed: straight up its own column, then east into the painter's
    # west wall, one column clear of the input pipe's riser.
    pipe(
        g,
        [(col["painter"], north - 1), (col["painter"], py + 1), (px - 2, py + 1)],
        into=(px - 1, py + 1),
    )

    # ── input room ───────────────────────────────────────────────────────────
    ix, iy = 80, 22
    stamp(g, ix, iy, ["+-+", "|I|", "+-+"])
    pipe(
        g,
        [(ix + 1, iy + 3), (ix + 1, iy + 4), (col["input"], iy + 4), (col["input"], north - 1)],
        into=(col["input"], north),
    )

    # ── the store ring ───────────────────────────────────────────────────────
    # 257 words have to be resident, so the forward pipe snakes the whole
    # north-west quarter; the return takes the free column east of the snake.
    stamp(g, 2, 6, RELAY)
    fwd = pipe(
        g,
        [
            (col["store_out"], north - 1),
            (col["store_out"], north - 2),
            *_serpentine(2, 38, north - 2, 10),
            (5, 10),
        ],
        into=(5, 9),
    )
    # A pipe's first cell must point *away* from its room, so both returns leave
    # their relay heading north before they may bend.
    ret = pipe(
        g,
        [(4, 5), (4, 4), (col["store_in"], 4), (col["store_in"], north - 1)],
        into=(col["store_in"], north),
    )
    if fwd + ret < STORE_WORDS + 2:
        raise Collision(f"store ring holds {fwd + ret}, needs {STORE_WORDS + 2}")

    # ── the register file: six slots, and a *short* loop, because its latency
    # is paid on every rotation and there are about thirty a tick.
    stamp(g, 58, north - 6, RELAY)
    ffwd = pipe(
        g,
        [(col["file_out"], north - 1), (col["file_out"], north - 2), (59, north - 2)],
        into=(59, north - 3),
    )
    fret = pipe(
        g,
        [
            (60, north - 7),
            (60, north - 8),
            (col["file_in"], north - 8),
            (col["file_in"], north - 1),
        ],
        into=(col["file_in"], north),
    )
    if ffwd + fret < FILE_WORDS + 2:
        raise Collision(f"file ring holds {ffwd + fret}, needs {FILE_WORDS + 2}")

    rows = [r.rstrip() for r in g.rows()]
    while rows and not rows[-1]:
        rows.pop()

    # ── the sidecar ──────────────────────────────────────────────────────────
    d = DebugMap("little-little-little-man — ring machine")
    d.region("panel", px - 1, 0, 21, 26, note="painter + LM-75, from lllm_panel", color="#a855f7")
    d.region("input", ix, iy, 3, 3, note="ASCII program, then one k a round", color="#64748b")
    d.region(
        "store-relay",
        2,
        6,
        6,
        4,
        color="#0ea5e9",
        note=f"turnaround of the {fwd + ret}-cell store ring",
    )
    d.region(
        "file-relay",
        58,
        north - 6,
        6,
        4,
        color="#0ea5e9",
        note=f"turnaround of the {ffwd + fret}-cell register file",
    )
    d.region(
        "channels",
        WX,
        WY,
        NC,
        ih,
        color="#94a3b8",
        note=f"{room.channels} vertical corridors carrying every routed edge",
    )
    for band, (lo, hi) in ZONE_COLS.items():
        d.region(
            f"band:{band}",
            WX + lo,
            WY,
            hi - lo + 1,
            ih,
            color="#1f2937",
            note=f"{band} pipe ops must stand here; nearest column binds",
        )
    for name in room.order:
        ys = room.glyph_ys[name]
        d.region(
            f"block:{name}",
            WX + CODE0,
            WY + ys[0],
            iw - CODE0,
            ys[-1] - ys[0] + 1,
            note=" ".join(WORKER[name][0]),
            color="#f59e0b",
            tags=["block"],
        )

    info = {
        "worker": (iw, ih),
        "channels": room.channels,
        "panel_pipes": lens,
        "store_ring": fwd + ret,
        "file_ring": ffwd + fret,
        "blocks": len(room.order),
    }
    return rows, d, info


if __name__ == "__main__":  # pragma: no cover - the generator's CLI
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write a labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args()
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
