from pathlib import Path

import pytest

from littleman_tools.runner import Littleman, LittlemanError

REPO = Path(__file__).parents[1]
PRIMITIVES = REPO / "tasks" / "solutions" / "primitives"
INPUT = [0, 0, 0, 1, 1, 0, 1, 1]
EXPECTED = [0, 0, 0, 1]


@pytest.mark.slow
def test_trace_matches_a_round_gated_judge_run() -> None:
    runner = Littleman()
    snapshots = runner.trace(
        PRIMITIVES / "transistor.man",
        input="0 0 / 1 1",
        expected="0 / 1",
        max_ticks=100,
    )
    settled = runner.judge(
        PRIMITIVES / "transistor.man",
        input="0 0 / 1 1",
        expected="0 / 1",
        max_ticks=100,
    )

    assert snapshots[-1].output == [0, 1]
    assert snapshots[-1].output_settled is True
    assert snapshots[-1] == settled


@pytest.mark.slow
def test_activity_profile_rejects_an_unsettled_trace() -> None:
    with pytest.raises(LittlemanError, match="before expected output settled"):
        Littleman().activity_profile(
            PRIMITIVES / "transistor.man",
            input=INPUT,
            expected=EXPECTED,
            max_ticks=1,
        )


@pytest.mark.slow
def test_activity_profile_separates_useful_work_from_walking() -> None:
    runner = Littleman()
    baseline = runner.activity_profile(
        PRIMITIVES / "transistor.man", input=INPUT, expected=EXPECTED, max_ticks=100
    )
    compact = runner.activity_profile(
        PRIMITIVES / "transistor-compact.man", input=INPUT, expected=EXPECTED, max_ticks=100
    )
    narrow = runner.activity_profile(
        PRIMITIVES / "transistor-compact-narrow.man",
        input=INPUT,
        expected=EXPECTED,
        max_ticks=100,
    )

    assert baseline.total_ticks == 76
    assert compact.total_ticks == 66
    assert narrow.total_ticks == 62
    assert compact.walking_ticks < baseline.walking_ticks
    assert narrow.walking_ticks < compact.walking_ticks
    assert narrow.straight_through_arrows == 0
    for profile in (baseline, compact, narrow):
        classified_ticks = (
            profile.compute_ticks
            + profile.control_ticks
            + profile.walking_ticks
            + profile.stall_ticks
        )
        assert classified_ticks == profile.runner_ticks
        assert profile.runner_ticks == profile.total_ticks
