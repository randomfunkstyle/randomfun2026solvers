"""``matmul`` on LM-1: the program, the generated machine, and the wall it hits.

Three things are asserted here, and the third is the interesting one.

* **The program is right.** Every public case is checked *round by round* against
  the emulator, plus synthetic cases at every corner of the constraint box
  (``2 <= N, M, K <= 16``, ``-99 <= a, b <= 99``) against a Python reference.
* **The machine is right.** The checked-in ``matmul_cpu.man`` is byte-identical to
  what the generator emits, the ROM fold is the minimum of a full sweep, and the
  grid really computes matrices on the reference interpreter (``LM1_SLOW=1``).
* **The machine is too small and too slow for the biggest case, and by how much.**
  Two independent hardware walls, both pinned below so they cannot rot silently:

  1. ``machine.tape_block`` tops out at **108 ring slots**. 16x16x16 needs 512 for
     A and B alone, so the tape physically cannot hold it — no program is clever
     enough to fix that, only a bigger STORE.
  2. Even inside the tape, a STORE access on the maximal tape costs
     ``105 + 8.3 * 107 ~ 993`` ticks (``ARCH.md`` §4.1), which is ~30x the
     emulator's flat 6 ticks/word. The 5 M tick cap therefore buys ~5 000
     accesses, and this program needs ~13 per multiply-accumulate.

  So ``matmul`` remains blocked on the banked / FIFO STORE that ``ARCH.md`` §4.1
  lists as future work — which is what §4.1 already predicted ("Banking … the only
  route to matmul's 768 slots, and still a stretch"). The program above is the
  half that is finished.

Measured on the reference interpreter, 92x75 (footprint 8464), tape N=107 —
output is **exact** on all six cases the tape can hold:

    case                   top slot      real ticks   billed est.   <= 5M cap
    2x2 warm up                  26         261,710       133,268   yes
    non-square 2x3x2             30         349,702       192,026   yes
    identity (4x4x4)             50       1,310,038       941,838   yes
    negative heavy (5x6x4)       72       2,229,479     2,014,687   yes
    max magnitude 7x5x9          98       5,292,220     5,908,278   no (1.06x)
    skinny 16x2x16               82       9,663,830     9,411,810   no (1.93x)
    16x16x16 full size          530     does not fit   67,116,866   no

4/7 public cases inside the cap; the emulator gets 7/7, so the program is right
and the machine is what falls short.
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

#: ``matmul.asm``'s first matrix slot. Slots 1..18 are its named scalars.
ABASE = 19

#: ``ARCH.md`` §4.1's measured tape latency, which is what actually has to be
#: billed: the emulator's flat 6 ticks/word understates it ~30x.
def tape_ticks(slots: int) -> float:
    return 105 + 8.3 * slots


PUBLIC = programs.rounds_for_problem(SLUG)


class _Counting(DictStore):
    """A store that counts words exchanged and remembers the top address used."""

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
    return [
        sum(a[i * m + t] * b[t * k + j] for t in range(m))
        for i in range(n)
        for j in range(k)
    ]


def _run(rounds: list[Round]) -> tuple[tuple[int, ...], _Counting, int]:
    store = _Counting()
    res = Emulator(programs.load(SLUG), store=store).run(
        rounds, max_instructions=MAX_INSTRUCTIONS
    )
    assert res.reason in ("halted", "input-exhausted"), res.reason
    return res.output, store, res.ticks


# ── the program ──────────────────────────────────────────────────────────────
def test_matmul_uses_thirteen_opcodes_on_a_depth_four_trie() -> None:
    """13 opcodes keeps the trie at ``ARCH.md`` §6's budgeted depth 4 (16 lanes).

    A 17th would force depth 5 and 32 lanes, roughly doubling the CPU's height.
    ``JMPF`` and ``BRZ`` are deliberately absent: counting *up* and testing
    ``cursor - end < 0`` with ``BRN`` needs neither, and each one omitted also
    removes a structures-band slab (8 rows for ``BRZ``, 5 for ``JMPF``).
    """
    prog = programs.load(SLUG)
    used = {op.mnemonic for op in prog.ops_used}
    assert used == {
        "IN", "ST", "LDI", "LD", "ADD", "SUB", "ADDI",
        "MUL", "LDA", "MOVA", "OUT", "BRN", "HALT",
    }  # fmt: skip
    assert len(used) == 13
    assert machine.plan(prog).k == 4
    assert not {"JMPF", "JMP", "BRZ"} & used


@pytest.mark.parametrize(("name", "rounds"), PUBLIC, ids=[n for n, _ in PUBLIC])
def test_every_public_case_matches_round_by_round(name: str, rounds: list[Round]) -> None:
    """Not just the concatenated output: each round's slice, in order.

    The concatenation can match while the split is wrong (a program that emits
    row-major C but interleaved would still pass a single-round comparison of the
    whole stream), and ``GRADING.md`` gates round N+1's input on round N's output.
    """
    output, _store, _ticks = _run(list(rounds))
    at = 0
    for index, rnd in enumerate(rounds):
        got = output[at : at + len(rnd.expected)]
        assert got == rnd.expected, f"{name}: round {index} mismatch"
        at += len(rnd.expected)
    assert at == len(output), f"{name}: {len(output) - at} extra output value(s)"


def test_every_public_case_is_covered() -> None:
    """All seven of ``matmul.json``'s public cases are exercised above."""
    assert len(PUBLIC) == 7
    assert {n for n, _ in PUBLIC} == {
        "2x2 warm up",
        "non-square 2x3x2",
        "identity",
        "16x16x16 full size",
        "skinny 16x2x16",
        "negative heavy",
        "max magnitude 7x5x9",
    }


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
    output, _store, _ticks = _run(rounds)
    assert output == rounds[0].expected


@pytest.mark.parametrize("sign", [1, -1], ids=["plus99", "minus99"])
def test_value_extremes_saturate_one_way(sign: int) -> None:
    """All entries at +/-99 and the largest shape: the widest possible |C[i][j]|.

    ``16 * 99 * 99 = 156_816`` — well inside 64 bits, but this is the case that
    would expose a sign or wraparound mistake in the accumulator.
    """
    n, m, k = 16, 16, 16
    a = [sign * 99] * (n * m)
    b = [-99] * (m * k)
    expected = tuple(reference(n, m, k, a, b))
    assert set(expected) == {sign * -99 * 99 * m}
    output, _store, _ticks = _run([Round(input=(n, m, k, *a, *b), expected=expected)])
    assert output == expected


def test_mixed_signs_and_zeros() -> None:
    """Zeros must not be mistaken for "absent" anywhere (no presence bitmap here)."""
    n, m, k = 4, 5, 3
    a = [0, 99, -99, 0, 1] * 4
    b = [0, 0, 0, 1, -1, 0, 99, -99, 0, 0, 0, 0, -1, 1, 0]
    expected = tuple(reference(n, m, k, a, b))
    output, _store, _ticks = _run([Round(input=(n, m, k, *a, *b), expected=expected)])
    assert output == expected


# ── the generated machine ────────────────────────────────────────────────────
@node_required
def test_the_checked_in_grid_matches_the_generator() -> None:
    """``matmul_cpu.man`` is generated, never hand-edited."""
    expected = "\n".join(machine.build_for(SLUG).rows) + "\n"
    assert GRID.read_text(encoding="utf-8") == expected, (
        "matmul_cpu.man is stale; regenerate with `python -m "
        f"randomfun2026solvers.lm1.machine {SLUG} --out {GRID}`"
    )


@node_required
def test_the_recorded_rom_fold_is_the_sweep_minimum() -> None:
    """``ROM_ROWS['matmul']`` beats every other fold on footprint.

    ``rom.rows_for_budget`` aims the ROM at ~48 columns, which is the wrong target
    here: the machine is already 92 wide because of the tape band, so the default
    trades away width it is not paying for and makes *height* the binding
    constraint. Sweeping is the only way to find that out.
    """
    prog = programs.load(SLUG)
    tape_n = machine.TAPE_SIZE[SLUG]
    chosen = machine.ROM_ROWS[SLUG]
    sizes = {}
    for rows in range(2, 46):
        sizes[rows] = machine.build(prog, tape_n=tape_n, rom_rows=rows).footprint
    best = min(sizes.values())
    assert sizes[chosen] == best, f"fold {chosen} gives {sizes[chosen]}, best is {best}"
    default = machine.build(prog, tape_n=tape_n).footprint
    assert best < default, "the override no longer buys anything; drop it"


@node_required
def test_the_generated_machine_is_the_recorded_size() -> None:
    m = machine.build_for(SLUG)
    assert (m.width, m.height) == (92, 75)
    assert m.footprint == 8464
    assert m.plan.k == 4
    assert m.tape_n == 107
    assert m.rom_rows == 18


# ── wall 1: the tape cannot hold a full-size matmul ──────────────────────────
def test_the_tape_tops_out_at_108_slots() -> None:
    """``tape_block`` is capacity-bound, and no fold makes it bigger.

    The ring is worker -> forward pipe -> relay -> return pipe -> worker, and
    ``fold`` only widens the return pipe's zig-zag *inwards*: every larger fold
    makes it **shorter**, not longer (108, 104, 100, … slots). So 107 is the
    largest ``n`` the generator can build, full stop.
    """
    assert machine.tape_block(107).width > 0
    with pytest.raises(machine.MachineError, match="no fold gives the tape"):
        machine.tape_block(108)
    assert machine.TAPE_SIZE[SLUG] == 107


def test_a_full_size_matmul_does_not_fit_the_tape() -> None:
    """16x16x16 needs 512 matrix slots; the tape has 88 above the scalars.

    This is a *hardware* limit, not a program one — the emulator (whose store is
    an unbounded dict) computes the case correctly, see the public-case test.
    """
    _out, store, _ticks = _run(list(dict(PUBLIC)["16x16x16 full size"]))
    assert store.top == ABASE + 16 * 16 + 16 * 16 - 1 == 530
    assert store.top > machine.TAPE_SIZE[SLUG]
    # Even with zero scalars the two matrices alone overflow the tape 4.7x over.
    assert 2 * 16 * 16 > machine.TAPE_SIZE[SLUG]


# ── wall 2: ticks, billed at the tape's real latency ─────────────────────────
#: Per public case: the top STORE address the program touches, and a ceiling on
#: the number of STORE words it exchanges. The counts are measured; the ceilings
#: carry 2 % slack so a small win does not break the test, while a regression in
#: the 12-access inner loop does.
_BUDGET = {
    "2x2 warm up": (26, 276),
    "non-square 2x3x2": (30, 364),
    "identity": (50, 1337),
    "16x16x16 full size": (530, 57396),
    "skinny 16x2x16": (82, 9986),
    "negative heavy": (72, 2263),
    "max magnitude 7x5x9": (98, 5390),
}

#: The cases whose *billed* tick estimate stays inside ``TICK_CAP``. Measured on
#: the reference interpreter the real machine runs 1.4-2.0x the estimate (261 710
#: ticks against 133 268 estimated for 2x2, 1 310 038 against 941 838 for the
#: 4x4x4 identity), so treat these as a lower bound on what the grid can do.
_WITHIN_CAP = {"2x2 warm up", "non-square 2x3x2", "identity", "negative heavy"}


@pytest.mark.parametrize(("name", "rounds"), PUBLIC, ids=[n for n, _ in PUBLIC])
def test_store_traffic_and_billed_ticks(name: str, rounds: list[Round]) -> None:
    """Bill every STORE word at ``105 + 8.3N`` instead of the emulator's flat 6.

    ``ARCH.md`` §2.7 found the emulator's model good to ~15 % *provided* store
    accesses are billed this way; billed at 6 ticks/word it is out by ~30x, which
    is exactly the factor that decides whether matmul fits.
    """
    top, ops_ceiling = _BUDGET[name]
    output, store, ticks = _run(list(rounds))
    assert output == tuple(v for r in rounds for v in r.expected)
    assert store.top == top
    assert store.ops <= ops_ceiling, f"{name}: {store.ops} STORE words, budget {ops_ceiling}"

    tape_n = machine.TAPE_SIZE[SLUG]
    billed = (ticks - 6 * store.ops) + store.ops * tape_ticks(tape_n)
    if name in _WITHIN_CAP:
        assert billed < TICK_CAP, f"{name}: {billed:,.0f} ticks over the {TICK_CAP:,} cap"
    else:
        assert billed > TICK_CAP, (
            f"{name}: {billed:,.0f} ticks now fits the {TICK_CAP:,} cap — the wall "
            "moved, move it out of _WITHIN_CAP's complement and re-measure"
        )


def test_store_traffic_scales_at_the_designed_cost_per_multiply() -> None:
    """~13.7 STORE words per multiply-accumulate at the largest shape.

    Twelve of those are the inner loop (``LD PA``/``LDA``/``ST TMPA`` for A,
    ``LD PA``/``ADD DELTA``/``LDA``/``MUL TMPA``/``ADD SUM``/``ST SUM`` for the
    product, and ``LD PA``/``ST PA``/``SUB AEND`` for the fused bump-and-test);
    the rest is the per-``C[i][j]`` prologue amortised over M terms. A two-cursor
    row-major version costs 16, which is where the transposed B layout pays for
    itself — this is the number to watch when touching the loop.
    """
    _out, store, _ticks = _run(list(dict(PUBLIC)["16x16x16 full size"]))
    per_mac = store.ops / (16 * 16 * 16)
    assert 13.0 <= per_mac <= 13.8, per_mac


def test_the_cap_cannot_be_met_by_any_program_on_this_tape() -> None:
    """The arithmetic behind "matmul needs a different STORE", as an assertion.

    Lower bound, independent of how the program is written:

    * 16x16x16 is 4096 multiply-accumulates. Packing several products into one
      64-bit multiply is the only way to cut that, and the widest packing that
      fits is 3 lanes (a lane must hold ``16 * 198 * 198 = 627_264``, i.e. 20 bits,
      and ``max_lane * 2^(20*3) `` already needs 60 of the 63 available bits) — so
      >= 1365 multiply steps.
    * With one accumulator and no fused multiply-add, a step is at least four
      STORE words: load the scalar, multiply by the packed operand, add the
      accumulator, store it back.
    * Packed B needs ``ceil(256/3) = 86`` slots, and A's 256 entries need
      ``ceil(256/7) = 37`` more (a 63-bit word holds seven 8-bit entries), so such
      a machine wants **123** slots — past the 108-slot tape, so it cannot even be
      built. And if it could, an access would cost ``105 + 8.3 * 123``.

    Multiply out and the floor is past the cap too, before loading, unpacking,
    the bias corrections or the output are counted at all.
    """
    steps = -(-4096 // 3)  # 3 lanes is the widest packing 64 bits allows
    slots = -(-256 // 3) + -(-256 // 7)  # packed B, then packed A
    assert slots == 123 > 108, "a packed matmul does not fit the tape either"
    floor = steps * 4 * tape_ticks(slots)
    assert floor > TICK_CAP, f"{floor:,.0f} vs {TICK_CAP:,}"


# ── the real interpreter ─────────────────────────────────────────────────────
#: Cases small enough to fit the 107-slot tape *and* to settle in reasonable wall
#: time, with the tick at which the last expected value lands (measured).
_REAL = [("2x2 warm up", 261_710), ("non-square 2x3x2", 349_702), ("identity", 1_310_038)]


@node_required
@slow
@pytest.mark.parametrize(("name", "ticks"), _REAL, ids=[n for n, _ in _REAL])
def test_the_grid_multiplies_matrices_on_the_reference_interpreter(
    name: str, ticks: int
) -> None:
    """The generated grid, not the emulator: exact output at the measured tick.

    Also pins the estimate's accuracy: the billed estimate is 133 268 / 192 026 /
    941 838 for these three, so the real machine runs 1.4-2.0x the model — the
    same direction and rough magnitude ``ARCH.md`` §2.7 reports for brackets/tcp.
    """
    from randomfun2026solvers.littleman import Littleman

    rounds = dict(PUBLIC)[name]
    expected = [v for r in rounds for v in r.expected]
    inp = " ".join(str(v) for r in rounds for v in r.input)
    lm = Littleman()
    assert list(lm.tick(GRID, ticks, input=inp).output) == expected
    assert len(lm.tick(GRID, ticks - 1, input=inp).output) < len(expected)
