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
    display_size,
    frames_for_problem,
    history_lesson_source,
    load,
    palette_source,
    problem_of,
    rounds_for_problem,
)

MAX_INSTRUCTIONS = 3_000_000

#: Problems ARCH.md §4.1 leaves blocked on the STORE block. No program here should
#: claim to solve one of these.
#:
#: ``gradebook`` is *not* here any more: the STORE block exists (``machine.py``'s tape),
#: so a problem that needs indexed memory is only blocked on having a program written
#: against ``LDA``/``MOVA`` — see ``tests/test_lm1_gradebook.py``.
BLOCKED = {
    "memory",
    "reverse-a-list",
    "sort-numbers",
    "subset-sum",
}

#: Problems graded on committed frames rather than on program output.
DISPLAY_PROBLEMS = {"plotter", "palette"}

#: Programs whose *emulated* tick estimate exceeds ``TICK_CAP`` on the largest
#: public case. Empty since ``matmul`` moved onto the STREAM block: every program
#: here fits now, on the estimate *and* on the real engine.
OVER_TICK_CAP: set[str] = set()

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
        "palette",
        "gradebook",
        "sudoku-validity",
        "matmul",
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
    if stem not in OVER_TICK_CAP:
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
        # Two arrays (ids, grades) indexed by a runtime student number. `DIVI 4`
        # turns a grade address back into that number, which is what lets the whole
        # program run on one cursor (see gradebook.asm).
        "gradebook": {"DIVI", "LDA", "MOVA"},
        # `DSP p` picks a pipe from its operand, which nearest-pipe binding cannot
        # express; one opcode per LM-75 port gives each its own lane (see plotter.asm).
        "plotter": {"DSPA", "DSPD", "DSPS", "NEG"},
        "palette": {"DSPA", "DSPD", "DSPS"},
        "triangle-closed": {"MUL", "DIVI"},
        # DIVI/MODI extract one bit of a unit's digit mask; LDP/STP reach the mask
        # through a cursor slot in 2 tape accesses instead of LDA/MOVA's 6. Both
        # indexed opcodes are drawn *without* a SPILL block — see machine.py's `_HW`.
        "sudoku-validity": {"DIVI", "MODI", "LDP", "STP"},
        # `SND`/`RCV` are the whole interface to the STREAM block, which holds both
        # matrices and runs the inner multiply-accumulate loop; `MUL` builds the
        # command words (8*arg + code) out of N, M and K. No indexed opcode at all any
        # more — matmul never addresses memory randomly (see matmul.asm, stream.py).
        "matmul": {"MUL", "SND", "RCV"},
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
@pytest.mark.parametrize("slug", sorted(DISPLAY_PROBLEMS))
def test_display_programs_commit_the_expected_frames_on_every_public_case(slug: str) -> None:
    """A display program emits no output, so the generic test above proves nothing.

    Replay the LM-75 port writes through the panel model and compare committed
    frames pixel for pixel. For ``plotter`` that also pins Bresenham's
    direction-sensitivity: a case drawn B->A selects different pixels from A->B.
    """
    from randomfun2026solvers.lm1.display import frames_from_writes

    width, height = display_size(slug)
    for (case, rounds), (_same, expected) in zip(
        rounds_for_problem(slug), frames_for_problem(slug), strict=True
    ):
        res = Emulator(load(slug)).run(rounds, max_instructions=MAX_INSTRUCTIONS)
        got = frames_from_writes(res.display_writes, width=width, height=height)
        want = [frame for round_frames in expected for frame in round_frames]
        assert got == want, f"{slug}/{case}: frame mismatch"
        assert not res.output, f"{slug}/{case}: emitted output on a display problem"


def test_palette_asm_is_up_to_date_with_the_problem_json() -> None:
    assert available()["palette"].read_text(encoding="utf-8") == palette_source()


def test_the_emulator_estimate_for_a_20_round_plotter_load_is_optimistic() -> None:
    """20 rounds of a long segment, with *real* tape latency — and still optimistic.

    Store accesses are billed at ``ARCH.md`` §4.1's ``105 + 8.3N`` rather than the
    emulator's flat 6 ticks/word, which understates them ~30x. Even so this estimate
    comes out at ~3.8M against the 5M cap where the generated machine really takes
    ~4.78M for the same load: **the estimate is ~20% optimistic and is not the
    margin.** ``tests/test_lm1_display.py`` measures the engine, and finds that the
    genuinely worst legal 20-round load overruns the cap outright.

    Kept because it is the only tick figure available without Node, and because a
    regression big enough to break *this* bound is worth catching in the fast tier.
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
