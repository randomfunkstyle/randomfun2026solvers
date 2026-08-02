#!/usr/bin/env python3
"""An exact router for **closed** circuits, and the open segments that feed them.

Why this module exists
----------------------
:mod:`place.score` prices an edge with :func:`place.ir.transit`, which is
Manhattan distance.  That is exact for an open monotone leg -- measured -- and
**optimistic for a closed lap**, because it routes the man through cells that
would need a steer glyph without charging him the cell.  :mod:`place.route`
patched over that with a *counting* argument (``lap_floor = n_ops + 4 -
n_turning_ops``) which is a genuine lower bound but answers only "how many
cells", never "which cells", and gets three things wrong in the direction that
matters:

1. **``x`` always turns.**  ``place.route.TURNING_OPS`` lumps ``x`` in with
   ``X``/``d``/``a`` as an op that *may* pay for a corner.  But ``x`` turns
   clockwise on BP's low bit and counter-clockwise otherwise -- it has no
   straight exit at all (``SPEC.md``, "Unlike ``d``/``a`` it **always** turns").
   A lap that wants to run straight through an ``x`` is not merely expensive, it
   does not exist, and a counting floor cannot see that.
2. **A branch's *other* exits still have to land somewhere.**  The floor prices
   one lap.  A three-way ``X`` has two more, and if the cheap lap uses the ``X``
   as its corner then the other two exits leave in directions the layout must
   still absorb.
3. **Fixed attachments.**  A worker's ``r`` has to be near its pipe and its
   entry has to meet the room's riser.  A floor that ignores the endpoints will
   claim a lap that cannot be attached to anything.

So this module routes for real: it searches self-avoiding paths under the
engine's own movement rule and returns **the glyph grid**, which is either a
construction proving the floor is reachable or a proof by exhaustion that it is
not.

Self-avoidance costs nothing
----------------------------
Restricting the search to self-avoiding paths is not an approximation.  A walk
may legally cross itself on a nop, but a tick is a cell *stood on* counted with
multiplicity, so a crossing saves footprint and never saves a single tick.  The
minimum-tick lap is therefore always achieved by a self-avoiding one, and
searching only those loses nothing.

The parity argument, tightened
------------------------------
:mod:`place.route` rounds the floor up to the next even number "because
rectangle perimeters are even", which is true but weaker than the fact:
**the grid is bipartite** -- colour a cell by the parity of ``x + y`` and every
step flips the colour -- so *every* closed walk on it has even length, whatever
its shape.  The rounding is therefore not a rectangle-specific artefact; it holds
for L-shapes, staircases and self-crossing figure-eights alike.  That matters
because it is what rules out the 5-cell lap the counting floor keeps offering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "DIRS",
    "STEER",
    "OP_TURNS",
    "Op",
    "Circuit",
    "route_loop",
    "route_open",
    "loop_floor_exact",
]

DIRS = {"E": (1, 0), "S": (0, 1), "W": (-1, 0), "N": (0, -1)}
CW = ("E", "S", "W", "N")
_IDX = {h: i for i, h in enumerate(CW)}
STEER = {">": "E", "<": "W", "^": "N", "v": "S", "V": "S"}

#: Which exits each self-turning glyph actually has, from ``SPEC.md``.  The
#: distinction ``place.route`` misses is on the last line.
OP_TURNS: dict[str, frozenset[str]] = {
    #: turn by sign(A): cw if A>0, ccw if A<0, straight if A==0
    "X": frozenset({"cw", "ccw", "straight"}),
    #: cw if BP>0, else straight
    "d": frozenset({"cw", "straight"}),
    #: ccw if BP>0, else straight
    "a": frozenset({"ccw", "straight"}),
    #: cw if BP's low bit is set, ccw otherwise -- it **always** turns
    "x": frozenset({"cw", "ccw"}),
}

#: Glyphs that neither steer nor branch: the man walks straight through them.
#: Everything not in :data:`STEER` or :data:`OP_TURNS` is one of these.


def turn(h: str, how: str) -> str:
    i = _IDX[h]
    return {"cw": CW[(i + 1) % 4], "ccw": CW[(i - 1) % 4], "straight": h}[how]


def exits(glyph: str, h: str) -> list[str]:
    """The headings the man can leave ``glyph`` with, arriving on heading ``h``."""
    if glyph in STEER:
        return [STEER[glyph]]
    if glyph in OP_TURNS:
        return [turn(h, t) for t in sorted(OP_TURNS[glyph])]
    return [h]


# ── the request ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Op:
    """One operation the circuit must perform, in order.

    :param glyph: the glyph itself.  Its class -- steer, self-turning, or
        straight-through -- is read off :data:`STEER` and :data:`OP_TURNS`, so
        the caller never has to declare it and cannot declare it wrongly.
    :param exit: for a self-turning glyph, which of its exits *this lap* takes.
        ``None`` lets the router choose, which is right when the caller is asking
        "what is the cheapest lap" and wrong when a specific branch is being
        priced -- a three-way ``X`` whose hot lap turns clockwise is a different
        layout question from one whose hot lap goes straight.
    :param at: pin the op to an absolute cell.  Used for a pipe glyph whose
        binding fixes where it may stand.
    """

    glyph: str
    exit: str | None = None
    at: tuple[int, int] | None = None

    def headings(self, h: str) -> list[str]:
        if self.exit is not None:
            if self.glyph in STEER:
                raise ValueError(f"a steer {self.glyph!r} has no choice of exit")
            return [turn(h, self.exit)]
        return exits(self.glyph, h)


@dataclass
class Circuit:
    """A routed circuit: the cells in walk order, and the glyphs on them."""

    cells: list[tuple[int, int]] = field(default_factory=list)
    glyphs: list[str] = field(default_factory=list)
    closed: bool = True

    @property
    def ticks(self) -> int:
        return len(self.cells)

    def extent(self) -> tuple[int, int]:
        xs = [c[0] for c in self.cells]
        ys = [c[1] for c in self.cells]
        return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)

    def render(self, box: tuple[int, int, int, int] | None = None) -> str:
        """The layout as a grid of glyphs -- the thing a proposal has to show."""
        if box is None:
            xs = [c[0] for c in self.cells]
            ys = [c[1] for c in self.cells]
            box = (min(xs), min(ys), max(xs), max(ys))
        x0, y0, x1, y1 = box
        m = dict(zip(self.cells, self.glyphs, strict=True))
        return "\n".join(
            "".join(m.get((x, y), " ") for x in range(x0, x1 + 1))
            for y in range(y0, y1 + 1)
        )

    def explain(self) -> str:
        w, h = self.extent()
        return (f"{'closed lap' if self.closed else 'open leg'} "
                f"{self.ticks} ticks, {w}x{h}\n" + self.render())


# ── the router ───────────────────────────────────────────────────────────────
def _turn_of(h_in: str, h_out: str) -> str:
    """``"straight"``, ``"cw"``, ``"ccw"`` or ``"reverse"`` between two headings."""
    if h_in == h_out:
        return "straight"
    if CW[(_IDX[h_in] + 1) % 4] == h_out:
        return "cw"
    if CW[(_IDX[h_in] - 1) % 4] == h_out:
        return "ccw"
    return "reverse"


_UNSTEER = {"E": ">", "S": "v", "W": "<", "N": "^"}


def _assign(turns, cells, ops):
    """Fit ``ops`` onto a routed path, or say it cannot be done.

    The path already fixes, at every cell, whether the man goes straight or turns
    and which way.  So the glyph question decomposes: a turn cell needs a glyph
    that turns *that* way, a straight cell one that does not turn, and the ops
    have to appear in order.  Assigning each op to the **earliest** cell that
    accepts it is optimal for feasibility -- placing it later can only take
    choices away from the ops behind it -- so one greedy pass decides it.

    Returns a glyph per cell, ``None`` where a steer filler is wanted, or
    ``None`` for the whole thing if the ops do not fit.
    """
    out = []
    i = 0
    for j, t in enumerate(turns):
        if t == "reverse":
            return None
        g = None
        if i < len(ops):
            op = ops[i]
            if op.at is None or op.at == cells[j]:
                gl = op.glyph
                if gl in OP_TURNS:
                    ok = t in OP_TURNS[gl] and (op.exit is None or op.exit == t)
                else:
                    ok = t == "straight"
                if ok:
                    g = gl
                    i += 1
        if g is None:
            # a pinned op whose cell just went to a filler can never be placed
            if any(o.at == cells[j] for o in ops[i:] if o.at is not None):
                return None
            g = "." if t == "straight" else None
        out.append(g)
    return out if i == len(ops) else None


def _search(ops, box, start, heading, length, closed, end, end_heading, blocked):
    """Search self-avoiding walks of exactly ``length`` cells for a fitting one.

    The search is over **paths**, not over glyph assignments, and that is the
    whole trick: a path fixes the turn at every cell, and given the turns the
    glyphs follow from one greedy pass (:func:`_assign`).  Searching glyphs
    directly -- five fillers times four headings at every cell -- re-explores the
    same path thousands of times over and does not terminate on anything
    interesting.

    Self-avoidance is not a restriction on optimality: see the module docstring.
    """
    x0, y0, x1, y1 = box
    cells: list = []
    heads: list = []
    seen: set = set()

    def build(exit_heading):
        hs = heads + [exit_heading]
        turns = [_turn_of(hs[j], hs[j + 1]) for j in range(len(cells))]
        g = _assign(turns, cells, ops)
        if g is None:
            return None
        return Circuit(
            list(cells),
            [ch if ch is not None else _UNSTEER[hs[j + 1]] for j, ch in enumerate(g)],
            closed=closed,
        )

    goal = start if closed else end

    def rec(cell, h, left):
        if closed and cell == start and cells:
            return build(h) if (left == 0 and h == heads[0]) else None
        if left == 0 or cell in seen or not (x0 <= cell[0] <= x1 and y0 <= cell[1] <= y1):
            return None
        if cell in blocked:
            return None
        # Two prunes, and the second is the one that makes the search finish.
        # Distance: the walk still has to reach its goal, and it moves one cell a
        # tick.  Parity: the grid is bipartite, so a walk of ``k`` moves can only
        # end on a cell whose distance from here has the same parity as ``k`` --
        # which halves the depth the search wastes on unreachable closures.
        if goal is not None:
            d = abs(cell[0] - goal[0]) + abs(cell[1] - goal[1])
            k = left - (0 if closed else 1)
            if d > k or (d - k) % 2:
                return None
        seen.add(cell)
        cells.append(cell)
        heads.append(h)
        try:
            if not closed and left == 1:
                if end is not None and cell != end:
                    return None
                for eh in ([end_heading] if end_heading else [h]):
                    c = build(eh)
                    if c is not None:
                        return c
                return None
            for nh in DIRS:
                if _turn_of(h, nh) == "reverse":
                    continue
                dx, dy = DIRS[nh]
                c = rec((cell[0] + dx, cell[1] + dy), nh, left - 1)
                if c is not None:
                    return c
            return None
        finally:
            if cells and cells[-1] == cell:
                seen.discard(cell)
                cells.pop()
                heads.pop()

    return rec(start, heading, length)


def route_loop(
    ops: str | list[Op],
    *,
    box: tuple[int, int, int, int] = (0, 0, 7, 7),
    start: tuple[int, int] = (0, 0),
    heading: str | None = None,
    blocked: frozenset = frozenset(),
    max_ticks: int = 40,
) -> Circuit | None:
    """The shortest **closed** lap performing ``ops`` once, in order.

    Lengths are tried in increasing order and only even ones are tried at all:
    the grid is bipartite, so a closed walk of odd length does not exist.  The
    first hit is therefore the minimum, and it comes with the glyphs on it.
    """
    ops = [Op(g) for g in ops] if isinstance(ops, str) else list(ops)
    lo = max(4, len(ops))
    lo += lo % 2
    heads = list(DIRS) if heading is None else [heading]
    for length in range(lo, max_ticks + 1, 2):
        for h in heads:
            c = _search(ops, box, start, h, length, True, None, None, blocked)
            if c is not None:
                return c
    return None


def route_open(
    ops: str | list[Op],
    *,
    box: tuple[int, int, int, int] = (0, 0, 7, 7),
    start: tuple[int, int] = (0, 0),
    heading: str = "E",
    end: tuple[int, int] | None = None,
    end_heading: str | None = None,
    blocked: frozenset = frozenset(),
    max_ticks: int = 40,
) -> Circuit | None:
    """The shortest **open** leg performing ``ops`` once, in order.

    This is the pre-send half of a worker: the cells between "the request landed"
    and "the answer was sent".  Those are the only cells a requester waits for,
    which is why they are routed separately from the lap that closes them.
    """
    ops = [Op(g) for g in ops] if isinstance(ops, str) else list(ops)
    for length in range(len(ops), max_ticks + 1):
        c = _search(ops, box, start, heading, length, False, end, end_heading,
                    blocked)
        if c is not None:
            return c
    return None


def loop_floor_exact(ops: str, **kw) -> int | None:
    """The routed minimum lap, or ``None`` if no lap of any allowed size exists.

    Compare against :func:`place.route.loop_floor`, which counts.  Where they
    disagree the counting floor is the optimistic one and this is the truth,
    because this one comes with a construction.
    """
    c = route_loop(ops, **kw)
    return None if c is None else c.ticks
