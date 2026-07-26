"""The LM-1 interpreter for `little-little-man`: it assembles, and it draws.

The emulator is the development oracle here — it replays the CPU's port writes
through the LM-75 model, so a frame diff is exact — but it models neither the
tape's cost nor the judge's frame gating, so a green run here is *necessary and
not sufficient*.  The engine sweep lives in the slow tier.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from randomfun2026solvers import llm_lm1, llm_sim
from randomfun2026solvers.lm1 import programs
from randomfun2026solvers.lm1.asm import assemble
from randomfun2026solvers.lm1.display import frames_from_writes
from randomfun2026solvers.lm1.emulator import Emulator

SLUG = "little-little-man"
MAX_INSTRUCTIONS = 4_000_000


@pytest.fixture(scope="module")
def program():
    text, slots = llm_lm1.build_asm()
    prog = assemble(text, name=SLUG)
    return prog, slots


def _cases() -> list[str]:
    return [name for name, _ in programs.rounds_for_problem(SLUG)]


def test_the_tables_agree_with_the_reference_interpreter() -> None:
    """Every glyph the reference gives a colour must get the same one here."""
    for code, cls in llm_lm1.CLASS_OF_BYTE.items():
        colour = llm_lm1.COLOUR_OF_CLASS[cls]
        ch = chr(code)
        if ch in "@. ":
            assert colour == llm_sim.COLOUR_EMPTY
        elif ch.isdigit():
            assert colour == llm_sim.COLOUR_DIGIT
        elif ch == "M":
            assert colour == llm_sim.COLOUR_M
        elif ch in "+-":
            assert colour == llm_sim.COLOUR_ARITH
        elif ch in "<>^vVX":
            assert colour == llm_sim.COLOUR_HEADING
        elif ch in "sr":
            assert colour == llm_sim.COLOUR_PIPE_OP
    assert llm_lm1.COLOUR_OF_CLASS[llm_lm1.CLS_WALL] == llm_sim.COLOUR_WALL
    assert llm_lm1.COLOUR_OF_CLASS[llm_lm1.CLS_PIPE] == llm_sim.COLOUR_PIPE
    assert llm_lm1.COLOUR_VALUE == llm_sim.COLOUR_PIPE_FULL
    assert llm_lm1.COLOUR_MAN == llm_sim.COLOUR_MAN


def test_the_arrow_and_heading_ranges_are_eight_apart() -> None:
    """The fix-up sweep turns a heading into an arrowhead by adding a constant."""
    assert llm_lm1.CLS_ARROW - llm_lm1.CLS_DIR == 8
    for d in range(4):
        assert llm_lm1.COLOUR_OF_CLASS[llm_lm1.CLS_ARROW + d] == llm_lm1.COLOUR_PIPE


def test_program_assembles_and_stays_inside_its_budgets(program) -> None:
    prog, slots = program
    assert prog.P > 0
    assert slots <= 512, f"{slots} store slots"
    used = {op.mnemonic for op in prog.ops_used}
    assert {"DSPA", "DSPD", "DSPS", "IN"} <= used, used
    # The decode trie is full: 17 opcodes costs a whole extra 32-lane band, which
    # measured at +32 rows and +43% on every instruction's issue cost.  This is a
    # standing reminder of the cliff, not a claim that we are under it yet.
    assert len(used) <= 20, sorted(used)


@pytest.mark.parametrize("case", _cases())
def test_every_public_case_draws_every_frame(program, case: str) -> None:
    prog, _slots = program
    width, height = programs.display_size(SLUG)
    rounds = dict(programs.rounds_for_problem(SLUG))[case]
    want = [f for rf in dict(programs.frames_for_problem(SLUG))[case] for f in rf]
    res = Emulator(prog).run(rounds, max_instructions=MAX_INSTRUCTIONS)
    got = frames_from_writes(res.display_writes, width=width, height=height)
    assert len(got) == len(want), f"{len(got)} frames committed, expected {len(want)}"
    for i, (g, e) in enumerate(zip(got, want, strict=True)):
        assert g == e, f"frame {i}"


@pytest.mark.parametrize("case", _cases())
def test_the_reference_interpreter_agrees_with_the_problem(case: str) -> None:
    """A guard on the oracle itself: the frames the test above compares against."""
    rounds = dict(programs.rounds_for_problem(SLUG))[case]
    want = [f for rf in dict(programs.frames_for_problem(SLUG))[case] for f in rf]
    got = llm_sim.run_rounds_from_inputs([[str(v) for v in r.input] for r in rounds])
    assert got == want


# ── the grid ──────────────────────────────────────────────────────────────────
GRID = Path(__file__).resolve().parents[1] / "tasks" / "solutions" / "little-little-man_cpu.man"


@pytest.fixture(scope="module")
def built():
    machine, program, _text = llm_lm1.build_machine()
    return machine, program


def test_checked_in_grid_still_matches_the_generator(built) -> None:
    machine, _program = built
    assert GRID.read_text() == "\n".join(machine.rows) + "\n"


def test_footprint_is_what_the_fold_sweep_found(built) -> None:
    machine, _program = built
    assert (machine.width, machine.height) == (203, 204)


def test_the_tape_is_sized_to_the_program_not_the_public_cases(built) -> None:
    _machine, program = built
    _text, slots = llm_lm1.build_asm()
    # 4 <= W, H <= 16, so the grid is 256 cells whatever the case holds.
    assert slots > llm_lm1.PANEL * llm_lm1.PANEL
    assert program.P > 0


@pytest.mark.slow
def test_the_grid_passes_every_public_case_on_the_engine() -> None:
    from randomfun2026solvers import optimize

    result = optimize.verify(GRID, SLUG, tick_cap=50_000_000)
    failed = [(c.name, c.detail) for c in result.cases if not c.passed]
    assert not failed, failed
    assert len(result.cases) == 14
    # Measured 20,275,186 average / 31,809,643 worst against a 50M cap.  The margin
    # is the point of the assertion: a change that doubles the worst case fails a
    # private test rather than merely scoring badly.
    #
    # The two-tier build (``hot=HOT``) measures 8,605,207 / 13,676,774 here and on
    # the native validator, and was **rejected by the judge at 4/28** — see
    # ``LLM-DESIGN.md``.  Until that divergence is explained, the shipped machine is
    # the single-tape one, and this assertion guards *it*.
    assert max(c.ticks for c in result.cases) < 40_000_000


def test_the_registered_asm_still_matches_the_generator() -> None:
    """`lm1/programs/little-little-man.asm` is generated; keep it from drifting."""
    from randomfun2026solvers.lm1 import programs as lm1_programs

    text, _slots = llm_lm1.build_asm()
    assert lm1_programs.available()[SLUG].read_text() == text
