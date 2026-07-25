"""Decision-layer tests for deterministic AST optimisation audits."""

from __future__ import annotations

from pathlib import Path

from randomfun2026solvers.manaudit import (
    SubmissionPoint,
    archive_frontier,
    audit_grid,
    classify_transition,
)

REPO = Path(__file__).parents[1]


def _point(
    name: str,
    *,
    score: int,
    factor: int,
    rooms: int = 4,
    pipes: int = 4,
    live: int = 100,
) -> SubmissionPoint:
    return SubmissionPoint(
        path=name,
        server_score=score,
        width=int(factor**0.5),
        height=int(factor**0.5),
        factor=factor,
        implied_ticks=score / factor,
        rooms=rooms,
        pipes=pipes,
        live_cells=live,
    )


def test_transition_separates_footprint_and_tick_improvements() -> None:
    before = _point("before", score=200_000, factor=2_000, live=120)
    after = _point("after", score=90_000, factor=1_000, live=100)

    transition = classify_transition(before, after)

    assert transition.factor_ratio == 0.5
    assert transition.tick_ratio == 0.9
    assert transition.rules == (
        "footprint-compaction",
        "execution-shortening",
        "code-density",
    )


def test_transition_names_a_speed_for_space_topology_rewrite() -> None:
    before = _point("before", score=100_000, factor=1_000)
    after = _point("after", score=80_000, factor=1_200, rooms=5, pipes=6)

    transition = classify_transition(before, after)

    assert "topology-rewrite" in transition.rules
    assert "speed-for-space-trade" in transition.rules
    assert "footprint-compaction" not in transition.rules


def test_memory_archive_is_a_worst_to_best_score_frontier() -> None:
    points = archive_frontier("memory", REPO / "solutions")

    assert len(points) == 8
    assert [point.server_score for point in points] == sorted(
        (point.server_score for point in points),
        reverse=True,
    )
    assert points[-1].server_score == 55_105_622


def test_current_memory_solution_round_trips_and_gets_structural_actions() -> None:
    path = REPO / "solutions" / "memory" / "000000055105622_memory.man"
    audit = audit_grid(path, slug="memory", history_root=REPO / "solutions")

    assert audit.ast_round_trip
    assert audit.history
    assert audit.transitions
    assert audit.recommendations
    assert all("memory" not in rec.evidence.lower() for rec in audit.recommendations)
    squash = next(rec for rec in audit.recommendations if rec.rule == "loop-squash")
    assert "--moves squash" in squash.command
    assert "--problem memory" in squash.command
