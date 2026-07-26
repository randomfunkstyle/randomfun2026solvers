"""Compact AST-generated direct brackets parser."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from randomfun2026solvers.brackets_compact import (
    MAX_PACKED_STACK,
    RING_CAPACITY_NEEDED,
    build,
    build_ast,
    debug_map,
)
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.man_debug import render_html
from randomfun2026solvers.manast import Atom, Joint, Run
from test_brackets_bounds import CASES, LEGAL, encoded, expected_answer

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = ROOT / "tasks" / "problems" / "brackets.json"
SOLUTION = ROOT / "tasks" / "solutions" / "brackets_compact.man"
OPTIMIZED = ROOT / "tasks" / "compacted" / "brackets_compact_optimized.man"
DEBUG_HTML = ROOT / "littleman" / "examples" / "brackets-compact.debug.html"
DEBUG_JSON = ROOT / "littleman" / "examples" / "brackets-compact.debug.json"


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text(encoding="utf-8"))["publicTestData"]


def test_generator_and_sidecars_are_current() -> None:
    rows = build()
    dbg = debug_map()
    assert rows == SOLUTION.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert json.loads(DEBUG_JSON.read_text(encoding="utf-8")) == dbg.to_dict()
    assert DEBUG_HTML.read_text(encoding="utf-8") == render_html(rows, dbg)


def test_grid_is_structural_ast_without_a_raw_atom() -> None:
    ast = build_ast()
    assert not ast.strays
    assert all(not isinstance(child, Atom) for room in ast.rooms for child in room.children)
    assert {type(child) for room in ast.rooms for child in room.children} == {Run, Joint}


def test_classifier_replaces_the_six_ascii_staircase() -> None:
    source = "\n".join(build())
    assert "`32`" in source
    for ascii_value in (40, 41, 91, 93, 123, 125):
        assert f"`{ascii_value}`" not in source


def test_numeric_ring_and_footprint_bounds() -> None:
    assert MAX_PACKED_STACK == 3_706_040_377_703_681
    assert MAX_PACKED_STACK < 2**63
    assert RING_CAPACITY_NEEDED == 3
    rows = build()
    assert (max(map(len, rows)), len(rows)) == (66, 19)


def test_no_empty_outer_grid_margin() -> None:
    rows = build()
    width = max(map(len, rows))
    padded = [row.ljust(width) for row in rows]
    assert any(row[0] != " " for row in padded)
    assert any(row[-1] != " " for row in padded)
    assert rows[0].strip()
    assert rows[-1].strip()


@pytest.mark.parametrize("case", public_cases(), ids=lambda case: case["name"])
def test_every_public_case(case: dict) -> None:
    result = FastLittleman(SOLUTION).run(
        case["in"],
        expected=[int(value) for value in case["out"]],
        max_ticks=20_000,
    )
    assert result.passed, (result.fatal, result.output)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_exact_bound_and_adversarial_cases(case) -> None:
    result = FastLittleman(SOLUTION).run(
        encoded(case.text),
        expected=[case.answer],
        max_ticks=20_000,
    )
    assert result.passed, (case.name, result.fatal, result.output)


def test_every_string_through_length_four() -> None:
    machine = FastLittleman(SOLUTION)
    checked = 0
    for size in range(5):
        for chars in itertools.product(sorted(LEGAL), repeat=size):
            text = "".join(chars)
            result = machine.run(
                encoded(text),
                expected=[expected_answer(text)],
                max_ticks=4_000,
            )
            assert result.passed, (text, result.fatal, result.output)
            checked += 1
    assert checked == 1_555


def test_checked_in_optimizer_result() -> None:
    rows = OPTIMIZED.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert (max(map(len, rows)), len(rows)) == (63, 15)

    machine = FastLittleman(OPTIMIZED)
    for case in public_cases():
        result = machine.run(
            case["in"],
            expected=[int(value) for value in case["out"]],
            max_ticks=20_000,
        )
        assert result.passed, (case["name"], result.fatal, result.output)
    for case in CASES:
        result = machine.run(
            encoded(case.text),
            expected=[case.answer],
            max_ticks=20_000,
        )
        assert result.passed, (case.name, result.fatal, result.output)
