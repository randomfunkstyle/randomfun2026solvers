"""A reroute may move a man's feet; it may never change what he does."""

from __future__ import annotations

from pathlib import Path

import pytest

from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.manflow import build_flow_graph
from randomfun2026solvers.manprofile import Profile, profile_program
from randomfun2026solvers.manreroute import Layout, graph_signature, reroute, route_edge

ROOT = Path(__file__).resolve().parents[1]
SOLUTIONS = ROOT / "tasks" / "solutions"

# The man is walked all the way round the room to reach 'H' from the east, when
# cutting the corner reaches it the same way in a third of the ticks.
DETOUR = [
    "+---------+",
    "|@       v|",
    "|         |",
    "|  H<<<<<<|",
    "+---------+",
]

GRIDS = [
    "reverse-a-list_split.man",
    "snake_panel_probe.man",
    pytest.param("brackets_stack.man", marks=pytest.mark.slow),
    pytest.param("sort-numbers_ring.man", marks=pytest.mark.slow),
    pytest.param("triangle_cpu.man", marks=pytest.mark.slow),
]


@pytest.mark.parametrize("name", GRIDS)
def test_laying_the_existing_corridors_reproduces_the_grid(name) -> None:
    """The layout model must be lossless, or a reroute is editing blind."""
    path = SOLUTIONS / name
    graph = build_flow_graph(path)

    original = [row.rstrip() for row in path.read_text().rstrip("\n").split("\n")]
    assert Layout(graph).render() == original


def _empty_profile(graph) -> Profile:
    """Traffic of 1 on every edge: enough to rank, no measurement needed."""
    from randomfun2026solvers.manprofile import EdgeCost

    edges = {}
    for edge in graph.edges:
        cost = EdgeCost()
        cost.add(1, edge.length)
        edges[edge.id] = cost
    return Profile(graph=graph, edges=edges, nodes={})


def test_a_detour_is_shortened() -> None:
    graph = build_flow_graph(DETOUR)
    before = sum(edge.length for edge in graph.edges)

    result = reroute(DETOUR, _empty_profile(graph), graph=graph)

    assert result.cells_after < before
    assert result.moves


@pytest.mark.parametrize("name", GRIDS)
def test_rerouting_preserves_the_flow_graph(name) -> None:
    path = SOLUTIONS / name
    graph = build_flow_graph(path)

    result = reroute(path, _empty_profile(graph), graph=graph)

    after = build_flow_graph(FastLittleman(result.grid))
    assert graph_signature(after) == graph_signature(graph)


@pytest.mark.parametrize("name", GRIDS)
def test_rerouting_never_adds_corridor(name) -> None:
    path = SOLUTIONS / name
    graph = build_flow_graph(path)

    result = reroute(path, _empty_profile(graph), graph=graph)

    assert result.cells_after <= result.cells_before
    # A global rip-up repacks corridors as well as shortening them, so a move
    # may be the same length in a different place — but never a longer one.
    assert all(move.saved_cells >= 0 for move in result.moves)


def test_a_corridor_is_never_routed_over_an_instruction() -> None:
    graph = build_flow_graph(DETOUR)
    result = reroute(DETOUR, _empty_profile(graph), graph=graph)

    pinned = {node.pos for node in graph.nodes}
    for cells in result.layout.paths.values():
        assert not (set(cells) & pinned)


def test_routing_is_deterministic() -> None:
    path = SOLUTIONS / "brackets_stack.man"
    graph = build_flow_graph(path)

    first = reroute(path, _empty_profile(graph), graph=graph)
    second = reroute(path, _empty_profile(graph), graph=graph)

    assert first.grid == second.grid


def test_route_edge_respects_its_length_limit() -> None:
    graph = build_flow_graph(DETOUR)
    layout = Layout(graph)
    edge = graph.edges[0]
    layout.lift(edge.id)

    assert route_edge(layout, edge, limit=0) is None
    found = route_edge(layout, edge, limit=edge.length)
    assert found is not None and len(found) <= edge.length


def test_crossings_share_a_cell_but_turns_do_not() -> None:
    """The one 2D rule the router runs on, stated as a test."""
    from randomfun2026solvers.fast_littleman import EAST, NORTH, SOUTH
    from randomfun2026solvers.manreroute import CellUse

    use = CellUse()
    use.add(EAST, EAST, 0)
    # Another corridor may cross going south — a space changes nobody's mind.
    assert use.accepts(SOUTH, SOUTH)
    use.add(SOUTH, SOUTH, 1)
    # But nobody may turn here now: an arrow would re-aim both crossers.
    assert not use.accepts(EAST, NORTH)
