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
from randomfun2026solvers.optimize import (  # noqa: E402
    _expected_flat,
    _expected_frames,
    _expected_string,
    verify,
)


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
    # The adapter corridor and two-read jump loop improvements compose: 105,580
    # before either change, 105,270 after the corridor, and 104,940 with both.
    assert result.step == 104_940
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


def test_pipe_attaches_at_a_room_corner() -> None:
    # A pipe may leave a room from a corner cell, not only a wall interior
    # (SPEC: the arrow's backward cell must be on the source room's *border*).
    # This reverse-a-list solution routes its output pipe out of the last
    # compute room's corner and snakes another pipe out of a second corner;
    # an interior-only scan misses both, leaving the output room with no
    # incoming pipe and a `no-pipe` fatal the moment the man tries to send.
    machine = FastLittleman(REPO / "tests" / "fixtures" / "corner_attached_pipe.man")

    output_room = next(r for r in machine.rooms if r.kind == "output")
    assert len(output_room.incoming) == 1  # the corner-attached pipe was found

    # No send/recv binds to a phantom -1 pipe.
    assert all(binding != -1 for binding in machine._bindings.values())

    # And it computes: reverse each list, in order, exactly like the reference.
    problem = scoring.load_problem("reverse-a-list")
    case = problem["publicTestData"][0]
    result = machine.run(
        scoring._case_input(case),
        expected=_expected_string(case),
    )
    assert result.passed
    assert result.fatal is None
    assert result.output == _expected_flat(case)


def test_display_data_pipe_started_beside_a_wall_is_found() -> None:
    # A display's DATA pipe can start at an arrowhead whose backward cell is
    # empty but whose side neighbour is the source room's wall; another stray
    # arrowhead points into that start. A naive "any arrow pointing in = a
    # continuation" test drops the real start, the display loses its DATA pipe,
    # and a DATA send rebinds to the SWAP pipe -> a spurious `display-swap`.
    machine = FastLittleman(REPO / "tests" / "fixtures" / "display_corner_data_pipe.man")

    display = next(r for r in machine.rooms if r.kind == "display")
    assert len(display.incoming) == 3  # ADDR, DATA and SWAP all present

    problem = scoring.load_problem("snake")
    case = problem["publicTestData"][0]
    result = machine.run(scoring._case_input(case), frames=_expected_frames(case))
    assert result.passed
    assert result.fatal is None


def test_optimize_verify_uses_fast_backend_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LM_VALIDATOR", raising=False)
    result = verify(REPO / "tasks" / "solutions" / "triangle_cpu.man", "triangle")
    assert result.passed
    assert [case.ticks for case in result.cases] == [364] * 6
