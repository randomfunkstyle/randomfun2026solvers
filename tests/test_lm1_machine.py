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
# One mechanism for "too slow for the default run", registered in pyproject.
slow = pytest.mark.slow

#: Every task the generator can build, with the tape size each needs — read from
#: the generator so the two cannot drift apart. Sizes come from the *problem
#: constraints*, not the public data: ``tcp`` allows n=48, so addresses reach
#: BUF+47 = 51 even though no public case goes past seq 35.
#:
#: The sizes are **highest address reached + 1**, and the ``+ 1`` is load-bearing:
#: a tape of exactly 51 crashes tcp at n=48 after 32 of 48 values (see
#: :func:`test_tcp_survives_the_constraint_limit`), and plotter hit the same wall
#: with 11 live values against ``tape_n=11``.
TARGETS = machine.TAPE_SIZE


def test_adapter_uses_u_to_free_its_old_rightmost_column() -> None:
    """Its sole west-side input lets receive and east steering share one cell."""
    assert machine.ADAPTER_W == 12
    assert {len(row) for row in machine._ADAPTER} == {machine.ADAPTER_W}
    assert {len(row) for row in machine._Y_ADAPTER} == {machine.ADAPTER_W}
    assert machine._ADAPTER[1].startswith("UX")
    assert machine._ADAPTER[3].endswith("@<")
    assert "Ns1srs" in machine._Y_ADAPTER[0]
    assert "s0s" in machine._Y_ADAPTER[2]


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


def test_a_tape_one_slot_too_small_is_rejected() -> None:
    """``tape_n`` is a slot *count*, so slot ``tape_n`` does not exist.

    Addressing it does not fault: the tape's worker walks past the end of its own
    ring and the machine stalls with no output at all, which looks exactly like a
    logic bug in the program. Cheap to catch here, and every recorded ``TAPE_SIZE``
    is checked against its program below.
    """
    prog = assemble("LDI 1\nST 4\nHALT\n")
    with pytest.raises(machine.MachineError, match="only reaches"):
        machine.build(prog, tape_n=4)
    machine.build(prog, tape_n=5)  # one more slot and it is legal


def test_every_recorded_tape_size_clears_its_programs_top_address() -> None:
    for slug, tape_n in machine.TAPE_SIZE.items():
        top = machine._highest_address(programs.load(slug))
        assert top < tape_n, f"{slug}: top slot {top} against a {tape_n}-slot tape"


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
    # build_for supplies the tape size *and* the panel resolution, which a display
    # program now requires: the panel is the problem's, not the program's.
    m = machine.build_for(slug)
    assert m.width > 0 and m.height > 0
    # The trie's depth is ceil(log2 |opcodes used|) — derived, not fixed at 4. It is
    # 3 for `matmul`, whose eight opcodes are what let the STREAM block replace the
    # inner loop; asserting a constant here would forbid ever getting smaller.
    used = len(m.plan.number)
    assert m.plan.k == max(1, (used - 1).bit_length())
    assert m.plan.lanes == 1 << m.plan.k >= used
    assert m.tape_n == tape_n
    assert "@" in "".join(m.rows)
    # A display problem gets a panel and no `O` room: SPEC.md makes emitting any
    # program output an error there. A STREAM problem has an `O` room too, but the
    # block owns it rather than the CPU (see stream.py).
    #
    # Taken from the *problem*, not from the CPU's opcodes, because a coprocessor may
    # own the panel: `snake-ring`'s CPU has no `DSP*` lane at all and its machine still
    # has to have exactly one panel (see snake_unit.py).
    grid = "".join(m.rows)
    display = machine.display_for(slug) is not None
    assert (":" in grid and "=" in grid) is display
    assert ("O" in grid) is not display


@node_required
def test_checked_in_grids_match_the_generator() -> None:
    """The ``.man`` files under ``tasks/solutions`` are generated, not hand-edited."""
    for slug in TARGETS:
        path = REPO / "tasks" / "solutions" / f"{slug}_cpu.man"
        expected = "\n".join(machine.build_for(slug).rows) + "\n"
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


@node_required
def test_tcp_survives_the_constraint_limit() -> None:
    """n=48 with the maximum legal delay — the case a tape of 51 slots crashes on.

    The public cases only reach seq ~35, so nothing there exercises the top of the
    buffer. Sized to exactly its highest address (BUF + 47 = 51) the machine dies
    ``fatal: wall`` after 32 of the 48 values, with the first 32 correct; 52 and 53
    both pass. That is why ``TARGETS``/``TAPE_SIZE`` use highest-address **+ 1**.

    The delay pattern is reversed blocks of 16 — the most out-of-order a stream can
    be without tripping the max-delay rule, since a packet 16 or more above the
    wanted seq must answer -1 and stop.
    """
    from randomfun2026solvers.littleman import Littleman

    n = 48
    order: list[int] = []
    for base in range(0, n, 16):
        block = list(range(base, min(base + 16, n)))
        block.reverse()
        order += block
    values = {s: 1 + (s * 37) % 999 for s in range(n)}

    buffered: dict[int, int] = {}
    want = 0
    rounds_in: list[str] = []
    rounds_out: list[str] = []
    for i, seq in enumerate(order):
        rounds_in.append(f"{n} {seq} {values[seq]}" if i == 0 else f"{seq} {values[seq]}")
        buffered[seq] = values[seq]
        drained = []
        while want in buffered:
            drained.append(buffered[want])
            want += 1
        rounds_out.append(" ".join(str(v) for v in drained))

    snap = Littleman().judge(
        REPO / "tasks" / "solutions" / "tcp_cpu.man",
        input=" / ".join(rounds_in),
        expected=" / ".join(rounds_out),
        max_ticks=5_000_000,
    )
    assert snap.fatal is None, f"fatal: {snap.fatal}"
    assert list(snap.output) == [values[s] for s in range(n)]
    assert snap.step < 5_000_000, f"{snap.step:,} ticks against the 5,000,000 step cap"
