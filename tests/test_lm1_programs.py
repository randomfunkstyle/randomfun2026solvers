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

#: Problems with **no LM-1 assembly program**. Read the name carefully: this set
#: says nothing about whether the problem is *solved*, only that `lm1/machine.py`
#: does not solve it, and it is wired to `test_the_expected_programs_exist` — which
#: asserts an `.asm` exists for every slug *not* listed here. Do not remove a slug
#: from it without adding the `.asm`.
#:
#: Three of its four members are in fact solved, by dataflow grids rather than by
#: programs, and beating every machine in this file by two to four orders of
#: magnitude (`littleman/DATAFLOW-SURVEY.md`; pinned in `tests/test_dataflow_grids.py`):
#: `memory` 32x32 / 19.7M (`memory_tape.build_v2(100)`, which emits
#: `littleman/programs/memory.man` byte for byte), `reverse-a-list` 21x21 / 483k and
#: `sort-numbers` 25x25 / 2.08M (both `value_ring.py`, pinned in
#: `tests/test_value_ring.py`). `subset-sum` is the only genuinely unsolved problem
#: in the set — a CPU build answers 6 of 7 public cases and is ~34x over the cap on
#: the seventh, because its wall is instruction issue rather than the STORE block.
#:
#: Historical note, since the original wording claimed otherwise: ARCH.md §4.1's
#: "blocked on the STORE block" was true when written and is now true of none of
#: them. The tape exists; what these four need is either an `.asm` (none has one) or,
#: for `subset-sum`, an issue path cheaper than fetch + decode + return.
#:
#: ``gradebook`` is *not* here any more: the STORE block exists (``machine.py``'s
#: tape), so a problem that needs indexed memory is only blocked on having a program
#: written against ``LDA``/``MOVA`` — see ``tests/test_lm1_gradebook.py``.
#:
#: ``sudoku-validity`` came off it for a different reason: it looked blocked because
#: its natural encoding is 3 x 9 x 9 = 243 set-membership flags against a tape that
#: caps at 103 slots, but as 27 one-cell bitmasks it needs only 36 addresses. What
#: unblocked it was an encoding change plus one opcode (`AND`), not a bigger tape.
BLOCKED = {
    "memory",
    "reverse-a-list",
    "sort-numbers",
    "subset-sum",
}

#: Problems graded on committed frames rather than on program output.
DISPLAY_PROBLEMS = {"plotter", "palette", "snake"}

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
        "snake",
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
        # `DECM n` fuses `LD n; SUBI 1; ST n` at the loop head *and* leaves ACC at
        # the pre-decrement value, so the end-of-string test comes free with it.
        "brackets": {"DECM", "DIVI", "MODI"},
        # LDA/MOVA in place of LDP/STP: keeping the address in ACC means the
        # `0`/`1` request literal never has to coexist with it, so tcp needs no
        # SPILL block at all (see tcp.asm's header).
        #
        # `INCM` is deliberately NOT here. It measured -0.9% on the public cases but
        # the judge scored that build 1,927,262,669 against 1,849,876,224 for this
        # one -- tcp is tape-bound, so removing an instruction re-times requests onto
        # worse ring phases. Reverted; see ARCH.md §4.1 on judging rather than
        # modelling a tape-bound program.
        "tcp": {"LDA", "MOVA"},
        # One packed cell per student: `AND` masks a field out of it (that is what
        # deletes the ids array and TOP's tie-break), `MUL`/`DIV` scale a grade by a
        # field weight the *operation* names, and `MOVA` is how the roster fills a
        # cell it addresses at runtime. `DIV` rather than `DIVI` because AVG divides
        # by N; `ADDI` was dropped to keep the count at 16 (see gradebook.asm).
        "gradebook": {"AND", "DIV", "MOVA", "MUL"},
        # `DSP p` picks a pipe from its operand, which nearest-pipe binding cannot
        # express; one opcode per LM-75 port gives each its own lane (see plotter.asm).
        # `MODI 1024` unpacks the cursor out of the word that also carries the
        # Bresenham error, which is what gets the inner loop to four tape accesses.
        # This is exactly 16 opcodes and so still a depth-4 trie — a 17th would add a
        # whole trie level to every instruction plus ~32 lane rows.
        "plotter": {"DSPA", "DSPD", "DSPS", "MODI", "NEG"},
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
        # snake keeps the whole game on cell indices (`y * 16 + x`, which is also the
        # display's ADDR word), so every extension here is either an indexed access or
        # a port: `LDA` reads the body FIFO's ring slot, `MOVA` writes it, `INCM`/`DECM`
        # move the two FIFO counters in one access each, `MODI` wraps the ring address
        # (free, where masking a stored counter would cost two more accesses a tick),
        # `DIV` is the wall test's runtime divisor, and `NEG` builds the two negative
        # direction deltas the ROM cannot hold.
        # snake-ring moves the body into a coprocessor ring, so every *indexed* opcode
        # disappears with it: `SND` is the whole interface, `MODI`/`DIV` are the wall
        # test, `INCM` grows the length in one read. Exactly sixteen opcodes, i.e. a
        # depth-4 trie — `NEG` became `LDI 0`/`SUBI n` and the ending became a blocking
        # `IN`, because a seventeenth costs a trie level plus its lane rows (measured:
        # 158x167 against 121x136).
        "snake-ring": {"DIV", "INCM", "MODI", "SND"},
        "snake": {
            "DECM",
            "DIV",
            "DSPA",
            "DSPD",
            "DSPS",
            "INCM",
            "LDA",
            "MODI",
            "MOVA",
            "NEG",
        },
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


def test_plotter_draws_exactly_bresenham_on_every_public_case() -> None:
    """The public cases cover 6 shapes; the restructured loop needs more than that.

    ``plotter.asm`` no longer runs the spec's pseudocode — it carries a packed
    ``2*err*1024 + addr - THR`` and branches on its sign — so "matches the committed
    frames" only proves the six shapes anyone thought to commit. This runs the real
    program on the emulator over every corner combination plus 400 pseudo-random
    segments and compares against the spec's own loop, transcribed below.

    Direction sensitivity is the point of the corner grid: A->B and B->A select
    different pixels, and a rewrite that quietly symmetrised the error term would pass
    the public cases and fail here.
    """
    import random

    from randomfun2026solvers.lm1.display import frames_from_writes

    width, height = display_size("plotter")

    def bresenham(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err, out = dx + dy, []
        while True:
            out.append((x0, y0))
            if (x0, y0) == (x1, y1):
                return out
            e2 = 2 * err
            if e2 >= dy:
                err, x0 = err + dy, x0 + sx
            if e2 <= dx:
                err, y0 = err + dx, y0 + sy

    edges = ((0, 1, 15, 31), (0, 1, 11, 23))
    segments = [
        (x0, y0, x1, y1) for x0 in edges[0] for y0 in edges[1] for x1 in edges[0] for y1 in edges[1]
    ]
    rng = random.Random(7)
    segments += [
        (rng.randrange(width), rng.randrange(height), rng.randrange(width), rng.randrange(height))
        for _ in range(400)
    ]

    prog = load("plotter")
    for i in range(0, len(segments), 20):  # 20 rounds is the constraints' limit
        chunk = segments[i : i + 20]
        res = Emulator(prog).run(
            [Round(input=s) for s in chunk], max_instructions=MAX_INSTRUCTIONS
        )
        got = frames_from_writes(res.display_writes, width=width, height=height)
        assert len(got) == len(chunk), res.reason
        for segment, frame in zip(chunk, got, strict=True):
            grid = [[0] * width for _ in range(height)]
            for x, y in bresenham(*segment):
                grid[y][x] = 15
            want = ["".join(format(v, "x") for v in row) for row in grid]
            assert list(frame) == want, f"{segment}: pixels differ from Bresenham"


def test_palette_asm_is_up_to_date_with_the_problem_json() -> None:
    assert available()["palette"].read_text(encoding="utf-8") == palette_source()


def test_the_plotter_inner_loop_stays_at_four_tape_accesses_a_pixel() -> None:
    """The one thing about ``plotter`` that must not regress, counted not modelled.

    **This test deliberately does not assert a tick figure.** The one it used to
    assert — the emulator's estimate with store accesses billed at ``ARCH.md``
    §4.1's ``105 + 8.3N`` — read 3.8M for a 20-round load the engine ran in 5.31M
    against a 5,000,000 cap, and that is exactly how a machine 6% over the cap
    shipped as "1.05x under" it. ``105 + 8.3N`` understates the rotating tape by an
    order of magnitude, and no arithmetic over it is a margin.
    ``tests/test_lm1_display.py::test_the_worst_legal_20_round_load_fits_the_step_cap_on_the_engine``
    is the margin; it runs the engine.

    What *is* worth pinning without Node is the quantity the rewrite bought, because
    it is a property of the program rather than of the hardware: tape accesses per
    pixel. At ~316 ticks each on the engine against ~45 for an instruction, they were
    75% of the bill at ~20 per pixel. The packed single-add loop makes it 4, and 4 is
    the floor for this shape (`ST Q`, `SUB ADDR1`, `LD Q`, `ADD DEL`).
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

    def run(segment: tuple[int, ...]) -> tuple[int, int]:
        store = Counting()
        res = Emulator(load("plotter"), store=store).run(
            [Round(input=segment)] * 20, max_instructions=MAX_INSTRUCTIONS
        )
        assert res.reason == "input-exhausted", res.reason
        return store.ops, res.words_skipped

    # Measured as a *slope*, so the per-round setup cancels and no arithmetic here
    # depends on knowing how big it is. The two shapes must agree in both step signs
    # and in the major axis, or their setups differ by the `NEG` arms and the slope
    # picks that up as loop cost.
    long_ops, long_skips = run((0, 0, 31, 1))  # 32 pixels, x-major, both steps +1
    short_ops, short_skips = run((0, 0, 1, 1))  # 2 pixels, ditto
    extra = 20 * 30
    assert (long_ops - short_ops) / extra == 4, (long_ops, short_ops)

    # The per-round setup, whatever is left once the loop is accounted for (one full
    # iteration plus the two accesses `final` exits through). It runs 20 times at ~316
    # ticks an access, i.e. ~11% of the 20-round bill, so it is worth its own ceiling.
    setup = short_ops // 20 - (4 + 2)
    assert setup <= 34, f"{setup} tape accesses a round of setup"

    # The tape is only half the story: the other half is the ROM lap a backward jump
    # pays (8 ticks a word, ARCH.md §5.4), which is why the loop is unrolled 4x — one
    # lap per four pixels rather than per pixel.
    assert (long_skips - short_skips) / extra < 45, (long_skips, short_skips)
