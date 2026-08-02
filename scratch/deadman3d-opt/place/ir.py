#!/usr/bin/env python3
"""The placement IR: a leg is a DAG of operation nodes that claim 2D positions.

Why an IR at all
----------------
Every layout decision on this machine has been made by hand, one nitpick at a
time, and the hand cannot see the thing that actually governs: **a leg costs
exactly the number of cells the man walks.**  He moves one cell per tick
(``SPEC.md`` tick order step 4, "every non-blocked little man advances one cell
along his heading"), and the glyph he lands on fires for free -- it is the same
tick he was going to spend stepping there anyway.

That single fact reshapes the problem.  The ops are not *costs* to be minimised;
they are *free riders* on a walk, and the walk is the cost.  So:

    leg ticks  =  length of the man's path in cells
    floor      =  the number of op cells that must be visited, in order

A leg sitting at its floor is "Manhattan-minimal" -- every tick it spends is a
tick it spends executing something -- and no amount of moving rooms around will
improve it.  The only remaining levers are **fewer ops** and **fewer laps**.
This is why the taped bank worker's ``19 + 8a`` cannot be improved by
relocation, and why a framework is worth building: it can tell you *that you are
at the floor* and stop you looking, which hand-inspection cannot.

The pieces
----------
:class:`Node`
    A set of operations -- a run of glyphs -- with a body layout, an entry port,
    an exit port, a lap count, and either a pinned position or freedom to move.
    Nodes nest: a node may carry a whole sub-:class:`Leg` instead of glyphs.

:class:`Edge`
    "the man must travel from here to there".  Cost is Manhattan distance times
    the number of traversals per access.  Legs are Manhattan-exact -- measured,
    the man walks the full distance and there are no shortcuts -- so the edge
    cost is the geometry, not an estimate.

:class:`Pipe`
    A typed endpoint with an attach cell that may be free or pinned.  Any ``r``
    or ``s`` node names the pipe it intends to bind, and :mod:`place.legal`
    enforces ARCH 7.1 against every rival in the same direction pool.

:class:`Placement`
    An assignment of positions to the free nodes and free pipe attachments.  It
    is the thing :func:`place.score.score` scores and :mod:`place.search`
    searches over.

Coordinates are screen coordinates throughout: ``+x`` east, ``+y`` south, which
is what ``machine.py`` and the reading-order tiebreak both assume.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO / "solvers" / "python") not in sys.path:
    sys.path.insert(0, str(REPO / "solvers" / "python"))

__all__ = [
    "PRE_SEND",
    "POST_SEND",
    "PIPE",
    "Phase",
    "Node",
    "Edge",
    "Pipe",
    "Leg",
    "Placement",
    "manhattan",
    "horizontal_body",
    "vertical_body",
]

# ── phases ───────────────────────────────────────────────────────────────────
#: The consumer is stopped waiting for this work.  Every tick is on the critical
#: path and is charged at the pre-send rate.
PRE_SEND = "pre_send"
#: The answer has already been sent; the man is walking home.  These ticks only
#: cost anything if the *next* request arrives before he is ready to serve it,
#: which is what the queueing term in :mod:`place.score` decides.
POST_SEND = "post_send"
#: Cells of a pipe, not of a room.  Charged per *cell*, not per tick: SPEC's tick
#: order shifts every pipe value one cell before any man executes, so each serial
#: pipe cell is one tick the consumer is stopped for, unconditionally.
PIPE = "pipe"

Phase = str

Cell = tuple[int, int]


def manhattan(a: Cell, b: Cell) -> int:
    """Walk distance between two cells.  Legs are Manhattan-exact (measured)."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def transit(a: Cell, b: Cell) -> int:
    """Ticks charged for travelling from ``a`` to ``b``, both endpoints excluded.

    A tick is a cell the man *stands on*, and the glyph under him fires on that
    same tick -- there is no separate execution time.  So the honest accounting
    is "count the distinct cells he stands on, once each", and an edge must not
    re-charge either endpoint: ``a`` is the source node's exit cell, already paid
    for in that node's body, and ``b`` is the destination's entry cell, likewise.

    What the edge really costs is the **intermediate** cells, of which a
    Manhattan walk of distance ``d`` has ``d - 1``.  Two nodes placed adjacent
    therefore cost nothing to travel between, which is correct: the man was
    stepping anyway.

    This is the difference between a floor that is reachable and one that is
    not.  Charging ``d`` would make every sequential leg look one tick per op
    worse than it is, and a leg at its true floor would never report as
    Manhattan-minimal.
    """
    d = manhattan(a, b)
    return max(0, d - 1)


# ── bodies ───────────────────────────────────────────────────────────────────
def horizontal_body(glyphs: str, east: bool = True) -> dict[Cell, str]:
    """A run of glyphs laid west-to-east (or east-to-west) on one row."""
    if east:
        return {(i, 0): g for i, g in enumerate(glyphs)}
    return {(-i, 0): g for i, g in enumerate(glyphs)}


def vertical_body(glyphs: str, south: bool = True) -> dict[Cell, str]:
    """A run of glyphs laid down a column (or up one)."""
    if south:
        return {(0, i): g for i, g in enumerate(glyphs)}
    return {(0, -i): g for i, g in enumerate(glyphs)}


# ── nodes ────────────────────────────────────────────────────────────────────
@dataclass
class Node:
    """A set of operations that claims one position in 2D.

    :param name: unique within its :class:`Leg`.
    :param body: ``{(dx, dy): glyph}`` relative to the node's origin.  Every cell
        in the body is a cell the man walks, so ``len(body)`` is the node's tick
        cost per lap -- there is no separate "execution time".
    :param entry: body-relative cell the man arrives on.  Defaults to the body's
        reading-order first cell.
    :param exit: body-relative cell the man leaves from.  Defaults to the body's
        reading-order last cell.
    :param laps: expected traversals of this body per access.  Fractional is
        normal and correct -- a skip loop whose trip count averages 5.17 costs
        5.17 body-lengths per access, and averaging before placing is exactly
        right because placement cannot see the distribution anyway.
    :param pos: pinned absolute origin, or ``None`` to let the search choose.
    :param phase: :data:`PRE_SEND` or :data:`POST_SEND` -- which side of the
        answer's ``S`` this work falls on.  This selects the rate, and it is the
        single most consequential field on the node: the same tick is worth
        14x more before the send than after it.
    :param pipe: for a node containing ``r``/``s``/``R``/``S``/``U``/``q``, the
        name of the :class:`Pipe` it intends to bind.  Checked against ARCH 7.1.
    :param pipe_at: which body cell carries the pipe glyph, if ``pipe`` is set.
    :param sub: a nested :class:`Leg`, for a node that is itself a subgraph.
        A node has either a ``body`` or a ``sub``, never both.
    :param opaque: if False, other nodes' men may walk this node's cells (the
        transparency question -- see :mod:`place.legal`, which decides it from
        register liveness rather than taking this field's word for it).
    """

    name: str
    body: dict[Cell, str] = field(default_factory=dict)
    entry: Cell | None = None
    exit: Cell | None = None
    laps: float = 1.0
    pos: Cell | None = None
    phase: Phase = PRE_SEND
    pipe: str | None = None
    pipe_at: Cell = (0, 0)
    sub: "Leg | None" = None
    opaque: bool = True

    def __post_init__(self) -> None:
        if self.body and self.sub is not None:
            raise ValueError(f"{self.name}: a node has a body or a sub-leg, not both")
        if not self.body and self.sub is None:
            raise ValueError(f"{self.name}: node has neither body nor sub-leg")
        if self.body:
            order = sorted(self.body, key=lambda c: (c[1], c[0]))
            if self.entry is None:
                self.entry = order[0]
            if self.exit is None:
                self.exit = order[-1]
            if self.pipe is not None and self.pipe_at not in self.body:
                raise ValueError(f"{self.name}: pipe_at {self.pipe_at} not in body")

    # -- geometry -------------------------------------------------------------
    @property
    def cells(self) -> int:
        """Cells the man walks per lap.  This *is* the node's tick cost."""
        if self.sub is not None:
            return self.sub.floor_cells()
        return len(self.body)

    @property
    def ticks(self) -> float:
        """Tick cost per access: cells walked, times laps."""
        return self.laps * self.cells

    def occupied(self, at: Cell) -> dict[Cell, str]:
        """Absolute ``{cell: glyph}`` when the node's origin is at ``at``."""
        if self.sub is not None:
            return self.sub.occupied_at(at)
        return {(at[0] + dx, at[1] + dy): g for (dx, dy), g in self.body.items()}

    def entry_abs(self, at: Cell) -> Cell:
        e = self.entry or (0, 0)
        return (at[0] + e[0], at[1] + e[1])

    def exit_abs(self, at: Cell) -> Cell:
        e = self.exit or (0, 0)
        return (at[0] + e[0], at[1] + e[1])

    def pipe_abs(self, at: Cell) -> Cell:
        return (at[0] + self.pipe_at[0], at[1] + self.pipe_at[1])

    def extent(self) -> tuple[int, int]:
        """(w, h) of the node's bounding box."""
        if self.sub is not None:
            return self.sub.extent()
        xs = [c[0] for c in self.body]
        ys = [c[1] for c in self.body]
        return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


# ── edges ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Edge:
    """"The man must travel from ``src``'s exit to ``dst``'s entry."

    :param weight: traversals per access.  A loop back-edge taken ``n`` times
        carries ``weight = n``; the ordinary sequential edge carries 1.
    :param phase: which side of the send this travel falls on.  Defaults to
        inheriting ``src``'s phase, resolved in :meth:`Leg.edge_phase`.
    :param free: if True the travel is known not to cost the walk -- the man was
        going to be there anyway (an edge that merely records ordering, e.g. a
        fall-through between two nodes that share a cell).  Used sparingly and
        always with a comment saying why.
    """

    src: str
    dst: str
    weight: float = 1.0
    phase: Phase | None = None
    free: bool = False
    note: str = ""


# ── pipes ────────────────────────────────────────────────────────────────────
#: Pipe names that carry values *into* a room -- the pool an ``r``/``R``/``U``/``q``
#: chooses from.  Verbatim from ``z3/bind.py``, which is the validated model.
INCOMING = {"rom", "in", "mem_resp", "Band.STREAM_RESP", "stream_resp"}


@dataclass
class Pipe:
    """A typed endpoint whose attach cell may be free or pinned.

    :param name: must be routed to the same names ``z3/bind.py`` uses, because
        the incoming/outgoing pool split is by name.
    :param attach: the cell on the room border that the pipe touches.  This, not
        the pipe's far end, is what the binding rule measures distance to.
    :param cells: serial cell count of the pipe body.  Charged at the pipe rate,
        per cell, unconditionally -- see :data:`PIPE`.
    :param free: if True the search may move ``attach``.
    """

    name: str
    attach: Cell
    cells: int = 2
    free: bool = False
    incoming: bool | None = None

    def is_incoming(self) -> bool:
        if self.incoming is not None:
            return self.incoming
        return self.name in INCOMING


# ── legs ─────────────────────────────────────────────────────────────────────
@dataclass
class Leg:
    """A DAG of operation nodes, plus the room it lives in and its pipes.

    :param weight: hot-path frequency -- how often this leg runs, relative to
        the others in the same score.  Throughput is what we optimise, so a leg
        that runs twice as often is worth twice as much.
    :param room: ``(x0, y0, x1, y1)`` inclusive interior bounds, or ``None`` for
        a free room whose extent is whatever the placement needs.
    """

    name: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    pipes: dict[str, Pipe] = field(default_factory=dict)
    weight: float = 1.0
    room: tuple[int, int, int, int] | None = None
    #: Name of the node holding the answer's ``S``.  Everything reachable before
    #: it is pre-send; everything after is post-send.  If ``None``, every node
    #: keeps whatever phase it was given.
    send_node: str | None = None

    # -- construction ---------------------------------------------------------
    def add(self, node: Node) -> Node:
        if node.name in self.nodes:
            raise ValueError(f"duplicate node {node.name}")
        self.nodes[node.name] = node
        return node

    def connect(self, src: str, dst: str, **kw) -> Edge:
        e = Edge(src, dst, **kw)
        self.edges.append(e)
        return e

    def add_pipe(self, pipe: Pipe) -> Pipe:
        self.pipes[pipe.name] = pipe
        return pipe

    # -- derived --------------------------------------------------------------
    def edge_phase(self, e: Edge) -> Phase:
        if e.phase is not None:
            return e.phase
        return self.nodes[e.src].phase

    def floor_cells(self) -> int:
        """The tick floor: op cells that must be visited, weighted by laps.

        This is the number a placement cannot beat.  A leg whose measured cost
        equals its floor is Manhattan-minimal and moving it is pointless; the
        only way down is deleting ops or cutting laps.
        """
        return sum(n.ticks for n in self.nodes.values())

    def is_cyclic(self) -> bool:
        """Does the man's path close?  A worker's does; a one-shot leg's does not.

        Matters because the two are priced by different floors.  An open leg's
        floor is its op count; a **closed lap** additionally needs four corner
        cells, because a rectilinear circuit turns at least four times and a
        turn consumes a cell's one glyph slot.  See :func:`place.route.loop_floor`.
        """
        seen, stack = set(), []

        def visit(n: str) -> bool:
            if n in stack:
                return True
            if n in seen:
                return False
            seen.add(n)
            stack.append(n)
            for e in self.edges:
                if e.src == n and visit(e.dst):
                    return True
            stack.pop()
            return False

        return any(visit(n) for n in self.nodes)

    def turning_ops(self) -> int:
        """Op cells whose glyph turns by itself, and so pays for its own corner."""
        turning = set("Xdax")
        return sum(
            1
            for n in self.nodes.values()
            for g in n.body.values()
            if g in turning
        )

    def op_cells(self) -> int:
        """Cells that do work: everything that is not a pure steer or a blank."""
        steer, blank = set("<>^vV"), set(". ")
        return sum(
            1
            for n in self.nodes.values()
            for g in n.body.values()
            if g not in steer and g not in blank
        )

    def free_nodes(self) -> list[str]:
        return [k for k, n in self.nodes.items() if n.pos is None]

    def free_pipes(self) -> list[str]:
        return [k for k, p in self.pipes.items() if p.free]

    def occupied_at(self, origin: Cell) -> dict[Cell, str]:
        out: dict[Cell, str] = {}
        for n in self.nodes.values():
            if n.pos is None:
                continue
            at = (origin[0] + n.pos[0], origin[1] + n.pos[1])
            out.update(n.occupied(at))
        return out

    def extent(self) -> tuple[int, int]:
        cells = [c for n in self.nodes.values() if n.pos for c in n.occupied(n.pos)]
        if not cells:
            return (0, 0)
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


# ── placements ───────────────────────────────────────────────────────────────
@dataclass
class Placement:
    """Positions for a leg's free nodes and free pipe attachments.

    A placement is *not* required to be legal; :mod:`place.legal` decides that
    and :mod:`place.score` refuses to score an illegal one.  Keeping the two
    apart is deliberate: a search wants to walk through infeasible ground.
    """

    leg: Leg
    node_pos: dict[str, Cell] = field(default_factory=dict)
    pipe_pos: dict[str, Cell] = field(default_factory=dict)

    def pos_of(self, name: str) -> Cell:
        n = self.leg.nodes[name]
        if n.pos is not None:
            return n.pos
        try:
            return self.node_pos[name]
        except KeyError:
            raise KeyError(f"node {name} is free but unplaced") from None

    def attach_of(self, name: str) -> Cell:
        p = self.leg.pipes[name]
        if not p.free:
            return p.attach
        return self.pipe_pos.get(name, p.attach)

    def touches(self) -> dict[str, Cell]:
        """``{pipe name: attach cell}`` in the shape ``z3/bind.decide`` wants."""
        return {k: self.attach_of(k) for k in self.leg.pipes}

    def glyph_map(self) -> dict[Cell, list[str]]:
        """``{cell: [node names occupying it]}`` -- the overlap question's input."""
        out: dict[Cell, list[str]] = {}
        for name, n in self.leg.nodes.items():
            for c in n.occupied(self.pos_of(name)):
                out.setdefault(c, []).append(name)
        return out

    def cells(self) -> dict[Cell, str]:
        out: dict[Cell, str] = {}
        for name, n in self.leg.nodes.items():
            out.update(n.occupied(self.pos_of(name)))
        return out

    def extent(self) -> tuple[int, int]:
        cs = self.cells()
        if not cs:
            return (0, 0)
        xs = [c[0] for c in cs]
        ys = [c[1] for c in cs]
        return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)

    def footprint(self) -> int:
        w, h = self.extent()
        return w * h

    def with_node(self, name: str, at: Cell) -> "Placement":
        d = dict(self.node_pos)
        d[name] = at
        return replace(self, node_pos=d)

    def with_pipe(self, name: str, at: Cell) -> "Placement":
        d = dict(self.pipe_pos)
        d[name] = at
        return replace(self, pipe_pos=d)
