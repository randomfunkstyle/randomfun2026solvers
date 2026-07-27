"""Geometric flow graph of a ``.man`` grid.

This module is deliberately *semantics-free*.  It never asks what a program
computes; it only asks where the little men walk.  The split that makes that
possible is in the language itself:

* ``' '`` ``'.'`` ``'>'`` ``'<'`` ``'^'`` ``'v'`` ``'V'`` are **corridor**
  glyphs.  They carry a man from one place to another and touch no hand, no
  backpack and no pipe.  Their only observable effect is the man's direction.
* Every other instruction is a **node**.  Its effect is independent of the
  direction the man walks it (the one exception, a numeric literal, is a rigid
  multi-cell run that this module keeps intact).

So the meaning of a program is exactly the graph: which node each man executes
next, and — for the conditional turns — which arm he takes.  The corridors
between the nodes are free to be redrawn, and every cell of corridor a man walks
costs one tick.  That is the whole optimisation surface.

Nodes are keyed by ``(cell, direction)`` because a conditional turn leaves in a
direction that depends on how it was entered; two walks of the same cell from
different sides are different graph nodes even though they run the same glyph.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from .fast_littleman import (
    ARROW_DIR,
    DIRS,
    VALID_OPS,
    Cell,
    Dir,
    FastLittleman,
    _add,
    _ccw,
    _cw,
)

__all__ = [
    "CORRIDOR_GLYPHS",
    "NEUTRAL_GLYPHS",
    "Arm",
    "Edge",
    "FlowGraph",
    "Node",
    "NodeKind",
    "build_flow_graph",
    "canonical_signature",
    "exits_for",
]

#: Glyphs a corridor may be paved with.  ``' '`` and ``'.'`` pass a man through
#: unchanged; the four arrows re-aim him.  Nothing here reads or writes state.
NEUTRAL_GLYPHS = frozenset(" .")
CORRIDOR_GLYPHS = NEUTRAL_GLYPHS | frozenset(ARROW_DIR)

#: Conditional turns.  The decision is made from runtime state, so the graph
#: keeps every arm the glyph can possibly take.
BRANCH_ARMS: dict[str, tuple[int, ...]] = {
    "X": (-1, 0, +1),  # sign(A): ccw / straight / cw
    "a": (-1, 0),  # BP > 0 -> ccw
    "d": (0, +1),  # BP > 0 -> cw
    "x": (-1, +1),  # BP low bit: cw if set else ccw
}

#: Receives that re-aim the man at the pipe he read from.  The arm set is the
#: room's incoming pipe sides, so it is a property of the layout, not of state.
TURN_ON_RECEIVE = frozenset("U")

#: ``Y`` forks: the runner turns cw, a fresh runner starts ccw.
FORK_GLYPHS = frozenset("Y")

TERMINAL_GLYPHS = frozenset("H")


class NodeKind(str, Enum):
    START = "start"  # the '@' a man spawns on
    PLAIN = "plain"  # one successor, leaves the way it was entered
    BRANCH = "branch"  # X / a / d / x
    RECEIVE_TURN = "receive-turn"  # U
    FORK = "fork"  # Y
    HALT = "halt"  # H
    WALL = "wall"  # walking off the room — a fatal the graph records, not a cell


@dataclass(frozen=True, slots=True)
class Arm:
    """One outgoing branch of a node, identified by turn relative to entry.

    ``turn`` is -1 (ccw), 0 (straight) or +1 (cw) for the conditional turns, and
    is the *label* that must survive a reroute: arm ``+1`` of an ``X`` has to
    stay arm ``+1``, or the program means something else.
    """

    turn: int
    direction: Dir


@dataclass(slots=True)
class Node:
    id: int
    pos: Cell
    glyph: str
    kind: NodeKind
    room: int
    in_dir: Dir
    #: Cells of a multi-cell numeric literal that must be walked to reach this
    #: node, in walk order.  Empty for every other glyph.  A literal is rigid:
    #: the reroute may change how a man arrives at its opening backtick but not
    #: the run itself.
    literal_run: tuple[Cell, ...] = ()
    out_edges: list[int] = field(default_factory=list)
    in_edges: list[int] = field(default_factory=list)

    @property
    def key(self) -> tuple[Cell, Dir]:
        return (self.pos, self.in_dir)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        x, y = self.pos
        return f"Node#{self.id}({self.glyph!r}@{x},{y} dir={self.in_dir} {self.kind.value})"


@dataclass(slots=True)
class Edge:
    """A corridor: the cells a man walks between two nodes.

    ``cells`` are the corridor cells *strictly between* the two node cells, in
    walk order.  ``len(cells)`` is the edge's cost in ticks — every cell is one
    tick — and is exactly what the router is trying to shrink.
    """

    id: int
    src: int
    arm: int  # -1 / 0 / +1, matching Arm.turn on the source node
    dst: int | None  # None when the walk dies (wall / bad glyph / corridor loop)
    cells: tuple[Cell, ...]
    exit_dir: Dir  # direction leaving the source cell
    entry_dir: Dir  # direction entering the destination cell
    room: int
    dead: str | None = None  # why the walk died, when dst is None

    @property
    def length(self) -> int:
        return len(self.cells)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        tail = self.dead or f"->#{self.dst}"
        return f"Edge#{self.id}(#{self.src} arm={self.arm:+d} {tail} len={self.length})"


@dataclass(slots=True)
class FlowGraph:
    program: FastLittleman
    nodes: list[Node]
    edges: list[Edge]
    #: node id each man starts on, in runner-id order (row-major over spawns,
    #: matching the engine).
    starts: list[int]
    #: cells pinned because a node sits on them, mapped to the node ids there.
    node_cells: dict[Cell, list[int]]

    def node_at(self, pos: Cell, direction: Dir) -> Node | None:
        for nid in self.node_cells.get(pos, ()):
            if self.nodes[nid].in_dir == direction:
                return self.nodes[nid]
        return None

    def corridor_cells(self) -> dict[Cell, list[int]]:
        """Every corridor cell, mapped to the edges that walk it."""
        used: dict[Cell, list[int]] = {}
        for edge in self.edges:
            for cell in edge.cells:
                used.setdefault(cell, []).append(edge.id)
        return used

    def total_corridor_cells(self) -> int:
        return len(self.corridor_cells())

    def describe(self) -> str:  # pragma: no cover - reporting
        lines = [
            f"{len(self.nodes)} nodes, {len(self.edges)} edges, "
            f"{self.total_corridor_cells()} corridor cells, "
            f"{len(self.starts)} man(men)"
        ]
        for edge in self.edges:
            src = self.nodes[edge.src]
            if edge.dst is None:
                dst_desc = f"<{edge.dead}>"
            else:
                node = self.nodes[edge.dst]
                dst_desc = f"{node.glyph!r}@{node.pos[0]},{node.pos[1]}"
            lines.append(
                f"  {src.glyph!r}@{src.pos[0]},{src.pos[1]} "
                f"arm={edge.arm:+d} -> {dst_desc}  len={edge.length}"
            )
        return "\n".join(lines)


def _literal_ahead(prog: FastLittleman, pos: Cell, direction: Dir) -> tuple[Cell, ...]:
    """Return the cells of a numeric literal opened at ``pos``, or ``()``.

    A backtick only opens a literal along the axis it pairs on, and the engine
    resolves that when it precomputes closers.  We rebuild the run by walking
    forward to the closing backtick the engine agreed on; if there is none in
    this direction the backtick is a no-op and forms no run.
    """
    if prog._char(*pos) != "`":
        return ()
    cells = [pos]
    cursor = pos
    for _ in range(max(prog.width, prog.height) + 2):
        cursor = _add(cursor, direction)
        ch = prog._char(*cursor)
        cells.append(cursor)
        if ch == "`":
            if (cursor, direction) in prog._literal_closers:
                return tuple(cells)
            return ()
        if ch not in " 0123456789":
            return ()
    return ()


def _node_kind(glyph: str) -> NodeKind:
    if glyph == "@":
        return NodeKind.START
    if glyph in BRANCH_ARMS:
        return NodeKind.BRANCH
    if glyph in TURN_ON_RECEIVE:
        return NodeKind.RECEIVE_TURN
    if glyph in FORK_GLYPHS:
        return NodeKind.FORK
    if glyph in TERMINAL_GLYPHS:
        return NodeKind.HALT
    return NodeKind.PLAIN


def _exits(prog: FastLittleman, node: Node) -> list[Arm]:
    """Every direction a man can leave ``node`` in, with its arm label."""
    kind = node.kind
    if kind is NodeKind.HALT:
        return []
    if kind is NodeKind.BRANCH:
        arms = []
        for turn in BRANCH_ARMS[node.glyph]:
            if turn == 0:
                arms.append(Arm(0, node.in_dir))
            elif turn > 0:
                arms.append(Arm(+1, _cw(node.in_dir)))
            else:
                arms.append(Arm(-1, _ccw(node.in_dir)))
        return arms
    if kind is NodeKind.FORK:
        # The parent turns cw and is held for a tick; the child leaves ccw.
        return [Arm(+1, _cw(node.in_dir)), Arm(-1, _ccw(node.in_dir))]
    if kind is NodeKind.RECEIVE_TURN:
        room = prog.rooms[node.room]
        sides = {prog.pipes[pid].dst_side for pid in room.incoming}
        if not sides:
            return [Arm(0, node.in_dir)]
        # Arm labels for U are the absolute side read from, encoded as the turn
        # that reaches them, so the reroute keeps each side's continuation.
        arms = []
        for side in sorted(sides, key=DIRS.index):
            turn = _turn_between(node.in_dir, side)
            arms.append(Arm(turn, side))
        return arms
    return [Arm(0, node.in_dir)]


def exits_for(prog: FastLittleman, node: Node, in_dir: Dir) -> list[Arm]:
    """``_exits`` for a hypothetical entry direction, without touching ``node``.

    Rotating a node is legal precisely because an instruction's effect does not
    depend on the direction it is walked, and a conditional turn's arms are
    *relative* — arm ``+1`` is "clockwise from however he came in".  So the arms
    survive a rotation with their labels intact; only the compass headings move.
    """
    if in_dir == node.in_dir:
        return _exits(prog, node)
    spun = Node(
        id=node.id,
        pos=node.pos,
        glyph=node.glyph,
        kind=node.kind,
        room=node.room,
        in_dir=in_dir,
        literal_run=node.literal_run,
    )
    return _exits(prog, spun)


def canonical_signature(graph: FlowGraph) -> tuple:
    """A fingerprint that survives rotation but not a change of meaning.

    :func:`manreroute.graph_signature` compares nodes by ``(cell, direction)``,
    which is exactly right while directions are pinned and useless once they are
    not.  This numbers nodes by a breadth-first walk from the men's starting
    cells instead, so identity comes from *where a node sits in the program*
    rather than from which way the man happened to be facing.  Two graphs match
    when every man runs the same glyphs in the same order and every conditional
    turn keeps the same arm labels.

    Cells are deliberately absent: a node's cell is pinned today, but binding
    (``s``/``r`` nearest-pipe) is what actually depends on it, and that is
    checked separately.
    """
    order: dict[int, int] = {}
    pending: deque[int] = deque()
    for start in graph.starts:
        if start not in order:
            order[start] = len(order)
            pending.append(start)

    encoded: list[tuple] = []
    while pending:
        nid = pending.popleft()
        node = graph.nodes[nid]
        arms: list[tuple] = []
        for edge_id in sorted(node.out_edges, key=lambda e: (graph.edges[e].arm, e)):
            edge = graph.edges[edge_id]
            if edge.dst is None:
                arms.append((edge.arm, "dead", edge.dead))
                continue
            if edge.dst not in order:
                order[edge.dst] = len(order)
                pending.append(edge.dst)
            arms.append((edge.arm, "node", order[edge.dst]))
        encoded.append((order[nid], node.glyph, len(node.literal_run), tuple(arms)))

    encoded.sort()
    return (tuple(order[s] for s in graph.starts), tuple(encoded))


def _turn_between(a: Dir, b: Dir) -> int:
    if a == b:
        return 0
    if _cw(a) == b:
        return +1
    if _ccw(a) == b:
        return -1
    return 2  # reversal — only reachable for U


def build_flow_graph(program: str | Path | Sequence[str] | FastLittleman) -> FlowGraph:
    """Walk every reachable ``(cell, direction)`` state and return the graph.

    The walk is exhaustive over branch arms, not a trace: an arm that the real
    program never takes still appears, because the router must keep it walkable.
    """
    prog = program if isinstance(program, FastLittleman) else FastLittleman(program)

    nodes: list[Node] = []
    node_by_key: dict[tuple[Cell, Dir], int] = {}
    node_cells: dict[Cell, list[int]] = {}
    edges: list[Edge] = []

    def intern(pos: Cell, direction: Dir, room: int, literal: tuple[Cell, ...] = ()) -> int:
        key = (pos, direction)
        existing = node_by_key.get(key)
        if existing is not None:
            return existing
        glyph = prog._char(*pos)
        node = Node(
            id=len(nodes),
            pos=pos,
            glyph=glyph,
            kind=_node_kind(glyph),
            room=room,
            in_dir=direction,
            literal_run=literal,
        )
        nodes.append(node)
        node_by_key[key] = node.id
        node_cells.setdefault(pos, []).append(node.id)
        return node.id

    spawns = sorted(
        (
            (room.spawn, room.id)
            for room in prog.rooms
            if room.kind == "compute" and room.spawn is not None
        ),
        key=lambda item: (item[0][1], item[0][0]),
    )

    starts = [intern(spawn, DIRS[0], room_id) for spawn, room_id in spawns]

    pending: deque[int] = deque(starts)
    seen: set[int] = set(starts)

    while pending:
        nid = pending.popleft()
        node = nodes[nid]
        for arm in _exits(prog, node):
            edge = _walk(prog, node, arm, intern)
            edge.id = len(edges)
            edges.append(edge)
            node.out_edges.append(edge.id)
            if edge.dst is not None:
                nodes[edge.dst].in_edges.append(edge.id)
                if edge.dst not in seen:
                    seen.add(edge.dst)
                    pending.append(edge.dst)

    return FlowGraph(
        program=prog,
        nodes=nodes,
        edges=edges,
        starts=starts,
        node_cells=node_cells,
    )


def _walk(prog: FastLittleman, node: Node, arm: Arm, intern) -> Edge:
    """Follow corridor from ``node`` along ``arm`` until the next node."""
    room = prog.rooms[node.room]
    corridor: list[Cell] = []
    pos = node.pos
    direction = arm.direction
    # A literal's run is walked as part of reaching its closing backtick, so a
    # node that owns a run starts the next walk from the run's last cell.
    if node.literal_run:
        pos = node.literal_run[-1]

    limit = (room.max[0] - room.min[0] + 1) * (room.max[1] - room.min[1] + 1) * 4 + 8
    for _ in range(limit):
        pos = _add(pos, direction)
        if room.on_border(pos) or not room.contains(pos):
            return Edge(
                id=-1,
                src=node.id,
                arm=arm.turn,
                dst=None,
                cells=tuple(corridor),
                exit_dir=arm.direction,
                entry_dir=direction,
                room=node.room,
                dead="wall",
            )
        ch = prog._char(*pos)
        if ch not in VALID_OPS and ch != "@":
            return Edge(
                id=-1,
                src=node.id,
                arm=arm.turn,
                dst=None,
                cells=tuple(corridor),
                exit_dir=arm.direction,
                entry_dir=direction,
                room=node.room,
                dead="bad-op",
            )
        if ch in NEUTRAL_GLYPHS:
            corridor.append(pos)
            continue
        if ch in ARROW_DIR:
            corridor.append(pos)
            direction = ARROW_DIR[ch]
            continue
        # A '@' is a nop for anyone walking it, but it is pinned, so it is a
        # node rather than corridor.
        literal = _literal_ahead(prog, pos, direction) if ch == "`" else ()
        dst = intern(pos, direction, node.room, literal)
        return Edge(
            id=-1,
            src=node.id,
            arm=arm.turn,
            dst=dst,
            cells=tuple(corridor),
            exit_dir=arm.direction,
            entry_dir=direction,
            room=node.room,
        )

    return Edge(
        id=-1,
        src=node.id,
        arm=arm.turn,
        dst=None,
        cells=tuple(corridor),
        exit_dir=arm.direction,
        entry_dir=direction,
        room=node.room,
        dead="corridor-loop",
    )


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m randomfun2026solvers.manflow",
        description="Print the geometric flow graph of a .man grid.",
    )
    parser.add_argument("program", type=Path)
    parser.add_argument("--edges", action="store_true", help="list every edge")
    args = parser.parse_args(argv)

    graph = build_flow_graph(args.program)
    if args.edges:
        print(graph.describe())
    else:
        print(graph.describe().splitlines()[0])
    dead = [e for e in graph.edges if e.dst is None]
    if dead:
        print(f"{len(dead)} dead arm(s): " + ", ".join(sorted({e.dead or '?' for e in dead})))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
