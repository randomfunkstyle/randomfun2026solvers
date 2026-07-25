#!/usr/bin/env python3
"""Freedom queries over the AST: *is this line free, and what breaks if it goes?*

:mod:`manmoves` can perform a cut. This module answers the questions you ask
**before** performing one, which is what makes a compactor steerable rather than
a slot machine:

* **Is this column free at all?** — does anything paint on it.
* **Can it be removed?** — free is not the same as removable: a line with
  nothing on it still cannot go if it is a room's own wall.
* **What is affected?** — which rooms narrow, which rooms slide, which pipes get
  shorter, and by how much. A refusal names the glyph that refused.
* **Does it even help?** — the score is ``max(w,h)**2``, so on a 50x64 grid every
  column cut in the world buys **nothing**. Reporting the factor delta stops a
  search from spending its budget on the wrong axis.

The verdict is never guessed. Occupancy explains *why*, but the yes/no comes from
actually attempting the drop on a copy and re-painting it — the same gate
:func:`~randomfun2026solvers.manmoves.try_drop` uses. An explanation can be
incomplete; a verdict must not be.

**Squashing a circuit** is the other half, and it is where the wins are on a grid
whose rooms span its full width. No global cut is available there, yet a loop
inside such a room can still be pulled in: a lap costs **one tick per cell**, so
a circuit with an empty column between its two vertical legs is spending two
ticks a lap on nothing. :func:`circuits` finds the closed walks by tracing the
man's own movement rules — steer glyphs force a heading, ``X``/``d``/``a``/``x``
turn by machine state, everything else lets the heading through — and reports,
per circuit, which of its columns and rows are **pure latency**.

That answer is layered, because "can I remove this column" has three different
answers depending on how far the ripple is allowed to travel:

``latent``
    the circuit only coasts over this column, so the *loop* would still work.
``room_free``
    nothing live anywhere in that column of the room, so the room can absorb it.
``grid_free``
    :func:`line_report` says the whole grid line goes — the box actually shrinks.

A column that is latent but not room-free is still worth knowing about: it is a
tick win with a repair cost, and those are exactly the moves a placer should be
offered rather than a compactor silently refused.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .manast import Ast, Corridor, Refine, parse_ast, render
from .manmoves import try_drop, try_squash
from .manstruct import DIRS, Kind, _classify_glyph

__all__ = [
    "Verdict",
    "Occupant",
    "LineReport",
    "Freedom",
    "line_report",
    "scan",
    "Circuit",
    "circuits",
    "BlockSquash",
    "squash_report",
    "report",
]

#: Clockwise with y growing down, so E turns into S.
_CW = {"E": "S", "S": "W", "W": "N", "N": "E"}
_CCW = {v: k for k, v in _CW.items()}
_STEER_EXIT = {">": "E", "<": "W", "^": "N", "v": "S"}

#: SPEC "Instruction set": each branch's exit set, relative to the heading it was
#: entered with. Stated rather than approximated — a branch treated as "could go
#: anywhere" invents loops that the program cannot execute.
_BRANCH_EXITS = {
    "X": ("cw", "ccw", "straight"),  # sign(A)
    "d": ("cw", "straight"),  # BP > 0
    "a": ("ccw", "straight"),  # BP > 0
    "x": ("cw", "ccw"),  # BP low bit; always turns
}


class Verdict(StrEnum):
    """Three outcomes, because *free* and *removable* are different questions."""

    EMPTY = "empty"  # nothing paints here at all
    FREE = "free"  # occupied, but only by things that give way; the drop works
    BLOCKED = "blocked"  # something refuses


@dataclass(frozen=True)
class Occupant:
    """One thing sitting on a line, and whether it gives way."""

    node: str
    role: str
    cells: int
    yields: bool
    why: str

    def __str__(self) -> str:
        mark = "~" if self.yields else "X"
        return f"{mark} {self.node:12s} {self.role:22s} {self.cells:3d} cell(s)  {self.why}"


@dataclass
class LineReport:
    """The answer to "can this row/column go", with its consequences."""

    axis: str  # "row" | "col"
    index: int
    verdict: Verdict
    occupants: list[Occupant] = field(default_factory=list)
    rooms_shrunk: list[int] = field(default_factory=list)
    rooms_moved: list[int] = field(default_factory=list)
    pipes_shortened: dict[int, int] = field(default_factory=dict)
    refusal: str = ""
    factor_before: int = 0
    factor_after: int = 0

    @property
    def removable(self) -> bool:
        return self.verdict is not Verdict.BLOCKED

    @property
    def gain(self) -> int:
        """Points of ``max(w,h)**2`` this cut buys. Often zero on the short axis."""
        return self.factor_before - self.factor_after

    @property
    def blockers(self) -> list[Occupant]:
        return [o for o in self.occupants if not o.yields]

    @property
    def pipe_only(self) -> bool:
        """Nothing here but pipe — so the line is a **re-route** candidate.

        Distinct from removable, and much more valuable than it looks. A cut
        cannot take this line, because deleting a whole horizontal leg is not
        shortening a pipe, it is re-routing one. But no instruction lives here:
        the line is pure geometry, paid for in ``max(w,h)**2``, and a router that
        folds the leg into a row already in use deletes it for free.
        """
        return bool(self.occupants) and all(o.node.startswith("pipe") for o in self.occupants)

    @property
    def pipes_here(self) -> list[str]:
        return sorted({o.node for o in self.occupants if o.node.startswith("pipe")})

    def __str__(self) -> str:
        head = f"{self.axis} {self.index:3d}  {self.verdict.value.upper():8s}"
        if self.removable:
            bits = []
            if self.rooms_shrunk:
                bits.append(f"shrinks room{','.join(map(str, self.rooms_shrunk))}")
            if self.rooms_moved:
                bits.append(f"slides room{','.join(map(str, self.rooms_moved))}")
            if self.pipes_shortened:
                bits.append(
                    "shortens "
                    + ", ".join(f"pipe{p}-{n}" for p, n in sorted(self.pipes_shortened.items()))
                )
            bits.append(
                f"factor {self.factor_before:,}->{self.factor_after:,} (gain {self.gain:,})"
            )
            return head + "  " + "; ".join(bits)
        return head + "  " + self.refusal


@dataclass
class Freedom:
    """Every row and column of a grid, scanned."""

    rows: list[LineReport] = field(default_factory=list)
    cols: list[LineReport] = field(default_factory=list)

    def removable_rows(self) -> list[LineReport]:
        return [r for r in self.rows if r.removable]

    def removable_cols(self) -> list[LineReport]:
        return [c for c in self.cols if c.removable]

    def pipe_only_lines(self) -> list[LineReport]:
        """Lines carrying pipe and nothing else: what a router can delete."""
        return [r for r in [*self.rows, *self.cols] if r.pipe_only]

    def paying_lines(self) -> list[LineReport]:
        """The only ones a score-driven search should care about: gain > 0."""
        return sorted(
            [r for r in [*self.rows, *self.cols] if r.removable and r.gain > 0],
            key=lambda r: -r.gain,
        )


# ── cell classification, from the AST alone ──────────────────────────────────
def _cell_map(ast: Ast) -> dict[tuple[int, int], tuple[str, Kind, int | None]]:
    """glyph, kind and owning room for every painted cell.

    Built from :func:`~randomfun2026solvers.manast.render` rather than from the
    engine, so a freedom query costs no analyse call and works on an AST that has
    already been mutated — the state in which every question actually gets asked.
    """
    rows = render(ast)
    pipe_cells = {c for p in ast.pipes for c in p.path}
    out: dict[tuple[int, int], tuple[str, Kind, int | None]] = {}
    for room in ast.rooms:
        bw, bh = room.size
        for dy in range(bh):
            for dx in range(bw):
                x, y = room.x + dx, room.y + dy
                glyph = rows[y][x] if y < len(rows) and x < len(rows[y]) else " "
                border = dx in (0, bw - 1) or dy in (0, bh - 1)
                out[(x, y)] = (glyph, _classify_glyph(glyph, on_border=border), room.id)
    for c in pipe_cells:
        out[c] = (rows[c[1]][c[0]] if c[1] < len(rows) and c[0] < len(rows[c[1]]) else " ",
                  Kind.PIPE, None)
    return out


# ── question 1: is this line free, and what does removing it touch? ──────────
def _occupants(ast: Ast, axis: str, index: int) -> list[Occupant]:
    """Who sits on the line — the *explanation* half of the answer."""
    ax = 0 if axis == "col" else 1
    out: list[Occupant] = []
    for room in ast.rooms:
        lo = room.y if ax else room.x
        hi = lo + (room.h + 1 if ax else room.w + 1)
        if not (lo <= index <= hi):
            continue
        tag = f"room{room.id}"
        if index in (lo, hi):
            out.append(
                Occupant(tag, "own wall", 1, False, f"{axis} {index} IS the box edge")
            )
            continue
        if room.pinned:
            out.append(Occupant(tag, f"pinned {room.kind}", room.h if ax else room.w, False,
                                room.note or "pinned"))
            continue
        # The two perpendicular walls the line crosses give way: the box narrows.
        out.append(Occupant(tag, "crossing walls", 2, True, f"{room.kind} box narrows by 1"))
        for child in room.children:
            cells = [c for c in child.paint() if c[ax] == index]
            if not cells:
                continue
            if isinstance(child, Corridor):
                out.append(Occupant(f"{tag}.corridor", "nop corridor", len(cells), True,
                                    "`.` is a nop: erasable"))
            else:
                kind = type(child).__name__.lower()
                glyphs = "".join(sorted({child.paint()[c] for c in cells}))
                out.append(Occupant(f"{tag}.{kind}{child.id}", f"live {kind}", len(cells), False,
                                    f"executing glyph(s) {glyphs!r}"))
    for pipe in ast.pipes:
        cells = [c for c in pipe.path if c[ax] == index]
        if not cells:
            continue
        tag = f"pipe{pipe.id}"
        if pipe.min_capacity is None:
            out.append(Occupant(tag, "pipe, capacity unknown", len(cells), False,
                                "no declared minimum: shortening could deadlock a ring"))
            continue
        # A cut *along* a run takes one cell off it; a cut *across* a leg tears it.
        along = len(cells) == 1
        slack = pipe.capacity - pipe.min_capacity
        gives = along and slack > 0
        out.append(Occupant(
            tag, "pipe run" if along else "pipe crossed sideways", len(cells), gives,
            f"slack {slack}" if along else f"{len(cells)} cells: the cut would tear it",
        ))
    for stray in ast.strays:
        if any(c[ax] == index for c in stray.paint()):
            out.append(Occupant(f"stray{stray.id}", "unclaimed glyph", 1, False,
                                "belongs to no room or pipe"))
    return out


def line_report(
    ast: Ast,
    axis: str,
    index: int,
    *,
    capacity: dict[tuple[int, ...], int] | None = None,
) -> LineReport:
    """Is this row/column free, can it go, and what happens if it does.

    The verdict comes from performing the drop on a copy, not from reading the
    occupancy: an explanation can miss a case, and a verdict that misses one
    corrupts a grid.
    """
    if axis not in ("row", "col"):
        raise ValueError("axis must be 'row' or 'col'")
    occ = _occupants(ast, axis, index)
    trial, outcome = try_drop(ast, axis, index, capacity=capacity)
    rep = LineReport(
        axis=axis,
        index=index,
        verdict=Verdict.BLOCKED,
        occupants=occ,
        factor_before=ast.geometry_factor,
        factor_after=ast.geometry_factor,
    )
    if trial is None:
        rep.refusal = str(outcome)
        return rep
    rep.verdict = Verdict.EMPTY if not occ else Verdict.FREE
    rep.rooms_shrunk = list(outcome.rooms_shrunk)  # type: ignore[union-attr]
    rep.rooms_moved = list(outcome.rooms_moved)  # type: ignore[union-attr]
    rep.pipes_shortened = dict(outcome.pipes_shortened)  # type: ignore[union-attr]
    rep.factor_after = trial.geometry_factor
    return rep


def scan(ast: Ast, *, capacity: dict[tuple[int, ...], int] | None = None) -> Freedom:
    """Every row and column of the grid, each with its own verdict."""
    w, h = ast.bbox
    return Freedom(
        rows=[line_report(ast, "row", y, capacity=capacity) for y in range(h)],
        cols=[line_report(ast, "col", x, capacity=capacity) for x in range(w)],
    )


# ── question 2: can this block be squashed? ──────────────────────────────────
@dataclass(frozen=True)
class Circuit:
    """A closed walk: a loop. **Its cell count is its ticks per lap.**

    Measured, not assumed: a ``counted_loop`` body of ``rs`` is two columns of
    four rows — eight perimeter cells — and the engine charges exactly 8 ticks a
    lap. So every cell the walk coasts over is a tick, and an empty one is a tick
    spent on nothing.
    """

    room: int
    #: the lap in visit order, duplicates kept — a self-crossing loop pays twice
    cells: tuple[tuple[int, int], ...]
    #: columns/rows the walk crosses over floor only, so the loop survives losing them
    latent_cols: tuple[int, ...] = ()
    latent_rows: tuple[int, ...] = ()
    #: of those, the ones with nothing live anywhere else in the room
    room_free_cols: tuple[int, ...] = ()
    room_free_rows: tuple[int, ...] = ()
    #: moves in one lap == ticks in one lap
    ticks: int = 0
    #: ticks a lap loses if every latent line comes out — **counted**, not doubled
    saved: int = 0

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        xs = [x for x, _ in self.cells]
        ys = [y for _, y in self.cells]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def ticks_per_lap(self) -> int:
        return self.ticks or len(self.cells)

    @property
    def squash(self) -> int:
        """Ticks per lap this loop would save if every latent line came out.

        Counted from the lap itself rather than as "two per line". A wide loop
        usually does cross an idle column twice — out and back — so the win is
        roughly double its width, but a loop that only passes once, or that
        crosses its own corridor and passes three times, is common enough that
        assuming the factor misreports the payoff in both directions.
        """
        return self.saved

    @property
    def floor(self) -> int:
        return self.ticks_per_lap - self.squash

    def __str__(self) -> str:
        x0, y0, x1, y1 = self.bbox
        s = (
            f"circuit in room{self.room}  ({x0},{y0})-({x1},{y1})  "
            f"{self.ticks_per_lap} ticks/lap"
        )
        if self.squash:
            s += f" -> {self.floor} if squashed (-{self.squash})"
            s += f"\n    latent cols {list(self.latent_cols)} rows {list(self.latent_rows)}"
            s += (
                f"\n    of those, free across the whole room: "
                f"cols {list(self.room_free_cols)} rows {list(self.room_free_rows)}"
            )
        else:
            s += " — tight, nothing to squash"
        return s


def _successors(
    cells: dict[tuple[int, int], tuple[str, Kind, int | None]],
    state: tuple[tuple[int, int], str],
) -> list[tuple[tuple[int, int], str]]:
    """Where the man can be next, following SPEC's own movement rules."""
    (x, y), heading = state
    dx, dy = DIRS[heading]
    nxt = (x + dx, y + dy)
    if nxt not in cells:
        return []
    glyph, kind, _ = cells[nxt]
    if kind in (Kind.WALL, Kind.PIPE, Kind.HALT):
        return []  # a wall is fatal, a pipe is not his to walk, a halt ends it
    if kind is Kind.STEER:
        return [(nxt, _STEER_EXIT[glyph])]
    if kind is Kind.BRANCH:
        turns = _BRANCH_EXITS.get(glyph, ("straight",))
        out = []
        for t in turns:
            h = _CW[heading] if t == "cw" else _CCW[heading] if t == "ccw" else heading
            out.append((nxt, h))
        return out
    return [(nxt, heading)]  # ops, literals, floor: the heading survives


def _sccs(nodes: list, succ) -> list[list]:
    """Tarjan, iterative — a room's state graph is thousands of nodes deep."""
    index: dict = {}
    low: dict = {}
    on_stack: dict = {}
    stack: list = []
    out: list[list] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(succ(root)))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            node, it = work[-1]
            advanced = False
            for child in it:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack[child] = True
                    work.append((child, iter(succ(child))))
                    advanced = True
                    break
                if on_stack.get(child):
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == node:
                        break
                out.append(comp)
    return out


def _shortest_cycle(succ, start) -> list | None:
    """The tightest closed walk through `start`, by BFS. ``None`` if there is none."""
    parent: dict = {start: None}
    frontier = [start]
    while frontier:
        nxt = []
        for u in frontier:
            for v in succ(u):
                if v == start:
                    cycle = [u]
                    while parent[cycle[-1]] is not None:
                        cycle.append(parent[cycle[-1]])
                    return list(reversed(cycle))
                if v not in parent:
                    parent[v] = u
                    nxt.append(v)
        frontier = nxt
    return None


def circuits(ast: Ast, *, limit: int = 64) -> list[Circuit]:
    """Every loop in the grid, tightest form first, with the latency it carries.

    Not the strongly connected components: on a real worker every loop shares
    cells with every other, so the SCC is the whole reachable body — it reported
    ``plotter``'s worker as one 414-tick circuit whose "latent" columns were the
    union of nine unrelated loops, which is worse than no answer. A loop must be
    an individual closed walk to be squashable.

    So: seed a BFS from every ``(steer|branch, heading)`` state — every cycle has
    to contain a turn, so nothing is missed — and take the *shortest* walk back to
    the seed. That is the lap the man actually runs.

    Working in ``(cell, heading)`` states rather than cells is what makes a
    self-crossing loop come out right: standing on one cell twice with two
    headings is two ticks, and a loop that crosses its own corridor would
    otherwise be reported a tick short.
    """
    cells = _cell_map(ast)
    live_by_room: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for c, (_g, kind, room) in cells.items():
        if room is not None and kind not in (Kind.FLOOR, Kind.NOP, Kind.WALL, Kind.PIPE):
            live_by_room[room].add(c)

    walkable = [
        c for c, (_g, kind, room) in cells.items()
        if room is not None and kind not in (Kind.WALL, Kind.PIPE)
    ]
    states = [(c, h) for c in walkable for h in DIRS]

    def succ(s):
        return _successors(cells, s)

    # Only a state inside a non-trivial SCC can lie on a cycle, so the seeds are
    # cheap to filter and the BFS never runs on straight-line code.
    recurrent: set = set()
    for comp in _sccs(states, succ):
        if len(comp) > 1 or comp[0] in succ(comp[0]):
            recurrent |= set(comp)

    seeds = [
        s for s in recurrent
        if cells[s[0]][1] in (Kind.STEER, Kind.BRANCH)
    ]

    seen: set[frozenset] = set()
    out: list[Circuit] = []
    for seed in sorted(seeds):
        cycle = _shortest_cycle(succ, seed)
        if not cycle:
            continue
        key = frozenset(cycle)
        if key in seen:
            continue
        seen.add(key)
        walk = [s[0] for s in cycle]
        distinct = set(walk)
        room = next((cells[c][2] for c in walk if cells[c][2] is not None), -1)
        x0, y0 = min(x for x, _ in walk), min(y for _, y in walk)
        x1, y1 = max(x for x, _ in walk), max(y for _, y in walk)

        def latent(axis: int, lo: int, hi: int, distinct=distinct) -> list[int]:
            found = []
            for i in range(lo + 1, hi):  # never the extremes: those carry the turns
                on = [c for c in distinct if c[axis] == i]
                if not on:
                    continue
                if all(cells[c][1] in (Kind.FLOOR, Kind.NOP) for c in on):
                    found.append(i)
            return found

        lat_cols = latent(0, x0, x1)
        lat_rows = latent(1, y0, y1)
        # Every lap position that disappears, counted once even where a latent row
        # and a latent column cross — that cell is removed by either cut, not both.
        cs, rs = set(lat_cols), set(lat_rows)
        saved = sum(1 for (x, y) in walk if x in cs or y in rs)
        out.append(Circuit(
            room=room,
            cells=tuple(walk),
            latent_cols=tuple(lat_cols),
            latent_rows=tuple(lat_rows),
            room_free_cols=tuple(
                i for i in lat_cols if not any(c[0] == i for c in live_by_room[room])
            ),
            room_free_rows=tuple(
                i for i in lat_rows if not any(c[1] == i for c in live_by_room[room])
            ),
            ticks=len(cycle),
            saved=saved,
        ))
    return sorted(out, key=lambda c: (-c.squash, c.ticks_per_lap))[:limit]




@dataclass(frozen=True)
class RoomLine:
    """One interior line of one room, with a **tried** verdict.

    "This column looks empty" and "this column can be removed" are different
    claims, and only the second is worth acting on. So each candidate is actually
    squashed on a copy: the room narrows, its children slide, the pipes on the wall
    that moved are grown to still reach it, and the result is re-painted. A refusal
    carries the reason, which is usually a pipe landing on a wall that slid.
    """

    axis: str
    index: int
    ok: bool
    reason: str = ""
    pipes_grown: tuple[int, ...] = ()
    #: ticks per lap this line costs the loops that coast over it. **Per lap** —
    #: multiply by the lap count for the score, which needs a run to know. On
    #: ``plotter`` a 2-ticks-per-lap column measured 92.5 avgTicks (~46 laps, ~1%
    #: of score), so quoting the per-lap figure alone understates it 46-fold.
    #: :func:`~randomfun2026solvers.manmoves.stretch_col` prices a line exactly:
    #: insert it and read the tick delta off the engine.
    ticks: int = 0

    def __str__(self) -> str:
        mark = "YES" if self.ok else "no "
        s = f"{mark} {self.axis} {self.index}"
        if self.ticks:
            s += f"  (-{self.ticks} ticks/lap; x laps for score — measure with stretch)"
        if self.ok and self.pipes_grown:
            s += f"  grows pipe{', pipe'.join(map(str, self.pipes_grown))} by 1"
        if not self.ok:
            s += f"  — {self.reason}"
        return s


@dataclass
class BlockSquash:
    """Can this block be pulled in, and what would have to be repaired?

    The shrink does **not** move the bounding box: the freed column becomes blank
    space behind the wall that came in. It pays in *ticks*, by shortening every
    loop that was coasting across it, and in free space a router can spend later.
    That is the whole reason to ask the question of a room whose grid lines are all
    blocked — which is the normal case for a worker spanning the full width.
    """

    node: str
    kind: str
    interior: tuple[int, int]
    free_cols: list[int] = field(default_factory=list)  # absolute grid columns
    free_rows: list[int] = field(default_factory=list)
    live_cells: int = 0
    attached_pipes: list[int] = field(default_factory=list)
    pinned: bool = False
    note: str = ""
    #: every candidate interior line, each with a tried verdict
    lines: list[RoomLine] = field(default_factory=list)

    @property
    def occupancy(self) -> float:
        w, h = self.interior
        return self.live_cells / (w * h) if w and h else 0.0

    @property
    def removable(self) -> list[RoomLine]:
        return [ln for ln in self.lines if ln.ok]

    @property
    def shrink(self) -> tuple[int, int]:
        """What can *actually* come out, counted from the tried verdicts."""
        ok = self.removable
        return (
            sum(1 for ln in ok if ln.axis == "col"),
            sum(1 for ln in ok if ln.axis == "row"),
        )

    @property
    def ticks_saved(self) -> int:
        return sum(ln.ticks for ln in self.removable)

    def __str__(self) -> str:
        w, h = self.interior
        dw, dh = self.shrink
        if self.pinned:
            return f"{self.node:10s} {self.kind:8s} {w}x{h}  PINNED — {self.note}"
        s = (
            f"{self.node:10s} {self.kind:8s} interior {w}x{h}, {self.live_cells} live "
            f"({self.occupancy:.0%})  removable: {dw} col(s) / {dh} row(s) -> "
            f"{w - dw}x{h - dh}"
        )
        if self.ticks_saved:
            s += f", -{self.ticks_saved} ticks/lap"
        for ln in self.lines:
            s += f"\n    {ln}"
        if not self.lines:
            s += "\n    no empty interior line: the room is already tight"
        return s


def squash_report(ast: Ast, *, verify: bool = True) -> list[BlockSquash]:
    """Per-room: **can a column or row be removed from it**, and what that costs.

    Asked of every room, because a grid whose every line is blocked can still have
    a room with slack in it — and that slack is where the ticks are. Each candidate
    interior line is squashed on a copy and re-painted, so a `YES` has been tried
    rather than eyeballed; pass ``verify=False`` for the cheap occupancy-only view.
    """
    cells = _cell_map(ast)
    # A latent line of a loop is a line the man coasts over, so removing it takes
    # ticks off every lap that crossed it. Attribute those ticks to the line, which
    # is what turns "this column is empty" into "this column costs 4 ticks a lap".
    ticks_for: dict[tuple[str, int], int] = defaultdict(int)
    if verify:
        for circ in circuits(ast):
            for i in circ.latent_cols:
                ticks_for[("col", i)] += sum(1 for (x, _y) in circ.cells if x == i)
            for i in circ.latent_rows:
                ticks_for[("row", i)] += sum(1 for (_x, y) in circ.cells if y == i)

    out: list[BlockSquash] = []
    for room in ast.rooms:
        live = {
            c for c, (_g, kind, r) in cells.items()
            if r == room.id and kind not in (Kind.FLOOR, Kind.NOP, Kind.WALL, Kind.PIPE)
        }
        pipe_in_room = {
            c for p in ast.pipes for c in p.path
            if room.x < c[0] < room.x + room.w + 1 and room.y < c[1] < room.y + room.h + 1
        }
        blocked = live | pipe_in_room
        free_cols = [
            x for x in range(room.x + 1, room.x + room.w + 1)
            if not any(c[0] == x for c in blocked)
        ]
        free_rows = [
            y for y in range(room.y + 1, room.y + room.h + 1)
            if not any(c[1] == y for c in blocked)
        ]
        attached = [
            p.id for p in ast.pipes
            if any(c in set(room.ports) for c in (p.path[0], p.path[-1]))
        ] if room.ports else [
            p.id for p in ast.pipes if p.src == room.id or p.dst == room.id
        ]
        lines: list[RoomLine] = []
        if verify and not room.pinned:
            for axis, idxs in (("col", free_cols), ("row", free_rows)):
                for i in idxs:
                    trial, outcome = try_squash(ast, room.id, axis, i)
                    lines.append(RoomLine(
                        axis=axis,
                        index=i,
                        ok=trial is not None,
                        reason="" if trial is not None else str(outcome),
                        pipes_grown=tuple(sorted(outcome.pipes_shortened))
                        if trial is not None else (),
                        ticks=ticks_for.get((axis, i), 0),
                    ))
        out.append(BlockSquash(
            node=f"room{room.id}",
            kind=room.kind,
            interior=(room.w, room.h),
            free_cols=free_cols if not room.pinned else [],
            free_rows=free_rows if not room.pinned else [],
            live_cells=len(live),
            attached_pipes=sorted(attached),
            pinned=room.pinned,
            note=room.note,
            lines=lines,
        ))
    return out


def _abuts_pinned(ast: Ast, rep: LineReport) -> str:
    """The pinned room whose wall a pipe on this line attaches to, if any.

    A pipe can only reach a wall from the line immediately outside it. If that
    wall belongs to a room that may not move, the line is not spare geometry
    however empty of instructions it looks — which is the case on ``plotter``'s
    row 0: it carries one pipe and nothing else, and it is the only row from which
    the display's top wall (its ADDR port) can ever be written.
    """
    ax = 0 if rep.axis == "col" else 1
    pinned_walls: dict[tuple[int, int], str] = {}
    for room in ast.rooms:
        if not room.pinned:
            continue
        bw, bh = room.size
        for dx in range(bw):
            for dy in range(bh):
                if dx in (0, bw - 1) or dy in (0, bh - 1):
                    pinned_walls[(room.x + dx, room.y + dy)] = f"room{room.id} ({room.kind})"
    for pipe in ast.pipes:
        for x, y in pipe.path:
            if (x, y)[ax] != rep.index:
                continue
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in pinned_walls:
                    return pinned_walls[(nx, ny)]
    return ""


# ── the whole answer, as text ────────────────────────────────────────────────
def report(ast: Ast, *, capacity: dict[tuple[int, ...], int] | None = None) -> str:
    """Every question this module answers, for one grid, in one page."""
    w, h = ast.bbox
    f = scan(ast, capacity=capacity)
    lines = [
        f"{w}x{h}  factor {ast.geometry_factor:,}  "
        f"(the score's {'width' if w >= h else 'height'} is what binds)",
        "",
        "── grid lines ──",
        f"rows: {len(f.removable_rows())}/{len(f.rows)} removable, "
        f"cols: {len(f.removable_cols())}/{len(f.cols)} removable",
    ]
    paying = f.paying_lines()
    if paying:
        lines.append(f"{len(paying)} of them actually lower the factor:")
        lines += [f"  {r}" for r in paying[:12]]
    else:
        # Do not say "squashing is the only lever" here. Squashing does not lower
        # the factor either — the freed column becomes blank space behind the wall
        # that came in. The score is factor x avgTicks, so the two levers are
        # independent and have to be named as such, or a search goes looking for
        # footprint in a move that only ever pays in ticks.
        lines.append(
            "NONE of them lowers the factor: every removable line is on the short axis, "
            "so the box does not shrink."
        )
        lines.append(
            "  score is factor x avgTicks, so what is left are two independent levers: "
            "RE-ROUTE a pipe off the binding axis (lowers the factor) and SQUASH a loop "
            "(lowers ticks, never the factor)."
        )
    for tag, group in (("row", f.rows), ("col", f.cols)):
        empty = [r.index for r in group if r.verdict is Verdict.EMPTY]
        free = [r.index for r in group if r.verdict is Verdict.FREE]
        lines.append(f"{tag}s: empty {empty or '-'}  free-but-occupied {free or '-'}")

    axis = "row" if h >= w else "col"
    pipe_only = [r for r in f.pipe_only_lines() if r.axis == axis]
    if pipe_only:
        lines += ["", f"── re-route candidates: {axis}s holding pipe and nothing else ──"]
        forced = {r.index: n for r in pipe_only if (n := _abuts_pinned(ast, r))}
        solo = [
            r for r in pipe_only if len(r.pipes_here) == 1 and r.index not in forced
        ]
        for r in pipe_only:
            cells = sum(o.cells for o in r.occupants)
            tag = "ONE pipe" if len(r.pipes_here) == 1 else f"{len(r.pipes_here)} pipes crossing"
            lines.append(
                f"{axis} {r.index}: {', '.join(r.pipes_here)} — {cells} cell(s), {tag}"
            )
            if r.index in forced:
                lines.append(
                    f"    FORCED: a pipe here attaches to {forced[r.index]}, and this is the "
                    f"only {axis} from which that wall can be reached — not slack"
                )
        extent = h if axis == "row" else w
        other = w if axis == "row" else h
        lines.append(
            f"  {len(solo)} carry a single pipe, so a re-route has nothing to avoid: "
            f"{extent} -> {extent - len(solo)}, factor {ast.geometry_factor:,} -> "
            f"{max(extent - len(solo), other) ** 2:,}"
        )
        # Adjacent multi-pipe lines are a *crossing band*, not slack. Each flow was
        # given its own line precisely because two of them share a direction and
        # would merge or deadlock if laid on one. Folding those is a routing
        # problem with a real constraint, so the all-in number is a ceiling and
        # must be labelled as one rather than quoted as available.
        lines.append(
            f"  ceiling if every one folded: {extent - len(pipe_only)}, factor "
            f"{max(extent - len(pipe_only), other) ** 2:,} — but {len(pipe_only) - len(solo)} of "
            f"them carry several pipes at once, which is a crossing band, not slack"
        )

    # For a blocked line the blockers *are* the answer, so name them on the axis
    # that sets the score. A row held by one glyph is a different proposition from
    # one held by nine, and only this tells them apart.
    group = f.rows if axis == "row" else f.cols
    near = sorted(
        (r for r in group if not r.removable),
        key=lambda r: (len(r.blockers), sum(o.cells for o in r.blockers)),
    )[:6]
    if near:
        lines += ["", f"── nearest misses on the binding axis ({axis}s) ──"]
        for r in near:
            lines.append(f"{axis} {r.index}: {len(r.blockers)} blocker(s)")
            for o in r.blockers:
                lines.append(f"    {o}")

    lines += ["", "── blocks ──"]
    lines += [str(b) for b in squash_report(ast)]

    circs = circuits(ast)
    lines += ["", f"── circuits ({len(circs)}) ──"]
    if not circs:
        lines.append("no closed walk found: nothing loops")
    for c in circs[:8]:
        lines.append(str(c))
    total = sum(c.squash for c in circs)
    if total:
        lines.append(f"total squashable: {total} ticks per lap across {len(circs)} circuit(s)")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path)
    ap.add_argument("--refine", type=int, choices=(0, 1), default=1)
    ap.add_argument(
        "--capacity",
        action="append",
        default=[],
        metavar="ID=N",
        help="declare a pipe's minimum capacity; undeclared pipes stay pinned",
    )
    args = ap.parse_args()
    caps = {}
    for spec in args.capacity:
        k, _, v = spec.partition("=")
        caps[int(k)] = int(v)
    ast = parse_ast(args.grid, refine=Refine(args.refine), capacity=caps)
    print(f"{args.grid.name}")
    print(report(ast))


if __name__ == "__main__":
    main()
