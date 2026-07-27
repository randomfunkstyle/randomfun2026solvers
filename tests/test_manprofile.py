"""The profiler's numbers are only worth having if they are the engine's."""

from __future__ import annotations

from pathlib import Path

import pytest

from randomfun2026solvers import scoring
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.manflow import build_flow_graph
from randomfun2026solvers.manprofile import profile_program, trace_case
from randomfun2026solvers.optimize import _expected_frames, verify

ROOT = Path(__file__).resolve().parents[1]
SOLUTIONS = ROOT / "tasks" / "solutions"

CASES = [
    ("reverse-a-list_split.man", "reverse-a-list", 5_000_000),
    pytest.param(
        "brackets_stack.man", "brackets", 5_000_000, marks=pytest.mark.slow
    ),
]


@pytest.mark.parametrize("name,slug,cap", CASES)
def test_traced_ticks_match_the_native_engine(name, slug, cap) -> None:
    """The tracing engine is a second implementation; it must agree exactly."""
    path = SOLUTIONS / name
    profile = profile_program(path, slug, tick_cap=cap)
    native = verify(path, slug, tick_cap=cap)

    traced = {name_: ticks for name_, ticks, _ in profile.cases}
    for case in native.cases:
        assert traced[case.name] == case.ticks


@pytest.mark.parametrize("name,slug,cap", CASES)
def test_every_tick_lands_on_the_graph(name, slug, cap) -> None:
    """No mismatches means the graph explains the walk cell for cell."""
    profile = profile_program(SOLUTIONS / name, slug, tick_cap=cap)

    assert not profile.mismatches
    assert profile.corridor_ticks > 0
    assert profile.total_ticks == sum(man.total for man in profile.men.values())


def test_traffic_prices_a_corridor_in_ticks() -> None:
    path = SOLUTIONS / "reverse-a-list_split.man"
    graph = build_flow_graph(path)
    profile = profile_program(path, "reverse-a-list", graph=graph)

    for edge_id, cost in profile.edges.items():
        assert cost.ticks == cost.traffic * graph.edges[edge_id].length


def test_hot_edges_are_ordered_and_deterministic() -> None:
    path = SOLUTIONS / "reverse-a-list_split.man"
    profile = profile_program(path, "reverse-a-list")

    ranked = profile.hot_edges()
    assert [cost.ticks for _, cost in ranked] == sorted(
        (cost.ticks for _, cost in ranked), reverse=True
    )
    assert ranked == profile_program(path, "reverse-a-list").hot_edges()


def test_a_blocked_man_is_counted_as_idle_not_walking() -> None:
    profile = profile_program(SOLUTIONS / "reverse-a-list_split.man", "reverse-a-list")

    assert profile.men
    for man in profile.men.values():
        assert 0 <= man.blocked <= man.total
        assert man.busy == man.total - man.blocked
    # The pacing man is the one who idles least, which is what the router aims at.
    lead = profile.bottleneck_men()[0]
    assert all(profile.men[lead].busy >= profile.men[m].busy for m in profile.men)


def test_frame_gated_problems_trace_to_the_same_tick_as_the_engine() -> None:
    """Frame gating lives only in the native backend; the tracer adds its own.

    Display problems are where most of the corridor is, so getting this wrong
    would silently mis-price the biggest grids.  Run the shortest snake case
    both ways and require the same tick.
    """
    path = SOLUTIONS / "snake_reroute.man"
    prog = FastLittleman(path)
    problem = scoring.load_problem("snake")
    case = min(
        problem["publicTestData"],
        key=lambda c: sum(len(r.get("in") or []) for r in scoring._rounds(c)),
    )

    result, trace = trace_case(
        prog,
        input=scoring._case_input(case),
        frames=_expected_frames(case),
        max_ticks=15_000_000,
    )
    native = prog.run(
        scoring._case_input(case),
        frames=_expected_frames(case),
        max_ticks=15_000_000,
    )

    assert native.passed
    assert result.step == native.step
    assert trace


def test_forked_children_are_profiled_not_dropped() -> None:
    """A ``Y`` child starts mid-corridor, not on an instruction.

    ``matmul_hand`` forks into hundreds of men.  Before the resume index they
    all failed to attach to the graph and the grid could not be profiled at all,
    which silently excluded our best-headroom problem from the pipeline.
    """
    path = SOLUTIONS / "matmul_hand.man"
    profile = profile_program(path, "matmul")

    assert not profile.mismatches
    assert len(profile.men) > 1
    assert profile.corridor_ticks > 0
