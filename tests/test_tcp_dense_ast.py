"""Regression coverage for the compact AST-authored TCP machine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers import tcp_dense_ast
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.man_debug import render_html
from randomfun2026solvers.manast import Atom, Joint, Run
from randomfun2026solvers.manmoves import hug_violations
from randomfun2026solvers.manparse import parse_program

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = ROOT / "tasks" / "problems" / "tcp.json"
SOLUTION = ROOT / "tasks" / "solutions" / "tcp_dense_ast.man"
DEBUG_HTML = ROOT / "littleman" / "examples" / "tcp-dense-ast.html"
DEBUG_JSON = ROOT / "littleman" / "examples" / "tcp-dense-ast.json"


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text(encoding="utf-8"))["publicTestData"]


def test_generator_and_debug_sidecars_are_current() -> None:
    rows = tcp_dense_ast.build()
    dbg = tcp_dense_ast.debug_map()
    assert rows == SOLUTION.read_text(encoding="utf-8").rstrip("\n").splitlines()
    assert json.loads(DEBUG_JSON.read_text(encoding="utf-8")) == dbg.to_dict()
    assert DEBUG_HTML.read_text(encoding="utf-8") == render_html(rows, dbg)


def test_compaction_remains_structural_ast() -> None:
    ast = tcp_dense_ast.build_ast()
    assert not ast.strays
    worker = next(room for room in ast.rooms if room.id == tcp_dense_ast.WORKER_ID)
    assert all(not isinstance(child, Atom) for child in worker.children)
    assert {type(child) for child in worker.children} <= {Run, Joint}
    assert hug_violations(ast) == []


def test_phase_sensitive_queue_ring_keeps_its_proven_capacity() -> None:
    pipes = {pipe.id: pipe for pipe in tcp_dense_ast.build_ast().pipes}
    assert pipes[0].capacity >= tcp_dense_ast.MIN_EAST_LEG
    assert (
        sum(pipes[pipe_id].capacity for pipe_id in tcp_dense_ast.RING_PIPES)
        >= tcp_dense_ast.MIN_RING_CAPACITY
    )


def test_every_pipe_operation_binds() -> None:
    program = parse_program(SOLUTION)
    pipe_ids = {pipe.id for pipe in program.pipes}
    assert len(program.rooms) == 4
    assert len(pipe_ids) == 5
    operations = [operation for room in program.rooms for operation in room.pipe_ops]
    assert {operation.pipe_id for operation in operations} == pipe_ids


@pytest.mark.slow
@pytest.mark.parametrize("case", public_cases(), ids=lambda case: case["name"])
def test_every_public_case(case: dict) -> None:
    input_ = " / ".join(" ".join(round_["in"]) for round_ in case["rounds"])
    expected = " / ".join(" ".join(round_["out"]) for round_ in case["rounds"])
    output = [int(value) for round_ in case["rounds"] for value in round_["out"]]
    result = FastLittleman(SOLUTION).run(
        input_,
        expected=expected,
        max_ticks=100_000,
    )
    assert result.passed, (result.fatal, result.output)
    assert result.output == output
