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


def test_transistor_candidates_are_y_free_artifacts() -> None:
    for candidate in CANDIDATES:
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
def test_compact_candidates_improve_the_runner_movement_metrics() -> None:
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
