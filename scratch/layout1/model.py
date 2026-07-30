"""The declarative layout problem: blocks, ports, pipes, rooms, and a cost.

This is Phase 1 of ``littleman/LAYOUT-MANAGER.md`` — the *interface*, kept
deliberately small:

    blocks : rect + ports(side, fixed | free offset); translation only
    pipes  : src port -> dst port, min_length, traffic weight
    rooms  : the solver may insert a forwarder where it beats a pipe
    cost   : sum(cells x weight) at the measured rate
    verify : check_bindings on every candidate, not just the winner

Four modelling decisions, each of which is one of the document's four properties:

**1. A port is a glyph cell *and* a touch cell, and they are different.**
``ARCH.md`` §7.1 binds an ``r``/``s`` to the nearest pipe *touch* cell by
Manhattan distance, so the model has to carry both: the glyph sits on a block's
interior row, the touch cell sits one cell outside the wall.  A model that
carried only "port at (x,y)" could not express a rebinding at all.

**2. A room is crossed in constant time whatever its size.**  ``R``/``U``
receive with no distance term (``SPEC.md`` §Nearest), so a forwarder's cost is
:data:`FORWARDER_CELLS` no matter how tall it is.  That is what made the store's
request teleport worth -5.92%: the room *spanned* the corridor the pipe used to
walk around.  It is also why a block that is itself a room can be **grown** to
reach its caller for the price of the stub alone — the -7.48% ``REACH`` family.

**3. Translation only.**  A :class:`Block` carries candidate ``xs``/``ys`` and
nothing else.  Little-man glyphs are direction-semantic, so a rotated block is a
regenerated unit, not a transformed one, and the solver may not invent one.

**4. Cost is length x frequency.**  :attr:`Pipe.weight` is *accesses*, not a
priority, and the objective is ``sum(cells x weight)``.  A solver minimising
total length optimises the wrong thing — rung 3 of the ladder is exactly that
test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── the cost model, measured ─────────────────────────────────────────────────
#: Tour ticks per accesses-weighted pipe cell.  From ``chain_pad`` (M13):
#: +5 cells -> +4,187,905 ticks, +15 -> +12,564,227, linear to five figures.
#: M12 measured 1,060,929 independently on the request leg; they agree to 5%.
TICKS_PER_WEIGHTED_CELL = 1_112_472

#: What one forwarder costs, in cells of the pipe it replaces.  A pipe is a FIFO
#: and pipelines a multi-word request one tick apart; one man on a six-cell
#: ``@>Rv``/``^s<`` loop re-serialises it at one word per six ticks.  M12 measured
#: it by subtraction: of 52 cells removed, 46.8 came back, so ~5.2 did not.
#:
#: This is a *floor*, not a fee schedule: it is the same 5.2 whether the room is
#: four rows tall or forty, which is the whole reason a room is a routing
#: primitive rather than an obstacle.
#:
#: **But it is not one number, and Phase 1 found that out.**  Backed out of
#: ``STORE_ANSWER_WEST``'s four measured builds, the same constant comes to
#: **1.48, 0.70 and 3.71 cells** on three adjacent pairs (see
#: ``store.answer_path_probe``).  That is the right sign and the right order —
#: the model ranks all four builds exactly as the tour did — but it is 3x to 7x
#: over on the answer path.  It should be: 5.2 was measured re-serialising a
#: *multi-word request* at one word per six ticks, and an answer is one word,
#: with nothing to re-serialise.  A solver that has to choose *how many*
#: forwarders needs this per-pipe, from the words-per-message of the traffic on
#: it; a solver only choosing *whether* survives the error, because the gap
#: between a room and a 40-cell pipe is far wider than the error in 5.2.
FORWARDER_CELLS = 5.2

#: The shortest pipe that can attach a room to a wall: one cell out of the wall
#: and one arrowhead into the next one.  ``ARCH.md`` §7.4b's "both pipes want to
#: be the 2-cell minimum".
MIN_PIPE = 2

SIDES = ("N", "S", "E", "W")


@dataclass(frozen=True)
class Port:
    """Where a pipe attaches to a block, and which glyph inside it will bind.

    ``offset`` is measured along the side from the block's origin corner, in
    *grid* rows/columns, and indexes the wall cell.  ``None`` means the solver
    may choose it (a "free offset" port); the interior range is 1..h-2 (or
    1..w-2) so a pipe never lands on a corner.

    ``glyph`` is ``'r'``, ``'s'`` or ``''``.  The empty string is a *room* port:
    an ``R``/``U`` that receives from any incoming pipe with no distance term, so
    it has nothing to bind and never appears in a ``check_bindings`` call.  That
    distinction is load-bearing — it is why a gate room can be grown to reach its
    caller while a gate's ``s`` glyphs provably cannot (``TAPED_CHAIN_REACH``).
    """

    name: str
    side: str
    offset: int | None = None
    glyph: str = ""
    #: The ``lm1.machine.Band`` this glyph must bind, from the block's own point
    #: of view.  ``''`` for room ports.
    band: str = ""
    #: How many cells *inside* the wall the glyph sits.  1 is flush against it;
    #: the CPU's memory ``r`` sits ``mem_pad`` columns in, which is the entire
    #: reason ``MEM_PAD_FOR`` and ``INPUT_NORTH_WEST`` interact — a deep glyph is
    #: a glyph the input room can get closer to than its own pipe.
    depth: int = 1
    #: Block-local cells of *every* glyph that binds this pipe, when the block is
    #: a real one whose body is known.  A taped gate's north write arm has three
    #: ``s`` glyphs and its south path five more, and it is the *tightest* of the
    #: ten that decides whether the block may move — one representative glyph
    #: would model the wrong thing.  Empty means "derive one from ``depth``".
    cells: tuple[tuple[int, int], ...] = ()
    #: Explicit candidate offsets, overriding the interior range.  Needed for a
    #: port on a **grown** wall: a gate's local feed may attach up the extension
    #: its roof bought, which is a negative offset in the reference frame, and it
    #: is exactly the move ``check_bindings`` has to be given the chance to refuse.
    choices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"port {self.name}: side {self.side!r} not in {SIDES}")
        if self.glyph not in ("", "r", "s"):
            raise ValueError(f"port {self.name}: glyph {self.glyph!r}")
        if bool(self.glyph) != bool(self.band):
            raise ValueError(f"port {self.name}: glyph and band must agree")


@dataclass
class Block:
    """A rectangle with ports.  Translation only — no rotation, no reflection.

    ``w``/``h`` are the *outer* dimensions including the walls, so the interior
    is ``(w - 2) x (h - 2)``.  ``xs``/``ys`` are the candidate origins the solver
    may translate to; a single-element tuple pins the block.

    ``grow`` names the sides that may be **extended** to reach a caller.  Only a
    block whose ports on that side are room ports (``glyph == ''``) may grow:
    ``U`` turns away from the *wall* the pipe attaches to, not from the direction
    the pipe comes from, which is the permission slip ``STORE_REQUEST_REACH``
    rests on and which was measured, not read (``probe_gate_grow.py``).  Growing
    a side with an ``r``/``s`` on it walks that glyph away from its pipe and is
    rejected by the model, not merely priced.

    **Known gap.**  That rule is too strict for one of the three measured moves.
    ``STORE_ANSWER_WEST`` widens the answer collector *westward across its own*
    ``s``, and it is safe because that room has exactly one outgoing pipe, so the
    ``s`` has no rival to lose to — the glyph is redrawn beside the new wall.
    That is a **regenerated** block, not a translated one (property 3), so it
    needs a separate primitive — "widen a room, carrying its glyphs" — that this
    model does not have.  Stated rather than bodged: a ``carry`` flag here would
    be one line, and untested.
    """

    name: str
    w: int
    h: int
    ports: tuple[Port, ...] = ()
    xs: tuple[int, ...] = (0,)
    ys: tuple[int, ...] = (0,)
    grow: frozenset[str] = field(default_factory=frozenset)
    #: How far each growable side may be pushed, in cells.
    grow_max: int = 0

    def __post_init__(self) -> None:
        if self.w < 3 or self.h < 3:
            raise ValueError(f"block {self.name}: {self.w}x{self.h} has no interior")
        names = [p.name for p in self.ports]
        if len(set(names)) != len(names):
            raise ValueError(f"block {self.name}: duplicate port names")
        for side in self.grow:
            if side not in SIDES:
                raise ValueError(f"block {self.name}: cannot grow {side!r}")
            for p in self.ports:
                if p.side == side and p.glyph:
                    raise ValueError(
                        f"block {self.name}: side {side} carries the binding glyph "
                        f"{p.glyph!r} on port {p.name}, so growing it would walk that "
                        f"glyph away from its pipe (TAPED_CHAIN_REACH's arms)"
                    )

    def port(self, name: str) -> Port:
        for p in self.ports:
            if p.name == name:
                return p
        raise KeyError(f"block {self.name} has no port {name!r}")


@dataclass(frozen=True)
class Pipe:
    """One directed route, its floor length, and how often it is walked.

    ``band`` is the ``lm1.machine.Band`` name the pipe answers to; it is what
    ``check_bindings`` matches an ``s``/``r`` against, so it is part of the
    problem statement rather than an artefact of the solution.

    ``weight`` is *accesses per tour*, in whatever unit the caller measures.  The
    cost of the whole layout is ``sum(cells x weight)`` and nothing else — the
    437-cell ``cpu->drum`` and the 60-cell ``adapter->store`` differ by 300x in
    length and by 300x the other way in what they are worth.
    """

    name: str
    src: tuple[str, str]  # (block, port)
    dst: tuple[str, str]
    band: str
    weight: float
    min_length: int = MIN_PIPE
    #: May the solver put a forwarder on this leg?
    allow_room: bool = True


@dataclass
class Problem:
    blocks: tuple[Block, ...]
    pipes: tuple[Pipe, ...]
    #: The grid the whole thing has to fit inside, ``(w, h)``.
    bounds: tuple[int, int]
    name: str = ""

    def block(self, name: str) -> Block:
        for b in self.blocks:
            if b.name == name:
                return b
        raise KeyError(f"no block {name!r}")

    def __post_init__(self) -> None:
        for p in self.pipes:
            for bname, pname in (p.src, p.dst):
                port = self.block(bname).port(pname)
                if port.glyph and port.band != p.band:
                    raise ValueError(
                        f"pipe {p.name}: port {bname}.{pname} binds band "
                        f"{port.band!r} but the pipe is {p.band!r}"
                    )


# ── the answer ───────────────────────────────────────────────────────────────
@dataclass
class Leg:
    """One drawn pipe of a route: its cells, in flow order."""

    cells: tuple[tuple[int, int], ...]

    @property
    def length(self) -> int:
        return len(self.cells)


@dataclass
class Route:
    """How one :class:`Pipe` was realised: one leg, or two legs and a room."""

    pipe: str
    legs: tuple[Leg, ...]
    #: The forwarder's rect, when the solver inserted one.
    room: tuple[int, int, int, int] | None = None

    @property
    def cells(self) -> float:
        """Cells *charged*, which is drawn cells plus any forwarder's floor."""
        n = float(sum(leg.length for leg in self.legs))
        return n + FORWARDER_CELLS if self.room is not None else n

    @property
    def drawn(self) -> int:
        return sum(leg.length for leg in self.legs)


@dataclass
class Solution:
    placement: dict[str, tuple[int, int]]
    #: Per block, how far each side was grown.
    growth: dict[tuple[str, str], int]
    #: The chosen offset of every port a pipe attaches to.
    offsets: dict[tuple[str, str], int]
    routes: dict[str, Route]
    problem: Problem

    @property
    def weighted_cells(self) -> float:
        by = {p.name: p for p in self.problem.pipes}
        return sum(r.cells * by[name].weight for name, r in self.routes.items())

    @property
    def ticks(self) -> float:
        return self.weighted_cells * TICKS_PER_WEIGHTED_CELL

    def describe(self) -> str:
        by = {p.name: p for p in self.problem.pipes}
        out = []
        for name in sorted(self.routes):
            r, p = self.routes[name], by[name]
            tag = f" +room{r.room}" if r.room is not None else ""
            out.append(
                f"    {name:<24} {r.drawn:>4} cells x {p.weight:g}"
                f" = {r.cells * p.weight:>12,.0f}{tag}"
            )
        for (b, side), n in sorted(self.growth.items()):
            if n:
                out.append(f"    grow {b}.{side} by {n}")
        return "\n".join(out)
