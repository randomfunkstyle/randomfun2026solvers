"""``sudoku-validity``: the auditor program, its hardware, and its cost.

The generic sweep in ``test_lm1_programs.py`` only compares each case's *flat*
output, which for a round-based problem is weaker than it looks — a program that
emitted its verdicts one round late would still concatenate correctly on a case
whose answers are all ``1``. So the cases are asserted **round by round** here,
and separately re-run on every prefix, which is the only way to catch a verdict
that depends on input the judge has not released yet.

The public data is also lopsided: five of six cases are one long valid prefix
followed by a single duplicate, and none of them isolates a *column*-only or
*box*-only collision from a row collision on the same cell. The synthetic grids
below do, one failure mode at a time.
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

from randomfun2026solvers.lm1 import machine, programs  # noqa: E402
from randomfun2026solvers.lm1.emulator import TICK_CAP, Emulator, Round  # noqa: E402
from randomfun2026solvers.lm1.isa import Sem  # noqa: E402
from randomfun2026solvers.lm1.store import DictStore  # noqa: E402

SLUG = "sudoku-validity"
GRID = REPO / "tasks" / "solutions" / f"{SLUG}_cpu.man"
MAX_INSTRUCTIONS = 3_000_000

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)
slow = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the full public-case sweeps",
)

#: ``ARCH.md`` §4.1's real tape latency, which the emulator's flat 6 ticks/word
#: understates by ~30x. ``machine.TAPE_SIZE`` picks N.
TAPE_N = machine.TAPE_SIZE[SLUG]
STORE_TICKS = 105 + 8.3 * TAPE_N


class CountingStore(DictStore):
    """A store that counts transactions, so a test can bill them realistically."""

    def __init__(self) -> None:
        super().__init__()
        self.ops = 0

    def _read(self, addr: int) -> int:
        self.ops += 1
        return super()._read(addr)

    def _write(self, addr: int, value: int) -> None:
        self.ops += 1
        super()._write(addr, value)


# ── reference model ──────────────────────────────────────────────────────────
def audit(cells: list[tuple[int, int, int]]) -> list[int]:
    """The verdicts a correct auditor emits for ``cells``, in order.

    Stops after the first ``0``: the problem ends the case there ("Your program
    only needs to output 0 once").
    """
    rows: list[set[int]] = [set() for _ in range(9)]
    cols: list[set[int]] = [set() for _ in range(9)]
    boxes: list[set[int]] = [set() for _ in range(9)]
    out: list[int] = []
    for r, c, v in cells:
        b = (r // 3) * 3 + c // 3
        if v in rows[r] or v in cols[c] or v in boxes[b]:
            out.append(0)
            break
        rows[r].add(v)
        cols[c].add(v)
        boxes[b].add(v)
        out.append(1)
    return out


def valid_grid() -> list[list[int]]:
    """A solved 9x9 grid, by the standard shifted-band construction."""
    return [[(3 * (r % 3) + r // 3 + c) % 9 + 1 for c in range(9)] for r in range(9)]


def rounds_of(cells: list[tuple[int, int, int]]) -> list[Round]:
    """One round per cell, each expecting the reference verdict."""
    verdicts = audit(cells)
    return [
        Round(input=(r, c, v), expected=(verdict,))
        for (r, c, v), verdict in zip(cells, verdicts, strict=False)
    ]


def run(cells: list[tuple[int, int, int]]) -> tuple[tuple[int, ...], CountingStore, int]:
    store = CountingStore()
    res = Emulator(programs.load(SLUG), store=store).run(
        rounds_of(cells), max_instructions=MAX_INSTRUCTIONS
    )
    assert res.reason in ("halted", "input-exhausted"), res.reason
    return res.output, store, res.ticks


def test_the_reference_grid_really_is_a_solved_sudoku() -> None:
    grid = valid_grid()
    assert all(sorted(row) == list(range(1, 10)) for row in grid)
    assert all(sorted(col) == list(range(1, 10)) for col in zip(*grid, strict=True))
    for br in (0, 3, 6):
        for bc in (0, 3, 6):
            box = [grid[br + i][bc + j] for i in range(3) for j in range(3)]
            assert sorted(box) == list(range(1, 10))


# ── the program and the opcodes it costs ─────────────────────────────────────
def test_program_fits_the_decode_trie_it_was_budgeted() -> None:
    """16 lanes is the whole budget: a 17th opcode forces k=5 and 32 lanes."""
    prog = programs.load(SLUG)
    plan = machine.plan(prog)
    assert len(prog.ops_used) <= 16, sorted(op.mnemonic for op in prog.ops_used)
    assert plan.k == 4 and plan.lanes == 16
    # No BRN and no JMP: both would add a structures-band slab, and each slab is
    # 13 columns of CPU *and* 13 ticks on every instruction's walk back to fetch.
    sems = {op.sem for op in prog.ops_used}
    assert Sem.BR_NEG not in sems and Sem.JUMP not in sems
    assert set(prog.ext_ops) == {"DIVI", "MODI", "LDP", "STP"}


def test_every_used_opcode_has_hardware() -> None:
    for op in programs.load(SLUG).ops_used:
        structured = op.sem in {Sem.JUMP, Sem.BR_ZERO, Sem.BR_NEG}
        assert machine.hw_micro(op.sem) or structured, op.mnemonic


def test_indexed_memory_needs_no_spill_block() -> None:
    """``LDP``/``STP`` are drawn with the sign-biased protocol, so no SPILL pipe.

    ``isa.py`` spells both with a spill slot; the hardware here does not, and the
    generator would refuse to lay a SPILL glyph at all — this is what makes the
    program buildable.
    """
    for sem in (Sem.LOAD_IND, Sem.STORE_IND):
        micro = machine.hw_micro(sem)
        assert micro, sem
        assert all(glyph not in "01" for glyph, _ in micro), micro
        assert sem in machine.MEMORY_SEMS


def test_the_tape_is_sized_to_the_slots_the_program_actually_uses() -> None:
    """27 unit masks plus three cursors: 30 slots, and an access costs 133 + 4.75N."""
    _, store, _ = run([(r, c, valid_grid()[r][c]) for r in range(9) for c in range(9)])
    assert max(store.cells) == 30
    assert min(store.cells) >= 1  # slot 0 is sign-ambiguous and must stay unused
    assert TAPE_N >= 30


# ── public cases, round by round ─────────────────────────────────────────────
PUBLIC = programs.problem_json(SLUG)["publicTestData"]
CASE_IDS = [case["name"] for case in PUBLIC]


def _cells(case: dict) -> list[tuple[int, int, int]]:
    return [tuple(int(t) for t in r["in"]) for r in case["rounds"]]  # type: ignore[misc]


@pytest.mark.parametrize("case", PUBLIC, ids=CASE_IDS)
def test_public_case_verdict_is_right_in_every_round(case: dict) -> None:
    cells = _cells(case)
    want = [int(r["out"][0]) for r in case["rounds"]]
    assert audit(cells) == want, "the reference model disagrees with the problem JSON"

    got, _, _ = run(cells)
    assert len(got) == len(want), f"{len(got)} verdicts for {len(want)} rounds"
    for i, ((r, c, v), expected) in enumerate(zip(cells, want, strict=True)):
        assert got[i] == expected, f"round {i} ({r} {c} {v}): got {got[i]}, want {expected}"


@pytest.mark.parametrize("case", PUBLIC, ids=CASE_IDS)
def test_public_case_answers_each_prefix_without_looking_ahead(case: dict) -> None:
    """Feed only the first k rounds; the verdicts must not change.

    Round k+1's input is withheld until round k's output lands (``GRADING.md``),
    so a program that batched its answers would deadlock here rather than pass.
    """
    cells = _cells(case)
    want = [int(r["out"][0]) for r in case["rounds"]]
    for k in range(1, len(cells) + 1):
        got, _, _ = run(cells[:k])
        assert list(got) == want[:k], f"prefix of {k} round(s)"


# ── one failure mode at a time ───────────────────────────────────────────────
def test_a_full_valid_grid_answers_one_every_round() -> None:
    """The worst case for an early-exit auditor: nothing ever short-circuits."""
    grid = valid_grid()
    cells = [(r, c, grid[r][c]) for r in range(9) for c in range(9)]
    got, _, _ = run(cells)
    assert list(got) == [1] * 81


def test_a_valid_grid_delivered_out_of_order_still_answers_one_every_round() -> None:
    """No cell is delivered twice, but the order is arbitrary (``io`` says so)."""
    grid = valid_grid()
    cells = [(r, c, grid[r][c]) for r in range(9) for c in range(9)]
    scrambled = cells[::7] + [x for i, x in enumerate(cells) if i % 7]
    assert sorted(scrambled) == sorted(cells)
    got, _, _ = run(scrambled)
    assert list(got) == [1] * 81


def test_duplicate_in_a_row_only() -> None:
    """Same row, different column, different box — so only the row check can fire."""
    cells = [(4, 0, 7), (4, 8, 7)]
    assert cells[0][1] // 3 != cells[1][1] // 3  # different boxes
    got, _, _ = run(cells)
    assert list(got) == [1, 0]


def test_duplicate_in_a_column_only() -> None:
    """Same column, different row, different box."""
    cells = [(0, 5, 3), (8, 5, 3)]
    assert cells[0][0] // 3 != cells[1][0] // 3
    got, _, _ = run(cells)
    assert list(got) == [1, 0]


def test_duplicate_in_a_box_only() -> None:
    """Same box, different row *and* different column."""
    cells = [(3, 3, 9), (4, 4, 9)]
    assert (cells[0][0] // 3, cells[0][1] // 3) == (cells[1][0] // 3, cells[1][1] // 3)
    got, _, _ = run(cells)
    assert list(got) == [1, 0]


def test_a_row_and_a_column_that_only_share_a_digit_are_both_fine() -> None:
    """The three units must not be conflated: same digit, no shared unit."""
    got, _, _ = run([(0, 0, 5), (4, 4, 5), (8, 8, 5)])
    assert list(got) == [1, 1, 1]


@pytest.mark.parametrize(
    ("name", "cells"),
    [
        ("both corners, digit 1", [(0, 0, 1), (8, 8, 1)]),
        ("both corners, digit 9", [(0, 0, 9), (8, 8, 9)]),
        ("first row, last row", [(0, 4, 9), (8, 4, 9)]),
        ("last column twice", [(3, 8, 1), (5, 8, 1)]),
        ("box 8 diagonal", [(6, 6, 4), (8, 8, 4)]),
        ("box 0 anti-diagonal", [(0, 2, 6), (2, 0, 6)]),
    ],
)
def test_extreme_coordinates_and_digits(name: str, cells: list[tuple[int, int, int]]) -> None:
    got, _, _ = run(cells)
    assert list(got) == audit(cells), name


def test_every_unit_is_tracked_independently() -> None:
    """Fill one whole unit of each kind, then collide inside it — 27 units, 27 probes."""
    for r in range(9):
        cells = [(r, c, c + 1) for c in range(8)] + [(r, 8, 1)]
        assert list(run(cells)[0]) == [1] * 8 + [0], f"row {r}"
    for c in range(9):
        cells = [(r, c, r + 1) for r in range(8)] + [(8, c, 1)]
        assert list(run(cells)[0]) == [1] * 8 + [0], f"column {c}"
    for b in range(9):
        br, bc = (b // 3) * 3, (b % 3) * 3
        inside = [(br + i // 3, bc + i % 3) for i in range(9)]
        cells = [(r, c, i + 1) for i, (r, c) in enumerate(inside[:8])]
        cells.append((*inside[8], 1))
        assert list(run(cells)[0]) == [1] * 8 + [0], f"box {b}"


def test_the_masks_match_the_reference_model_cell_for_cell() -> None:
    """The tape's 27 masks must be exactly the reference sets, bit for bit."""
    grid = valid_grid()
    cells = [(r, c, grid[r][c]) for r in range(9) for c in range(9)]
    _, store, _ = run(cells)
    rows = [0] * 9
    cols = [0] * 9
    boxes = [0] * 9
    for r, c, v in cells:
        rows[r] |= 1 << v
        cols[c] |= 1 << v
        boxes[(r // 3) * 3 + c // 3] |= 1 << v
    assert [store.cells[3 + r] for r in range(9)] == rows
    assert [store.cells[12 + c] for c in range(9)] == cols
    assert [store.cells[21 + b] for b in range(9)] == boxes


# ── cost ─────────────────────────────────────────────────────────────────────
def test_the_worst_legal_case_fits_the_tick_cap_with_real_tape_latency() -> None:
    """81 rounds of a valid grid, with store access billed at ``105 + 8.3N``.

    A *valid* grid is the worst case: an auditor that finds a duplicate stops, so
    every early-exit case is cheaper. The emulator bills a store word at a flat 6
    ticks, which understates the tape by ~30x, so the margin is measured here at
    ``ARCH.md`` §4.1's real figure instead.
    """
    grid = valid_grid()
    cells = [(r, c, grid[r][c]) for r in range(9) for c in range(9)]
    got, store, ticks = run(cells)
    assert list(got) == [1] * 81
    billed = ticks + store.ops * STORE_TICKS
    assert billed < TICK_CAP, f"{billed:,.0f} ticks over the {TICK_CAP:,} cap"
    # Room to spare, not a squeeze: the measured grid runs ~0.8M ticks.
    assert billed < TICK_CAP / 2, f"{billed:,.0f} ticks leaves under 2x of margin"


def test_cost_per_round_stays_where_the_design_put_it() -> None:
    """A regression fence on the three terms the score is made of.

    Ticks are ~47% tape access, ~37% instructions and ~16% ROM recirculation, so a
    change that quietly adds a store transaction or a hundred ROM words to the
    round is worth catching here rather than in a wasm sweep.
    """
    grid = valid_grid()
    cells = [(r, c, grid[r][c]) for r in range(9) for c in range(9)]
    store = CountingStore()
    res = Emulator(programs.load(SLUG), store=store).run(
        rounds_of(cells), max_instructions=MAX_INSTRUCTIONS
    )
    rounds = 81
    # 14 header + ~9.8 average dispatch + 3 units x 6 + OUT/SUBI/BRZ.
    assert res.instructions / rounds < 46
    # 5 to build the three cursors, then LDP + STP (2 each) per unit.
    assert store.ops / rounds == 17
    # One ROM lap per round; the executed instructions cover the rest of it.
    assert res.words_skipped / rounds < 350


# ── the generated machine ────────────────────────────────────────────────────
@node_required
def test_the_machine_generates_and_every_pipe_binds() -> None:
    """``build`` raises unless every ``r``/``s`` is *strictly* nearest its own pipe."""
    m = machine.build_for(SLUG)
    assert m.tape_n == TAPE_N
    assert m.plan.k == 4
    assert "@" in "".join(m.rows)


@node_required
def test_the_checked_in_grid_matches_the_generator() -> None:
    expected = "\n".join(machine.build_for(SLUG).rows) + "\n"
    assert GRID.read_text(encoding="utf-8") == expected, (
        f"{GRID.name} is stale; regenerate with "
        f"`python -m randomfun2026solvers.lm1.machine {SLUG} --out {GRID}`"
    )


@pytest.mark.slow  # drives the engine over a whole problem
@node_required
def test_the_rom_fold_is_the_footprint_minimum() -> None:
    """The default fold aims the ROM at the CPU's ~45 columns, which is wrong here.

    Once the tape and adapter push the machine past 80 columns, folding to the CPU
    makes the ROM needlessly tall and *height* becomes binding. ``ROM_ROWS`` is the
    swept minimum, so no other fold may beat it.

    Swept in the configuration ``build_for`` actually ships — display, stream and above
    all ``middle_order``. Sweeping bare ``build(prog, tape_n=...)`` instead measures a
    machine nobody generates: without the lane order `sudoku-validity` comes out 83x80
    against the shipped 80x80, and once ``ADAPTER_TAPE_GAP`` went 6 -> 1 that phantom
    build's optimum moved to 24 rows while the real one stayed at 23. The old range
    (25..49) also skipped the recorded fold entirely, so "no other fold may beat it"
    was never actually asked about the folds nearest it.
    """
    prog = programs.load(SLUG)
    shipped = dict(
        tape_n=TAPE_N,
        display=machine.display_for(SLUG),
        stream=machine.STREAM_SIZE.get(SLUG),
        middle_order=machine.LANE_ORDER.get(SLUG),
    )
    chosen = machine.build(prog, rom_rows=machine.ROM_ROWS[SLUG], **shipped).footprint
    assert chosen == machine.build_for(SLUG).footprint, "the sweep is not the shipped build"
    for rows in range(18, 50):
        try:
            other = machine.build(prog, rom_rows=rows, **shipped)
        except machine.MachineError:
            continue  # a fold whose pipes do not bind is not a rival
        assert other.footprint >= chosen, f"{rows} rows beats the recorded fold"
    default = machine.build(prog, **shipped).footprint
    assert chosen < default, "the default fold is already optimal; drop the override"


@node_required
@slow
def test_public_cases_pass_on_the_real_interpreter() -> None:
    from randomfun2026solvers import optimize

    res = optimize.verify(GRID, SLUG, tick_cap=3_000_000)
    failed = [c.name for c in res.cases if not c.passed]
    assert res.passed, f"{SLUG}: {failed}"


@node_required
@slow
def test_the_worst_public_case_is_well_inside_the_cap_on_the_real_engine() -> None:
    """A valid grid is the worst case; measure *that*, not the average."""
    from randomfun2026solvers import scoring

    res = scoring.score_program(GRID, SLUG)
    worst = max(c.ticks for c in res.cases)
    assert worst < scoring.DEFAULT_TICK_CAP / 4, f"worst case {worst:,} ticks"
    assert res.score is not None and res.avg_ticks is not None
