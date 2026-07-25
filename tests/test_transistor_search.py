from pathlib import Path

import pytest

from littleman_tools.transistor_search import (
    CandidateMeasurement,
    CandidateResult,
    local_turn_variants,
    pareto_frontier,
)

REPO = Path(__file__).parents[1]
AND_GATE = REPO / "tasks" / "solutions" / "primitives" / "and-gate.man"


def _result(name: str, **metrics: int) -> CandidateResult:
    return CandidateResult(
        name=name,
        source="",
        measurement=CandidateMeasurement(
            side=metrics.get("side", 10),
            first_result_tick=metrics.get("first", 12),
            stream_ticks=metrics.get("stream", 62),
            walking_ticks=metrics.get("walking", 18),
            stall_ticks=metrics.get("stall", 0),
            straight_through_arrows=metrics.get("straight", 0),
        ),
    )


def test_local_turn_variants_change_one_nop_at_a_time() -> None:
    variants = tuple(local_turn_variants(".x."))

    assert variants[0].name == "seed"
    assert variants[0].source == ".x."
    assert {variant.source for variant in variants[1:]} == {
        ">x.",
        "<x.",
        "^x.",
        "vx.",
        ".x>",
        ".x<",
        ".x^",
        ".xv",
    }


def test_pareto_frontier_drops_dominated_and_duplicate_measurements() -> None:
    frontier = pareto_frontier(
        (
            _result("seed"),
            _result("duplicate"),
            _result("slower", stream=63),
            _result("smaller-but-slower", side=9, stream=70),
        )
    )

    assert [result.name for result in frontier] == ["seed", "smaller-but-slower"]


@pytest.mark.slow
def test_local_turn_search_keeps_only_the_seed_on_its_frontier() -> None:
    from littleman_tools.transistor_search import search_local_turns

    frontier = search_local_turns(AND_GATE.read_text(encoding="utf-8"))

    assert [result.name for result in frontier] == ["seed"]
    assert frontier[0].measurement.stream_ticks == 189
