"""Tests for the LM-1 machine generator (``lm1/rom.py``, ``lm1/machine.py``).

Three tiers, cheapest first:

* pure-Python invariants of the ROM encoding and the word re-encoding — these pin
  the two bugs that are *invisible* in the ASCII (backtick columns and the
  fixed-width rescaling of jump counts);
* generation of the checked-in machines, which runs the engine's structural
  analysis and so also asserts every pipe binding (``ARCH.md`` §7.1) and the pipe
  count;
* the full public-case runs, behind ``LM1_SLOW=1`` — a few minutes of wasm.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1 import machine, programs, rom  # noqa: E402
from randomfun2026solvers.lm1.asm import assemble  # noqa: E402
from randomfun2026solvers.lm1.isa import Sem  # noqa: E402

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)
slow = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the full public-case sweeps",
)

#: The two graded problems this generator exists for, and the tape size each needs.
#: Sized from the *problem constraints*, not the public data: ``tcp`` allows n=48,
#: so addresses reach BUF+47 = 51 even though no public case goes past 35.
#: The non-display programs this file covers; the display ones (``plotter``,
#: ``palette``) need a panel and a ROM fold, so they go through ``build_for`` in
#: ``test_lm1_display.py`` instead. ``gradebook`` likewise has its own file.
#:
#: brackets/tcp are sized to the address each actually reaches, not to the
#: constraint: the tape is a rotating ring, so a slot costs real ticks (~114/case
#: on brackets, ~999 on tcp) at no footprint change.
TARGETS = {"brackets": 5, "tcp": 51, "sudoku-validity": 37}


# ── ROM encoding ─────────────────────────────────────────────────────────────
def test_group_is_palindromic_in_its_backtick_offsets() -> None:
    """Reversal must map a group's backtick columns onto themselves.

    A westbound serpentine row is emitted reversed, so if the offsets moved, one
    column would hold backticks in rows 1 and 3 and pair them *across* row 2's
    ``s`` — a non-digit between a vertical pair, which is a load error.
    """
    for width in range(1, 5):
        g = rom.group_cells(7, width)
        offsets = {i for i, ch in enumerate(g) if ch == "`"}
        mirrored = {len(g) - 1 - i for i in offsets}
        assert offsets == mirrored, (width, g)


@pytest.mark.parametrize("rows", [1, 2, 3, 5, 8])
def test_every_backtick_column_holds_one_per_literal_row(rows: int) -> None:
    """So vertical pairs are (row 1, row 2), (row 3, row 4), … — empty, hence nops."""
    words = [0, 12, 345, 6, 78, 9, 100, 2, 33, 444, 5, 60]
    lay = rom.build_rom(words, rows=rows)
    literal_rows = range(1, lay.rows_used + 1)
    columns: dict[int, list[int]] = {}
    for (x, y), ch in lay.cells.items():
        if ch == "`" and y in literal_rows:
            columns.setdefault(x, []).append(y)
    for x, ys in columns.items():
        assert sorted(ys) == list(literal_rows), f"column {x} has backticks on {sorted(ys)}"


def test_rom_rejects_negative_words() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        rom.build_rom([1, -2])


def test_rows_for_budget_trades_width_for_height() -> None:
    wide = rom.rows_for_budget(200, 3, budget=200)
    narrow = rom.rows_for_budget(200, 3, budget=40)
    assert narrow > wide >= 1


# ── word re-encoding ─────────────────────────────────────────────────────────
def test_words_are_fixed_width_two_per_instruction() -> None:
    """The `>rbr` fetch always takes two words, so the image must be padded.

    Left variable-width, ``LDI 42 / OUT / HALT`` pairs up as ``(LDI, 42),
    (OUT, HALT)`` and emits 42 forever without ever halting.
    """
    prog = assemble("LDI 42\nOUT\nHALT\n")
    p = machine.plan(prog)
    words = machine.rom_words(prog, p)
    assert prog.P == 4  # the assembler's abstract, variable-width form
    assert len(words) == 2 * len(prog.instrs) == 6
    assert words[1::2] == [42, 0, 0]  # unused operands are zero, not the next opcode


def test_jump_counts_are_rescaled_into_the_fixed_width_image() -> None:
    """Instruction k lives at word 2k, so a skip is 2 * (instructions to drop)."""
    prog = assemble("JMP skip\nLDI 1\nOUT\nskip: LDI 2\nOUT\nHALT\n")
    p = machine.plan(prog)
    words = machine.rom_words(prog, p)
    jmp = next(i for i in prog.instrs if i.sem is Sem.JUMP)
    # target is instruction 3, the jump is instruction 0 -> drop 1 and 2
    assert words[1] == 2 * 2
    assert jmp.operand != words[1]  # the assembler's count was in the abstract form


def test_backward_jump_is_a_whole_lap_minus_the_body() -> None:
    prog = assemble("back: LDI 1\nOUT\nJMP back\nHALT\n")
    p = machine.plan(prog)
    words = machine.rom_words(prog, p)
    # 4 instructions; jump is #2, target #0 -> (0 - 2 - 1) mod 4 = 1 instruction
    assert words[5] == 2 * 1


def test_store_address_zero_is_rejected() -> None:
    """Slot 0 is sign-ambiguous: the operation is encoded in the address's sign."""
    prog = assemble("LDI 1\nST 0\nHALT\n")
    p = machine.plan(prog)
    with pytest.raises(machine.MachineError, match="addresses start at 1"):
        machine.rom_words(prog, p)


def test_opcode_numbers_come_from_the_lane_rows() -> None:
    """The trie sorts leaves bit-reversed, so choosing a row chooses the number."""
    prog = programs.load("brackets")
    p = machine.plan(prog)
    assert p.k == 4 and p.lanes == 16
    assert all(0 <= n < 16 for n in p.number.values())
    assert len(set(p.row.values())) == len(p.row)  # one lane per opcode
    # IN hugs the north-side input pipe, OUT the output pipe at the bottom
    ins = [m for m in p.sem if p.sem[m] is Sem.INPUT]
    outs = [m for m in p.sem if p.sem[m] is Sem.OUTPUT]
    assert p.row[ins[0]] == 1
    assert p.row[outs[0]] == 2 * p.lanes - 1


def test_every_used_opcode_has_hardware() -> None:
    for slug in TARGETS:
        for op in programs.load(slug).ops_used:
            structured = op.sem in {Sem.JUMP, Sem.BR_ZERO, Sem.BR_NEG}
            assert machine.hw_micro(op.sem) or structured, op.mnemonic


# ── generation (runs the engine's structural analysis) ────────────────────────
@node_required
@pytest.mark.parametrize(("slug", "tape_n"), sorted(TARGETS.items()))
def test_machine_generates_and_every_pipe_binds(slug: str, tape_n: int) -> None:
    """``build`` raises unless every ``r``/``s`` is *strictly* nearest its own pipe.

    It also counts the pipes the engine finds. Both checks exist because a
    mis-binding is invisible until the program reads the wrong words — the tie
    between the ROM pipe and the tape's response pipe that stalled every jump was
    caught exactly here.
    """
    m = machine.build(programs.load(slug), tape_n=tape_n)
    assert m.width > 0 and m.height > 0
    assert m.plan.k == 4
    assert m.tape_n == tape_n
    assert "@" in "".join(m.rows)
    # A display problem gets a panel and no `O` room: SPEC.md makes emitting any
    # program output an error there.
    grid = "".join(m.rows)
    display = any(s.value.startswith("display") for s in m.plan.sem.values())
    assert (":" in grid and "=" in grid) is display
    assert ("O" in grid) is not display


@node_required
def test_checked_in_grids_match_the_generator() -> None:
    """The ``.man`` files under ``tasks/solutions`` are generated, not hand-edited."""
    for slug, tape_n in TARGETS.items():
        path = REPO / "tasks" / "solutions" / f"{slug}_cpu.man"
        expected = "\n".join(machine.build(programs.load(slug), tape_n=tape_n).rows) + "\n"
        assert path.read_text(encoding="utf-8") == expected, (
            f"{path.name} is stale; regenerate with "
            f"`python -m randomfun2026solvers.lm1.machine {slug} --out {path}`"
        )


# ── full runs ────────────────────────────────────────────────────────────────
@node_required
@slow
@pytest.mark.parametrize(("slug", "tape_n"), sorted(TARGETS.items()))
def test_public_cases_pass_on_the_real_interpreter(slug: str, tape_n: int) -> None:
    from randomfun2026solvers import optimize

    path = REPO / "tasks" / "solutions" / f"{slug}_cpu.man"
    # sudoku-validity's 81-round case settles at ~2.3M, so 3M left too little
    # headroom for it to survive an unrelated slowdown.
    res = optimize.verify(path, slug, tick_cap=5_000_000)
    failed = [c.name for c in res.cases if not c.passed]
    assert res.passed, f"{slug}: {failed}"
