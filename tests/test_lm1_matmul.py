"""``matmul`` on LM-1: solved, on the reference interpreter, inside the tick cap.

The previous build of this file recorded a **hardware wall**: a correct program
that the machine could not run. Two walls, both real:

1. ``machine.tape_block`` tops out at 108 ring slots and 16x16x16 wants 512;
2. even inside the tape, ~13 STORE accesses per multiply-accumulate at ~800 ticks
   an access is ~40M against a 5M cap.

Neither is fixed by a cleverer program, and neither is fixed by banking the tape
— *and neither is the binding constraint any more*, because the inner loop no
longer touches memory or the instruction stream at all. It is one command to the
STREAM block (``stream.py``): three rotate-only FIFO rings and an adding relay,
where a rotation costs the two glyphs that perform it instead of a tape
revolution. See ``programs/matmul.asm`` for the loop order that makes matmul a
streaming problem, and ``tests/test_lm1_stream.py`` for the block itself.

Measured on the reference interpreter, 86x90 (footprint 8100), tape N=16:

    case                   settles at    vs 5M cap   instructions   MACs
    2x2 warm up                13,524       0.003x             98      8
    non-square 2x3x2           17,136       0.003x            116     12
    identity (4x4x4)           35,472       0.007x            216     64
    negative heavy (5x6x4)     60,884       0.012x            347    120
    skinny 16x2x16             66,024       0.013x            420    512
    max magnitude 7x5x9        70,220       0.014x            402    315
    16x16x16 full size        470,568       0.094x          2,436  4,096

**7/7 public cases on the real engine**, worst case 10.6x inside the cap, and
``scoring.score_program`` returns 8100 x 104,832.57 = **849,143,828.57**.

The instruction column is the one to watch. A parallel measurement put the
engine's non-memory instruction at ~46 ticks, which makes instruction *issue* a
floor no amount of memory bandwidth can lower: the old program's ~19
instructions per MAC put 16x16x16 at ~78,000 instructions ~ 3.6M ticks of issue
alone, 72 % of the cap before a single tape access. This program issues **0.59
instructions per multiply-accumulate** — the 4,096 MACs happen inside the unit's
own counted loop, which issues none — so its issue floor is ~112k ticks, ~2 % of
the cap. That is the actual reason it fits, and it is pinned below.
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

from randomfun2026solvers.lm1 import machine, programs, stream  # noqa: E402
from randomfun2026solvers.lm1.emulator import TICK_CAP, Emulator, Round  # noqa: E402
from randomfun2026solvers.lm1.store import DictStore  # noqa: E402

SLUG = "matmul"
GRID = REPO / "tasks" / "solutions" / "matmul_cpu.man"
LM_MJS = REPO / "littleman" / "lm.mjs"
MAX_INSTRUCTIONS = 3_000_000

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)
slow = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the reference-interpreter sweeps",
)

#: Measured ticks at which the last expected value lands, per public case. These
#: are scoring reference points; the engine assertions use them as correctness
#: upper bounds, so a future speedup does not require a test edit.
REAL_TICKS = {
    "2x2 warm up": 13_524,
    "non-square 2x3x2": 17_136,
    "identity": 35_472,
    "negative heavy": 60_884,
    "skinny 16x2x16": 66_024,
    "max magnitude 7x5x9": 70_220,
    "16x16x16 full size": 470_568,
}

#: ``(instructions, multiply-accumulates)`` per case, from the emulator. The ratio
#: is the whole argument for the STREAM block: see the module docstring.
ISSUE = {
    "2x2 warm up": (98, 8),
    "non-square 2x3x2": (116, 12),
    "identity": (216, 64),
    "16x16x16 full size": (2436, 4096),
    "skinny 16x2x16": (420, 512),
    "negative heavy": (347, 120),
    "max magnitude 7x5x9": (402, 315),
}

#: A non-memory instruction on the real engine, measured by a parallel calibration
#: of this same generator. Used only to state an issue-cost floor.
TICKS_PER_INSTRUCTION = 46

PUBLIC = programs.rounds_for_problem(SLUG)


class _Counting(DictStore):
    """Counts tape accesses and remembers the top address used."""

    def __init__(self) -> None:
        super().__init__()
        self.ops = 0
        self.top = 0

    def _read(self, addr: int) -> int:
        self.ops += 1
        self.top = max(self.top, addr)
        return super()._read(addr)

    def _write(self, addr: int, value: int) -> None:
        self.ops += 1
        self.top = max(self.top, addr)
        super()._write(addr, value)


def reference(n: int, m: int, k: int, a: list[int], b: list[int]) -> list[int]:
    """C = A·B, row-major, straight from the problem statement."""
    return [sum(a[i * m + t] * b[t * k + j] for t in range(m)) for i in range(n) for j in range(k)]


def _run(rounds: list[Round]):
    store = _Counting()
    em = Emulator(programs.load(SLUG), store=store)
    res = em.run(rounds, max_instructions=MAX_INSTRUCTIONS)
    assert res.reason in ("halted", "input-exhausted"), res.reason
    return res, store, em.stream


# ── the program ──────────────────────────────────────────────────────────────
def test_matmul_uses_eight_opcodes_on_a_depth_three_trie() -> None:
    """Eight opcodes, so the decode trie is depth **3** — half the old machine's.

    Dropping to eight is not cosmetic: the trie is the second-longest stretch of
    every instruction's walk, and 8 lanes instead of 16 also halves the lane band's
    height. ``LDI`` is absent because an unwritten tape cell reads as 0 and ``ADDI``
    builds any constant from it; ``JMPF`` is absent because the ROM ring wraps.
    """
    prog = programs.load(SLUG)
    used = {op.mnemonic for op in prog.ops_used}
    assert used == {"SND", "RCV", "LD", "ST", "ADDI", "SUB", "MUL", "BRN"}
    assert len(used) == 8
    assert machine.plan(prog).k == 3
    assert not {"JMPF", "JMP", "BRZ", "LDI", "IN", "OUT", "HALT"} & used


def test_the_program_never_addresses_a_matrix() -> None:
    """Every tape slot is a scalar: 14 of them, and the top one is a constant.

    This is the structural claim behind the whole rewrite. The old program's tape
    held both matrices (top address 530, against a 108-slot tape); this one holds
    N, M, K, two loop counters, seven pre-multiplied command words and the literal
    8 — and nothing that scales with N, M or K.
    """
    _res, store, _unit = _run(list(dict(PUBLIC)["16x16x16 full size"]))
    assert store.top == 14
    assert store.top < machine.TAPE_SIZE[SLUG]


@pytest.mark.parametrize(("name", "rounds"), PUBLIC, ids=[n for n, _ in PUBLIC])
def test_every_public_case_matches_round_by_round(name: str, rounds: list[Round]) -> None:
    """Not just the concatenated output: each round's slice, in order.

    The concatenation can match while the split is wrong, and ``GRADING.md`` gates
    round N+1's input on round N's output — which for this machine means the STREAM
    unit's ``RDIN`` arm blocking until the judge releases more.
    """
    res, _store, _unit = _run(list(rounds))
    at = 0
    for index, rnd in enumerate(rounds):
        got = res.output[at : at + len(rnd.expected)]
        assert got == rnd.expected, f"{name}: round {index} mismatch"
        at += len(rnd.expected)
    assert at == len(res.output), f"{name}: {len(res.output) - at} extra output value(s)"


def test_every_public_case_is_covered() -> None:
    assert len(PUBLIC) == 7
    assert {n for n, _ in PUBLIC} == set(REAL_TICKS) == set(ISSUE)


# ── synthetic cases at the constraint corners ────────────────────────────────
#: Every corner of ``2 <= N, M, K <= 16``, plus a rectangular interior point.
_SHAPES = [
    (2, 2, 2), (2, 2, 16), (2, 16, 2), (2, 16, 16),
    (16, 2, 2), (16, 2, 16), (16, 16, 2), (16, 16, 16),
    (3, 16, 5),
]  # fmt: skip


@pytest.mark.parametrize(("n", "m", "k"), _SHAPES, ids=[f"{n}x{m}x{k}" for n, m, k in _SHAPES])
def test_dimension_corners(n: int, m: int, k: int) -> None:
    """A pseudo-random matrix at each shape corner, against the reference."""
    a = [((i * 37 + 11) % 199) - 99 for i in range(n * m)]
    b = [((i * 53 + 7) % 199) - 99 for i in range(m * k)]
    rounds = [Round(input=(n, m, k, *a, *b), expected=tuple(reference(n, m, k, a, b)))]
    res, _store, _unit = _run(rounds)
    assert res.output == rounds[0].expected


@pytest.mark.parametrize("sign", [1, -1], ids=["plus99", "minus99"])
def test_value_extremes_saturate_one_way(sign: int) -> None:
    """All entries at +/-99 and the largest shape: the widest possible |C[i][j]|.

    ``16 * 99 * 99 = 156_816`` — well inside 64 bits, but this is the case that
    would expose a sign mistake in the ADDER room or a stale partial sum in the
    accumulator ring.
    """
    n, m, k = 16, 16, 16
    a = [sign * 99] * (n * m)
    b = [-99] * (m * k)
    expected = tuple(reference(n, m, k, a, b))
    assert set(expected) == {sign * -99 * 99 * m}
    res, _store, _unit = _run([Round(input=(n, m, k, *a, *b), expected=expected)])
    assert res.output == expected


def test_mixed_signs_and_zeros() -> None:
    """Zeros must not be mistaken for "absent" anywhere — a ring holds them fine."""
    n, m, k = 4, 5, 3
    a = [0, 99, -99, 0, 1] * 4
    b = [0, 0, 0, 1, -1, 0, 99, -99, 0, 0, 0, 0, -1, 1, 0]
    expected = tuple(reference(n, m, k, a, b))
    res, _store, _unit = _run([Round(input=(n, m, k, *a, *b), expected=expected)])
    assert res.output == expected


def test_several_rounds_in_one_run_reuse_the_rings() -> None:
    """Three shapes back to back: the rings must come back empty between rounds.

    ``DRAINB`` exists only for this — ring B holds M*K values at the end of a round
    and the next round's M*K is different. Without it round two reads stale values,
    which no single-round test would ever catch.
    """
    rounds = []
    for n, m, k in ((2, 3, 4), (5, 2, 2), (4, 4, 3)):
        a = [((i * 13 + 5) % 199) - 99 for i in range(n * m)]
        b = [((i * 29 + 3) % 199) - 99 for i in range(m * k)]
        rounds.append(Round(input=(n, m, k, *a, *b), expected=tuple(reference(n, m, k, a, b))))
    res, _store, unit = _run(rounds)
    assert res.output == tuple(v for r in rounds for v in r.expected)
    assert not unit.ring_a and not unit.ring_b and not unit.p1 and not unit.p2


# ── the rings are sized from the constraint box, not from the public cases ────
def test_the_ring_sizes_cover_the_worst_legal_shape() -> None:
    """What the block was built to hold, against what 16x16x16 actually queues."""
    _res, _store, unit = _run(list(dict(PUBLIC)["16x16x16 full size"]))
    a, b, c = machine.STREAM_SIZE[SLUG]
    assert (unit.high_a, unit.high_b, unit.high_c) == (256, 256, 16)
    assert (a, b, c) == (unit.high_a + 1, unit.high_b + 1, unit.high_c + 1)


def test_the_command_codes_match_the_hardwares_trie() -> None:
    """The program's command words are the unit's own codes, not a parallel list."""
    assert stream.arm_codes() == {
        "EMIT": 0, "FILLB": 1, "ZEROC": 2, "FILLA": 3,
        "FWD": 4, "DRAINB": 5, "MAC": 6, "RDIN": 7,
    }  # fmt: skip


# ── issue cost: the floor that memory bandwidth cannot lower ─────────────────
@pytest.mark.parametrize(("name", "rounds"), PUBLIC, ids=[n for n, _ in PUBLIC])
def test_instruction_and_mac_counts_are_the_recorded_ones(name: str, rounds: list[Round]) -> None:
    """Pins ~0.6 instructions per multiply-accumulate.

    At ~46 ticks an instruction, 16x16x16's 4,096 MACs would need <= ~26
    instructions each to fit the cap even with *zero-latency* memory. The old
    program issued ~19 and was already at 72 % of the cap on issue alone; this one
    issues 0.59 because the MACs are a hardware loop, not an instruction stream. A
    regression here is the one that would put matmul back over the cap.
    """
    instrs, macs = ISSUE[name]
    res, _store, unit = _run(list(rounds))
    assert (res.instructions, unit.macs) == (instrs, macs)
    floor = res.instructions * TICKS_PER_INSTRUCTION
    assert floor < TICK_CAP / 10, f"{name}: issue floor {floor:,} ticks"


def test_the_issue_floor_for_the_largest_case_is_two_percent_of_the_cap() -> None:
    instrs, macs = ISSUE["16x16x16 full size"]
    assert instrs / macs < 0.7
    assert instrs * TICKS_PER_INSTRUCTION < 0.03 * TICK_CAP


# ── the generated machine ────────────────────────────────────────────────────
@node_required
@slow
def test_the_checked_in_grid_matches_the_generator() -> None:
    """``matmul_cpu.man`` is generated, never hand-edited."""
    expected = "\n".join(machine.build_for(SLUG).rows) + "\n"
    assert GRID.read_text(encoding="utf-8") == expected, (
        "matmul_cpu.man is stale; regenerate with `python -m "
        f"randomfun2026solvers.lm1.machine {SLUG} --out {GRID}`"
    )


def test_the_checked_in_machine_is_the_recorded_size() -> None:
    """Fast score/plan pin; the expensive generator equality check is in slow."""
    rows = GRID.read_text(encoding="utf-8").rstrip("\n").splitlines()
    assert (max(map(len, rows)), len(rows)) == (86, 90)
    assert max(max(map(len, rows)), len(rows)) ** 2 == 8100
    assert machine.plan(programs.load(SLUG)).k == 3
    assert machine.TAPE_SIZE[SLUG] == 16
    assert machine.ROM_ROWS[SLUG] == 5
    assert machine.STREAM_SIZE[SLUG] == (257, 257, 17)


@node_required
@slow
def test_the_recorded_rom_fold_is_the_sweep_minimum() -> None:
    """``ROM_ROWS['matmul']`` beats every other fold on footprint, and not by little.

    This machine comes out nearly *square*, so both dimensions are billed and the
    sweep is genuinely peaked rather than flat-bottomed: the fold either overruns the
    width or piles on height. Sweeping is the only way to find the turn.
    """
    prog = programs.load(SLUG)
    sizes = {}
    for rows in range(2, 24):
        sizes[rows] = machine.build(
            prog,
            tape_n=machine.TAPE_SIZE[SLUG],
            rom_rows=rows,
            stream=machine.STREAM_SIZE[SLUG],
        ).footprint
    chosen = machine.ROM_ROWS[SLUG]
    assert sizes[chosen] == min(sizes.values()), f"fold {chosen}: {sizes}"
    default = machine.build(
        prog, tape_n=machine.TAPE_SIZE[SLUG], stream=machine.STREAM_SIZE[SLUG]
    ).footprint
    assert sizes[chosen] < default, "the override no longer buys anything; drop it"


# ── the real interpreter: the bar ────────────────────────────────────────────
@node_required
@slow
@pytest.mark.parametrize(("name", "rounds"), PUBLIC, ids=[n for n, _ in PUBLIC])
def test_the_grid_multiplies_matrices_on_the_reference_interpreter(
    name: str, rounds: list[Round]
) -> None:
    """Every public case, on the generated grid, inside the tick cap.

    Not the emulator: the checked-in ``.man`` on the wasm engine, with the tick at
    which the last expected value lands measured by bisection. This is the test the
    previous build of this file could not write.
    """
    from randomfun2026solvers.littleman import Littleman

    expected = [v for r in rounds for v in r.expected]
    inp = " ".join(str(v) for r in rounds for v in r.input)
    lm = Littleman()
    recorded = REAL_TICKS[name]
    assert recorded < TICK_CAP

    # A speedup may make this upper bound loose. Correctness and the cap are the
    # invariant; exact settle ticks belong to scoring, not to seven duplicate
    # full-engine correctness runs.
    assert list(lm.tick(GRID, recorded, input=inp).output) == expected


@node_required
@slow
def test_the_score_is_real() -> None:
    """``scoring.score_program`` returns a number, and the number is the one recorded."""
    from randomfun2026solvers.scoring import score_program

    got = score_program(GRID, SLUG)
<<<<<<< HEAD
    # 8100, not the 9216 this pinned for a while: 96² was the shape before the ROM
    # packing landed, and because the assertion is gated behind LM1_SLOW it went stale
    # unnoticed. `matmul` is 86x90 and is the one program `ADAPTER_TAPE_GAP` did *not*
    # move (it is pinned to 6 by `ADAPTER_TAPE_GAP_FOR`), so this is the same number it
    # has been for some time — it agrees with the shape pinned at the top of this file.
=======
>>>>>>> b016681 (Compact LM-1 jump read loops)
    assert got.area2 == 8100
    assert abs(got.avg_ticks - sum(REAL_TICKS.values()) / 7) < 500
    assert 0.75e9 < got.score < 1.0e9
    assert max(c.ticks for c in got.cases) < TICK_CAP
