"""Goal-sized 25x25 AST brackets parser."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from randomfun2026solvers.brackets_goal25 import build, build_ast, debug_map
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.man_debug import render_html
from randomfun2026solvers.manast import Atom, Joint, Run
from randomfun2026solvers.manparse import parse_program
from test_brackets_bounds import CASES, LEGAL, encoded, expected_answer

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = ROOT / "tasks" / "problems" / "brackets.json"
SOLUTION = ROOT / "tasks" / "solutions" / "brackets_goal25.man"
DEBUG_HTML = ROOT / "littleman" / "examples" / "brackets-goal25.debug.html"
DEBUG_JSON = ROOT / "littleman" / "examples" / "brackets-goal25.debug.json"


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


def test_goal_footprint_and_pipe_bindings() -> None:
    rows = build()
    assert (max(map(len, rows)), len(rows)) == (25, 25)

    program = parse_program(SOLUTION)
    worker = max(program.rooms, key=lambda room: room.width * room.height)
    input_pipe = next(
        pipe.id
        for pipe in program.pipes
        if pipe.dst == worker.id and program.rooms[pipe.src].kind == "input"
    )
    output_pipe = next(
        pipe.id
        for pipe in program.pipes
        if pipe.src == worker.id and program.rooms[pipe.dst].kind == "output"
    )
    ring_in = next(
        pipe.id
        for pipe in program.pipes
        if pipe.dst == worker.id and program.rooms[pipe.src].kind == "compute"
    )
    ring_out = next(
        pipe.id
        for pipe in program.pipes
        if pipe.src == worker.id and program.rooms[pipe.dst].kind == "compute"
    )

    assert {(op.cell, op.glyph) for op in worker.pipe_ops if op.pipe_id == input_pipe} == {
        ((4, 14), "r"),
        ((2, 20), "r"),
    }
    assert {(op.cell, op.glyph) for op in worker.pipe_ops if op.pipe_id == output_pipe} == {
        ((2, 6), "s"),
    }
    assert all(
        op.pipe_id in {ring_in, ring_out}
        for op in worker.pipe_ops
        if op.pipe_id not in {input_pipe, output_pipe}
    )


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
