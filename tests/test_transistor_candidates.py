from dataclasses import dataclass
from itertools import product
from pathlib import Path

import pytest

from littleman_tools.runner import Littleman

REPO = Path(__file__).parents[1]
PRIMITIVES = REPO / "tasks" / "solutions" / "primitives"
TRUTH_TABLE_INPUT = [0, 0, 0, 1, 1, 0, 1, 1]
TRUTH_TABLE_OUTPUT = [0, 0, 0, 1]


@dataclass(frozen=True)
class Candidate:
    name: str
    path: Path


@dataclass(frozen=True)
class Metrics:
    side: int
    first_result_tick: int
    four_result_ticks: int
    walking_ticks: int
    straight_through_arrows: int


CANDIDATES = (
    Candidate("baseline", PRIMITIVES / "transistor.man"),
    Candidate("compact", PRIMITIVES / "transistor-compact.man"),
    Candidate("compact-narrow", PRIMITIVES / "transistor-compact-narrow.man"),
)
AND_GATE = Candidate("and-gate", PRIMITIVES / "and-gate.man")
TEN_TICK_GATE = Candidate("and-gate-ten-tick", PRIMITIVES / "and-gate-ten-tick.man")


def test_transistor_candidates_are_y_free_artifacts() -> None:
    for candidate in (*CANDIDATES, AND_GATE, TEN_TICK_GATE):
        source = candidate.path.read_text(encoding="utf-8")
        assert "Y" not in source, candidate.name


def _measure(candidate: Candidate, runner: Littleman) -> Metrics:
    first_result_tick = max(
        runner.judge(
            candidate.path,
            input=[data, control],
            expected=[data if control else 0],
            max_ticks=100,
        ).step
        for data, control in product((0, 1), repeat=2)
    )
    profile = runner.activity_profile(
        candidate.path,
        input=TRUTH_TABLE_INPUT,
        expected=TRUTH_TABLE_OUTPUT,
        max_ticks=100,
    )
    source = candidate.path.read_text(encoding="utf-8").splitlines()
    return Metrics(
        side=max(len(source), *(len(row) for row in source)),
        first_result_tick=first_result_tick,
        four_result_ticks=profile.total_ticks,
        walking_ticks=profile.walking_ticks,
        straight_through_arrows=profile.straight_through_arrows,
    )


@pytest.mark.slow
def test_transistor_candidates_preserve_the_controlled_forwarding_contract() -> None:
    runner = Littleman()
    for candidate in CANDIDATES:
        snapshot = runner.judge(
            candidate.path,
            input=TRUTH_TABLE_INPUT,
            expected=TRUTH_TABLE_OUTPUT,
            max_ticks=100,
        )
        assert snapshot.output == TRUTH_TABLE_OUTPUT, candidate.name
        assert snapshot.output_settled is True, candidate.name


@pytest.mark.slow
def test_compact_transistors_improve_the_runner_movement_metrics() -> None:
    runner = Littleman()
    baseline, compact, narrow = (_measure(candidate, runner) for candidate in CANDIDATES)

    assert compact.side < baseline.side
    assert compact.first_result_tick < baseline.first_result_tick
    assert compact.four_result_ticks < baseline.four_result_ticks
    assert narrow.side < compact.side
    assert narrow.first_result_tick < compact.first_result_tick
    assert narrow.four_result_ticks < compact.four_result_ticks
    assert compact.walking_ticks < baseline.walking_ticks
    assert narrow.walking_ticks < compact.walking_ticks
    assert compact.straight_through_arrows < baseline.straight_through_arrows
    assert narrow.straight_through_arrows < compact.straight_through_arrows


@pytest.mark.slow
def test_and_gate_is_the_promoted_faster_binary_forwarding_candidate() -> None:
    runner = Littleman()
    narrow = _measure(CANDIDATES[-1], runner)
    and_gate = _measure(AND_GATE, runner)

    assert and_gate.side == 8
    assert and_gate.first_result_tick == 9
    assert and_gate.four_result_ticks == 33
    assert and_gate.four_result_ticks < narrow.four_result_ticks
    assert and_gate.walking_ticks < narrow.walking_ticks
    assert and_gate.straight_through_arrows == 1


@pytest.mark.slow
def test_terminal_bend_pipe_is_valid_and_u_gate_beats_ten_ticks_per_pair() -> None:
    runner = Littleman()
    and_gate = _measure(AND_GATE, runner)

    analysis = runner.analyze(AND_GATE.path)
    incoming_pipe = analysis.pipes[1]
    assert [segment.pos.as_tuple() for segment in incoming_pipe.path] == [(3, 3), (2, 3)]
    assert [segment.dir.as_tuple() for segment in incoming_pipe.path] == [(-1, 0), (0, 1)]
    assert [cell.as_tuple() for cell in runner.route(AND_GATE.path, 5, 5)] == [(3, 3), (2, 3)]

    snapshot = runner.judge(
        AND_GATE.path,
        input=TRUTH_TABLE_INPUT,
        expected=TRUTH_TABLE_OUTPUT,
        max_ticks=100,
    )

    assert snapshot.output == TRUTH_TABLE_OUTPUT
    assert snapshot.output_settled is True
    assert and_gate.side == 8
    assert and_gate.first_result_tick == 9
    assert and_gate.four_result_ticks == 33


@pytest.mark.slow
def test_ten_tick_gate_remains_as_a_baseline_candidate() -> None:
    ten_tick_gate = _measure(TEN_TICK_GATE, Littleman())

    assert ten_tick_gate.side == 8
    assert ten_tick_gate.first_result_tick == 10
    assert ten_tick_gate.four_result_ticks == 40
