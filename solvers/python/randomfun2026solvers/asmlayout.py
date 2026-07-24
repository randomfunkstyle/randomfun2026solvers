"""Place a linear instruction sequence on a grid, honouring pipe-nearest rules.

`circuit.py` routes a little man's path once you have decided where every
instruction goes. This module decides that *for* you, which is the part that does
not survive being done by hand: a pipe instruction only talks to the pipe whose
anchor is nearest to its own cell, so `r`, `s` and `S` are only correct in certain
columns, and moving one op shifts every op after it.

The idea is a **serpentine**: the man walks a boustrophedon inside a rectangle,
and :meth:`Serpentine.emit` slides each instruction forward along that path until
it lands on a cell where its intended pipe genuinely wins, padding with blanks
(1 tick each) in between. Placement is therefore correct by construction rather
than by inspection, and adding an instruction cannot silently re-target the ones
after it.

Blank padding costs ticks, so keep serpentine rows narrow enough that every
anchor window comes round often.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from randomfun2026solvers.circuit import GLYPH, Circuit, Collision, E, W, N, S

__all__ = ["Anchor", "Anchors", "Serpentine", "PlacementError"]


class PlacementError(RuntimeError):
    """No cell in the region can host an instruction with its constraint."""


@dataclass(frozen=True)
class Anchor:
    """A pipe's attachment cell, just outside the room, and its direction of flow."""

    name: str
    pos: tuple[int, int]
    incoming: bool          # True: the man `r`s from it. False: he `s`ends into it.


@dataclass
class Anchors:
    """Every pipe touching one room, so 'which pipe wins here' is computable."""

    anchors: list[Anchor] = field(default_factory=list)

    def add(self, name: str, pos: tuple[int, int], *, incoming: bool) -> None:
        self.anchors.append(Anchor(name, pos, incoming))

    def winner(self, cell: tuple[int, int], *, incoming: bool) -> tuple[str, int]:
        """(name, margin) of the pipe an `r` (incoming) or `s` would reach from `cell`.

        Mirrors the engine: Manhattan distance to the anchor, ties by reading order
        (top to bottom, left to right) — so a tie is reported with margin 0 and
        callers should refuse to rely on it.
        """
        cands = [a for a in self.anchors if a.incoming is incoming]
        if not cands:
            raise PlacementError(f"no {'incoming' if incoming else 'outgoing'} pipes")
        scored = sorted(
            (abs(cell[0] - a.pos[0]) + abs(cell[1] - a.pos[1]), a.pos[1], a.pos[0], a.name)
            for a in cands
        )
        best = scored[0]
        margin = (scored[1][0] - best[0]) if len(scored) > 1 else 1 << 20
        return best[3], margin

    def check(self, cell: tuple[int, int], want: str, *, incoming: bool, min_margin: int = 1) -> bool:
        name, margin = self.winner(cell, incoming=incoming)
        return name == want and margin >= min_margin


# instructions that talk to a pipe, and whether they read or write
PIPE_OPS = {"r": True, "R": True, "U": True, "q": True, "s": False, "S": False}


class Serpentine:
    """A boustrophedon lane through a rectangle, onto which instructions are placed.

    ``emit("r", pipe="IN")`` walks forward until the current cell's nearest
    incoming pipe really is ``IN``, then writes it. ``emit("M")`` writes at once.
    Row turns are laid automatically; instructions never land on a turn cell.
    """

    def __init__(self, circuit: Circuit, anchors: Anchors, x0: int, y0: int,
                 x1: int, y1: int, *, first: tuple[int, int] = E, min_margin: int = 2):
        self.c = circuit
        self.a = anchors
        self.min_margin = min_margin
        self.path: list[tuple[int, int]] = []
        self.turns: dict[tuple[int, int], tuple[int, int]] = {}
        d = first
        for y in range(y0, y1 + 1):
            xs = range(x0, x1 + 1) if d == E else range(x1, x0 - 1, -1)
            row = [(x, y) for x in xs]
            if self.path:                       # step down into this row
                self.turns[self.path[-1]] = S
                self.turns[row[0]] = d
            self.path.extend(row)
            d = W if d == E else E
        self.i = 0

    # -- internals ---------------------------------------------------------
    def _advance(self) -> tuple[int, int]:
        while self.i < len(self.path):
            cell = self.path[self.i]
            if cell in self.turns:              # turn glyph, not an instruction slot
                self.c.set(*cell, GLYPH[self.turns[cell]])
                self.i += 1
                continue
            return cell
        raise PlacementError("serpentine exhausted")

    # -- public ------------------------------------------------------------
    def emit(self, ops: str, *, pipe: str | None = None) -> None:
        """Place `ops`. If `pipe` is given, every pipe instruction in `ops` is
        placed only where that pipe is genuinely nearest; blanks pad the gap."""
        for ch in ops:
            while True:
                cell = self._advance()
                need = PIPE_OPS.get(ch)
                if need is None or pipe is None:
                    break
                if self.a.check(cell, pipe, incoming=need, min_margin=self.min_margin):
                    break
                self.c.set(*cell, " ")          # pad: wrong pipe would win here
                self.i += 1
            self.c.set(*cell, ch)
            self.i += 1

    def pad_to(self, cell: tuple[int, int]) -> None:
        """Blank-fill up to (and excluding) `cell`, which must lie ahead on the lane."""
        while self._advance() != cell:
            self.c.set(*self.path[self.i], " ")
            self.i += 1

    @property
    def here(self) -> tuple[int, int]:
        return self._advance()

    @property
    def heading(self) -> tuple[int, int]:
        cell = self._advance()
        j = self.path.index(cell)
        if j + 1 >= len(self.path):
            raise PlacementError("no heading at the end of the lane")
        nxt = self.path[j + 1]
        return (nxt[0] - cell[0], nxt[1] - cell[1])

    def report(self) -> list[str]:
        """Per-instruction audit: cell, glyph, resolved pipe and margin."""
        out = []
        for cell in self.path[: self.i]:
            ch = self.c.get(*cell)
            need = PIPE_OPS.get(ch)
            if need is None:
                continue
            name, margin = self.a.winner(cell, incoming=need)
            out.append(f"  {ch!r} at {cell} -> {name} (margin {margin})")
        return out


class Assembler:
    """Stack of horizontal bands, each holding straight code or a branch.

    Bands are laid top to bottom and wired together down a reserved gutter, so
    adding or resizing a band cannot break the ones after it. This is the other
    half of not doing layout by hand: `circuit.py` refuses bad geometry, the
    :class:`Serpentine` picks legal cells for pipe ops, and this picks the bands.
    """

    def __init__(self, circuit: Circuit, anchors: Anchors, x0: int, x1: int,
                 gutter_down: int, gutter_up: int, *, min_margin: int = 2):
        self.c, self.a = circuit, anchors
        self.x0, self.x1 = x0, x1
        self.gd, self.gu = gutter_down, gutter_up
        self.min_margin = min_margin
        self.y = 0
        self.entry: tuple[int, int] | None = None   # where the per-op loop restarts
        self._pending: tuple[tuple[int, int], tuple[int, int]] | None = None

    def _connect(self, frm: tuple[int, int], frm_dir, to: tuple[int, int]) -> None:
        """Route from a band's exit to the next band's entry via the down-gutter."""
        if frm[1] == to[1] and frm_dir == E and frm[0] < to[0]:
            self.c.route(frm, E, [], to, E)
            return
        self.c.route(frm, frm_dir, [(self.gd, frm[1]), (self.gd, to[1])], to, E)

    def _wire(self, entry: tuple[int, int]) -> None:
        """Connect the previous band's exit into this band's entry, if any."""
        if self._pending is None:
            return
        frm, frm_dir, gap = self._pending
        self._pending = None
        # Land in the gutter cell BESIDE the entry, never on it: the entry holds the
        # band's first instruction. Travel in the gap row that every band leaves behind
        # it -- bands flush against each other leave the corridor nowhere to run.
        # Step one cell ALONG the lane's heading before descending. A branch's arms
        # route up the merge column, and on a westbound serpentine row "one east" is
        # backwards -- into code already placed.
        step = (frm[0] + frm_dir[0], frm[1] + frm_dir[1])
        self.c.route(frm, frm_dir, [step, (step[0], gap), (self.gd, gap)],
                     (self.gd, entry[1]), E)

    def _fit_rows(self, steps: list[tuple[str, str | None]], y0: int, limit: int = 10) -> int:
        """Smallest band height that can host `steps` at row `y0`.

        A pipe op is only legal in certain columns, and on a single eastbound row a
        window that has gone past cannot come back — the serpentine's next row is
        what brings it round again. So height follows from the constraints; it is
        not something to guess. Trialled on a throwaway grid at the real `y0`,
        since every distance depends on it.
        """
        for rows in range(1, limit + 1):
            probe = Circuit(self.c.w, self.c.h)
            try:
                s = Serpentine(probe, self.a, self.x0, y0, self.x1, y0 + rows - 1,
                               min_margin=self.min_margin)
                for ops, pipe in steps:
                    s.emit(ops, pipe=pipe)
            except (PlacementError, Collision):
                continue
            return rows
        raise PlacementError(f"no band height <= {limit} fits {steps!r} at row {y0}")

    def linear(self, steps: list[tuple[str, str | None]], rows: int | None = None) -> tuple[tuple[int, int], tuple[int, int]]:
        """A band of straight-line code. `steps` is [(ops, pipe|None), ...]."""
        y0 = self.y
        if rows is None:
            rows = self._fit_rows(steps, y0)
        s = Serpentine(self.c, self.a, self.x0, y0, self.x1, y0 + rows - 1,
                       min_margin=self.min_margin)
        for ops, pipe in steps:
            s.emit(ops, pipe=pipe)
        entry, exit_ = (self.x0, y0), s.here
        self._wire(entry)
        self._pending = (exit_, s.heading, y0 + rows)   # follow the lane's heading
        self.y = y0 + rows + 1
        return entry, exit_

    def branch(self, test: str, arms: dict[str, list[tuple[str, str | None]]]
               ) -> tuple[tuple[int, int], tuple[int, int]]:
        """`test` (X or d/a/x) on the middle row; arms on the rows around it.

        `arms` keys: 'cw' (CW turn), 'ccw', 'straight'. Every arm leaves the merge
        cell heading east — arms that keep their own heading never actually join,
        which is a mistake that routes cleanly and behaves wrongly.
        """
        y_ccw, y_test, y_cw = self.y, self.y + 1, self.y + 2
        x = self.x0 + 1
        self._wire((self.x0, y_test))
        self.c.set(x, y_test, test)
        widest = 0
        placed: dict[str, tuple[int, int]] = {}
        for key, y in (("ccw", y_ccw), ("cw", y_cw), ("straight", y_test)):
            steps = arms.get(key)
            if steps is None:
                continue
            sx = x + 1 if key == "straight" else x
            if key != "straight":
                self.c.turn(sx, y, E)
                sx += 1
            s = Serpentine(self.c, self.a, sx, y, self.x1 - 2, y,
                           min_margin=self.min_margin)
            for ops, pipe in steps:
                s.emit(ops, pipe=pipe)
            placed[key] = s.here
            widest = max(widest, s.here[0])
        merge = (widest + 1, y_test)
        for key, end in placed.items():
            if key == "straight":
                self.c.route(end, E, [], merge, E)
            else:
                self.c.route(end, E, [(merge[0], end[1])], merge, E)
        # the exit sits in the MIDDLE row of a branch band, so the gap row has to
        # come from the band's extent, not from the exit cell
        self._pending = (merge, E, y_cw + 1)
        self.y = y_cw + 2
        return (x, y_test), merge
