#!/usr/bin/env python3
"""The inner-logic graph — one dataflow model of a room's contents.

The compactor's geometric layer sees a room as cells; the semantic layer needs to
see it as *computation*: which glyph feeds which, where the man may branch, what
each step reads and writes. That is the :class:`InnerLogicGraph` — the **single**
room-walker every family shares. Families must not each re-derive dataflow; they
match patterns against this graph.

A :class:`LogicNode` is one executed step: its :attr:`op` glyph, its structural
:class:`manstruct.Kind`, the :attr:`cells` it occupies, the registers it
:attr:`reads` / :attr:`writes` (from :mod:`mansem`), and its predecessor /
successor node ids. Branch glyphs (``X d a x``) and ``Y`` have several ``outs``
— conditional multi-successor edges — because their exit is state-dependent.

**Frozen register names.** Nodes name registers ``A``, ``B``, ``BP`` and the
man's heading ``HEAD`` — nothing else. ``reads`` / ``writes`` draw from
``mansem.REGISTERS`` (``A`` / ``B`` / ``BP``); a node that consumes or steers the
heading names it ``HEAD`` so a heading-coalesce pass can track it as a
first-class edge. This spelling is shared with :mod:`manrules`.

:func:`build_logic_graph` — the real walker (chase ``manstruct.CellInfo.exits``
entry-heading→exit-heading transits through ``manast`` nodes) — is **owned by
stream P7** and raises :class:`NotImplementedError` until it lands. This module
freezes only the node/graph types other streams type against.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field

from .manparse import Program
from .manrules import Cell
from .mansem import BPFacts, glyph_effect, run_effect
from .manstruct import DIRS, CellInfo, Kind, Structure, analyze_structure

__all__ = [
    "HEAD",
    "LogicNode",
    "InnerLogicGraph",
    "build_logic_graph",
    "build_all",
    "bp_provenance",
]

#: The heading pseudo-register name (not one of ``mansem.REGISTERS``): a node that
#: reads or sets the man's heading names it this, so heading dataflow is explicit.
HEAD = "HEAD"


@dataclass(frozen=True)
class LogicNode:
    """One executed step in a room's dataflow.

    :param id: node id, unique within its :class:`InnerLogicGraph`.
    :param op: the instruction glyph (``+``, ``M``, ``b``, ``s`` …).
    :param kind: its :class:`manstruct.Kind`.
    :param cells: the cell(s) this step occupies.
    :param reads: registers read — from ``mansem.REGISTERS`` (``A`` / ``B`` /
        ``BP``); include :data:`HEAD` if it consumes the heading.
    :param writes: registers written — same namespace, plus :data:`HEAD` if it
        steers.
    :param ins: predecessor node ids.
    :param outs: successor node ids — several for a branch / ``Y``, none for a
        halt or a terminal.
    """

    id: int
    op: str
    kind: Kind
    cells: frozenset[Cell]
    reads: frozenset[str] = field(default_factory=frozenset)
    writes: frozenset[str] = field(default_factory=frozenset)
    ins: list[int] = field(default_factory=list)
    outs: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class InnerLogicGraph:
    """The dataflow graph of one room.

    :param room_id: the room this graph models.
    :param nodes: node id → :class:`LogicNode`.
    :param entry: the id of the node the man reaches first.
    """

    room_id: int
    nodes: dict[int, LogicNode]
    entry: int


# ── the walk ─────────────────────────────────────────────────────────────────
#: Cell kinds the man *executes* — everything that becomes a node. Floor, nops,
#: the spawn marker and IO markers are pure latency the walk passes through; walls
#: and pipes are off-limits.
_INSTRUCTION_KINDS = frozenset({Kind.OP, Kind.LITERAL, Kind.STEER, Kind.BRANCH, Kind.HALT})

#: Steer glyph → forced exit heading (manstruct's ``_STEER_EXIT``, re-stated so we
#: do not reach into a private). ``V`` is a steer in SPEC but ``manstruct``
#: classifies it ``OP``; we follow the structural read, so it is not listed here.
_STEER_EXIT = {">": "E", "<": "W", "^": "N", "v": "S"}

#: 90° rotations of a heading (y grows down, so "left"/"right" are the two
#: perpendiculars — we enumerate both for a branch, since the glyph alone does not
#: say which way it turns).
_LEFT = {"E": "N", "N": "W", "W": "S", "S": "E"}
_RIGHT = {"E": "S", "S": "W", "W": "N", "N": "E"}

#: Glyphs that write ``BP`` by pure transform (decrement / halve). A provenance
#: walk treats them as transparent and keeps tracing back to the real definition.
_BP_TRANSFORM = frozenset("m] ")

_IO_KINDS = frozenset({"input", "output", "display"})


def _step(cell: Cell, heading: str) -> Cell:
    dx, dy = DIRS[heading]
    return (cell[0] + dx, cell[1] + dy)


def _enterable(info: CellInfo | None, room_id: int, heading: str) -> bool:
    """Can the man step into `info` heading `heading` and still be in this room?"""
    if info is None or info.room != room_id:
        return False
    if info.kind in (Kind.WALL, Kind.PIPE, Kind.VOID):
        return False
    return heading in info.exits


def _advance(
    cells: dict[Cell, CellInfo], room_id: int, start: Cell, heading: str
) -> tuple[Cell, str] | None:
    """Step off `start` heading `heading` and walk transparent latency to the next
    executed cell.

    Floor / nops / the spawn marker preserve the heading, so the sub-walk is
    deterministic: it ends at the first :data:`_INSTRUCTION_KINDS` cell (returned
    with the heading the man arrives on) or at a blocked cell / room exit
    (``None``). Steers and branches are themselves instruction cells, so they end
    the advance rather than being traversed here.
    """
    cur = start
    seen: set[Cell] = set()
    while True:
        nxt = _step(cur, heading)
        info = cells.get(nxt)
        if not _enterable(info, room_id, heading):
            return None
        assert info is not None
        if info.kind in _INSTRUCTION_KINDS:
            return (nxt, heading)
        if nxt in seen:  # a floor loop with no instruction: dead latency
            return None
        seen.add(nxt)
        cur = nxt


def _exit_headings(info: CellInfo, heading_in: str) -> list[str]:
    """Every heading the man may leave `info` with, having entered heading
    `heading_in`.

    Deterministic for steers and plain ops; multi-valued for the branch family,
    ``Y`` (split) and ``U`` (receive-and-turn), whose exit is state-dependent — so
    the walk emits several successor edges rather than collapsing to one.
    """
    g = info.glyph
    if info.kind is Kind.HALT:
        return []
    if g == "Y":  # split: two children, perpendicular to entry
        return [_LEFT[heading_in], _RIGHT[heading_in]]
    if g == "U":  # receive-and-turn: away from the chosen pipe — any heading
        return list(DIRS)
    if info.kind is Kind.BRANCH:  # X a d x: straight, or turn either way
        return [heading_in, _LEFT[heading_in], _RIGHT[heading_in]]
    if info.kind is Kind.STEER:
        return [_STEER_EXIT[g]]
    return [heading_in]  # op / literal: heading survives


def _find_entry(
    cells: dict[Cell, CellInfo], room: object, room_id: int
) -> tuple[Cell, str] | None:
    """Where the man first executes: from ``@`` heading east, else a heuristic."""
    spawn = getattr(room, "spawn", None)
    if spawn is not None:
        res = _advance(cells, room_id, spawn, "E")
        if res is not None:
            return res
    # No spawn (or it faces a wall): fall back to the first executed cell in
    # reading order, heading east. Conservative, never crashes.
    instrs = sorted(
        c
        for c, info in cells.items()
        if info.room == room_id and info.kind in _INSTRUCTION_KINDS
    )
    if not instrs:
        return None
    return (instrs[0], "E")


class _Union:
    """Tiny union-find over cells, for coalescing straight runs into one node."""

    def __init__(self) -> None:
        self.parent: dict[Cell, Cell] = {}

    def find(self, c: Cell) -> Cell:
        self.parent.setdefault(c, c)
        root = c
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[c] != root:
            self.parent[c], c = root, self.parent[c]
        return root

    def union(self, a: Cell, b: Cell) -> None:
        self.parent[self.find(a)] = self.find(b)


def _plain(info: CellInfo) -> bool:
    """A cell that may fuse into a straight run: an op/literal with a fixed,
    single exit (excludes steers, branches, halts and the turning ops ``U``/``Y``)."""
    return info.kind in (Kind.OP, Kind.LITERAL) and info.glyph not in ("U", "Y")


def _node_effect(
    glyphs: str, cells_ordered: list[Cell], cells: dict[Cell, CellInfo]
) -> tuple[frozenset[str], frozenset[str], Kind]:
    """``(reads, writes, kind)`` for one node.

    A steer/branch/split names :data:`HEAD` in ``writes`` (it sets the heading); a
    branch also names the register it tests in ``reads``; ``U`` writes ``A`` and
    :data:`HEAD`. A run of one or more plain glyphs uses
    :func:`mansem.run_effect` over its glyph string.
    """
    if len(cells_ordered) == 1:
        info = cells[cells_ordered[0]]
        g = info.glyph
        if info.kind is Kind.STEER:
            return frozenset(), frozenset({HEAD}), Kind.STEER
        if info.kind is Kind.BRANCH:
            return frozenset(glyph_effect(g).needs), frozenset({HEAD}), Kind.BRANCH
        if info.kind is Kind.HALT:
            return frozenset(), frozenset(), Kind.HALT
        if g == "Y":  # split
            return frozenset(), frozenset({HEAD}), info.kind
        if g == "U":  # receive and turn
            return frozenset(), frozenset({"A", HEAD}), info.kind
    needs, writes = run_effect(glyphs)
    kind = Kind.LITERAL if all(cells[c].kind is Kind.LITERAL for c in cells_ordered) else Kind.OP
    return needs, writes, kind


def _build_from_structure(structure: Structure, room_id: int) -> InnerLogicGraph:
    cells = structure.cells
    room = structure.program.rooms_by_id.get(room_id)
    if room is None or getattr(room, "kind", "compute") in _IO_KINDS:
        # IO / display rooms carry no executed logic: a minimal, empty graph.
        return InnerLogicGraph(room_id=room_id, nodes={}, entry=-1)

    entry = _find_entry(cells, room, room_id)
    if entry is None:
        return InnerLogicGraph(room_id=room_id, nodes={}, entry=-1)
    entry_cell, entry_heading = entry

    # ── cell-level walk: discover executed cells and their (multi-)successors ──
    succ: dict[Cell, set[Cell]] = {}
    pred: dict[Cell, set[Cell]] = {}
    queue: deque[tuple[Cell, str]] = deque([(entry_cell, entry_heading)])
    seen_states: set[tuple[Cell, str]] = {(entry_cell, entry_heading)}
    while queue:
        node_cell, h_in = queue.popleft()
        info = cells[node_cell]
        succ.setdefault(node_cell, set())
        for eh in _exit_headings(info, h_in):
            res = _advance(cells, room_id, node_cell, eh)
            if res is None:
                continue
            nxt_cell, nxt_heading = res
            succ[node_cell].add(nxt_cell)
            pred.setdefault(nxt_cell, set()).add(node_cell)
            state = (nxt_cell, nxt_heading)
            if state not in seen_states:
                seen_states.add(state)
                queue.append(state)

    instr_cells = set(succ) | set(pred) | {entry_cell}
    for c in instr_cells:
        succ.setdefault(c, set())
        pred.setdefault(c, set())

    # ── coalesce straight runs (a simple edge between two plain cells) ─────────
    uf = _Union()
    for c in instr_cells:
        uf.find(c)
    for u in instr_cells:
        for v in succ[u]:
            simple = succ[u] == {v} and pred[v] == {u}
            adjacent = abs(u[0] - v[0]) + abs(u[1] - v[1]) == 1
            if simple and adjacent and _plain(cells[u]) and _plain(cells[v]):
                uf.union(u, v)

    groups: dict[Cell, list[Cell]] = {}
    for c in instr_cells:
        groups.setdefault(uf.find(c), []).append(c)

    # deterministic node ids: order groups by their first cell in reading order
    def _min_cell(cs: list[Cell]) -> Cell:
        return min(cs, key=lambda c: (c[1], c[0]))

    ordered_roots = sorted(groups, key=lambda r: (_min_cell(groups[r])[1], _min_cell(groups[r])[0]))
    node_id_of_cell: dict[Cell, int] = {}
    for nid, root in enumerate(ordered_roots):
        for c in groups[root]:
            node_id_of_cell[c] = nid

    def _order_group(cs: list[Cell]) -> list[Cell]:
        """Walk order within a fused run (a simple internal chain)."""
        members = set(cs)
        heads = [c for c in cs if not (pred[c] & members)]
        start = heads[0] if heads else _min_cell(cs)
        out: list[Cell] = []
        cur: Cell | None = start
        while cur is not None and cur not in out:
            out.append(cur)
            nxts = [n for n in succ[cur] if n in members]
            cur = nxts[0] if nxts else None
        for c in cs:  # any stragglers (should not happen for a chain)
            if c not in out:
                out.append(c)
        return out

    nodes: dict[int, LogicNode] = {}
    for nid, root in enumerate(ordered_roots):
        cs = _order_group(groups[root])
        glyphs = "".join(cells[c].glyph for c in cs)
        reads, writes, kind = _node_effect(glyphs, cs, cells)
        ins: list[int] = []
        outs: list[int] = []
        for c in cs:
            for p in pred[c]:
                if p not in groups[root]:
                    ins.append(node_id_of_cell[p])
            for s in succ[c]:
                if s not in groups[root]:
                    outs.append(node_id_of_cell[s])
        nodes[nid] = LogicNode(
            id=nid,
            op=glyphs,
            kind=kind,
            cells=frozenset(cs),
            reads=reads,
            writes=writes,
            ins=sorted(dict.fromkeys(ins)),
            outs=sorted(dict.fromkeys(outs)),
        )

    return InnerLogicGraph(room_id=room_id, nodes=nodes, entry=node_id_of_cell[entry_cell])


def build_logic_graph(prog: object, room_id: int) -> InnerLogicGraph:
    """Walk room `room_id` of `prog` into an :class:`InnerLogicGraph`.

    Chases ``manstruct.CellInfo.exits`` transits from the man's entry (the ``@``
    spawn facing east, or a heuristic first-executed cell), labelling each executed
    glyph with its :func:`mansem.glyph_effect` and coalescing straight runs of
    plain ops into single :func:`mansem.run_effect` nodes. Branch / ``X`` / ``d`` /
    ``a`` / ``x`` / ``Y`` / ``U`` cells emit **several** successor edges (their exit
    is state-dependent); a counted loop's back-edge falls out of the walk as a
    cycle in the node graph.

    `prog` may be a :class:`manparse.Program`, an :class:`manstruct.Structure`, or a
    path / inline grid (parsed via :func:`manstruct.analyze_structure`; that route
    drives the reference engine and is slow — pass a ``Program`` for fast, pure
    use). IO / display rooms return a minimal empty graph (``entry == -1``).
    """
    if isinstance(prog, Structure):
        structure = prog
    elif isinstance(prog, (Program, str, os.PathLike)):
        structure = analyze_structure(prog)
    else:  # be liberal: anything analyze_structure accepts
        structure = analyze_structure(prog)  # type: ignore[arg-type]
    return _build_from_structure(structure, room_id)


def build_all(prog: object) -> dict[int, InnerLogicGraph]:
    """Every room's :class:`InnerLogicGraph`, keyed by room id.

    Parses (or reuses) the structure once, then walks each room — so a caller that
    wants the whole program pays the (possibly engine-backed) parse a single time.
    """
    if isinstance(prog, Structure):
        structure = prog
    else:
        structure = analyze_structure(prog)  # type: ignore[arg-type]
    return {r.id: _build_from_structure(structure, r.id) for r in structure.program.rooms}


# ── backpack provenance ──────────────────────────────────────────────────────
def _literal_before_b(glyphs: str) -> int | None:
    """The integer a ``… <literal> b`` prefix loads into ``BP``, or ``None``.

    Handles one bare digit or one backtick literal immediately before ``b`` — the
    same conservative shape :mod:`rules_loops` proves. Anything else yields
    ``None``.
    """
    b = glyphs.find("b")
    if b <= 0:
        return None
    prefix = glyphs[:b]
    if prefix.endswith("`"):
        inner = prefix[:-1]
        start = inner.rfind("`")
        if start < 0:
            return None
        digits = inner[start + 1 :]
        return int(digits) if digits.isdigit() else None
    last = prefix[-1]
    return int(last) if last.isdigit() else None


def _divisors(n: int) -> frozenset[int]:
    """Unroll-factor divisors of a positive literal count (``2..64``)."""
    if n <= 0:
        return frozenset()
    return frozenset(d for d in range(2, min(n, 64) + 1) if n % d == 0)


def bp_provenance(graph: InnerLogicGraph, node_id: int) -> BPFacts:
    """Classify the ``BP`` value entering node `node_id`, **conservatively**.

    Walks the dataflow backward over ``ins`` to the nearest real definition of
    ``BP``, skipping pure transforms (``m`` decrement, ``]`` halve, which keep the
    provenance of whatever they modify). It returns:

    * ``source="literal"`` with a ``const`` and its :func:`_divisors` when the count
      comes from a ``… <literal> b`` load (the only shape an unroll may trust);
    * ``source="pipe-q"`` when a ``q`` feeds it (count is a runtime pipe length —
      an unroll must refuse);
    * ``source="derived"`` for a ``b`` whose operand is not a literal;
    * :meth:`mansem.BPFacts.unknown` when nothing defines ``BP``, or when two
      backward paths disagree.

    A missing node id, or a node with no reaching definition, is ``unknown`` — never
    an error, so a recogniser can query freely.
    """
    if node_id not in graph.nodes:
        return BPFacts.unknown()

    results: list[BPFacts] = []
    visited: set[int] = {node_id}
    frontier: deque[int] = deque(graph.nodes[node_id].ins)
    while frontier:
        nid = frontier.popleft()
        if nid in visited or nid not in graph.nodes:
            continue
        visited.add(nid)
        node = graph.nodes[nid]
        if "BP" not in node.writes:
            frontier.extend(node.ins)
            continue
        g = node.op
        if set(g) <= _BP_TRANSFORM:  # pure BP transform: see through it
            frontier.extend(node.ins)
        elif "q" in g:
            results.append(BPFacts(const=None, divisible_by=frozenset(), source="pipe-q"))
        elif "b" in g:
            val = _literal_before_b(g)
            if val is not None:
                results.append(BPFacts(const=val, divisible_by=_divisors(val), source="literal"))
            else:
                results.append(BPFacts(const=None, divisible_by=frozenset(), source="derived"))
        else:
            results.append(BPFacts.unknown())
        # a real definition stops this backward path (do not trace past it)

    if not results:
        return BPFacts.unknown()
    first = results[0]
    if all(r == first for r in results):
        return first
    return BPFacts.unknown()
