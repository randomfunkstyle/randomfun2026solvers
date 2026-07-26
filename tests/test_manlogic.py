#!/usr/bin/env python3
"""Fast, pure tests for the inner-logic-graph builder (stream P7).

Every fixture is a hand-built single-room :class:`~randomfun2026solvers.manparse.Program`
so the walker runs without the reference engine — :func:`manstruct.analyze_structure`
does no engine work when handed a ``Program``. We assert node ops, register
reads/writes (from ``mansem``), that branches keep several successors, that a
counted loop closes a back-edge, and that ``bp_provenance`` proves a literal count
and refuses a ``q``-sourced one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from randomfun2026solvers.manlogic import (
    InnerLogicGraph,
    bp_provenance,
    build_all,
    build_logic_graph,
)
from randomfun2026solvers.manparse import Program, Room
from randomfun2026solvers.manstruct import Kind, analyze_structure


# ── fixtures ─────────────────────────────────────────────────────────────────
def room_program(interior: list[str], *, kind: str = "compute") -> Program:
    """A one-room program from interior ASCII, wrapped in a wall box.

    ``@`` marks the man's spawn. No engine is consulted: the ``Program`` is built
    directly so the whole test stays in-process.
    """
    w = max(len(r) for r in interior)
    inner = [r.ljust(w) for r in interior]
    top = "+" + "-" * w + "+"
    rows = [top] + ["|" + r + "|" for r in inner] + [top]
    spawn = None
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "@":
                spawn = (x, y)
    width, height = w + 2, len(rows)
    room = Room(
        id=0,
        min=(0, 0),
        max=(width - 1, height - 1),
        content=rows,
        kind=kind,
        spawn=spawn,
    )
    return Program(width=width, height=height, rooms=[room], pipes=[])


def _node(graph: InnerLogicGraph, *, op: str | None = None, kind: Kind | None = None):
    for n in graph.nodes.values():
        if (op is None or n.op == op) and (kind is None or n.kind is kind):
            return n
    raise AssertionError(f"no node op={op!r} kind={kind}")


def _has_cycle(graph: InnerLogicGraph) -> bool:
    color: dict[int, int] = {}

    def visit(nid: int) -> bool:
        color[nid] = 1  # grey
        for s in graph.nodes[nid].outs:
            if color.get(s, 0) == 1:
                return True
            if color.get(s, 0) == 0 and visit(s):
                return True
        color[nid] = 2  # black
        return False

    return any(visit(nid) for nid in graph.nodes if color.get(nid, 0) == 0)


# ── straight run: a single arithmetic node with mansem reads/writes ──────────
def test_plus_node_reads_ab_writes_a() -> None:
    g = build_logic_graph(room_program(["@+H"]), 0)
    plus = _node(g, op="+")
    assert plus.reads == frozenset({"A", "B"})
    assert plus.writes == frozenset({"A"})
    assert g.entry == plus.id  # the man reaches `+` first
    # `+` flows into the halt; the halt terminates.
    assert _node(g, op="H").kind is Kind.HALT
    assert plus.outs == [_node(g, op="H").id]
    assert not _has_cycle(g)


def test_mw_chain_is_one_run_composed_by_run_effect() -> None:
    # M (B=A) then W (swap) are a straight run: one coalesced node whose reads /
    # writes are run_effect("MW") = needs {A}, writes {A, B}.
    g = build_logic_graph(room_program(["@MWH"]), 0)
    run = _node(g, op="MW")
    assert run.kind is Kind.OP
    assert run.reads == frozenset({"A"})
    assert run.writes == frozenset({"A", "B"})
    assert run.cells == frozenset({(2, 1), (3, 1)})


# ── branch: two conditional successors, never collapsed ──────────────────────
def test_branch_has_two_successors() -> None:
    g = build_logic_graph(room_program(["@XH", " H"]), 0)
    x = _node(g, kind=Kind.BRANCH)
    assert x.op == "X"
    assert x.reads == frozenset({"A"})  # X tests sign(A)
    assert x.writes == frozenset({"HEAD"})  # and steers on it
    assert len(x.outs) >= 2  # straight and turned exits both modelled


# ── counted loop: a branch, a back-edge, and a literal-provable BP ───────────
LOOP_6B = ["@6b>d  H", "   mr", "    s", "   ^<"]
LOOP_Q = ["@q>d  H", "  mr", "   s", "  ^<"]


def test_counted_loop_backedge_and_branch() -> None:
    g = build_logic_graph(room_program(LOOP_6B), 0)
    d = _node(g, op="d")
    assert d.kind is Kind.BRANCH
    assert d.reads == frozenset({"BP"})
    assert len(d.outs) >= 2  # exit (BP==0) and body (BP>0)
    # The `rs` body coalesced into one run: receive-then-send writes A.
    body = _node(g, op="rs")
    assert body.writes == frozenset({"A"})
    # The loop closes: the head steer is re-entered from the decrement `m`.
    head = _node(g, op=">")
    assert len(head.ins) == 2
    assert _has_cycle(g)


def test_bp_provenance_literal_even() -> None:
    g = build_logic_graph(room_program(LOOP_6B), 0)
    facts = bp_provenance(g, _node(g, op="d").id)
    assert facts.source == "literal"
    assert facts.const == 6
    assert 2 in facts.divisible_by  # even → unroll-by-2 is provable
    assert 3 in facts.divisible_by


def test_bp_provenance_pipe_q_is_not_provable() -> None:
    g = build_logic_graph(room_program(LOOP_Q), 0)
    facts = bp_provenance(g, _node(g, op="d").id)
    assert facts.source == "pipe-q"
    assert facts.const is None
    assert not facts.divisible_by  # a runtime pipe length: unrolling must refuse


def test_bp_provenance_unknown_when_no_definition() -> None:
    g = build_logic_graph(room_program(["@+H"]), 0)
    facts = bp_provenance(g, _node(g, op="+").id)
    assert facts.source == "unknown"
    assert facts.const is None


# ── IO / display rooms: a minimal, crash-free graph ──────────────────────────
def test_display_room_is_empty_graph() -> None:
    g = build_logic_graph(room_program(["      "], kind="display"), 0)
    assert g.nodes == {}
    assert g.entry == -1


def test_input_room_is_empty_graph() -> None:
    g = build_logic_graph(room_program([" I "], kind="input"), 0)
    assert g.nodes == {}
    assert g.entry == -1


# ── build_all covers every room ──────────────────────────────────────────────
def test_build_all_returns_a_graph_per_room() -> None:
    graphs = build_all(room_program(LOOP_6B))
    assert set(graphs) == {0}
    assert isinstance(graphs[0], InnerLogicGraph)
    assert graphs[0].nodes  # the compute room has executed logic


# ── engine-backed smoke: build over a real checked-in solution ───────────────
@pytest.mark.slow
def test_build_all_on_real_solution_does_not_crash() -> None:
    grid = Path(__file__).resolve().parents[1] / "tasks" / "solutions" / "brackets_cpu.man"
    if not grid.exists():
        pytest.skip("brackets_cpu.man not present")
    structure = analyze_structure(grid)  # drives the reference engine (hence slow)
    graphs = build_all(structure)
    assert set(graphs) == {r.id for r in structure.program.rooms}
    # At least one compute room has a walked graph, and every edge is well-formed.
    assert any(g.nodes for g in graphs.values())
    for g in graphs.values():
        for nid, node in g.nodes.items():
            assert node.id == nid
            for s in node.outs:
                assert s in g.nodes
            for p in node.ins:
                assert p in g.nodes
