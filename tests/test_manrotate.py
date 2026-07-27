"""Turning a node may change which way a man faces, never what he does."""

from __future__ import annotations

from pathlib import Path

import pytest

from randomfun2026solvers.fast_littleman import EAST, NORTH, SOUTH, WEST, FastLittleman
from randomfun2026solvers.manflow import (
    NodeKind,
    build_flow_graph,
    canonical_signature,
    exits_for,
)
from randomfun2026solvers.manprofile import profile_program
from randomfun2026solvers.manrotate import (
    apply_moves,
    apply_rotation,
    initial_rotation,
    propose_rotations,
    rotatable_nodes,
)

ROOT = Path(__file__).resolve().parents[1]
SOLUTIONS = ROOT / "tasks" / "solutions"

BRANCH = [
    "+------+",
    "|@ 1X H|",
    "|    v |",
    "|    H |",
    "+------+",
]

LITERAL = [
    "+----------+",
    "|@ `240` H |",
    "+----------+",
]

GRIDS = [
    "reverse-a-list_split.man",
    pytest.param("brackets_stack.man", marks=pytest.mark.slow),
    pytest.param("sort-numbers_ring.man", marks=pytest.mark.slow),
]


def test_a_literal_may_not_be_turned() -> None:
    """Walked backwards a literal is a different number, so it stays put."""
    graph = build_flow_graph(LITERAL)
    free = rotatable_nodes(graph)

    literals = [n.id for n in graph.nodes if n.literal_run]
    assert literals
    assert not (set(literals) & free)


def test_a_man_may_not_be_turned_off_his_starting_heading() -> None:
    graph = build_flow_graph(BRANCH)
    starts = {n.id for n in graph.nodes if n.kind is NodeKind.START}

    assert starts and not (starts & rotatable_nodes(graph))


def test_arms_keep_their_labels_and_move_round_the_compass() -> None:
    """The whole rotation argument in one assertion: arms are relative."""
    graph = build_flow_graph(BRANCH)
    turn = next(node for node in graph.nodes if node.glyph == "X")

    facing_east = {arm.turn: arm.direction for arm in exits_for(graph.program, turn, EAST)}
    facing_south = {arm.turn: arm.direction for arm in exits_for(graph.program, turn, SOUTH)}

    assert facing_east.keys() == facing_south.keys() == {-1, 0, +1}
    assert facing_east[0] == EAST and facing_south[0] == SOUTH
    assert facing_east[+1] == SOUTH and facing_south[+1] == WEST
    assert facing_east[-1] == NORTH and facing_south[-1] == EAST


def test_identity_rotation_is_the_graph_itself() -> None:
    graph = build_flow_graph(SOLUTIONS / "reverse-a-list_split.man")

    spun = apply_rotation(graph, initial_rotation(graph))

    assert spun is not None
    assert canonical_signature(spun) == canonical_signature(graph)


def test_a_rotation_that_would_merge_two_nodes_is_refused() -> None:
    """Two nodes can share a cell; turning one onto the other is not a reroute."""
    graph = build_flow_graph(SOLUTIONS / "reverse-a-list_split.man")
    shared = [ids for ids in graph.node_cells.values() if len(ids) > 1]
    if not shared:
        pytest.skip("this grid has no cell walked from two directions")

    first, second = shared[0][0], shared[0][1]
    rotation = initial_rotation(graph)
    rotation[first] = rotation[second]

    assert apply_rotation(graph, rotation) is None


@pytest.mark.parametrize("name", GRIDS)
def test_proposed_turns_keep_the_program(name) -> None:
    path = SOLUTIONS / name
    graph = build_flow_graph(path)
    slug = name.split("_")[0]
    profile = profile_program(path, slug, graph=graph)

    moves, _, _ = propose_rotations(graph, profile)
    grid = apply_moves(graph, moves)

    after = build_flow_graph(FastLittleman(grid))
    assert canonical_signature(after) == canonical_signature(graph)


def test_no_turns_means_the_grid_is_untouched() -> None:
    path = SOLUTIONS / "reverse-a-list_split.man"
    graph = build_flow_graph(path)

    original = [row.rstrip() for row in path.read_text().rstrip("\n").split("\n")]
    assert apply_moves(graph, []) == original


def test_every_proposed_turn_pays() -> None:
    path = SOLUTIONS / "snake_reroute.man"
    graph = build_flow_graph(path)
    profile = profile_program(path, "snake", graph=graph, tick_cap=15_000_000)

    moves, _, _ = propose_rotations(graph, profile)

    assert all(move.saved_ticks > 0 for move in moves)
    # And the proposal is reproducible: same grid and profile, same turns.
    again, _, _ = propose_rotations(graph, profile)
    assert [(m.node_id, m.direction) for m in moves] == [
        (m.node_id, m.direction) for m in again
    ]


def test_canonical_signature_catches_a_changed_instruction() -> None:
    graph = build_flow_graph(BRANCH)
    other = build_flow_graph(
        [
            "+------+",
            "|@ 2X H|",  # the '1' the man loads is now a '2'
            "|    v |",
            "|    H |",
            "+------+",
        ]
    )

    assert canonical_signature(graph) != canonical_signature(other)


def test_canonical_signature_ignores_cells_no_man_reaches() -> None:
    """Only what runs is the program; unreachable glyphs are not part of it.

    In ``BRANCH`` the ``v``/``H`` pair below the turn is never walked — both of
    ``X``'s other arms hit a wall — so editing them cannot change the meaning,
    and a gate that claimed otherwise would block harmless reroutes.
    """
    graph = build_flow_graph(BRANCH)
    dead_edited = build_flow_graph(
        [
            "+------+",
            "|@ 1X H|",
            "|    v |",
            "|    s |",
            "+------+",
        ]
    )

    assert canonical_signature(graph) == canonical_signature(dead_edited)
