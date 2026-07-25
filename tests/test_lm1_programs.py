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
    problem_json,
    problem_of,
    rounds_for_problem,
)

MAX_INSTRUCTIONS = 3_000_000

#: Problems ARCH.md §4.1 leaves blocked on the STORE block, plus the two display
#: problems. No program here should claim to solve one of these.
#:
#: ``sudoku-validity`` came off this list: it looked blocked because its natural
#: encoding is 3 x 9 x 9 = 243 set-membership flags and the tape caps at 103 slots,
#: but as 27 one-cell bitmasks it needs only 36 addresses. What unblocked it was an
#: encoding change plus one opcode (`AND`), not a bigger STORE block.
BLOCKED = {
    "memory",
    "reverse-a-list",
    "sort-numbers",
    "subset-sum",
    "gradebook",
    "matmul",
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
        "plotter",
        "sudoku-validity",
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
        # LDA/MOVA in place of LDP/STP: keeping the address in ACC means the
        # `0`/`1` request literal never has to coexist with it, so tcp needs no
        # SPILL block at all (see tcp.asm's header).
        "tcp": {"LDA", "MOVA"},
        # `DSP p` picks a pipe from its operand, which nearest-pipe binding cannot
        # express; one opcode per LM-75 port gives each its own lane (see plotter.asm).
        "plotter": {"DSPA", "DSPD", "DSPS", "NEG"},
        "triangle-closed": {"MUL", "DIVI"},
        # 27 one-cell bitmasks instead of 243 flags: `AND` tests membership, and
        # the set is a plain ADD because the test proved the bit clear. LDA/MOVA
        # index the masks; DIVI builds the box number (see sudoku-validity.asm).
        "sudoku-validity": {"AND", "DIVI", "LDA", "MOVA"},
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


# ── display problems are graded on frames, not on output ─────────────────────
def test_plotter_draws_exactly_bresenham_on_every_public_case() -> None:
    """``plotter`` emits no program output, so the generic test above proves nothing.

    Replay its LM-75 port writes through the panel model and compare committed
    frames pixel for pixel. Bresenham is direction-sensitive, so a case drawn B->A
    has different pixels from A->B and this catches getting that backwards.
    """
    from randomfun2026solvers.lm1.display import frames_from_writes

    prob = problem_json("plotter")
    width = prob["io"]["display"]["width"]
    height = prob["io"]["display"]["height"]
    for case in prob["publicTestData"]:
        prog = load("plotter")
        rounds = [
            Round(input=tuple(int(v) for v in (r.get("in") or [])), expected=())
            for r in case["rounds"]
        ]
        res = Emulator(prog).run(rounds, max_instructions=MAX_INSTRUCTIONS)
        got = frames_from_writes(res.display_writes, width=width, height=height)
        want = [r["frames"][0] for r in case["rounds"] if r.get("frames")]
        assert got == want, f"plotter/{case['name']}: frame mismatch"
        assert not res.output, f"plotter/{case['name']}: emitted output on a display problem"


def test_plotter_fits_the_tick_cap_at_the_worst_legal_case() -> None:
    """20 rounds of the longest possible segment, with *real* tape latency.

    The public cases top out at 8 rounds, so they say nothing about the private
    ones. Store accesses are billed at ``ARCH.md`` §4.1's ``105 + 8.3N`` rather
    than the emulator's flat 6 ticks/word, which understates them ~30x.
    """
    from randomfun2026solvers.lm1.store import DictStore

    class Counting(DictStore):
        def __init__(self) -> None:
            super().__init__()
            self.ops = 0

        def _read(self, addr: int) -> int:
            self.ops += 1
            return super()._read(addr)

        def _write(self, addr: int, value: int) -> None:
            self.ops += 1
            super()._write(addr, value)

    tape_n = 11
    rounds = [Round(input=(0, i % 24, 31, (i * 7 + 3) % 24)) for i in range(20)]
    store = Counting()
    res = Emulator(load("plotter"), store=store).run(rounds, max_instructions=MAX_INSTRUCTIONS)
    ticks = res.ticks + store.ops * (105 + 8.3 * tape_n)
    assert ticks < TICK_CAP, f"{ticks:,.0f} ticks over the {TICK_CAP:,} cap"
