"""The display-problem harness: round gating, frame compare, scoring plumbing.

The fast tier here runs no engine at all. A display run is fully described by
"which frames did it commit, and when", so :class:`FakeDisplayEngine` stands in
for ``FastLittleman`` and reproduces exactly what the native runner does with
``frames`` (``fast_littleman_native.cpp``): compare each commit against the next
expected frame, stop the moment the last one matches, die on the first that
differs. That is enough to pin the harness's own logic — the prefix search that
recovers the matched-frame count, the output-on-a-display-problem rule, the tick
cap, and the score arithmetic — in milliseconds.

The end-to-end proof (a real ``.man`` on a real engine) is marked ``slow``.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from randomfun2026solvers import scoring
from randomfun2026solvers.fast_littleman import FastResult
from randomfun2026solvers.pathfinder_check import (
    CaseResult,
    Runner,
    case_input,
    check_all,
    expected_frames,
    flat_frames,
    resolve_backend,
    run_case,
    truncate_frames,
)

REPO = Path(__file__).resolve().parents[1]
PATHFINDER = REPO / "tasks" / "problems" / "pathfinder.json"

# A grid the harness never runs: only its bounding box matters to the footer.
# 6 wide x 4 tall -> area2 = 36.
STUB_GRID = "+----+\n|@   |\n|    |\n+----+\n"


def pathfinder_cases() -> list[dict[str, Any]]:
    return json.loads(PATHFINDER.read_text(encoding="utf-8"))["publicTestData"]


# ── a stand-in for the native display engine ─────────────────────────────────
@dataclass
class FakeDisplayEngine:
    """Commits ``commits[i]`` at tick ``ticks[i]``, judged the way the native runner does.

    ``output`` is what the program wrote to program output (an error on a display
    problem, even when every frame matched). Anything the expectation asks for
    beyond ``commits`` is a stall, and reports the tick cap.
    """

    commits: list[list[str]]
    ticks: list[int]
    output: list[int] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run(
        self,
        input: str | None = None,
        *,
        frames: Any = None,
        expected: Any = None,
        max_ticks: int = 5_000_000,
    ) -> FastResult:
        self.calls.append({"input": input, "frames": frames, "max_ticks": max_ticks})
        want = [list(f) for r in (frames or []) for f in r]
        matched = 0
        for i, got in enumerate(self.commits):
            if matched >= len(want):
                break
            if list(got) != want[matched]:
                return FastResult(
                    output=[],
                    step=self.ticks[i],
                    halted=False,
                    reason="wrong-frame",
                    fatal="wrong-frame",
                    passed=False,
                )
            matched += 1
            if matched == len(want):
                return FastResult(
                    output=list(self.output),
                    step=self.ticks[i],
                    halted=False,
                    reason="output-settled",
                    fatal=None,
                    passed=not self.output,
                )
        if not want:
            return FastResult(
                output=list(self.output),
                step=0,
                halted=False,
                reason="output-settled",
                fatal=None,
                passed=not self.output,
            )
        # The native runner leaves ``passed`` *unknown* when it runs out of ticks
        # with frames outstanding — verified against FastLittleman on
        # palette_cpu.man at an insufficient cap.
        return FastResult(
            output=[], step=max_ticks, halted=False, reason="tick-cap", fatal=None, passed=None
        )


def one_round_case(frames: list[list[str]], name: str = "fake") -> dict[str, Any]:
    return {"name": name, "rounds": [{"in": [1, 2], "out": [], "frames": frames}]}


def frame(tag: str) -> list[str]:
    """A tiny 4x2 frame whose content is keyed by ``tag``."""
    return [tag * 4, tag * 4]


# ── the problem JSON: rounds, frames, input staging ──────────────────────────
def test_pathfinder_json_is_the_shape_the_harness_assumes():
    cases = pathfinder_cases()
    assert len(cases) == 7
    first = cases[0]
    assert [len(r["frames"]) for r in first["rounds"]] == [1, 9, 12, 1]
    # 16x16 panel, one hex digit per pixel.
    for row in first["rounds"][0]["frames"][0]:
        assert len(row) == 16
    assert len(first["rounds"][0]["frames"][0]) == 16
    # every round expects no *program* output — frames are the whole contract
    assert all(r.get("out") == [] for r in first["rounds"])


def test_expected_frames_keeps_the_round_nesting_the_engines_gate_on():
    case = pathfinder_cases()[0]
    per_round = expected_frames(case)
    assert [len(r) for r in per_round] == [1, 9, 12, 1]
    assert len(flat_frames(case)) == 23
    assert flat_frames(case)[0] == per_round[0][0]


def test_gated_input_separates_rounds_so_the_engine_can_withhold_them():
    case = pathfinder_cases()[0]
    text = case_input(case)
    # three separators for four rounds; the setup round carries 256 board cells + rx ry
    assert text.count("/") == 3
    assert len(text.split("/")[0].split()) == 258
    assert all(len(part.split()) == 2 for part in text.split("/")[1:])


def test_ungated_input_is_one_flat_stream():
    case = pathfinder_cases()[0]
    text = case_input(case, gated=False)
    assert "/" not in text
    assert len(text.split()) == 258 + 2 * 3


def test_truncate_frames_takes_a_prefix_and_keeps_the_rounds():
    per_round = [[frame("a")], [frame("b"), frame("c")], [frame("d")]]
    assert truncate_frames(per_round, 0) == [[], [], []]
    assert truncate_frames(per_round, 2) == [[frame("a")], [frame("b")], []]
    assert truncate_frames(per_round, 4) == per_round
    assert truncate_frames(per_round, 99) == per_round


# ── verdicts against the fake engine ─────────────────────────────────────────
def test_a_clean_run_reports_every_frame_and_the_final_frames_tick():
    want = [frame("1"), frame("2"), frame("3")]
    engine = FakeDisplayEngine(commits=want, ticks=[10, 20, 30])
    res = run_case(STUB_GRID, one_round_case(want), backend="fast", engine=engine)
    assert res.as_tuple() == (True, 3, None, 30, None)


def test_the_first_bad_frame_is_located_by_prefix_search():
    want = [frame("1"), frame("2"), frame("3"), frame("4"), frame("5")]
    commits = [frame("1"), frame("2"), frame("3"), frame("9"), frame("5")]
    engine = FakeDisplayEngine(commits=commits, ticks=[10, 20, 30, 40, 50])
    res = run_case(STUB_GRID, one_round_case(want), backend="fast", engine=engine)
    passed, matched, index, ticks, error = res.as_tuple()
    assert (passed, matched, index, ticks) == (False, 3, 3, 40)
    assert "did not expect" in error
    # the search is a bisection, not a rerun per frame
    assert len(engine.calls) <= 1 + 5


@pytest.mark.parametrize("bad", [0, 1, 2, 3, 4])
def test_every_mismatch_position_is_found(bad: int):
    want = [frame(str(i)) for i in range(5)]
    commits = [frame("x") if i == bad else f for i, f in enumerate(want)]
    engine = FakeDisplayEngine(commits=commits, ticks=[10, 20, 30, 40, 50])
    res = run_case(STUB_GRID, one_round_case(want), backend="fast", engine=engine)
    assert (res.frames_matched, res.first_mismatch_index) == (bad, bad)


def test_the_mismatch_search_can_be_switched_off():
    want = [frame("1"), frame("2")]
    engine = FakeDisplayEngine(commits=[frame("1"), frame("9")], ticks=[10, 20])
    res = run_case(STUB_GRID, one_round_case(want), backend="fast", engine=engine, locate=False)
    assert res.passed is False
    assert res.frames_matched is None and res.first_mismatch_index is None
    assert len(engine.calls) == 1


def test_output_on_a_display_problem_fails_even_when_every_frame_matched():
    want = [frame("1")]
    engine = FakeDisplayEngine(commits=want, ticks=[7], output=[42])
    res = run_case(STUB_GRID, one_round_case(want), backend="fast", engine=engine)
    assert res.passed is False
    assert res.frames_matched == 1 and res.first_mismatch_index is None
    assert "emitted 1 output value" in res.error


def test_a_stall_reports_the_cap_and_no_mismatch_index():
    want = [frame("1"), frame("2"), frame("3")]
    engine = FakeDisplayEngine(commits=[frame("1")], ticks=[10])
    res = run_case(STUB_GRID, one_round_case(want), backend="fast", engine=engine, cap=1000)
    assert res.passed is False
    assert res.ticks == 1000
    assert res.frames_matched == 1
    assert res.first_mismatch_index is None
    assert "tick cap" in res.error


def test_a_case_with_no_frames_is_rejected_rather_than_silently_passing():
    engine = FakeDisplayEngine(commits=[], ticks=[])
    res = run_case(
        STUB_GRID,
        {"name": "empty", "rounds": [{"in": [1], "out": []}]},
        backend="fast",
        engine=engine,
    )
    assert res.passed is False and "no frames" in res.error


def test_the_engine_is_handed_slash_separated_rounds_by_default():
    want = [frame("1"), frame("2")]
    case = {
        "name": "two rounds",
        "rounds": [
            {"in": [1], "out": [], "frames": [want[0]]},
            {"in": [2], "out": [], "frames": [want[1]]},
        ],
    }
    engine = FakeDisplayEngine(commits=want, ticks=[10, 20])
    run_case(STUB_GRID, case, backend="fast", engine=engine)
    call = engine.calls[0]
    assert call["input"] == "1 / 2"
    # nested per round, so the engine can withhold round 2 until frame 1 commits
    assert [len(r) for r in call["frames"]] == [1, 1]


def test_ungated_collapses_the_case_to_a_single_round():
    want = [frame("1"), frame("2")]
    case = {
        "name": "two rounds",
        "rounds": [
            {"in": [1], "out": [], "frames": [want[0]]},
            {"in": [2], "out": [], "frames": [want[1]]},
        ],
    }
    engine = FakeDisplayEngine(commits=want, ticks=[10, 20])
    run_case(STUB_GRID, case, backend="fast", engine=engine, gated=False)
    call = engine.calls[0]
    assert call["input"] == "1 2"
    assert [len(r) for r in call["frames"]] == [2]


# ── backends ──────────────────────────────────────────────────────────────────
def test_backend_resolution_follows_lm_validator(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LM_VALIDATOR", raising=False)
    assert resolve_backend() == "fast"
    monkeypatch.setenv("LM_VALIDATOR", "reference")
    assert resolve_backend() == "reference"
    assert resolve_backend("fast") == "fast"  # an explicit choice always wins
    monkeypatch.setenv("LM_VALIDATOR", "FAST")
    assert resolve_backend() == "fast"
    with pytest.raises(ValueError):
        resolve_backend("wasm")


def test_a_grid_that_will_not_load_fails_every_case_instead_of_raising():
    with Runner("nothing here at all\n", backend="fast") as runner:
        assert runner.load_error is not None
        res = runner.run_case(one_round_case([frame("1")]))
    assert res.passed is False and res.error.startswith("load:")


# ── scoring plumbing ──────────────────────────────────────────────────────────
def _problem(cases: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"slug": "fake", "scoring": "footprint-tick", "publicTestData": cases, **extra}


def test_check_all_scores_area2_times_average_ticks():
    want = [frame("1")]
    cases = [one_round_case(want, "a"), one_round_case(want, "b")]
    engine = FakeDisplayEngine(commits=want, ticks=[100])
    res = check_all(STUB_GRID, _problem(cases), backend="fast", engine=engine)
    assert res.passed and res.n_passed == 2
    assert (res.width, res.height, res.area2) == (6, 4, 36)
    assert res.avg_ticks == 100.0
    assert res.score == 36 * 100.0
    # the footprint must be the one scoring.py computes, not a private copy
    assert scoring.footprint(STUB_GRID) == (res.width, res.height, res.area2)


def test_check_all_refuses_to_score_a_failing_program():
    want = [frame("1")]
    cases = [one_round_case(want, "a")]
    engine = FakeDisplayEngine(commits=[frame("9")], ticks=[100])
    res = check_all(STUB_GRID, _problem(cases), backend="fast", engine=engine)
    assert res.passed is False
    assert res.score is None
    assert res.avg_ticks == 100.0


def test_check_all_uses_the_problems_own_tick_cap_not_the_5m_default():
    want = [frame("1")]
    engine = FakeDisplayEngine(commits=want, ticks=[5])
    check_all(
        STUB_GRID,
        _problem([one_round_case(want)], tickCap=15_000_000),
        backend="fast",
        engine=engine,
    )
    assert engine.calls[0]["max_ticks"] == 15_000_000

    engine = FakeDisplayEngine(commits=want, ticks=[5])
    check_all(STUB_GRID, _problem([one_round_case(want)]), backend="fast", engine=engine)
    assert engine.calls[0]["max_ticks"] == scoring.DEFAULT_TICK_CAP

    engine = FakeDisplayEngine(commits=want, ticks=[5])
    check_all(
        STUB_GRID,
        _problem([one_round_case(want)], tickCap=15_000_000),
        backend="fast",
        cap=999,
        engine=engine,
    )
    assert engine.calls[0]["max_ticks"] == 999


def test_the_real_pathfinder_json_carries_the_15m_cap():
    prob = scoring.load_problem("pathfinder")
    assert prob["tickCap"] == 15_000_000
    assert prob["scoring"] == "footprint-tick"
    assert prob["io"]["display"] == {"width": 16, "height": 16}


def test_check_all_can_run_a_subset_of_cases():
    want = [frame("1")]
    cases = [one_round_case(want, "a"), one_round_case(want, "b")]
    engine = FakeDisplayEngine(commits=want, ticks=[100])
    res = check_all(STUB_GRID, _problem(cases), backend="fast", names=["b"], engine=engine)
    assert [c.name for c in res.cases] == ["b"]


def test_the_table_names_every_case_and_shows_the_footer():
    want = [frame("1")]
    cases = [one_round_case(want, "a straight shot")]
    engine = FakeDisplayEngine(commits=want, ticks=[100])
    text = check_all(STUB_GRID, _problem(cases), backend="fast", engine=engine).table()
    assert "a straight shot" in text
    assert "PASS" in text
    assert "area2 = max(w,h)^2 = 36" in text
    assert "score = area2 x avg_ticks = 3,600" in text


def test_case_result_tuple_is_the_documented_shape():
    res = CaseResult(
        name="x",
        passed=False,
        frames_expected=5,
        frames_matched=2,
        first_mismatch_index=2,
        ticks=99,
        error="boom",
    )
    assert res.as_tuple() == (False, 2, 2, 99, "boom")


# ── end-to-end: a real grid on a real engine ─────────────────────────────────
# Kept slow (AGENTS.md: "anything measuring ticks on a real grid"). Add pathfinder
# cases here the same way once a grid exists:
#     check_all(REPO / "tasks" / "solutions" / "pathfinder_*.man", "pathfinder")
PLOTTER_MAN = REPO / "tasks" / "solutions" / "plotter_block.man"
PLOTTER_TICKS = {
    "main diagonal": 2359,
    "three rounds": 5817,
    "one pixel": 289,
    "both ways": 3866,
    "around the border": 8524,
    "octant fan": 9088,
}


@pytest.mark.slow
@pytest.mark.parametrize("backend", ["fast", "reference"])
def test_a_checked_in_display_solution_passes_with_the_known_ticks(backend: str):
    """Both engines must agree, frame for frame and tick for tick.

    ``plotter_block.man`` is the grid behind submission 5b3df73a (20/20 cases,
    area2 3136). Its six public cases are the ones measured here.
    """
    res = check_all(PLOTTER_MAN, "plotter", backend=backend)
    assert res.passed, [c.error for c in res.cases if not c.passed]
    assert {c.name: c.ticks for c in res.cases} == PLOTTER_TICKS
    assert (res.width, res.height, res.area2) == (44, 56, 3136)
    assert res.avg_ticks == 4990.5
    assert res.score == pytest.approx(3136 * 4990.5)


@pytest.mark.slow
@pytest.mark.parametrize("backend", ["fast", "reference"])
def test_a_corrupted_expectation_is_reported_at_the_right_frame(backend: str):
    """Deliberately break one expected frame: the harness must name its index."""
    case = copy.deepcopy(scoring.load_problem("plotter")["publicTestData"][4])
    assert case["name"] == "around the border"
    row = case["rounds"][2]["frames"][0][0]
    case["rounds"][2]["frames"][0][0] = ("1" if row[0] != "1" else "2") + row[1:]
    res = run_case(PLOTTER_MAN, case, backend=backend, cap=200_000)
    assert res.as_tuple()[:4] == (False, 2, 2, 6664)


@pytest.mark.slow
def test_the_harness_agrees_with_scoring_score_program():
    """The footer's arithmetic must match the repo's own scorer on a real grid."""
    res = check_all(PLOTTER_MAN, "plotter", backend="fast")
    ref = scoring.score_program(PLOTTER_MAN, "plotter")
    assert ref.avg_ticks == res.avg_ticks
    assert ref.score == res.score
    assert ref.area2 == res.area2
