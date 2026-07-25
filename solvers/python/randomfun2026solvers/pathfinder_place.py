#!/usr/bin/env python3
"""A binding-aware placer: pour a block-graph program into one room.

:mod:`pathfinder_prog` is a *program* -- 51 blocks of straight glyph runs over
one man, an 18-word ring and two auxiliary FIFOs.  This module is the machinery
that turns such a program into glyphs on a grid while respecting the one
constraint that dominates the whole floorplan: ``s``/``r`` bind to the
**nearest** pipe, so every pipe op has to physically stand where its pipe wins.

The placer is deliberately generic in the binding: it takes any predicate
``ok(x, y, tok)`` and refuses to place a pipe op where the predicate is false,
routing a corridor to a legal cell instead.  That is what lets the same code
measure completely different anchor schemes -- see :mod:`pathfinder_ring`.

How it walks
------------
The man's track is built one token at a time from a cursor ``(x, y, dir)``:

* if the cursor's cell is free *and* legal for the token, the glyph lands there
  and the man steps on in the same direction -- so a run of tokens is a
  straight line, which is what makes the output readable;
* otherwise a Dijkstra search over cells lays the shortest corridor of blanks
  and turn glyphs to a cell that *is* legal.  Corridor cells are recorded so no
  later glyph lands on one -- a man in transit would execute it.

The search settles each cell once, so a corridor is always a simple path and
can be written straight out.  Two guards keep it from painting the man into a
corner:

* a token may only land on a cell whose *next* cell, in the heading the man
  arrives with, is still free -- that is where he steps afterwards;
* among equal-length corridors the search prefers cells with more occupied
  neighbours, which fills the room from its edges inwards instead of leaving a
  lace of unusable single cells.

Blocks, stubs and lanes
-----------------------
Every block owns an **entry stub**: one direction glyph that its first token
follows.  A stub may be walked onto from any of its four neighbours, so several
predecessors merge onto it without the join needing to know the block's shape.
Branch tokens (``X``, ``x``, ``d``) are always last in a block; after placing
one, each lane's first cell follows from the man's heading (straight /
clockwise / counter-clockwise, per SPEC.md).  A lane whose target block is not
laid yet becomes that block's fall-through, so the hot path stays straight; the
rest are routed as joins.

Numeric literals
----------------
```nnn``` pairs backticks on rows **and columns independently**, so two
literals whose backticks share a column form a vertical pair, and a non-digit
caught between them is a *load* error -- confirmed against the reference
interpreter.  The placer therefore reserves two columns for backticks and
nothing else (blanks between a pair are legal) and lays every multi-digit
literal eastward from the first column to the second, space-padded.  Each such
row then holds exactly two backticks -- a horizontal pair around its own digits
-- and each backtick column holds only backticks and blanks, so every vertical
pair encloses blanks.  Single digits need no delimiter and are ordinary glyphs.
"""
from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable

from randomfun2026solvers.circuit import CCW, CW, GLYPH, Circuit, Collision, E

__all__ = ["PIPE_OPS", "Placer", "PlacerError", "TURN_OF", "check_backticks", "glyph_of"]

DIRS = (E, (-1, 0), (0, -1), (0, 1))
PIPE_OPS = ("rr", "sr", "rf", "sf", "rg", "sg", "sp", "ri")

#: Lane name -> the man's new heading, given his heading at the branch glyph.
TURN_OF = {
    "X": {"zero": lambda d: d, "pos": lambda d: CW[d], "neg": lambda d: CCW[d]},
    "x": {"one": lambda d: CW[d], "zero": lambda d: CCW[d]},
    "d": {"pos": lambda d: CW[d], "zero": lambda d: d},
}


class PlacerError(RuntimeError):
    pass


def glyph_of(tok: str) -> str:
    """The single glyph a token becomes, or ``""`` for a delimited literal.

    Pipe ops are two-letter *names* (``sr`` = send-to-ring) but one glyph: the
    pipe is chosen by where the glyph stands, which is the whole point.
    """
    if tok in PIPE_OPS:
        return tok[0]
    if tok[0] == "L":
        return tok[1:] if len(tok) == 2 else ""
    return tok


class Placer:
    """Pour a block graph into ``circuit`` under a pipe-binding predicate."""

    def __init__(
        self,
        circuit: Circuit,
        ok: Callable[[int, int, str], bool],
        *,
        backtick_cols: tuple[int, int],
        pipe_ops: Iterable[str] = PIPE_OPS,
    ) -> None:
        self.c = circuit
        self.ok = ok
        self.pipe_ops = frozenset(pipe_ops)
        self.bt = backtick_cols
        self.used: set[tuple[int, int]] = set()
        self.transit: set[tuple[int, int]] = set()
        self.x = self.y = 0
        self.d = E
        self.glyph_cells = 0
        self.travel_cells = 0
        self.entry: dict[str, list[tuple[int, int]]] = {}
        self.spans: dict[str, list[tuple[int, int]]] = {}
        self._plan: tuple[int, int] | None = None
        self.held: set[tuple[int, int]] = set()
        self._cur = "?"
        #: How much free room a glyph must leave the man to step into.
        self.escape = 40
        #: Tie-break weight pulling corridors towards what is already placed.
        self.hug = 1
        #: Room a *reserved* cell must keep; the cursor's own need is `escape`.
        self.dock_escape = 50
        #: Ceiling on the cursor's escape demand, and how far parking moves a lane.
        self.escape_cap = 400
        self.park_min = 2
        self.bus_pad = 0

    # ── grid primitives ──────────────────────────────────────────────────────
    def free(self, x: int, y: int) -> bool:
        return 0 <= x < self.c.w and 0 <= y < self.c.h and (x, y) not in self.used

    def code_ok(self, x: int, y: int) -> bool:
        """May a *glyph* (as opposed to a blank) land here?"""
        return self.free(x, y) and x not in self.bt

    def _put(self, x: int, y: int, ch: str, *, transit: bool = False) -> None:
        if not self.free(x, y):
            raise PlacerError(f"({x},{y}) already used, cannot place {ch!r}")
        self.c.set(x, y, ch)
        self.used.add((x, y))
        if transit:
            self.transit.add((x, y))
            self.travel_cells += 1
        else:
            self.glyph_cells += 1
        self.spans.setdefault(self._cur, []).append((x, y))

    @staticmethod
    def _without(unused, cells):
        blocked = set(cells)
        return lambda cx, cy: (cx, cy) not in blocked and unused(cx, cy)

    @staticmethod
    def _with(unused, cell):
        return lambda cx, cy: (cx, cy) == cell or unused(cx, cy)

    def _audit(self, where: str) -> None:
        if self.reach(self.x, self.y, 12) < 12:
            raise PlacerError(f"AUDIT {where}: cursor ({self.x},{self.y}) in a pocket")

    def _occ(self, x: int, y: int) -> int:
        return sum(1 for dx, dy in DIRS if not self.free(x + dx, y + dy))

    def reach(self, x: int, y: int, cap: int, unused=None) -> int:
        """Free cells reachable from ``(x, y)``, counted no further than ``cap``.

        The man has to *keep going* after every glyph, so a cell whose free
        neighbourhood is a pocket is a trap however legal it looks.  This is the
        guard that keeps the walk out of pockets; ``cap`` bounds the flood so it
        stays cheap enough to run per candidate.
        """
        free = unused or self.free
        if not free(x, y):
            return 0
        seen = {(x, y)}
        stack = [(x, y)]
        while stack and len(seen) < cap:
            cx, cy = stack.pop()
            for dx, dy in DIRS:
                n = (cx + dx, cy + dy)
                if n not in seen and free(*n):
                    seen.add(n)
                    stack.append(n)
        return len(seen)

    # ── the corridor search ──────────────────────────────────────────────────
    def _search(self, goal: Callable[..., bool], holds=None, footprint=None) -> None:
        """Walk the cursor to the cheapest cell satisfying ``goal``.

        Each cell settles at most once, so the corridor is a simple path and
        every cell of it can be written exactly once.  ``goal(x, y, d, free)``
        sees the cell, the heading the man arrives with, and a ``free`` test
        that also excludes the corridor cells this very route would consume --
        without which a route could tunnel through the run it is heading for.
        """
        self._dbg = [0, 0]
        start = (self.x, self.y)
        if not self.free(*start):
            raise PlacerError(f"cursor {start} is already occupied")
        foot = footprint or (lambda x, y, d: ((x, y),))
        hold = holds or (lambda x, y, d, unused: ())
        if goal(self.x, self.y, self.d, self.free) and self._holds_escape(
            self.free,
            foot(self.x, self.y, self.d),
            hold(self.x, self.y, self.d, self.free),
        ):
            return
        dist = {start: 0}
        came: dict[tuple[int, int], tuple[int, int]] = {}
        heads = {start: self.d}
        pq = [(0, 0, start)]
        tie = 0
        while pq:
            cost, _, cell = heapq.heappop(pq)
            if cost > dist.get(cell, 1 << 30):
                continue
            x, y = cell
            if cell != start:
                chain = [cell]
                while chain[-1] in came:
                    chain.append(came[chain[-1]])
                chain.reverse()
                taken = set(chain[:-1])

                def unused(cx: int, cy: int, taken: set = taken) -> bool:
                    return self.free(cx, cy) and (cx, cy) not in taken

                g = goal(x, y, heads[cell], unused)
                self._dbg[0] += bool(g)
                self._dbg[1] += 1
                if g and self._holds_escape(
                    unused,
                    foot(x, y, heads[cell]),
                    hold(x, y, heads[cell], unused),
                ):
                    d = self.d
                    for i, (cx, cy) in enumerate(chain[:-1]):
                        nx, ny = chain[i + 1]
                        nd = (nx - cx, ny - cy)
                        self._put(cx, cy, " " if nd == d else GLYPH[nd], transit=True)
                        d = nd
                    self.x, self.y, self.d = x, y, heads[cell]
                    return
            for nd in DIRS:
                nx, ny = x + nd[0], y + nd[1]
                if not self.free(nx, ny):
                    continue
                nc = cost + 8 - self.hug * self._occ(nx, ny)
                if nc < dist.get((nx, ny), 1 << 30):
                    dist[(nx, ny)] = nc
                    came[(nx, ny)] = cell
                    heads[(nx, ny)] = nd
                    tie += 1
                    heapq.heappush(pq, (nc, tie, (nx, ny)))
        raise PlacerError(
            f"no corridor from {start} dir {self.d} for {self._cur}: "
            f"{len(self.used)} cells used, {len(dist)} reachable, "
            f"{len(self.held)} held {sorted(self.held)[:12]}, "
            f"goal ok {self._dbg[0]}/{self._dbg[1]}, escape {self.escape}"
        )

    def _holds_escape(self, unused, extra, holds=()) -> bool:
        """Would every reserved cell still have somewhere to go?

        A reservation keeps its *cell*, not its *neighbourhood*: later glyphs --
        or, worse, other reservations -- can wall a promised lane or dock into a
        pocket, and the failure only surfaces much later as a walk with nowhere
        to continue.  So every placement re-checks that each outstanding
        reservation, and each one this placement is about to make, still opens
        onto room.
        """
        cells = list(self.held) + list(holds)
        if not cells:
            return True
        blocked = set(cells) | set(extra)
        want = min(self.escape, self.dock_escape)

        for cell in cells:
            def local(cx, cy, keep=cell):
                if (cx, cy) == keep:
                    return True
                return (cx, cy) not in blocked and unused(cx, cy)
            if self.reach(cell[0], cell[1], want, local) < want:
                return False
        return True

    # ── placing tokens ───────────────────────────────────────────────────────
    def emit(
        self, tok: str, *, lanes: Iterable[str] = (), held_lanes: Iterable[str] = ()
    ) -> tuple[int, int]:
        """Place one token; returns the cell its glyph landed on.

        ``lanes`` names the branch lanes leaving this glyph (``TURN_OF``); each
        one's first cell has to be free, or the man walks into a wall or over
        live code the moment the branch is taken.
        """
        if tok[0] == "L" and len(tok) > 2:
            return self._emit_literal(tok[1:])
        glyph = glyph_of(tok)
        pipe = tok in self.pipe_ops
        turns = [TURN_OF[tok][k] for k in lanes]
        holds = [TURN_OF[tok][k] for k in held_lanes]

        def goal(x, y, d, unused) -> bool:
            if not self.code_ok(x, y) or not unused(x + d[0], y + d[1]):
                return False
            if any(not unused(x + t(d)[0], y + t(d)[1]) for t in turns):
                return False
            if pipe and not self.ok(x, y, tok):
                return False
            outs = [(x + d[0], y + d[1])] + [(x + t(d)[0], y + t(d)[1]) for t in turns]
            after = self._without(unused, [(x, y), *outs])
            # Every lane needs its *own* way out.  One free neighbour is not
            # enough when a sibling lane is going to want the same cell, so a
            # branch demands two -- the cheap stand-in for a disjoint-paths test.
            want = 2 if len(outs) > 1 else 1
            for o in outs:
                local = self._with(after, o)
                if sum(1 for e in DIRS if after(o[0] + e[0], o[1] + e[1])) < want:
                    return False
                if self.reach(o[0], o[1], self.escape, local) < self.escape:
                    return False
            return True

        self._search(
            goal, lambda x, y, d, unused: [(x + t(d)[0], y + t(d)[1]) for t in holds]
        )
        x, y, d = self.x, self.y, self.d
        self._put(x, y, glyph)
        self.x, self.y = x + d[0], y + d[1]
        self._audit(f"emit {tok}")
        return (x, y)

    def _emit_literal(self, digits: str) -> tuple[int, int]:
        a, b = self.bt
        if len(digits) > b - a - 1:
            raise PlacerError(f"literal `{digits}` does not fit in columns {a}..{b}")

        def goal(x, y, d, unused) -> bool:
            return x == a - 1 and all(unused(a + i, y) for i in range(b - a + 2))

        self._search(goal, footprint=lambda x, y, d: [(x + i, y) for i in range(b - a + 2)])
        y = self.y
        self._put(self.x, y, " " if self.d == E else GLYPH[E], transit=True)
        text = "`" + digits.rjust(b - a - 1) + "`"
        for i, ch in enumerate(text):
            self._put(a + i, y, ch)
        self.x, self.y, self.d = b + 1, y, E
        self._audit(f"literal {digits}")
        return (b, y)

    # ── reservations ─────────────────────────────────────────────────────────
    #
    # Two things have to still be there when the placer comes back for them: a
    # branch lane's first cell (the man steps onto it the moment the branch is
    # taken) and a dock cell beside a block's stub for every predecessor that
    # will have to merge in later.  Both are held out of the free set the
    # instant they are promised, because a cell that is merely *empty now* is
    # not a cell you can have later.
    def _hold(self, cell: tuple[int, int]) -> None:
        if not self.free(*cell):
            raise PlacerError(f"cannot reserve {cell}: already used")
        self.used.add(cell)
        self.held.add(cell)

    def _release(self, cell: tuple[int, int]) -> None:
        self.used.discard(cell)
        self.held.discard(cell)

    # ── blocks ───────────────────────────────────────────────────────────────
    def open_block(self, name: str, *, bus: int = 1) -> None:
        """Lay the block's entry **bus** -- ``bus`` direction glyphs in a row.

        Every glyph of the bus points the same way and leads into the block's
        first token, so a later predecessor may merge onto the bus from *any*
        cell beside *any* of them.  That is what removes the need to reserve a
        docking cell per predecessor: a reserved cell can be fenced off long
        before it is used, while a bus offers ``3 * bus`` ways in and the join
        only fails if the whole neighbourhood is gone.
        """
        self._cur = name

        def line(x, y, d):
            return [(x + d[0] * i, y + d[1] * i) for i in range(bus)]

        def goal(x, y, d, unused) -> bool:
            for e in DIRS:
                cells = line(x, y, e)
                nxt = (x + e[0] * bus, y + e[1] * bus)
                if not all(self.code_ok(*c) and unused(*c) for c in cells):
                    continue
                if not unused(*nxt):
                    continue
                after = self._without(unused, [*cells])
                if self.reach(nxt[0], nxt[1], self.escape, after) >= self.escape:
                    self._plan = e
                    return True
            return False

        self._search(
            goal, footprint=lambda x, y, d: line(x, y, self._plan) if self._plan else ((x, y),)
        )
        e = self._plan
        for c in line(self.x, self.y, e):
            self._put(c[0], c[1], GLYPH[e])
        self.entry[name] = line(self.x, self.y, e)
        self.x, self.y, self.d = self.x + e[0] * bus, self.y + e[1] * bus, e

    def park(self) -> None:
        """Walk the cursor out of a branch's elbow into open room, and hold it.

        A lane left standing next to its branch glyph is boxed in by the branch,
        by its sibling lanes and by whatever the fall-through lays next, and the
        collapse only shows up when the lane is finally picked up.  Parking
        spends two or three cells of corridor to put the lane somewhere with
        room on three sides, which is what makes the walk survive.
        """
        home = (self.x, self.y)

        def goal(x, y, d, unused) -> bool:
            if abs(x - home[0]) + abs(y - home[1]) < self.park_min:
                return False
            after = self._without(unused, [(x, y)])
            if sum(1 for e in DIRS if after(x + e[0], y + e[1])) < 3:
                return False
            return self.reach(x, y, self.escape, unused) >= self.escape

        self._search(goal)
        self._hold((self.x, self.y))

    def join(self, name: str) -> None:
        """Route the cursor onto an already-laid block's entry bus."""
        self._cur = "->" + name
        targets: dict[tuple[int, int], tuple[int, int]] = {}
        for sx, sy in self.entry[name]:
            for dx, dy in DIRS:
                cell = (sx - dx, sy - dy)
                if self.free(*cell):
                    targets.setdefault(cell, (dx, dy))
        if not targets:
            raise PlacerError(f"no way onto {name}'s bus at {self.entry[name]}")
        self._search(lambda x, y, d, unused: (x, y) in targets)
        need = targets[(self.x, self.y)]
        self._put(self.x, self.y, " " if need == self.d else GLYPH[need], transit=True)
        self.x, self.y = self.x + need[0], self.y + need[1]
        self.d = need

    # ── the whole program ────────────────────────────────────────────────────
    def lay(self, prog: dict, start: str = "INIT", *, halt: str = "HALT") -> None:
        """Pour a whole block graph in.

        Blocks are laid depth-first, and at a branch the placer descends into
        **every** lane before moving on, cheapest subtree first.  Leaving a lane
        dangling is what kills a walk: the branch, its siblings and whatever the
        fall-through lays next box the lane in, and the collapse only surfaces
        when the lane is finally picked up.  Descending immediately means at
        most one lane per branch on the stack, and the deepest one -- the block
        chain that carries the rest of the program -- is always the last.
        """
        prog = dict(prog)
        prog[halt] = (["H"], None)
        order = {"X": ("zero", "pos", "neg"), "x": ("one", "zero"), "d": ("pos", "zero")}

        def succs(name):
            toks, s = prog[name]
            if s is None:
                return []
            if isinstance(s, str):
                return [(None, s)]
            return [(k, s[k]) for k in order[toks[-1]] if k in s]

        indeg: dict[str, int] = {start: 1}
        for name in prog:
            for _, t in succs(name):
                indeg[t] = indeg.get(t, 0) + 1

        laid: set[str] = set()
        left = [sum(len(toks) for toks, _ in prog.values())]

        def subtree(name: str) -> int:
            """Tokens the lane would still have to lay -- the descent order."""
            seen, stack, n = set(), [name], 0
            while stack:
                b = stack.pop()
                if b in seen or b in laid:
                    continue
                seen.add(b)
                n += len(prog[b][0])
                stack += [t for _, t in succs(b)]
            return n

        def step(tok, lanes=(), held=()):
            left[0] -= 1
            self.escape = min(self.escape_cap, 40 + left[0])
            return self.emit(tok, lanes=lanes, held_lanes=held)

        def chain(name: str) -> None:
            while name is not None:
                if name in laid:
                    self.join(name)
                    return
                laid.add(name)
                self.open_block(name, bus=max(1, indeg.get(name, 1) - 1 + self.bus_pad))
                toks, _ = prog[name]
                ss = succs(name)
                if len(ss) < 2:
                    for t in toks:
                        step(t)
                    name = ss[0][1] if ss else None
                    continue
                for t in toks[:-1]:
                    step(t)
                pick = max(range(len(ss)), key=lambda i: subtree(ss[i][1]))
                bx, by = step(
                    toks[-1],
                    lanes=[k for k, _ in ss],
                    held=[k for i, (k, _) in enumerate(ss) if i != pick],
                )
                d = self.d
                cells = [
                    ((bx + TURN_OF[toks[-1]][k](d)[0], by + TURN_OF[toks[-1]][k](d)[1]),
                     TURN_OF[toks[-1]][k](d), t)
                    for k, t in ss
                ]
                for cell, _, _ in cells:
                    self._hold(cell)
                for i, (cell, nd, target) in enumerate(cells):
                    if i == pick:
                        continue
                    self._release(cell)
                    self.x, self.y, self.d = cell[0], cell[1], nd
                    self._cur = "lane:" + target
                    self.park()
                    self._release((self.x, self.y))
                    chain(target)
                cell, nd, name = cells[pick]
                self._release(cell)
                self.x, self.y, self.d = cell[0], cell[1], nd

        chain(start)
        missing = set(prog) - laid
        if missing:
            raise PlacerError(f"blocks never laid: {sorted(missing)}")


def check_backticks(rows: list[str]) -> None:
    """Reject a grid whose backticks pair across a non-digit (a *load* error)."""
    h = len(rows)
    w = max(len(r) for r in rows)

    def at(x: int, y: int) -> str:
        return rows[y][x] if x < len(rows[y]) else " "

    for y in range(h):
        cols = [x for x in range(w) if at(x, y) == "`"]
        for a, b in zip(cols[0::2], cols[1::2], strict=False):
            bad = [x for x in range(a + 1, b) if at(x, y) not in "0123456789 "]
            if bad:
                raise Collision(f"row {y}: backticks {a}..{b} enclose {at(bad[0], y)!r}")
    for x in range(w):
        col = [y for y in range(h) if at(x, y) == "`"]
        for a, b in zip(col[0::2], col[1::2], strict=False):
            bad = [y for y in range(a + 1, b) if at(x, y) not in "0123456789 "]
            if bad:
                raise Collision(f"column {x}: backticks {a}..{b} enclose {at(x, bad[0])!r}")
