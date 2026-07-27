"""The AST-authored two-tape packet sender and queue scanner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers import tcp_queue_ast
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.man_debug import render_html
from randomfun2026solvers.manast import PipeNode, RoomNode

ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "tasks" / "solutions" / "tcp_queue_ast.man"
DEBUG_HTML = ROOT / "littleman" / "examples" / "tcp-queue-ast.html"
DEBUG_JSON = ROOT / "littleman" / "examples" / "tcp-queue-ast.json"
PROBLEM = ROOT / "tasks" / "problems" / "tcp.json"


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def test_semantic_model_matches_every_public_round() -> None:
    for case in public_cases():
        rounds = [[int(value) for value in round_["in"]] for round_ in case["rounds"]]
        expected = [[int(value) for value in round_["out"]] for round_ in case["rounds"]]
        assert tcp_queue_ast.simulate(rounds) == expected, case["name"]


def test_a_full_miss_does_not_advance_the_missing_index() -> None:
    """The correction to the proposed algorithm is observable on the next round."""
    rounds = [[4, 2, 30], [3, 40], [0, 10], [1, 20]]
    assert tcp_queue_ast.simulate(rounds) == [[], [], [10], [20, 30, 40]]


def test_max_delay_is_checked_before_queueing() -> None:
    assert tcp_queue_ast.simulate([[20, 1, 301], [16, 316]]) == [[], [-1]]


def test_the_grid_is_rendered_from_authored_ast_nodes() -> None:
    ast = tcp_queue_ast.build_ast()
    assert len(ast.rooms) == 6
    assert len(ast.pipes) == 7
    assert not ast.strays
    assert all(isinstance(room, RoomNode) for room in ast.rooms)
    assert all(isinstance(pipe, PipeNode) for pipe in ast.pipes)
    assert tcp_queue_ast.build() == SOLUTION.read_text().rstrip("\n").splitlines()


def test_debug_sidecars_match_the_same_generator_state() -> None:
    rows = tcp_queue_ast.build()
    dbg = tcp_queue_ast.debug_map()
    assert json.loads(DEBUG_JSON.read_text()) == dbg.to_dict()
    assert DEBUG_HTML.read_text() == render_html(rows, dbg)


def test_each_fifo_loop_can_hold_every_packet_plus_its_fence() -> None:
    ast = tcp_queue_ast.build_ast()
    pipes = {pipe.id: pipe for pipe in ast.pipes}
    required = tcp_queue_ast.MAX_QUEUED + 1
    assert pipes[3].capacity + pipes[4].capacity >= required
    assert pipes[5].capacity + pipes[6].capacity >= required


def test_scanner_uses_fifo_fences_instead_of_timing_padding() -> None:
    worker = tcp_queue_ast._worker()
    joined = "\n".join(worker.rows())
    assert "q" not in joined
    assert "`16`" not in joined
    assert (worker.get(30, 12), worker.get(29, 12), worker.get(28, 12)) == (
        "1",
        "N",
        "s",
    )


def test_every_worker_pipe_op_binds_to_its_declared_ast_pipe() -> None:
    """Wrong nearest-pipe binding can move plausible data with no load error."""
    ast = tcp_queue_ast.build_ast()
    paths = {tuple(pipe.path): pipe.id for pipe in ast.pipes}
    expected = {
        tcp_queue_ast._debug_point(*cell): pipe_id
        for cell, pipe_id in {
            (5, 44): 1,  # packet seq
            (14, 44): 1,  # direct packet value
            (15, 44): 2,  # direct output
            (29, 46): 3,  # stored index
            (11, 47): 2,  # -1 output
            (5, 48): 1,  # stored packet value
            (51, 50): 5,  # stored value
            (51, 51): 5,  # value-side fence
            (29, 53): 3,  # index-side fence
            (56, 58): 6,  # fence dummy
            (35, 59): 4,  # scanned index
            (56, 59): 6,  # matched value
            (29, 61): 3,  # mismatched index rotates
            (15, 62): 2,  # queued match output
            (56, 64): 6,  # mismatched value
            (51, 66): 5,  # mismatched value rotates
        }.items()
    }
    lm = Littleman()
    for cell, pipe_id in expected.items():
        route = tuple(point.as_tuple() for point in lm.route(SOLUTION, *cell))
        assert paths[route] == pipe_id, cell


def test_shortest_stream_runs_on_the_real_engine() -> None:
    snap = Littleman().judge(
        SOLUTION.read_text(),
        input="1 0 42",
        expected="42",
        max_ticks=1_000,
    )
    assert snap.fatal is None
    assert list(snap.output) == [42]


@pytest.mark.slow
@pytest.mark.parametrize("case", public_cases(), ids=lambda case: case["name"])
def test_public_cases_on_the_real_engine(case: dict) -> None:
    snap = Littleman().judge(
        SOLUTION.read_text(),
        input=" / ".join(" ".join(round_["in"]) for round_ in case["rounds"]),
        expected=" / ".join(" ".join(round_["out"]) for round_ in case["rounds"]),
        max_ticks=100_000,
    )
    assert snap.fatal is None
    assert list(snap.output) == [int(value) for round_ in case["rounds"] for value in round_["out"]]


@pytest.mark.slow
def test_a_rotated_mismatch_can_become_needed_in_the_same_round() -> None:
    """The new fence pass must wait for packet 3 after packet 2 unlocks it."""
    snap = Littleman().judge(
        SOLUTION.read_text(),
        input="4 3 40 / 2 30 / 0 10 / 1 20",
        expected=" /  / 10 / 20 30 40",
        max_ticks=20_000,
    )
    assert snap.fatal is None
    assert list(snap.output) == [10, 20, 30, 40]
