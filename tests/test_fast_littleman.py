"""The independent in-memory Little Man validator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import scoring  # noqa: E402
from randomfun2026solvers.fast_littleman import (  # noqa: E402
    FastLittleman,
    FastLittlemanError,
)
from randomfun2026solvers.littleman import Littleman  # noqa: E402
from randomfun2026solvers.optimize import _expected_frames, _expected_string, verify  # noqa: E402


def test_class_runs_repeated_cases_without_files_or_node() -> None:
    source = (REPO / "littleman" / "examples" / "echo.man").read_text()
    machine = FastLittleman(source)

    assert machine.run([7]).output == [7]
    assert machine.run([-42]).output == [-42]
    assert machine.run([7]).step == 4


def test_native_and_python_engines_agree_on_exact_ticks() -> None:
    machine = FastLittleman(REPO / "tasks" / "solutions" / "triangle_cpu.man")
    problem = scoring.load_problem("triangle")
    for case in problem["publicTestData"]:
        kwargs = {
            "input": scoring._case_input(case),
            "expected": _expected_string(case),
        }
        native = machine.run(**kwargs)
        python = machine.run(**kwargs, native=False)
        assert native.output == python.output
        assert native.step == python.step == 364
        assert native.passed is python.passed is True


def test_display_frames_are_judged_in_memory() -> None:
    machine = FastLittleman(REPO / "tasks" / "solutions" / "palette_cpu.man")
    problem = json.loads((REPO / "tasks" / "problems" / "palette.json").read_text(encoding="utf-8"))
    case = problem["publicTestData"][0]
    result = machine.run(
        scoring._case_input(case),
        frames=_expected_frames(case),
    )
    assert result.passed
    assert result.step == 105_580
    assert result.output == []


def test_wrong_expected_output_is_rejected_immediately() -> None:
    machine = FastLittleman(REPO / "littleman" / "examples" / "echo.man")
    result = machine.run([7], expected=[8])
    assert not result.passed
    assert result.fatal == "wrong-output"
    assert result.output == [7]


def test_load_error_does_not_fall_through_to_node() -> None:
    with pytest.raises(FastLittlemanError, match="no rooms"):
        FastLittleman("not a room\n")


def test_aligned_literals_in_separate_rooms_do_not_pair_through_walls() -> None:
    source = """\
+-----+
|@`1`H|
+-----+
+-----+
|@`2`H|
+-----+"""

    # The reference interpreter scopes literal pairing to each room. A
    # canvas-wide column scan would instead pair the two rooms' delimiters
    # through their `-` walls and report a spurious non-digit load error.
    assert Littleman().analyze(source).rooms
    FastLittleman(source)


def test_optimize_verify_uses_fast_backend_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LM_VALIDATOR", raising=False)
    result = verify(REPO / "tasks" / "solutions" / "triangle_cpu.man", "triangle")
    assert result.passed
    assert [case.ticks for case in result.cases] == [364] * 6
