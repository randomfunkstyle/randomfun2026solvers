"""Runs every LM-1 task program against every public test case.

This is the step-2 acceptance test from ``ARCH.md`` §8: "all task programs written
and passing against the emulator. Proves the ISA is sufficient before any ASCII is
drawn."
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1.emulator import TICK_CAP, Emulator, Round  # noqa: E402
from randomfun2026solvers.lm1.isa import LM1_V1  # noqa: E402
from randomfun2026solvers.lm1.programs import (  # noqa: E402
    PROBLEM_DIR,
    available,
    history_lesson_source,
    load,
    problem_of,
    rounds_for_problem,
)

MAX_INSTRUCTIONS = 3_000_000

#: Problems ARCH.md §4.1 leaves blocked on the STORE block, plus the two display
#: problems. No program here should claim to solve one of these.
BLOCKED = {
    "memory",
    "reverse-a-list",
    "sort-numbers",
    "sudoku-validity",
    "subset-sum",
    "gradebook",
    "matmul",
    "plotter",
    "palette",
}

PROGRAMS = sorted(available())
CASES = [
    (stem, name, rounds)
    for stem in PROGRAMS
    for name, rounds in rounds_for_problem(problem_of(stem))
]


def test_the_expected_programs_exist() -> None:
    assert set(problem_of(s) for s in PROGRAMS) == {
        "triangle",
        "hello-world",
        "max-element",
        "atoi",
        "brackets",
        "tcp",
        "history-lesson",
    }


def test_no_program_claims_a_blocked_problem() -> None:
    assert not {problem_of(s) for s in PROGRAMS} & BLOCKED


@pytest.mark.parametrize("stem", PROGRAMS)
def test_program_assembles(stem: str) -> None:
    prog = load(stem)
    assert prog.P > 0
    assert prog.ring_capacity[0] == prog.P + 2
    assert (PROBLEM_DIR / f"{problem_of(stem)}.json").exists()


@pytest.mark.parametrize(("stem", "case", "rounds"), CASES, ids=[f"{s}:{n}" for s, n, _ in CASES])
def test_public_case_passes(stem: str, case: str, rounds: list[Round]) -> None:
    prog = load(stem)
    res = Emulator(prog).run(rounds, max_instructions=MAX_INSTRUCTIONS)
    expected = tuple(v for r in rounds for v in r.expected)
    assert res.output == expected, f"{stem}/{case}: {res.reason}"
    assert res.reason in ("halted", "input-exhausted"), f"{stem}/{case}: {res.reason}"
    assert res.ticks < TICK_CAP, f"{stem}/{case}: {res.ticks} ticks over the cap"


@pytest.mark.parametrize("stem", PROGRAMS)
def test_programs_without_extensions_also_run_on_arch_v1(stem: str) -> None:
    """Programs that use no ext opcode must assemble and pass under ARCH.md §6."""
    if load(stem).ext_ops:
        pytest.skip(f"{stem} uses ISA extensions: {load(stem).ext_ops}")
    prog = load(stem, isa=LM1_V1)
    assert prog.isa.decode_bits == 4  # the depth-4 trie ARCH.md budgets for
    for _name, rounds in rounds_for_problem(problem_of(stem)):
        res = Emulator(prog).run(rounds, max_instructions=MAX_INSTRUCTIONS)
        assert res.output == tuple(v for r in rounds for v in r.expected)


def test_extension_users_are_exactly_the_ones_we_expect() -> None:
    ext = {stem: set(load(stem).ext_ops) for stem in PROGRAMS}
    assert {k: v for k, v in ext.items() if v} == {
        "brackets": {"DIVI", "MODI"},
        "tcp": {"LDP", "STP"},
        "triangle-closed": {"MUL", "DIVI"},
    }


def test_history_lesson_asm_is_up_to_date_with_the_problem_json() -> None:
    path = available()["history-lesson"]
    assert path.read_text(encoding="utf-8") == history_lesson_source()


def test_cli_grades_every_program_green() -> None:
    from randomfun2026solvers.lm1.__main__ import main

    assert main(["list"]) == 0
    assert main(["grade"]) == 0
    assert main(["asm", "triangle"]) == 0
    assert main(["run", "triangle", "--input", "4"]) == 0
