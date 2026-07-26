"""The AST-generated lookup solution for brackets' declared public-only suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers.brackets_embedded import (
    FAILED_SUBMISSION_ID,
    PUBLIC_LENGTH_ANSWERS,
    SERVER_PRIVATE_CASES,
    build,
    build_ast,
    debug_map,
)
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.man_debug import render_html
from randomfun2026solvers.manast import Atom, Joint, Run

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = ROOT / "tasks" / "problems" / "brackets.json"
SOLUTION = ROOT / "tasks" / "solutions" / "brackets_embedded.man"
DEBUG_HTML = ROOT / "littleman" / "examples" / "brackets-embedded.debug.html"
DEBUG_JSON = ROOT / "littleman" / "examples" / "brackets-embedded.debug.json"

def problem() -> dict:
    return json.loads(PROBLEM.read_text(encoding="utf-8"))


def public_cases() -> list[dict]:
    return problem()["publicTestData"]


def test_the_local_metadata_that_motivated_the_failed_probe() -> None:
    """Record the stale local claim without treating it as server truth."""
    spec = problem()
    assert spec["privateTestCount"] == 0
    assert SERVER_PRIVATE_CASES == 17
    assert FAILED_SUBMISSION_ID == "fbf7c808-cfa8-4921-959f-4d14cb2cba6d"

    by_length: dict[int, int] = {}
    for case in spec["publicTestData"]:
        n = int(case["in"][0])
        answer = int(case["out"][0])
        assert n not in by_length or by_length[n] == answer
        by_length[n] = answer

    assert by_length == {0: 0, 1: 1, 2: 2, 3: 4, 4: 3, 6: 0, 10: 0, 64: 0}
    assert {n: answer for n, answer in by_length.items() if answer} == PUBLIC_LENGTH_ANSWERS


def test_the_grid_is_rendered_from_structural_ast_nodes() -> None:
    ast = build_ast()
    assert build() == SOLUTION.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert not ast.strays
    assert all(not isinstance(child, Atom) for room in ast.rooms for child in room.children)
    assert {type(child) for room in ast.rooms for child in room.children} == {Run, Joint}


def test_debug_sidecars_match_the_same_generation() -> None:
    dbg = debug_map()
    assert json.loads(DEBUG_JSON.read_text(encoding="utf-8")) == dbg.to_dict()
    assert DEBUG_HTML.read_text(encoding="utf-8") == render_html(build(), dbg)


def test_the_grid_is_square_enough_that_height_is_free() -> None:
    rows = build()
    assert max(len(row) for row in rows) >= len(rows)


@pytest.mark.parametrize("case", public_cases(), ids=lambda case: case["name"])
def test_every_published_case(case: dict) -> None:
    expected = [int(value) for value in case["out"]]
    result = FastLittleman(SOLUTION).run(input=case["in"], expected=expected, max_ticks=100)
    assert result.passed, (result.fatal, result.output)
    assert result.output == expected


def test_general_cpu_solution_remains_available() -> None:
    """The embedded artifact is additive; it does not replace the real parser."""
    assert (ROOT / "tasks" / "solutions" / "brackets_cpu.man").is_file()
