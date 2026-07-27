"""Parallel value-carrier implementation of ``reverse-a-list``."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from randomfun2026solvers import reverse_list_split
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.man_debug import render_html
from randomfun2026solvers.manmoves import hug_violations
from randomfun2026solvers.manparse import parse_program

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = ROOT / "tasks" / "problems" / "reverse-a-list.json"
SOLUTION = ROOT / "tasks" / "solutions" / "reverse-a-list_split.man"
DEBUG_HTML = ROOT / "littleman" / "examples" / "reverse-a-list-split.debug.html"
DEBUG_JSON = ROOT / "littleman" / "examples" / "reverse-a-list-split.debug.json"


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text(encoding="utf-8"))["publicTestData"]


def test_generator_and_debug_sidecars_are_current() -> None:
    rows = reverse_list_split.build()
    dbg = reverse_list_split.debug_map()
    assert rows == SOLUTION.read_text(encoding="utf-8").rstrip("\n").splitlines()
    assert json.loads(DEBUG_JSON.read_text(encoding="utf-8")) == dbg.to_dict()
    assert DEBUG_HTML.read_text(encoding="utf-8") == render_html(rows, dbg)


def test_ast_has_only_the_two_io_pipes_and_no_hugging_path() -> None:
    ast = reverse_list_split.build_ast()
    assert not ast.strays
    assert len(ast.rooms) == 3
    assert len(ast.pipes) == 2
    assert hug_violations(ast) == []

    program = parse_program(SOLUTION)
    pipe_ids = {pipe.id for pipe in program.pipes}
    operations = [operation for room in program.rooms for operation in room.pipe_ops]
    assert {operation.pipe_id for operation in operations} == pipe_ids


def test_rank_schedule_is_reversed_and_each_circuit_has_unique_phases() -> None:
    """Halved ranks interleave exactly; alternating carriers cannot collide."""
    period = reverse_list_split.DELAY_PERIOD
    spawn = reverse_list_split.CONTROLLER_PERIOD
    for length in range(1, reverse_list_split.MAX_LIST_LENGTH + 1):
        release = {
            rank: (
                (length - rank) * spawn
                + (rank // 2) * period
                + (reverse_list_split.PARITY_SKEW if rank % 2 else 0)
            )
            for rank in range(1, length + 1)
        }
        assert sorted(release, key=release.get) == list(range(1, length + 1))
        assert len(set(release.values())) == length

    same_parity_phases = {
        (index * 2 * spawn) % period for index in range(reverse_list_split.MAX_LIST_LENGTH // 2)
    }
    assert len(same_parity_phases) == reverse_list_split.MAX_LIST_LENGTH // 2


@pytest.mark.slow
def test_every_legal_length_and_value_extreme() -> None:
    machine = FastLittleman(SOLUTION)
    basis = [-1_000_000, 1_000_000, 0, -1, 1, 999_999, -999_999]
    for length in range(1, reverse_list_split.MAX_LIST_LENGTH + 1):
        values = [basis[index % len(basis)] for index in range(length)]
        result = machine.run(
            [length, *values],
            expected=list(reversed(values)),
            max_ticks=10_000,
        )
        assert result.passed, (length, result)


@pytest.mark.slow
def test_randomized_multi_round_streams() -> None:
    rng = random.Random(20_260_727)
    machine = FastLittleman(SOLUTION)
    for case_index in range(250):
        rounds = [
            [
                rng.randint(-1_000_000, 1_000_000)
                for _ in range(rng.randint(1, reverse_list_split.MAX_LIST_LENGTH))
            ]
            for _ in range(rng.randint(1, 3))
        ]
        input_ = " / ".join(" ".join(map(str, [len(values), *values])) for values in rounds)
        expected = " / ".join(" ".join(map(str, reversed(values))) for values in rounds)
        result = machine.run(input_, expected=expected, max_ticks=10_000)
        assert result.passed, (case_index, rounds, result)


@pytest.mark.slow
@pytest.mark.parametrize("case", public_cases(), ids=lambda case: case["name"])
def test_every_public_round(case: dict) -> None:
    input_ = " / ".join(" ".join(round_["in"]) for round_ in case["rounds"])
    expected = " / ".join(" ".join(round_["out"]) for round_ in case["rounds"])
    want = [int(value) for round_ in case["rounds"] for value in round_["out"]]
    result = FastLittleman(SOLUTION).run(
        input_,
        expected=expected,
        max_ticks=10_000,
    )
    assert result.passed, (result.fatal, result.output)
    assert result.output == want


@pytest.mark.slow
def test_full_list_on_reference_interpreter() -> None:
    values = list(range(reverse_list_split.MAX_LIST_LENGTH))
    snap = Littleman().judge(
        SOLUTION.read_text(encoding="utf-8"),
        input=" ".join(map(str, [len(values), *values])),
        expected=" ".join(map(str, reversed(values))),
        max_ticks=10_000,
    )
    assert snap.fatal is None
    assert list(snap.output) == list(reversed(values))
