"""The unrolled LM-1 training program: a CNN that learns, on the emulator.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §4.1a, §4.2, §5.

This module emits one straight-line ``.asm`` program that trains
:mod:`~randomfun2026solvers.mnist_model`'s Q12 CNN on the packed MNIST of
:mod:`~randomfun2026solvers.mnist_data`, and runs it on ``lm1.emulator``. The
claim the tests make is *equality between tiers*: the emulator running this
assembly reproduces the reference model bit for bit, parameter for parameter,
loss for loss. Nothing here asserts an accuracy — the accuracy is a result, and
it is only meaningful because the two tiers agree.

Three facts about the target shape everything below.

**Jumps cost ring words, so the program is unrolled.** A taken backward jump
recirculates ``(target - pc - 1) mod P`` words at ~4.8 ticks each. Rolled up, the
~1,836-MAC inner loop would spend more time discarding instructions than doing
arithmetic (§5). So forward and backward are fully unrolled and the only backward
jumps are the sample loop and the epoch loop, where the discard is amortised over
a whole sample.

**Unrolling makes rings cheap.** Every ring position in this program is a
compile-time constant: :class:`_Gen` tracks ring B's rotation and the
accumulator's contents as it emits, and asserts its model against the layout on
every command. That is the whole reason a rotate-only store is programmable here.

**The multiplicand ring cannot be written by the CPU, so ``UPDB`` writes it.**
The STREAM unit's twelve arms can put a CPU-computed value on ring *A*
(``PUSHA``) but not on ring *B* — ``FILLB`` fills from the input room, and the
image arrives packed, fifteen nibbles a word, so it has to pass through the CPU.
Yet ring B is the multiplicand ring, and the conv pass that costs 648 of the
1,836 MACs only fits the unit with the *image* there and the weight as the
scalar (§4.2's "nine offset-sequential passes"). The way out uses an arm that is
already there: ``UPDB`` writes ``b - ((a * g) >> lr_shift)`` back into ring B, so
with ``g = 1`` (from a ring-B region of ones) and ``a = (b - v) << lr_shift`` it
writes exactly ``v``. ``a`` is an exact multiple of ``2**lr_shift``, so the shift
is lossless and the write is exact, not approximate. Cost is three commands a
word — about 10% of a sample against a hypothetical thirteenth arm — and no
hardware change, which is why it is done this way rather than by widening the
unit. See the task-4 report.

**What the unit's shift means here.** ``UPDB``'s ``lr_shift`` is one attribute of
the built unit, and it has to serve both callers: the dense weight update wants
``w -= (dz[j] * f[i] >> 12) >> 6`` and floor division composes, so a single
``>> 18`` is exact; the ring-B write above then needs ``a`` scaled by ``2**18``.
So the unit is built with ``lr_shift = SHIFT + lr_shift`` and never with 6.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from randomfun2026solvers import mnist_data as md
from randomfun2026solvers import mnist_model as mm
from randomfun2026solvers.lm1 import asm, isa
from randomfun2026solvers.lm1.dsprelay import PORT_ADDR, PORT_DATA, PORT_SWAP
from randomfun2026solvers.lm1.emulator import Emulator
from randomfun2026solvers.lm1.store import DictStore, StreamUnit

__all__ = [
    "SEED",
    "RING_SIZES",
    "PANEL",
    "PANELS",
    "LOSS_PANEL",
    "ACC_PANEL",
    "STORE_WORDS",
    "acc_row",
    "build_machine",
    "loss_row",
    "EXP_TABLE",
    "LN_MANT_TABLE",
    "LN2",
    "STAGES",
    "RunReport",
    "emit_source",
    "first_train_sample",
    "probe_one_sample",
    "run_emulator",
    "main",
]

#: The init seed, shared with :func:`mnist_model.init_params`.
SEED = 1

#: Panel geometry: two 64x32 LM-75s, the loss curve on the first and the accuracy
#: curve on the second. Panel order is the grid's reading order, which is also the
#: order the engine numbers its displays in, so ``LOSS_PANEL``/``ACC_PANEL`` index
#: ``machine.build(display=PANELS)`` and ``RunResult.panel_frames`` alike.
PANEL = (64, 32)
PANELS = (PANEL, PANEL)
LOSS_PANEL = 0
ACC_PANEL = 1

# ── the panel layout ─────────────────────────────────────────────────────────
#: Rows 0..29 are the plot area, row 30 the x-axis, row 31 spare; columns 0..1 are
#: the y-axis and columns 2..63 one per epoch. 62 columns is the epoch ceiling the
#: geometry gives, and an epoch past it repaints the last column rather than
#: wrapping onto the y-axis (:func:`_plot`).
PLOT_ROWS = 30
AXIS_ROW = 30
Y_AXIS_COLS = 2
COL0 = Y_AXIS_COLS
MAX_EPOCH_COLS = PANEL[0] - COL0

#: Colours: background, axes, the training curve, the validation curve.
BG_COLOUR, AXIS_COLOUR, TRAIN_COLOUR, VAL_COLOUR = 0, 8, 9, 14

#: The loss panel's fixed vertical scale, in Q12 units, and the Q12 per plotted
#: row that follows from it. The measured 30-epoch range is train 2,366..7,387 and
#: validation 2,702..4,839, so 2,000..8,000 covers both with a margin and divides
#: into 30 rows exactly — a whole number of Q12 units a row is what keeps the
#: emitted ``DIVI`` a single instruction.
LOSS_LO, LOSS_HI = 2000, 8000
LOSS_PER_ROW = (LOSS_HI - LOSS_LO) // PLOT_ROWS


def loss_row(loss: int) -> int:
    """Which plot row a Q12 loss lands on: low loss at the bottom, clamped.

    The oracle for the emitted arithmetic — :func:`_plot_value` emits exactly this
    and ``tests/test_mnist_cnn.py`` compares the two over the whole range,
    including both clamps.
    """
    q = (loss - LOSS_LO) // LOSS_PER_ROW if loss >= LOSS_LO else -1
    return PLOT_ROWS - 1 - min(max(q, 0), PLOT_ROWS - 1)


def acc_row(acc: int) -> int:
    """Which plot row an accuracy percentage lands on: 0% at the bottom.

    ``* 29 // 100`` rather than ``* 30 // 100`` so that 100% lands on row 0 and 0%
    on row 29 without a clamp — the two ends of the axis are exactly the two ends
    of the plot area.
    """
    return PLOT_ROWS - 1 - min(max(acc, 0), 100) * (PLOT_ROWS - 1) // 100

C = StreamUnit.CODES

# ── the frozen tables ────────────────────────────────────────────────────────
# `mnist_model` builds these from `math.exp`/`math.log`/`round` at import, which
# keeps the *reason* for each number next to it. Two tiers have to agree on them
# bit for bit, so they are frozen here as literals and a test asserts the two
# agree element for element. The tightest margin measured in Task 2 is a relative
# 5.6e-06 against a libm error of ~1e-16, so the assertion should never fire;
# if it does, that is real news about the platform and not a rounding nit.
EXP_TABLE: tuple[int, ...] = (
    4096, 3190, 2484, 1935, 1507, 1174, 914, 712, 554, 432, 336, 262,
    204, 159, 124, 96, 75, 58, 46, 35, 28, 21, 17, 13, 10, 8, 6, 5, 4, 3, 2, 2,
)  # fmt: skip
LN_MANT_TABLE: tuple[int, ...] = (
    0, 126, 248, 367, 482, 595, 704, 810, 914, 1015, 1114, 1210, 1304, 1396,
    1486, 1575, 1661, 1745, 1828, 1909, 1989, 2067, 2143, 2218, 2292, 2365,
    2436, 2506, 2575, 2642, 2709, 2775, 2839,
)  # fmt: skip
LN2 = 2839

# ── model shape, restated as generator constants ─────────────────────────────
IMG = md.IMG  # 8
NF = mm.N_FILTERS  # 2
K = mm.KERNEL  # 3
CO = mm.CONV_OUT  # 6
CELLS = mm.CONV_CELLS  # 36
TAPS = mm.CONV_TAPS  # 9
NFEAT = mm.N_FEATURES  # 18
NCLS = mm.N_CLASSES  # 10
SHIFT = mm.SHIFT  # 12
SCALE = mm.SCALE  # 4096

#: The padded gradient patch: tap ``t = ky*3+kx`` sits at offset ``ky*8+kx`` of an
#: 8-wide window into the image, so one ``MAC 19`` over ``x[base .. base+18]``
#: lands all nine taps on the nine useful cells of a 19-cell accumulator and the
#: ten cells between them take harmless junk. ``base(c) + 18 <= 63`` always, so
#: the window never runs off the image segment.
GRAD_SPAN = 19
GRAD_CELL = {(ky * K + kx): ky * IMG + kx for ky in range(K) for kx in range(K)}

# ── ring B: one cyclic buffer, four regions ──────────────────────────────────
#
# X       the image, row-major Q12 — written by the UPDB trick, read by the conv
#         forward pass (as the multiplicand) and by the conv weight gradient.
# ONE     a run of ones: the `g` UPDB needs to write ring B, and the `b` a
#         `MAC 1` needs to lift a CPU scalar into the accumulator.
# WI      the dense weights, feature-major: block `i` is `W[0..9][i]`, so the
#         forward pass is one `MAC 10` a feature and the update one `UPDB 10`.
# WJ      the same weights, class-major: block `j` is `W[j][0..17]`, so the
#         pooled-gradient pass is one `MAC 18` a class. Two layouts because the
#         forward reduces over `i` and the backward over `j`, and a reduction has
#         to run across MAC *commands* while the parallel axis is what one
#         command walks. Both are updated by their own UPDB lap and stay equal,
#         because `(a*g) >> s` is symmetric in `a` and `g`.
#
# Every block carries as much filler as it has weights. The filler is what makes
# a UPDB lap self-aligning: `UPDB n` moves the gradient ring from P1 to P2, and
# only a `MAC` can move it back, so the refilling `MAC n` has to consume *some*
# ring B — spending it on filler leaves the next block exactly under the head.
X0, NX = 0, IMG * IMG
ONE0, NONE = X0 + NX, 72
WI0, WI_STRIDE = ONE0 + NONE, 2 * NCLS
WJ0, WJ_STRIDE = WI0 + NFEAT * WI_STRIDE, 2 * NFEAT
RING_B = WJ0 + NCLS * WJ_STRIDE

#: ``(a_slots, b_slots, c_slots)`` for ``machine.build(stream=...)``. Ring A
#: never holds more than the six scalars one conv tap pushes; ring B is the
#: layout above; the accumulator's high-water mark is the 72 conv seeds.
RING_SIZES = (16, RING_B, 80)

#: How many ring B words the boot ``FILLB`` consumes, i.e. how many words of
#: initial state the input room hands over before the first image.
BOOT_WORDS = RING_B

#: Every ROM literal is non-negative (``ARCH.md`` §4.2), so the initial weights
#: reach the ring biased by this and one UPDB lap subtracts it again.
WEIGHT_BIAS = SCALE


# ── the STORE map ────────────────────────────────────────────────────────────
class _Store:
    """Named STORE addresses, allocated in one place so the map is auditable.

    **Allocation starts at 1, not 0.** The hardware STORE encodes read-vs-write in
    the *sign* of the address word, so slot 0 is sign-ambiguous and unaddressable
    (``machine.rom_words`` refuses ``ST 0`` outright). The emulator's ``DictStore``
    does not care, which is exactly why this has to be pinned here: allocating from
    zero built a program that ran perfectly on the emulator and could not be turned
    into a machine at all. Slot 0 is left permanently unused.
    """

    def __init__(self) -> None:
        self._next = 1
        self.names: dict[str, tuple[int, int]] = {}

    def alloc(self, name: str, n: int = 1) -> int:
        base = self._next
        self.names[name] = (base, n)
        self._next += n
        setattr(self, name, base)
        return base

    @property
    def size(self) -> int:
        return self._next


S = _Store()
S.alloc("PRE", NF * CELLS)  # conv pre-activations, f*36 + c
S.alloc("POOLED", NFEAT)
S.alloc("ARGMAX", NFEAT)
S.alloc("DPOOLED", NFEAT)
S.alloc("LOGITS", NCLS)
S.alloc("EXPS", NCLS)
S.alloc("PROBS", NCLS)
S.alloc("DZ", NCLS)
S.alloc("CONVW", NF * TAPS)
S.alloc("CONVB", NF)
S.alloc("DENSEB", NCLS)
S.alloc("CBG", NF)  # conv bias gradient accumulator
S.alloc("X", NX)  # the Q12 image the ring B copy was written from
S.alloc("EXP", len(EXP_TABLE))
S.alloc("LNM", len(LN_MANT_TABLE))
S.alloc("PKW", md.WORDS_PER_IMAGE)
S.alloc("LABEL")
S.alloc("TOP")
S.alloc("PRED")
S.alloc("BEST")
S.alloc("BAT")
S.alloc("TOTAL")
S.alloc("LO")
S.alloc("HL")
S.alloc("DD")
S.alloc("TMP")
S.alloc("TMP2")
S.alloc("BASE")
S.alloc("LOSS")
S.alloc("CORRECT")
S.alloc("VLOSS")
S.alloc("VCORRECT")
S.alloc("TCOUNT")
S.alloc("VCOUNT")
S.alloc("EPOCHS")
#: The plot column the next epoch paints, so the panels do not have to derive it
#: from the epoch count — which is an *input* word and therefore unknown at emit
#: time. Held in the STORE rather than a ring slot because ``ADD`` reads it
#: straight into the ADDR word (``row * 64 + PCOL``).
S.alloc("PCOL")
STORE_WORDS = S.size


class _RingError(AssertionError):
    """The generator's model of a ring disagreed with the layout it emitted."""


def _layout() -> list[str]:
    """One name per ring B slot, in cyclic order — what the assertions check."""
    slots = [f"X{q}" for q in range(NX)]
    slots += [f"ONE{k}" for k in range(NONE)]
    for i in range(NFEAT):
        slots += [f"WI{i}.{j}" for j in range(NCLS)]
        slots += [f"WIF{i}.{k}" for k in range(NCLS)]
    for j in range(NCLS):
        slots += [f"WJ{j}.{i}" for i in range(NFEAT)]
        slots += [f"WJF{j}.{k}" for k in range(NFEAT)]
    if len(slots) != RING_B:
        raise _RingError(f"layout is {len(slots)} slots, RING_B is {RING_B}")
    return slots


BLAY = _layout()


class _Gen:
    """Emits asm while tracking ring A, ring B and the accumulator exactly.

    The tracking is not decoration: a ``MAC n`` reads whatever happens to be
    under ring B's head, so a generator that has lost count emits a program that
    computes something plausible and wrong. Every command below updates the model
    and every ring B read states which slot it expects.
    """

    def __init__(self, *, lr_shift: int) -> None:
        self.lines: list[str] = []
        self.lr_shift = lr_shift
        self.unit_shift = SHIFT + lr_shift
        self.bpos = 0  # layout index currently under ring B's head
        self.p1: list[str] = []  # accumulator, ADDER -> unit (drained by RDP)
        self.p2: list[str] = []  # accumulator, unit -> ADDER (seeded by ZEROC)
        #: Ring A, as a queue of tags. It is modelled by name and not just by
        #: count because ``PUSHA`` does two jobs — it queues a scalar for the next
        #: ``MAC`` *and* records the scalar the next ``UPDB`` will use — so a push
        #: meant only for a UPDB leaves a word behind that the following MAC would
        #: eat instead of its own. That bug computes plausible nonsense; this
        #: queue turns it into an assertion at emit time.
        self.aq: list[str] = []
        self.instr = 0
        #: Label prefix. The train and validation bodies are two unrolled copies
        #: of the same sections, so every local label needs a scope or the two
        #: collide — and the assembler is right to refuse a duplicate.
        self.scope = ""

    def sym(self, name: str) -> str:
        return f"{self.scope}{name}"

    # ── raw emission ────────────────────────────────────────────────────────
    def op(self, text: str, note: str = "") -> None:
        self.lines.append(f"        {text:<22}{'; ' + note if note else ''}".rstrip())
        self.instr += 1

    def say(self, note: str) -> None:
        self.lines.append(f"        ; {note}")

    def blank(self) -> None:
        self.lines.append("")

    def label(self, name: str) -> None:
        self.lines.append(f"{name}:")

    # ── CPU shorthands ──────────────────────────────────────────────────────
    def ldi(self, v: int, note: str = "") -> None:
        if v < 0:
            raise ValueError(f"LDI {v}: ROM literals are non-negative (ARCH §4.2)")
        self.op(f"LDI {v}", note)

    #: The widest ROM literal this program is allowed to hold. ``rom.digit_width``
    #: is uniform across the ROM, so one oversized constant widens *every* word:
    #: at 17 digits (``16**14``, the naive nibble shift) the uniform serpentine
    #: costs 21 cells a word against 11 at 7 digits — a 48% tax on the whole ROM
    #: for one instruction's convenience. Bounding it costs ~56 instructions a
    #: sample body, which is 1% of one.
    MAX_LITERAL_DIGITS = 7
    SHIFT_CHUNK = 5  # 16**5 = 1,048,576: seven digits

    def divi_pow16(self, k: int, note: str = "") -> None:
        """``ACC //= 16**k``, in chunks no literal wider than the budget."""
        if k < 0:
            raise ValueError("negative nibble shift")
        first = note
        while k:
            step = min(k, self.SHIFT_CHUNK)
            self.op(f"DIVI {16 ** step}", first)
            first = ""
            k -= step

    def ld(self, addr: int, note: str = "") -> None:
        self.op(f"LD {addr}", note)

    def st(self, addr: int, note: str = "") -> None:
        self.op(f"ST {addr}", note)

    def snd(self, note: str = "") -> None:
        self.op("SND", note)

    def rcv(self, note: str = "") -> None:
        self.op("RCV", note)

    # ── command words ───────────────────────────────────────────────────────
    def _word(self, arm: str, arg: int) -> int:
        return 16 * arg + C[arm]

    def cmd(self, arm: str, arg: int, note: str = "") -> None:
        """One command with a compile-time argument: ``LDI w`` then ``SND``."""
        self.ldi(self._word(arm, arg), note or f"{arm} {arg}")
        self.snd()

    def cmd_acc(self, arm: str, note: str = "") -> None:
        """One command whose argument is already in ACC (runtime scalars)."""
        self.op("MULI 16", note or f"{arm} <acc>")
        self.op(f"ADDI {C[arm]}")
        self.snd()

    # ── ring B model ────────────────────────────────────────────────────────
    def expect_b(self, first: str, note: str = "") -> None:
        got = BLAY[self.bpos]
        if got != first:
            raise _RingError(f"ring B head is {got}, expected {first} ({note})")

    def _advance_b(self, n: int) -> None:
        self.bpos = (self.bpos + n) % RING_B

    def rotb(self, n: int, note: str = "") -> None:
        n %= RING_B
        if n:
            self.cmd("ROTB", n, note or f"ROTB {n}")
            self._advance_b(n)

    def rotb_to(self, slot: str, note: str = "") -> None:
        target = BLAY.index(slot)
        self.rotb((target - self.bpos) % RING_B, note or f"ring B -> {slot}")

    # ── accumulator model ───────────────────────────────────────────────────
    def zeroc(self, n: int, tag: str) -> None:
        self.cmd("ZEROC", n, f"ZEROC {n} ({tag})")
        self.p2 += [tag] * n

    def pusha(self, v: int, tag: str = "") -> None:
        self.cmd("PUSHA", v)
        self.aq.append(tag or str(v))

    def pusha_acc(self, note: str = "", tag: str = "") -> None:
        self.cmd_acc("PUSHA", note)
        self.aq.append(tag or note or "acc")

    def mac(self, n: int, note: str = "", *, scalar: str | None = None) -> None:
        if not self.aq:
            raise _RingError("MAC with ring A empty")
        got = self.aq.pop(0)
        if scalar is not None and got != scalar:
            raise _RingError(f"MAC {n} would consume ring A's {got!r}, wanted {scalar!r}")
        if len(self.p2) < n:
            raise _RingError(f"MAC {n} with only {len(self.p2)} words in P2")
        self.cmd("MAC", n, note or f"MAC {n}")
        self.p1 += self.p2[:n]
        self.p2 = self.p2[n:]
        self._advance_b(n)

    def fwd(self, n: int) -> None:
        if len(self.p1) < n:
            raise _RingError(f"FWD {n} with only {len(self.p1)} words in P1")
        self.cmd("FWD", n)
        self.p2 += self.p1[:n]
        self.p1 = self.p1[n:]

    def updb(self, n: int, note: str = "") -> None:
        """One rank-one update. Ring A must hold *exactly* the scalar UPDB will use.

        The model reads a remembered ``_scalar_a`` and leaves ``ring_a`` alone; the
        *drawn* arm has no register to remember it in (the shift needs both hands),
        so it re-reads the scalar off ring A every iteration and pushes it straight
        back — a rotation, which equals "leave ring A alone" only for a one-value
        ring. So the two tiers agree exactly while ``aq`` holds one word, and would
        disagree *silently* otherwise: the grid would multiply by whatever was queued
        ahead of the scalar and the emulator would not. ``updb_from_acc`` creates
        that precondition (``PUSHA``, ``UPDB``, ``MAC 0``); this asserts it, so a
        future caller that queues two words gets an emit-time error instead of two
        tiers that disagree. See ``stream.updb_body``'s docstring.
        """
        if len(self.aq) != 1:
            raise _RingError(
                f"UPDB {n} with ring A holding {self.aq}: the drawn arm re-reads its "
                "scalar off ring A every iteration, so ring A must hold exactly the "
                "scalar and nothing else or the grid and the emulator diverge silently"
            )
        if len(self.p1) < n:
            raise _RingError(f"UPDB {n} with only {len(self.p1)} words in P1")
        self.cmd("UPDB", n, note or f"UPDB {n}")
        self.p2 += self.p1[:n]
        self.p1 = self.p1[n:]
        self._advance_b(n)

    def rdp(self, note: str = "") -> None:
        if not self.p1:
            raise _RingError("RDP with P1 empty")
        self.cmd("RDP", 0, note or "RDP")
        self.rcv()
        self.p1 = self.p1[1:]

    def drain_a(self, tag: str | None = None) -> None:
        """``MAC 0``: pop one scalar off ring A and do nothing with it.

        ``PUSHA`` both records UPDB's scalar *and* queues a word on ring A, so a
        PUSHA that only meant to set the scalar leaves a word behind. Ring A has
        a finite depth in hardware, so the word has to go — and if it did not, the
        *next* MAC would multiply by it.
        """
        self.mac(0, "MAC 0 (drop the PUSHA scalar)", scalar=tag)

    def updb_from_acc(self, n: int, *, tag: str, note: str = "") -> None:
        """ACC holds UPDB's scalar: push it, run the update, drop the queued word."""
        self.pusha_acc(note or f"PUSHA {tag}", tag=tag)
        self.updb(n, f"UPDB {n}")
        self.drain_a(tag)

    def lift(self, n: int, tag: str, values: Iterable[int]) -> None:
        """Put ``n`` CPU-held words into P1: ``ZEROC`` then ``MAC 1`` against a one.

        ``0 + v * 1 == v``, so a MAC against ring B's ONE run is how a CPU value
        reaches the accumulator at all — nothing else writes it.
        """
        self.zeroc(n, tag)
        self.rotb_to("ONE0")
        for k, addr in enumerate(values):
            self.expect_b(f"ONE{k}", "lift")
            self.ld(addr)
            self.pusha_acc(f"PUSHA {tag}[{k}]", tag=f"lift{k}")
            self.mac(1, "MAC 1 (lift into P1)", scalar=f"lift{k}")

    def acc_empty(self, where: str) -> None:
        if self.p1 or self.p2:
            raise _RingError(f"{where}: accumulator not empty ({self.p1}/{self.p2})")
        if self.aq:
            raise _RingError(f"{where}: ring A still holds {self.aq}")


# ── sections ─────────────────────────────────────────────────────────────────
def _header(g: _Gen, *, lr_shift: int, train_n: int, val_n: int) -> None:
    """The maps a human needs to read the listing: STORE, ring B, arm codes.

    The listing is ~10,000 lines of straight-line code addressing the STORE by
    number, and Task 8 will read it while chasing a disagreement between the
    emulator and the grid. Naming the regions once at the top is the difference
    between "ST 341" meaning something and meaning nothing.
    """
    g.lines.append("; Generated by randomfun2026solvers.mnist_cnn — do not edit.")
    g.lines.append(f"; MNIST CNN, unrolled: lr_shift={lr_shift}, {train_n} train, {val_n} val.")
    g.lines.append("; STREAM unit: depth-4 trie, 16*arg+code.")
    g.lines.append(";")
    g.lines.append("; STORE map:")
    for name, (base, n) in S.names.items():
        span = f"{base}" if n == 1 else f"{base}..{base + n - 1}"
        g.lines.append(f";   {name:<8} {span}")
    g.lines.append(";")
    g.lines.append(f"; ring B ({RING_B} words, cyclic):")
    g.lines.append(f";   X    {X0}..{X0 + NX - 1}      the image, row-major Q12")
    g.lines.append(f";   ONE  {ONE0}..{ONE0 + NONE - 1}    ones: UPDB's g, and MAC 1's b")
    g.lines.append(f";   WI   {WI0}..{WJ0 - 1}   dense weights, feature-major, stride {WI_STRIDE}")
    g.lines.append(f";   WJ   {WJ0}..{RING_B - 1}   dense weights, class-major, stride {WJ_STRIDE}")
    g.lines.append(";")
    g.lines.append("; command codes: " + ", ".join(f"{k}={v}" for k, v in C.items()))
    g.lines.append("        .unit stream4")
    g.lines.append(
        f"        .equ {asm.STREAM_LR_SHIFT_EQU} {SHIFT + lr_shift}"
        f"   ; UPDB's shift: SHIFT + lr_shift({lr_shift})"
    )
    g.blank()


def _boot(g: _Gen, *, epochs_from_input: bool, epochs: int) -> None:
    """Read the epoch count, load the tables, fill ring B, unbias the weights."""
    g.label("boot")
    if epochs_from_input:
        g.op("IN", "epochs")
    else:
        g.ldi(epochs, "epochs (baked)")
    g.st(S.EPOCHS)

    g.say("the frozen exp and log tables, into the STORE for indexed reads")
    for k, v in enumerate(EXP_TABLE):
        g.ldi(v)
        g.st(S.EXP + k)
    for k, v in enumerate(LN_MANT_TABLE):
        g.ldi(v)
        g.st(S.LNM + k)

    g.say("conv weights live in the STORE: the conv pass wants them as scalars")
    params = mm.init_params(seed=SEED)
    for k, w in enumerate(params.conv_w):
        g.ldi(w + WEIGHT_BIAS, f"conv_w[{k}] = {w}")
        g.op(f"SUBI {WEIGHT_BIAS}")
        g.st(S.CONVW + k)
    for k in range(NF):
        g.ldi(0)
        g.st(S.CONVB + k)
    for k in range(NCLS):
        g.ldi(0)
        g.st(S.DENSEB + k)

    g.say(f"ring B: {RING_B} words from the input room, weights biased by {WEIGHT_BIAS}")
    g.cmd("FILLB", RING_B, f"FILLB {RING_B}")

    g.say("one UPDB lap subtracts the bias: a is constant, so 72 slots a command")
    g.zeroc(NONE, "one")
    g.rotb_to("ONE0")
    g.pusha(1, tag="one")
    g.mac(NONE, f"MAC {NONE} (P1 <- ones)", scalar="one")
    g.rotb_to("WI0.0")
    chunk = NONE
    total = NFEAT * WI_STRIDE + NCLS * WJ_STRIDE
    if total % chunk:
        raise _RingError(f"{total} weight slots is not a multiple of {chunk}")
    g.say(
        f"a = {WEIGHT_BIAS}<<{g.unit_shift} built at runtime: as a literal it would be 11"
    )
    g.say("digits, and rom.digit_width is uniform, so it would widen all 17,207 words")
    for _ in range(total // chunk):
        g.ldi(WEIGHT_BIAS)
        g.op(f"MULI {1 << g.unit_shift}")
        g.pusha_acc(f"PUSHA {WEIGHT_BIAS}<<{g.unit_shift}", tag="bias")
        g.updb(chunk, f"UPDB {chunk} (subtract the bias)")
        g.drain_a("bias")
        g.pusha(0, tag="zero")
        g.mac(chunk, f"MAC {chunk} (P1 <- the ones again)", scalar="zero")
        g.rotb(-chunk)
    g.rotb_to("X0")
    g.say("drop the ones: every sample body starts with an empty accumulator")
    for _ in range(NONE):
        g.rdp()
    g.acc_empty("after boot")


def _load_sample(g: _Gen) -> None:
    """Five words off the dataset ring, unpacked into ring B's image segment.

    The unpack and the ring write are fused per pixel because they share the
    value: ``UPDB`` can only *adjust* a ring B slot, so writing ``v`` needs the
    slot's current contents, which is the previous sample's pixel — kept in the
    STORE for exactly this reason.
    """
    g.say("five packed words off the dataset ring")
    for w in range(md.WORDS_PER_IMAGE):
        g.cmd("RDIN", 0, "RDIN")
        g.rcv()
        g.st(S.PKW + w)
    g.divi_pow16(4, "label: nibble 4 of word 4")
    g.op("MODI 16")
    g.st(S.LABEL)

    g.say("P1 <- 72 ones: 64 for the image write, the rest seed the conv")
    g.acc_empty("load_sample")
    g.zeroc(NONE, "one")
    g.rotb_to("ONE0")
    g.pusha(1, tag="one")
    g.mac(NONE, f"MAC {NONE} (P1 <- ones)", scalar="one")

    g.rotb_to("X0")
    for q in range(NX):
        word, nib = divmod(q, md.NIBBLES_PER_WORD)
        g.expect_b(f"X{q}", "image write")
        g.ld(S.X + q, f"x[{q}]: the slot's current contents")
        g.st(S.TMP)
        g.ld(S.PKW + word)
        g.divi_pow16(nib)
        g.op("MODI 16")
        g.op(f"MULI {SCALE}")
        g.op(f"DIVI {mm.PIXEL_MAX}")
        g.st(S.X + q, "the new Q12 pixel")
        g.ld(S.TMP)
        g.op(f"SUB {S.X + q}", "b - v")
        g.op(f"MULI {16 << g.unit_shift}", f"PUSHA (b-v)<<{g.unit_shift}")
        g.op(f"ADDI {C['PUSHA']}")
        g.snd()
        g.aq.append("write")
        g.updb(1, "UPDB 1 -> ring B holds v")
        g.drain_a("write")
    g.say("hand P1's leftover ones back to P2: every tap starts with a full P2")
    g.fwd(NONE - NX)


def _conv_forward(g: _Gen) -> None:
    """Nine offset-sequential passes a filter, over the image in ring B.

    For a fixed tap the pass walks the image and the accumulator in order at a
    constant offset, six cells at a time with the row stride made up by ``ROTB``.
    The accumulator's 72 cells start at 1 rather than 0 — they are the ones the
    image write left behind, and reusing them is cheaper than draining them and
    zeroing fresh ones. The CPU takes the 1 back off when it reads the cell.
    """
    for f in range(NF):
        g.blank()
        g.say(f"conv filter {f}: 9 taps x 6 rows of MAC 6")
        for t in range(TAPS):
            ky, kx = divmod(t, K)
            d = ky * IMG + kx
            g.ld(S.CONVW + f * TAPS + t, f"w[{f}][{t}] as the scalar, six copies")
            g.op("MULI 16")
            g.op(f"ADDI {C['PUSHA']}")
            for _ in range(CO):
                g.snd()
                g.aq.append(f"w{f}.{t}")
            g.rotb_to(f"X{d}", f"tap {t}: offset {d}")
            for r in range(CO):
                g.expect_b(f"X{d + r * IMG}", f"conv f{f} t{t} row {r}")
                g.mac(CO, f"MAC {CO} (row {r})", scalar=f"w{f}.{t}")
                if r < CO - 1:
                    g.rotb(IMG - CO)
            if t < TAPS - 1:
                if f == 0:
                    g.pusha(0, tag="zero")
                    g.mac(CELLS, f"MAC {CELLS} (step over filter 1's seeds)", scalar="zero")
                    g.fwd(NF * CELLS)
                else:
                    g.fwd(CELLS)
        g.say(f"read filter {f}'s 36 raw sums out of P1")
        for c in range(CELLS):
            g.rdp(f"pre[{f}][{c}]")
            g.op("SUBI 1", "the accumulator seeded at 1")
            g.op(f"DIVI {SCALE}", "accumulate then shift, once")
            g.op(f"ADD {S.CONVB + f}")
            g.st(S.PRE + f * CELLS + c)


def _relu_pool(g: _Gen) -> None:
    """ReLU and 2x2 max pooling, on the CPU, ties to the lowest index.

    The mask the backward pass needs is ``pre[argmax] > 0``, which is the same
    predicate as ``pooled > 0`` — so no ReLU'd copy of the 72 activations is
    stored, and the argmax is only ever consulted when the gate is open.
    """
    g.blank()
    g.say("ReLU + MaxPool2: pooled[i] and the winning cell")
    for i in range(NFEAT):
        f, w = divmod(i, mm.POOL_OUT * mm.POOL_OUT)
        py, px = divmod(w, mm.POOL_OUT)
        c0 = (py * mm.POOL) * CO + px * mm.POOL
        cells = [f * CELLS + c0, f * CELLS + c0 + 1, f * CELLS + c0 + CO, f * CELLS + c0 + CO + 1]
        g.ld(S.PRE + cells[0], f"feature {i}")
        g.st(S.BEST)
        g.ldi(cells[0])
        g.st(S.BAT)
        for n, cell in enumerate(cells[1:]):
            skip = g.sym(f"pool{i}_{n}")
            g.ld(S.PRE + cell)
            g.op(f"SUB {S.BEST}")
            g.op(f"BRN {skip}")
            g.op(f"BRZ {skip}")
            g.ld(S.PRE + cell)
            g.st(S.BEST)
            g.ldi(cell)
            g.st(S.BAT)
            g.label(skip)
        g.ld(S.BEST)
        g.op(f"BRN {g.sym(f'pool{i}_neg')}")
        g.st(S.POOLED + i)
        g.op(f"JMP {g.sym(f'pool{i}_done')}")
        g.label(g.sym(f"pool{i}_neg"))
        g.ldi(0)
        g.st(S.POOLED + i)
        g.label(g.sym(f"pool{i}_done"))
        g.ld(S.BAT)
        g.st(S.ARGMAX + i)


def _dense_forward(g: _Gen) -> None:
    """One ``MAC 10`` a feature over the feature-major weights, then the biases."""
    g.blank()
    g.say("dense: 18 x { PUSHA f[i], MAC 10 }")
    g.acc_empty("dense_forward")
    g.zeroc(NCLS, "logit")
    g.rotb_to("WI0.0")
    for i in range(NFEAT):
        g.expect_b(f"WI{i}.0", "dense forward")
        g.ld(S.POOLED + i)
        g.pusha_acc(f"PUSHA f[{i}]", tag="f")
        g.mac(NCLS, f"MAC {NCLS}", scalar="f")
        if i < NFEAT - 1:
            g.fwd(NCLS)
            g.rotb(NCLS, "step over the filler")
    for j in range(NCLS):
        g.rdp(f"logit[{j}]")
        g.op(f"DIVI {SCALE}")
        g.op(f"ADD {S.DENSEB + j}")
        g.st(S.LOGITS + j)


def _softmax(g: _Gen) -> None:
    """Max-subtract, the 32-entry exp LUT with interpolation, then normalise."""
    g.blank()
    g.say("argmax over logits (predict reads logits, not probs) and the max")
    g.ld(S.LOGITS)
    g.st(S.TOP)
    g.ldi(0)
    g.st(S.PRED)
    for j in range(1, NCLS):
        g.ld(S.LOGITS + j)
        g.op(f"SUB {S.TOP}")
        g.op(f"BRN {g.sym(f'top{j}')}")
        g.op(f"BRZ {g.sym(f'top{j}')}")
        g.ld(S.LOGITS + j)
        g.st(S.TOP)
        g.ldi(j)
        g.st(S.PRED)
        g.label(g.sym(f"top{j}"))

    g.say("exp((z - top)) from the LUT: index (-z) >> 10, interpolate below 31")
    g.ldi(0)
    g.st(S.TOTAL)
    for j in range(NCLS):
        g.ld(S.TOP)
        g.op(f"SUB {S.LOGITS + j}", "d = top - z >= 0")
        g.st(S.DD)
        g.op("DIVI 1024")
        g.op(f"SUBI {len(EXP_TABLE) - 1}")
        g.op(f"BRN {g.sym(f'exp{j}')}")
        g.ldi(EXP_TABLE[-1], "clamped: exp(z) is under one Q12 tick")
        g.st(S.EXPS + j)
        g.op(f"JMP {g.sym(f'exp{j}_done')}")
        g.label(g.sym(f"exp{j}"))
        g.ld(S.DD)
        g.op("DIVI 1024")
        g.op(f"ADDI {S.EXP}")
        g.op("LDA", "lo")
        g.st(S.LO)
        g.ld(S.DD)
        g.op("DIVI 1024")
        g.op(f"ADDI {S.EXP + 1}")
        g.op("LDA", "hi")
        g.op(f"SUB {S.LO}")
        g.st(S.HL)
        g.ld(S.DD)
        g.op("MODI 1024")
        g.op(f"MUL {S.HL}")
        g.op("DIVI 1024")
        g.op(f"ADD {S.LO}")
        g.st(S.EXPS + j)
        g.label(g.sym(f"exp{j}_done"))
        g.ld(S.EXPS + j)
        g.op(f"ADD {S.TOTAL}")
        g.st(S.TOTAL)
    for j in range(NCLS):
        g.ld(S.EXPS + j)
        g.op(f"MULI {SCALE}")
        g.op(f"DIV {S.TOTAL}")
        g.st(S.PROBS + j)


def _report_sample(g: _Gen, *, loss: int, correct: int) -> None:
    """Online metrics: cross-entropy from the log table, and the prediction."""
    g.blank()
    g.say("cross-entropy: -ln(probs[label]) through the 33-endpoint log table")
    g.ld(S.LABEL)
    g.op(f"ADDI {S.PROBS}")
    g.op("LDA")
    g.st(S.TMP)
    g.op(f"BRZ {g.scope}clamp")
    g.op(f"JMP {g.scope}have")
    g.label(f"{g.scope}clamp")
    g.ldi(1, "a zero probability caps the loss at -ln(1/4096)")
    g.st(S.TMP)
    g.label(f"{g.scope}have")
    g.ldi(0)
    g.st(S.TMP2, "the exponent")
    for step in range(SHIFT):
        g.ld(S.TMP)
        g.op(f"SUBI {SCALE}")
        g.op(f"BRN {g.scope}norm{step}")
        g.op(f"JMP {g.scope}norm{step}_done")
        g.label(f"{g.scope}norm{step}")
        g.ld(S.TMP)
        g.op("MULI 2")
        g.st(S.TMP)
        g.ld(S.TMP2)
        g.op("SUBI 1")
        g.st(S.TMP2)
        g.label(f"{g.scope}norm{step}_done")
    shift = SHIFT - 5
    g.ld(S.TMP)
    g.op(f"SUBI {SCALE}", "frac = mantissa - 1.0")
    g.st(S.TMP)
    g.op(f"DIVI {1 << shift}")
    g.op(f"ADDI {S.LNM}")
    g.op("LDA")
    g.st(S.LO)
    g.ld(S.TMP)
    g.op(f"DIVI {1 << shift}")
    g.op(f"ADDI {S.LNM + 1}")
    g.op("LDA")
    g.op(f"SUB {S.LO}")
    g.st(S.HL)
    g.ld(S.TMP)
    g.op(f"MODI {1 << shift}")
    g.op(f"MUL {S.HL}")
    g.op(f"DIVI {1 << shift}")
    g.op(f"ADD {S.LO}")
    g.st(S.TMP)
    g.ld(S.TMP2)
    g.op(f"MULI {LN2}")
    g.op(f"ADD {S.TMP}")
    g.op("NEG", "loss = -ln(p)")
    g.op(f"ADD {loss}")
    g.st(loss)

    g.ld(S.PRED)
    g.op(f"SUB {S.LABEL}")
    g.op(f"BRZ {g.scope}hit")
    g.op(f"JMP {g.scope}scored")
    g.label(f"{g.scope}hit")
    g.ld(correct)
    g.op("ADDI 1")
    g.st(correct)
    g.label(f"{g.scope}scored")


def _backward(g: _Gen) -> None:
    """``dz``, the pooled gradient, the conv gradients, then both UPDB laps."""
    g.blank()
    g.say("dz = probs - onehot(label)")
    for j in range(NCLS):
        g.ld(S.PROBS + j)
        g.st(S.DZ + j)
    g.ld(S.LABEL)
    g.op(f"ADDI {S.DZ}")
    g.st(S.TMP)
    g.op(f"LDP {S.TMP}")
    g.op(f"SUBI {SCALE}")
    g.op(f"STP {S.TMP}")

    g.blank()
    g.say("dpooled: one MAC 18 a class over the class-major weights")
    g.acc_empty("dpooled")
    g.zeroc(NFEAT, "dpooled")
    g.rotb_to("WJ0.0")
    for j in range(NCLS):
        g.expect_b(f"WJ{j}.0", "dpooled")
        g.ld(S.DZ + j)
        g.pusha_acc(f"PUSHA dz[{j}]", tag="dz")
        g.mac(NFEAT, f"MAC {NFEAT}", scalar="dz")
        if j < NCLS - 1:
            g.fwd(NFEAT)
            g.rotb(NFEAT, "step over the filler")
    for i in range(NFEAT):
        g.rdp(f"dpooled[{i}]")
        g.op(f"DIVI {SCALE}")
        g.st(S.DPOOLED + i)

    g.blank()
    g.say("route through argmax and the ReLU gate, then the conv weight gradient")
    for f in range(NF):
        g.ldi(0)
        g.st(S.CBG + f)
    per = NFEAT // NF
    for f in range(NF):
        g.acc_empty(f"conv grad f{f}")
        g.zeroc(GRAD_SPAN, f"cgrad{f}")
        g.rotb_to("X0")
        for n in range(per):
            i = f * per + n
            g.ld(S.POOLED + i, f"gate: dpre is zero unless pooled[{i}] > 0")
            g.op(f"BRZ {g.sym(f'cg{i}_zero')}")
            g.ld(S.DPOOLED + i)
            g.op(f"JMP {g.sym(f'cg{i}_have')}")
            g.label(g.sym(f"cg{i}_zero"))
            g.ldi(0)
            g.label(g.sym(f"cg{i}_have"))
            g.st(S.TMP)
            g.op(f"ADD {S.CBG + f}", "conv bias gradient is the sum of dpre")
            g.st(S.CBG + f)
            g.ld(S.ARGMAX + i)
            g.op(f"SUBI {f * CELLS}", "cell within the filter")
            g.st(S.TMP2)
            g.op(f"DIVI {CO}")
            g.op(f"MULI {IMG}")
            g.st(S.BASE)
            g.ld(S.TMP2)
            g.op(f"MODI {CO}")
            g.op(f"ADD {S.BASE}")
            g.st(S.BASE, "base = (c/6)*8 + c%6")
            g.expect_b("X0", "conv grad")
            g.cmd_acc("ROTB", "ring B -> X[base]")
            g.ld(S.TMP)
            g.pusha_acc("PUSHA dpre", tag="dpre")
            g.cmd("MAC", GRAD_SPAN, f"MAC {GRAD_SPAN} (the padded 3x3 patch)")
            if g.aq.pop(0) != "dpre":
                raise _RingError("conv gradient MAC would eat the wrong scalar")
            if len(g.p2) < GRAD_SPAN:
                raise _RingError("conv gradient MAC with P2 short")
            g.p1 += g.p2[:GRAD_SPAN]
            g.p2 = g.p2[GRAD_SPAN:]
            g.ldi(RING_B - GRAD_SPAN)
            g.op(f"SUB {S.BASE}")
            g.cmd_acc("ROTB", "ring B -> X0")
            if n < per - 1:
                g.fwd(GRAD_SPAN)
        for a in range(GRAD_SPAN):
            g.rdp()
            if a in GRAD_CELL.values():
                t = next(k for k, v in GRAD_CELL.items() if v == a)
                g.op(f"DIVI {1 << (SHIFT + g.lr_shift)}", f"w -= grad >> {g.lr_shift}")
                g.st(S.TMP)
                g.ld(S.CONVW + f * TAPS + t)
                g.op(f"SUB {S.TMP}")
                g.st(S.CONVW + f * TAPS + t)
        g.ld(S.CBG + f)
        g.op(f"DIVI {1 << g.lr_shift}")
        g.st(S.TMP)
        g.ld(S.CONVB + f)
        g.op(f"SUB {S.TMP}")
        g.st(S.CONVB + f)

    g.blank()
    g.say("dense bias")
    for j in range(NCLS):
        g.ld(S.DZ + j)
        g.op(f"DIVI {1 << g.lr_shift}")
        g.st(S.TMP)
        g.ld(S.DENSEB + j)
        g.op(f"SUB {S.TMP}")
        g.st(S.DENSEB + j)

    g.blank()
    g.say("weight update, feature-major lap: a = f[i], g = dz[j]")
    g.acc_empty("WI update")
    g.lift(NCLS, "dz", [S.DZ + j for j in range(NCLS)])
    g.rotb_to("WI0.0")
    for i in range(NFEAT):
        g.expect_b(f"WI{i}.0", "WI update")
        g.ld(S.POOLED + i)
        g.updb_from_acc(NCLS, tag="f", note=f"PUSHA f[{i}]")
        g.pusha(0, tag="zero")
        g.mac(NCLS, f"MAC {NCLS} (filler: P1 <- dz again)", scalar="zero")
    for _ in range(NCLS):
        g.rdp()

    g.blank()
    g.say("weight update, class-major lap: a = dz[j], g = f[i] — same product")
    g.acc_empty("WJ update")
    g.lift(NFEAT, "f", [S.POOLED + i for i in range(NFEAT)])
    g.rotb_to("WJ0.0")
    for j in range(NCLS):
        g.expect_b(f"WJ{j}.0", "WJ update")
        g.ld(S.DZ + j)
        g.updb_from_acc(NFEAT, tag="dz", note=f"PUSHA dz[{j}]")
        g.pusha(0, tag="zero")
        g.mac(NFEAT, f"MAC {NFEAT} (filler: P1 <- f again)", scalar="zero")
    for _ in range(NFEAT):
        g.rdp()
    g.acc_empty("end of backward")


def _epoch_report(g: _Gen, *, train_n: int, val_n: int) -> None:
    """Four ``OUT``s an epoch: train loss, train accuracy, val loss, val accuracy."""
    g.blank()
    g.say("four words an epoch: train loss, train acc, val loss, val acc")
    g.ld(S.LOSS)
    g.op(f"DIVI {train_n}")
    g.op("OUT")
    g.ld(S.CORRECT)
    g.op("MULI 100")
    g.op(f"DIVI {train_n}")
    g.op("OUT")
    g.ld(S.VLOSS)
    g.op(f"DIVI {val_n}")
    g.op("OUT")
    g.ld(S.VCORRECT)
    g.op("MULI 100")
    g.op(f"DIVI {val_n}")
    g.op("OUT")


# ── the panels ───────────────────────────────────────────────────────────────
#: The relay opcode that reaches each panel. One lane per panel, because a room may
#: feed at most one display, so each panel needs its own relay room — see
#: ``lm1/isa.py``'s ``DSP2`` and ``tests/test_mnist_display.py``'s R1.
_PANEL_OP = ("DSP", "DSP2")


def _dsp(g: _Gen, panel: int, port: int, note: str = "") -> None:
    g.op(f"{_PANEL_OP[panel]} {port}", note)


def _axes(g: _Gen) -> None:
    """Paint both panels' axes once, at boot.

    The panels are persistent framebuffers (every commit is ``SWAP 1``), so the
    axes are drawn once and every later epoch adds four pixels to what is already
    there. With ``SWAP 0`` the machine would have to repaint 158 axis cells and
    every earlier epoch's points on every commit.
    """
    g.blank()
    g.say(f"axes, painted once: colour {AXIS_COLOUR} down columns 0-1 and along row 30")
    for panel in (LOSS_PANEL, ACC_PANEL):
        for row in range(PLOT_ROWS):
            g.ldi(row * PANEL[0])
            _dsp(g, panel, PORT_ADDR)
            g.ldi(AXIS_COLOUR)
            for _ in range(Y_AXIS_COLS):
                _dsp(g, panel, PORT_DATA)
        g.ldi(AXIS_ROW * PANEL[0])
        _dsp(g, panel, PORT_ADDR)
        g.ldi(AXIS_COLOUR)
        for _ in range(PANEL[0]):
            _dsp(g, panel, PORT_DATA)
        g.ldi(1)
        _dsp(g, panel, PORT_SWAP, "commit, keeping the framebuffer")


def _plot_point(g: _Gen, panel: int, colour: int) -> None:
    """ACC holds the plot row; paint one pixel of ``colour`` in this epoch's column."""
    g.op(f"MULI {PANEL[0]}")
    g.op(f"ADD {S.PCOL}", "ADDR = row * 64 + this epoch's column")
    _dsp(g, panel, PORT_ADDR)
    g.ldi(colour)
    _dsp(g, panel, PORT_DATA)


def _plot_loss(g: _Gen, addr: int, colour: int, tag: str) -> None:
    """One point on the loss panel, from the mean Q12 loss in ``store[addr]``.

    The arithmetic is :func:`loss_row`'s, including both clamps: a loss below the
    scale pins to the bottom row and one at or above the top pins to row 0. Without
    the clamps an out-of-range epoch would compute an ADDR outside the panel, which
    the panel model rejects and the hardware would silently wrap.
    """
    below, inside, done = g.sym(f"{tag}_lo"), g.sym(f"{tag}_in"), g.sym(f"{tag}_at")
    g.ld(addr)
    g.op(f"SUBI {LOSS_LO}")
    g.op(f"BRN {below}")
    g.op(f"DIVI {LOSS_PER_ROW}")
    g.op(f"SUBI {PLOT_ROWS - 1}")
    g.op(f"BRN {inside}")
    g.ldi(0, "at or above the top of the scale")
    g.op(f"JMP {done}")
    g.label(below)
    g.ldi(PLOT_ROWS - 1, "below the bottom of the scale")
    g.op(f"JMP {done}")
    g.label(inside)
    g.op("NEG", f"row = {PLOT_ROWS - 1} - q")
    g.label(done)
    _plot_point(g, LOSS_PANEL, colour)


def _plot_acc(g: _Gen, addr: int, n: int, colour: int) -> None:
    """One point on the accuracy panel, from the correct count in ``store[addr]``.

    Two floors, in :func:`acc_row`'s order: the percentage first (as ``OUT`` reports
    it) and then the row, so the pixel is always on the row the reported number
    means. No clamp is needed — 0% is row 29 and 100% is row 0 exactly.
    """
    g.ld(addr)
    g.op("MULI 100")
    g.op(f"DIVI {n}", "the percentage the epoch report would print")
    g.op(f"MULI {PLOT_ROWS - 1}")
    g.op("DIVI 100")
    g.op(f"SUBI {PLOT_ROWS - 1}")
    g.op("NEG", f"row = {PLOT_ROWS - 1} - acc * 29 / 100")
    _plot_point(g, ACC_PANEL, colour)


def _plot(g: _Gen, *, train_n: int, val_n: int) -> None:
    """Four points an epoch: two on the loss panel, two on the accuracy panel.

    ``SWAP 1`` on each panel, not ``SWAP 0``: the frame the engine commits is the
    whole curve so far, and clearing would erase it.
    """
    g.blank()
    g.say("the epoch's four points: train/val loss, then train/val accuracy")
    g.ld(S.LOSS)
    g.op(f"DIVI {train_n}")
    g.st(S.TMP)
    _plot_loss(g, S.TMP, TRAIN_COLOUR, "ptl")
    g.ld(S.VLOSS)
    g.op(f"DIVI {val_n}")
    g.st(S.TMP)
    _plot_loss(g, S.TMP, VAL_COLOUR, "pvl")
    g.ldi(1)
    _dsp(g, LOSS_PANEL, PORT_SWAP, "commit the loss panel, keeping it")

    _plot_acc(g, S.CORRECT, train_n, TRAIN_COLOUR)
    _plot_acc(g, S.VCORRECT, val_n, VAL_COLOUR)
    g.ldi(1)
    _dsp(g, ACC_PANEL, PORT_SWAP, "commit the accuracy panel, keeping it")

    g.say("advance the epoch column, pinned at the last one so ADDR stays in range")
    g.ld(S.PCOL)
    g.op("ADDI 1")
    g.st(S.PCOL)
    g.op(f"SUBI {PANEL[0] - 1}")
    g.op(f"BRN {g.sym('plot_col_ok')}")
    g.ldi(PANEL[0] - 1)
    g.st(S.PCOL)
    g.label(g.sym("plot_col_ok"))


def emit_source(
    *,
    epochs_from_input: bool = True,
    dropout: bool = False,
    lr_shift: int,
    epochs: int = 1,
    train_samples: int | None = None,
    val_samples: int | None = None,
    single_step: bool = False,
    panels: bool = False,
) -> str:
    """The whole ``.asm`` text: boot, an unrolled train body, an unrolled val body.

    ``single_step`` emits one training sample and halts, with no epoch
    bookkeeping — what the one-step equality test runs.

    ``panels`` chooses how an epoch reports itself, and the two are **variants of one
    program** rather than a flag on a feature: ``machine.build`` refuses a machine
    that has both a display and an ``O`` room (``SPEC.md`` — a display-judged machine
    must emit nothing), so a build cannot have both. ``False`` keeps the four ``OUT``
    words an epoch, which is what every equality test against the reference model
    reads; ``True`` paints the same four numbers as four pixels on two LM-75s and
    emits nothing at all, which is what the grid does.
    """
    if dropout:
        raise NotImplementedError(
            "dropout is off by design: 18 features at batch 1 cannot spare any (spec §4.1)"
        )
    if panels and single_step:
        raise ValueError("single_step has no epoch to plot: the panels build needs the epoch loop")
    train_n = md.N_TRAIN if train_samples is None else train_samples
    val_n = md.N_VAL if val_samples is None else val_samples

    g = _Gen(lr_shift=lr_shift)
    _header(g, lr_shift=lr_shift, train_n=train_n, val_n=val_n)
    if panels:
        # Before the boot, not after: the axes depend on nothing, and painting them
        # first means the machine puts a frame on both walls within its first few
        # thousand ticks instead of after the 856-word `FILLB`. That is what makes the
        # grid checkable on a real engine at all — the whole training run is far past
        # any tick cap, but "both panels commit their axes" is not.
        _axes(g)
        g.ldi(COL0, "the first epoch's plot column")
        g.st(S.PCOL)
    _boot(g, epochs_from_input=epochs_from_input and not single_step, epochs=epochs)

    if single_step:
        g.blank()
        g.scope = "one_"
        _load_sample(g)
        _conv_forward(g)
        _relu_pool(g)
        _dense_forward(g)
        _softmax(g)
        _backward(g)
        g.rotb_to("X0", "park ring B so the layout indexes it")
        g.op("HALT")
        return "\n".join(g.lines) + "\n"

    g.blank()
    g.label("epoch")
    for addr in (S.LOSS, S.CORRECT, S.VLOSS, S.VCORRECT):
        g.ldi(0)
        g.st(addr)
    g.ldi(train_n)
    g.st(S.TCOUNT)

    g.blank()
    g.label("train_body")
    g.scope = "tr_"
    before = dict(bpos=g.bpos, p1=list(g.p1), p2=list(g.p2), aq=list(g.aq))
    _load_sample(g)
    _conv_forward(g)
    _relu_pool(g)
    _dense_forward(g)
    _softmax(g)
    _report_sample(g, loss=S.LOSS, correct=S.CORRECT)
    _backward(g)
    g.rotb_to("X0", "park ring B for the next sample")
    _check_loop_invariant(g, before, "train_body")
    g.ld(S.TCOUNT)
    g.op("SUBI 1")
    g.st(S.TCOUNT)
    g.op("BRZ val_setup")
    g.op("JMP train_body")

    g.blank()
    g.label("val_setup")
    g.ldi(val_n)
    g.st(S.VCOUNT)
    g.label("val_body")
    g.scope = "va_"
    before = dict(bpos=g.bpos, p1=list(g.p1), p2=list(g.p2), aq=list(g.aq))
    _load_sample(g)
    _conv_forward(g)
    _relu_pool(g)
    _dense_forward(g)
    _softmax(g)
    _report_sample(g, loss=S.VLOSS, correct=S.VCORRECT)
    g.rotb_to("X0", "park ring B for the next sample")
    _check_loop_invariant(g, before, "val_body")
    g.ld(S.VCOUNT)
    g.op("SUBI 1")
    g.st(S.VCOUNT)
    g.op("BRZ epoch_end")
    g.op("JMP val_body")

    g.blank()
    g.label("epoch_end")
    g.scope = "ep_"
    if panels:
        _plot(g, train_n=train_n, val_n=val_n)
    else:
        _epoch_report(g, train_n=train_n, val_n=val_n)
    g.ld(S.EPOCHS)
    g.op("SUBI 1")
    g.st(S.EPOCHS)
    g.op("BRZ done")
    g.op("JMP epoch")
    g.label("done")
    g.rotb_to("X0", "park ring B so the layout indexes it")
    g.op("HALT")
    return "\n".join(g.lines) + "\n"


def _check_loop_invariant(g: _Gen, before: dict, where: str) -> None:
    """A loop body must leave every ring exactly as it found it.

    This is the assertion that makes an unrolled ring program safe: the body is
    re-entered thousands of times, so a one-word drift in ring B's rotation or a
    stranded accumulator cell would silently corrupt every later sample.
    """
    now = dict(bpos=g.bpos, p1=list(g.p1), p2=list(g.p2), aq=list(g.aq))
    if now != before:
        raise _RingError(f"{where} does not restore the rings: {before} -> {now}")


# ── running it ───────────────────────────────────────────────────────────────
def first_train_sample() -> tuple[list[int], int]:
    """``(pixels, label)`` of train image 0 — the sample the one-step test uses."""
    train_words, _ = md.load_packed()
    return md.unpack_image(train_words[: md.WORDS_PER_IMAGE])


def _boot_stream() -> list[int]:
    """The ring B image the boot ``FILLB`` reads: zeros, ones, and biased weights."""
    p = mm.init_params(seed=SEED)
    words = [0] * NX + [1] * NONE
    for i in range(NFEAT):
        words += [p.dense_w[j * NFEAT + i] + WEIGHT_BIAS for j in range(NCLS)]
        words += [1] * NCLS
    for j in range(NCLS):
        words += [p.dense_w[j * NFEAT + i] + WEIGHT_BIAS for i in range(NFEAT)]
        words += [1] * NFEAT
    if len(words) != RING_B:
        raise _RingError(f"boot stream is {len(words)} words, ring B is {RING_B}")
    if any(w < 0 for w in words):
        raise ValueError("boot stream must be ROM-safe: every word non-negative")
    return words


def _dataset_stream(*, epochs: int, train_n: int, val_n: int) -> list[int]:
    """The dataset ring, flattened: one lap of train then val words an epoch.

    The real machine has two permanently-full rings and each lap is an epoch; the
    emulator has one input room, so the same words are handed over in the same
    order, repeated once an epoch.
    """
    train_words, val_words = md.load_packed()
    per = md.WORDS_PER_IMAGE
    lap = train_words[: train_n * per] + val_words[: val_n * per]
    return lap * max(epochs, 1)


#: The sections of one training step, in order — the granularity the staged tests
#: bisect at. A disagreement between the tiers shows up as one named section
#: rather than as one of 20,000 instructions, which is the difference between a
#: five-minute fix and an afternoon.
STAGES = ("boot", "load", "conv", "pool", "dense", "backward")


def probe_one_sample(
    stage: str = "backward", *, lr_shift: int = 6
) -> tuple[dict[int, int], list[int]]:
    """Run one training step up to ``stage`` and hand back the machine's state.

    Returns ``(store cells, ring B in layout order)``. Ring B is parked at ``X0``
    before the halt so the layout constants index it directly.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; have {STAGES}")
    reached = STAGES[: STAGES.index(stage) + 1]

    g = _Gen(lr_shift=lr_shift)
    _header(g, lr_shift=lr_shift, train_n=1, val_n=0)
    _boot(g, epochs_from_input=False, epochs=1)
    if "load" in reached:
        _load_sample(g)
    if "conv" in reached:
        _conv_forward(g)
    if "pool" in reached:
        _relu_pool(g)
    if "dense" in reached:
        _dense_forward(g)
        _softmax(g)
        _report_sample(g, loss=S.LOSS, correct=S.CORRECT)
    if "backward" in reached:
        _backward(g)
    g.rotb_to("X0", "park ring B so the layout indexes it")
    g.op("HALT")

    program = asm.assemble("\n".join(g.lines) + "\n", name="mnist-probe", isa=isa.LM1_EXT)
    store = DictStore(size=STORE_WORDS)
    em = Emulator(program, store=store)
    unit = em.stream
    assert isinstance(unit, StreamUnit)
    stream = _boot_stream() + _dataset_stream(epochs=1, train_n=1, val_n=0)
    result = em.run(input=stream, max_instructions=10**9)
    if not result.halted:
        raise RuntimeError(f"probe did not halt: {result.reason}")
    cells = store.snapshot()
    return {a: cells.get(a, 0) for a in range(STORE_WORDS)}, list(unit.ring_b)


@dataclass
class RunReport:
    """What a run produced: the stats, and the cost figures for the report."""

    stats: list[mm.EpochStat]
    params: mm.Params
    instructions: int
    ticks: int
    program_words: int
    ring_high: tuple[int, int, int]
    #: One committed-frame list per panel, in panel order — empty unless the run was
    #: asked for ``frames=True``, which needs the ``panels=True`` build (the ``OUT``
    #: build paints nothing). This is the emulator side of Task 8's differential
    #: test: the same frames the engine's own ``frames_per_display()`` reports.
    frames: list[list[list[str]]] = field(default_factory=list)


def _extract_params(store: DictStore, unit: StreamUnit) -> mm.Params:
    """Read the machine's parameters back out: STORE for conv, ring B for dense.

    Ring B *is* where the dense weights live — there is no arm that reads one
    back to the CPU, and emitting 180 words through the output room would corrupt
    the epoch report, so the ring is read directly. Both layouts are checked
    against each other, which is a free consistency proof on every run.
    """
    cells = store.snapshot()
    ring = list(unit.ring_b)
    wi = [cells.get(S.CONVW + k, 0) for k in range(NF * TAPS)]
    dense_wi = [0] * (NCLS * NFEAT)
    dense_wj = [0] * (NCLS * NFEAT)
    for i in range(NFEAT):
        for j in range(NCLS):
            dense_wi[j * NFEAT + i] = ring[WI0 + i * WI_STRIDE + j]
    for j in range(NCLS):
        for i in range(NFEAT):
            dense_wj[j * NFEAT + i] = ring[WJ0 + j * WJ_STRIDE + i]
    if dense_wi != dense_wj:
        raise AssertionError("ring B's two dense layouts disagree — a UPDB lap is wrong")
    return mm.Params(
        conv_w=wi,
        conv_b=[cells.get(S.CONVB + k, 0) for k in range(NF)],
        dense_w=dense_wi,
        dense_b=[cells.get(S.DENSEB + j, 0) for j in range(NCLS)],
    )


def run_emulator(
    *,
    epochs: int,
    lr_shift: int = 6,
    samples: int | None = None,
    return_params: bool = False,
    report: bool = False,
    frames: bool = False,
) -> list[mm.EpochStat] | mm.Params | RunReport:
    """Assemble and run the program. ``epochs=0`` means one training step.

    ``frames=True`` runs the ``panels=True`` build instead and returns the committed
    frames of both panels in :attr:`RunReport.frames`. It emits no output words, so
    there are no ``stats`` to report — the curve is *in* the frames, which is exactly
    the claim Task 8's differential test checks against the engine.
    """
    single = epochs == 0
    if frames and single:
        raise ValueError("frames=True needs at least one epoch: a single step plots nothing")
    train_n = samples if samples is not None else md.N_TRAIN
    val_n = samples if samples is not None else md.N_VAL
    src = emit_source(
        lr_shift=lr_shift,
        epochs=max(epochs, 1),
        train_samples=train_n,
        val_samples=0 if single else val_n,
        single_step=single,
        panels=frames,
    )
    program = asm.assemble(src, name="mnist", isa=isa.LM1_EXT)
    # The store is *sized*: an address the map does not name is a fault, not a
    # neighbouring table read. An off-by-one in the exp LUT's clamp would otherwise
    # index the log table and return a plausible number.
    store = DictStore(size=STORE_WORDS)
    em = Emulator(program, store=store)
    unit = em.stream
    assert isinstance(unit, StreamUnit)

    stream = _boot_stream()
    if not single:
        stream = [epochs] + stream
    stream += _dataset_stream(epochs=max(epochs, 1), train_n=train_n, val_n=0 if single else val_n)
    result = em.run(input=stream, max_instructions=10**12)
    if not result.halted:
        raise RuntimeError(f"the machine did not halt: {result.reason}")

    params = _extract_params(store, unit)
    out = list(result.output)
    if len(out) % 4:
        raise AssertionError(f"{len(out)} output words is not four an epoch")
    stats = [
        mm.EpochStat(
            train_loss=out[k],
            train_acc=out[k + 1],
            val_loss=out[k + 2],
            val_acc=out[k + 3],
        )
        for k in range(0, len(out), 4)
    ]
    if report or frames:
        return RunReport(
            stats=stats,
            params=params,
            instructions=result.instructions,
            ticks=result.ticks,
            program_words=program.P,
            ring_high=(unit.high_a, unit.high_b, unit.high_c),
            frames=result.panel_frames(PANELS) if frames else [],
        )
    return params if return_params else stats


# ── the machine ──────────────────────────────────────────────────────────────
#: How the machine is placed. Every one of these is a *pinned search result*, not a
#: preference: ``build`` sweeps ``mem_pad`` and ``stream_pad`` itself, and on a
#: 19,840-word ROM one attempt costs seconds, so the 800-attempt sweep is minutes.
#: Pinning them keeps ``build_machine()`` a fast function and makes the placement
#: reviewable. The `.man` test regenerates the grid, so a value that stops working
#: fails loudly rather than silently picking another.
#:
#: ``STREAM_PAD`` is the smallest value that puts the block's response lane east of
#: its command pipe's descent (``_stream`` refuses otherwise), and ``DSP_PAD`` the
#: smallest that puts both display lanes east of *that* response lane — which is
#: what gives each panel's command pipe a clear corridor row under the CPU
#: (``machine._display_stack``).
MEM_PAD = 54
STREAM_PAD = 48
DSP_PAD = 55

#: How far below the CPU's fetch row the ROM corridor attaches. Not a preference
#: either: the jump slabs' discard ``r`` sits five rows above the south wall and must
#: stay nearer the ROM pipe on the *west* wall than the STREAM block's response pipe
#: on the *south* (§7.1 is nearest, not nearest-useful). ``machine._stream`` records
#: that on ``snake`` all 4,800 (fold, mem_pad, stream_pad) combinations failed on
#: exactly this binding, and that ``matmul`` only escapes it by having no ``JMPF``.
#: This program has jumps and a response pipe, so it needs the third knob: dropping
#: the ROM's attachment point *into* the slab band takes the slab ``r``'s distance to
#: the ROM from 69 cells to 33, which is inside the response pipe's 42.
ROM_TOUCH_DROP = 32

#: The ROM fold. The ROM is 19,840 words and the packed builder trades width for
#: height, so this is the one real dimension knob on the machine; the value is swept
#: for the smallest bounding box rather than argued (see the task-7 report).
ROM_ROWS = 220

#: The epoch count is **baked**, not read from input, and that is forced rather than
#: chosen: ``SPEC.md`` § Input and output makes *a second I/O room a load error*, and
#: the machine already has one — the STREAM block's, which is where every dataset
#: word arrives (``RDIN``) and where the boot ``FILLB`` reads ring B from. A CPU
#: ``IN`` lane would draw a second ``I`` and the grid would not load at all. 30 is
#: the run the loss scale was measured over, and the panels hold 62 columns.
MACHINE_EPOCHS = 30


def build_machine(*, epochs: int = MACHINE_EPOCHS):  # -> machine.Machine
    """The two-panel grid: CPU, ROM, tape STORE, STREAM block, two relay-fed LM-75s.

    ``store="tape"`` and not ``"grid"``: the man-memory grid is 36x1,065 at 352
    slots, which reaches hundreds of rows below the CPU and straight through the
    STREAM block's own 112 columns. The tape is 33x46 and fits beside the CPU, above
    the block. It is the slower tier per access and that is accepted — this machine
    is a demo, not a scored submission, and no tick target applies to it.
    """
    from randomfun2026solvers.lm1 import machine

    if not 1 <= epochs <= MAX_EPOCH_COLS:
        raise ValueError(f"the panels hold {MAX_EPOCH_COLS} epoch columns, not {epochs}")
    program = asm.assemble(
        emit_source(lr_shift=6, panels=True, epochs_from_input=False, epochs=epochs),
        name="mnist-cnn",
        isa=isa.LM1_EXT,
    )
    return machine.build(
        program,
        tape_n=STORE_WORDS,
        rom_rows=ROM_ROWS,
        mem_pad=MEM_PAD,
        stream_pad=STREAM_PAD,
        dsp_pad=DSP_PAD,
        rom_touch_drop=ROM_TOUCH_DROP,
        stream=RING_SIZES,
        display=PANELS,
        store="tape",
    )


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the unrolled MNIST CNN trainer")
    ap.add_argument("--report", action="store_true", help="run it and print the curve")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--samples", type=int, default=None, help="cap images an epoch")
    ap.add_argument("--lr-shift", type=int, default=6)
    ap.add_argument("--asm", type=Path, default=None, help="write the .asm here")
    ap.add_argument("--man", type=Path, default=None)
    ap.add_argument("--html", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    # The `.man` build and the `OUT` build are two variants of one program (see
    # `emit_source`), so `--man` selects which one this invocation is about: the grid
    # paints panels and emits nothing, and the reference run emits four words an epoch.
    grid = bool(args.man or args.html or args.json)
    src = emit_source(
        lr_shift=args.lr_shift,
        panels=grid,
        epochs_from_input=not grid,
        epochs=MACHINE_EPOCHS if grid else 1,
    )
    program = asm.assemble(src, name="mnist-cnn" if grid else "mnist", isa=isa.LM1_EXT)
    print(program.report())
    print(f"STORE words: {STORE_WORDS}, ring sizes {RING_SIZES}")
    if args.asm:
        args.asm.write_text(src, encoding="utf-8")
        print(f"wrote {args.asm}")

    payload: dict[str, object] = {
        "program_words": program.P,
        "instructions": len(program.instrs),
        "store_words": STORE_WORDS,
        "ring_sizes": list(RING_SIZES),
    }
    if args.report:
        run = run_emulator(
            epochs=args.epochs, lr_shift=args.lr_shift, samples=args.samples, report=True
        )
        assert isinstance(run, RunReport)
        print(f"instructions executed: {run.instructions:,}  emulated ticks: {run.ticks:,}")
        print(f"ring high-water (a, b, c): {run.ring_high}")
        print("epoch  train_loss  train_acc  val_loss  val_acc")
        for n, s in enumerate(run.stats, start=1):
            print(
                f"{n:5d}  {s.train_loss:10d}  {s.train_acc:8d}%"
                f"  {s.val_loss:8d}  {s.val_acc:6d}%"
            )
        payload["stats"] = [vars(s) for s in run.stats]
        payload["instructions_executed"] = run.instructions

    m = build_machine() if grid else None
    if m is not None:
        print(m.report())
        payload |= {
            "width": m.width,
            "height": m.height,
            "footprint": m.footprint,
            "panels": [list(p) for p in m.panels],
            "epochs": MACHINE_EPOCHS,
            "placement": {
                "rom_rows": ROM_ROWS,
                "mem_pad": MEM_PAD,
                "stream_pad": STREAM_PAD,
                "dsp_pad": DSP_PAD,
                "rom_touch_drop": ROM_TOUCH_DROP,
            },
            "panel_ports": [
                {band: list(cell) for band, cell in ports.items()} for ports in m.panel_ports
            ],
        }
    if args.json:
        # The house sidecar is the region map (``tools/debug-trace.mjs`` reads it); the
        # program's own facts ride alongside it under one key rather than in a second
        # file, so the two can never describe different builds.
        doc = m.debug_map().to_dict() if m is not None else {}
        doc["mnist"] = payload
        args.json.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")
    # All three in one invocation, so an overlay can never describe a grid it was not
    # generated beside (AGENTS.md § Generators emit their own debug sidecars).
    if args.html and m is not None:
        m.debug_map().write_html(m.rows, args.html)
        print(f"wrote {args.html}")
    if args.man and m is not None:
        args.man.write_text(m.text(), encoding="utf-8")
        print(f"wrote {args.man}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
