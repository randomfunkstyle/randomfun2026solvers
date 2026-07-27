"""The flow graph is the contract: it must describe the walk exactly."""

from __future__ import annotations

from pathlib import Path

import pytest

from randomfun2026solvers.fast_littleman import EAST, SOUTH, WEST
from randomfun2026solvers.manflow import NodeKind, build_flow_graph

SOLUTIONS = Path(__file__).resolve().parents[1] / "tasks" / "solutions"

STRAIGHT = [
    "+------+",
    "|@ rs H|",
    "+------+",
]

# A man leaves '@' east, is turned south by 'v', west by '<', and reaches 'H'.
CORNER = [
    "+-----+",
    "|@   v|",
    "|     |",
    "|H   <|",
    "+-----+",
]

BRANCH = [
    "+------+",
    "|@ 1X H|",
    "|    v |",
    "|    H |",
    "+------+",
]


def test_corridor_glyphs_are_edges_not_nodes() -> None:
    graph = build_flow_graph(STRAIGHT)

    glyphs = [node.glyph for node in graph.nodes]
    assert glyphs == ["@", "r", "s", "H"]
    # The single space between '@' and 'r' is corridor, so that edge costs a tick.
    first = graph.edges[graph.nodes[0].out_edges[0]]
    assert first.length == 1


def test_arrows_are_corridor_and_carry_the_turn() -> None:
    graph = build_flow_graph(CORNER)

    assert [node.glyph for node in graph.nodes] == ["@", "H"]
    edge = graph.edges[0]
    # Three cells east, the 'v', two down, the '<', three west: all corridor.
    assert edge.length == len(edge.cells) > 0
    assert edge.exit_dir == EAST
    assert edge.entry_dir == WEST


def test_every_arm_of_a_conditional_turn_is_enumerated() -> None:
    graph = build_flow_graph(BRANCH)

    turn = next(node for node in graph.nodes if node.glyph == "X")
    assert turn.kind is NodeKind.BRANCH
    arms = {graph.edges[eid].arm for eid in turn.out_edges}
    # sign(A) can send him left, straight or right, so all three are in the graph
    # whether or not the program ever takes them.
    assert arms == {-1, 0, +1}


def test_arms_leave_in_the_direction_the_turn_implies() -> None:
    graph = build_flow_graph(BRANCH)

    turn = next(node for node in graph.nodes if node.glyph == "X")
    by_arm = {graph.edges[eid].arm: graph.edges[eid] for eid in turn.out_edges}
    assert by_arm[0].exit_dir == EAST
    assert by_arm[+1].exit_dir == SOUTH


def test_walking_off_the_room_is_recorded_not_raised() -> None:
    graph = build_flow_graph(BRANCH)

    dead = [edge for edge in graph.edges if edge.dst is None]
    assert dead and all(edge.dead == "wall" for edge in dead)


@pytest.mark.parametrize(
    "name",
    [
        "reverse-a-list_split.man",
        "brackets_stack.man",
        "sort-numbers_ring.man",
        "triangle_cpu.man",
    ],
)
def test_real_solutions_parse_into_a_connected_graph(name) -> None:
    graph = build_flow_graph(SOLUTIONS / name)

    assert graph.nodes and graph.edges and graph.starts
    # Every node the walk found is reachable, so nothing is orphaned by parsing.
    reachable = set(graph.starts)
    for edge in graph.edges:
        if edge.dst is not None:
            reachable.add(edge.dst)
    assert reachable == {node.id for node in graph.nodes}
