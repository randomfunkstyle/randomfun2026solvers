#!/usr/bin/env python3
"""The STREAM block — rotate-only FIFO rings and a fused multiply-accumulate.

``ARCH.md`` §4.1 lists two memory tiers and predicts that neither reaches
``matmul``: the tape costs ``105 + 8.3N`` ticks *per access* and tops out at 108
slots, while ``matmul`` at 16x16x16 wants 512 slots and ~13 accesses per
multiply-accumulate. Banking the tape does not save it — 4096 MACs times four
accesses times any tape latency is already past the 5M cap.

What does save it is the observation that **matmul never addresses memory
randomly**. A streams once, in storage order; B streams once per row of A; the
row of C being accumulated streams once per term. A structure that can only be
*rotated* is enough, and a rotation costs what a pipe costs: one tick per cell,
with the values moving concurrently.

So this block is a third tier:

===============  ==========================  ==========================
tier             cost per access             addressing
===============  ==========================  ==========================
``STORE`` tape   ~105 + 8.3N ticks           random, ``0 a`` / ``1 a v``
``STREAM`` ring  ~2 ticks (one ``r``/``s``)  none — FIFO order only
===============  ==========================  ==========================

A ring is two pipes and a relay room (a pipe may not loop back to its own room,
``SPEC.md``), so its capacity is just its length in cells and its cost is the
``r``/``s`` pair that rotates it one place.

Three rings and one adder
-------------------------

::

      I --> +--------------------------+ --> O
            |  STREAM UNIT             |
   cmd ---> |  MAIN, decode, 8 arms    | --resp--> CPU
            +--------------------------+
              |  ^      |  ^     |   ^
              A-ring    B-ring   prod|P1
              (relay)   (relay)   +--v-----+
                                  | ADDER  |
                                  +--------+

* **ring A** holds A row-major and is *drained*: ``MAC`` pops one scalar per call.
* **ring B** holds B row-major and is *rotated*: ``MAC`` pops K values and pushes
  each straight back, so K rotations advance it exactly one row of B and
  ``M*K`` rotations bring it back to where it started.
* **ring C** is the accumulator row, and it is a ring *through the ADDER room*:
  the unit sends products, the ADDER adds each one to the next circulating
  partial sum. That is what removes the third register the fused MAC would
  otherwise need — ``A`` holds the operand, ``B`` holds the scalar for the whole
  row, and the sum lives in the ring.

The fused MAC is therefore four glyphs, ``r s * s``, in a counted loop: read
``B[t][j]``, put it back, multiply by the scalar still sitting in ``B``, and hand
the product to the ADDER. ~12 ticks per multiply-accumulate against the ~11 000
the tape would have cost.

Two rules make the whole thing bind (``ARCH.md`` §7.1)
------------------------------------------------------

The unit has eleven pipes, and *every* ``r``/``s`` in it is decided by geometry.
Two invariants, both asserted in the tests and re-checked against the engine's
own ``route``:

1. **Every outgoing pipe attaches to the east wall, on the row of the ``s`` that
   uses it.** An ``s`` at its own pipe's row is at distance ``IW - x`` and every
   rival is that plus a row difference, so it wins strictly whatever column the
   arm sits in.
2. **Incoming pipes attach where their readers are.** At depth 3 that means the
   ring returns and the input on the west wall (their readers are the western
   arms), ``cmd`` on the north wall beside ``MAIN``, and the accumulator's return
   on the *south* wall under the two eastern arms that read it. Rows are then
   chosen so each reader is strictly nearest its own — the tightest margin is one
   cell (``MAC``'s ``r`` on ``B_ret`` against ``in``), which is why this is
   asserted rather than argued. At depth 4 the same rule lands somewhere else;
   see below.

Arms are laid out west to east as trie leaves and their bodies run *down* their
own column, so "which row a glyph is on" is a free variable the layout uses to
satisfy rule 1 — padding a body with blanks moves a pipe glyph onto its row.

Two decode widths, and rule 2 changes shape between them
--------------------------------------------------------

The eight arms above are ``matmul``'s, on a depth-3 trie. The MNIST trainer needs
four more — ``PUSHA``, ``ROTB``, ``RDP`` and ``UPDB`` (:class:`~.store.StreamUnit`
models them) — and gets them on a depth-4 trie: sixteen leaves, twelve arms, four
spare, ``16 * arg + code``. The widths are never mixed in one decode, because at
mod-8 a ``PUSHA`` word reads as ``EMIT`` and at mod-16 an existing odd-argument
``FILLA`` word reads as a different arm entirely, so a program has to name the
width it was written against (``asm.UNITS``' ``stream`` / ``stream4``).

Four new arms cost **no new pipes** — they reuse the rings that are already there,
which matters because a new pipe would be a new rival for every ``r`` and ``s`` in
the unit. What they do cost is rule 2's shape: at depth 4 **all four incoming
pipes are on the west wall**, one row each. :data:`UPDB_BANDS` gives the reason in
full; the short version is that ``UPDB`` reads the accumulator, then ring A's
return, then ring B's, in that order, and §7.1's distances never let a south or
north pipe win *between* two west-wall reads — a south pipe's distance falls with
the row exactly as a west pipe's does above its own row, so one of them always
wins, and north is the mirror image. With every incoming pipe on one wall, rule 2
collapses into rule 1 and no per-arm argument is needed at all.

Both widths are drawn and placed
-------------------------------

The depth-4 unit is 65x32 against depth 3's 33x23 — sixteen leaves at pitch 4 set
the width and ``UPDB``'s 17-glyph body sets the height.

The placed block's **width is 112 at every ring size**, because it is set by the unit
plus the fixed east strip (six turn columns, the O room and ``resp``'s climb) and
nothing there grows: depth 3's is 67 the same way. That is the number a caller needs
against a grid cap. Only the **height** grows with the rings, as
``41 + rows_a + rows_b`` — 43 with both bands at one row, 51 at the trainer's
``b_slots=856``, 59 with both bands at nine. Quoting one height as "the" size is how
round 9 handed on 112x51 as if it were fixed.

The depth-4 block needs one thing depth 3 does not. ``prod`` leaves the unit's east
wall *between* ring B's two ports in the perimeter's cyclic order (``MAC`` must push
``b`` back before it can multiply, so ``prod`` is below ``b_fwd``; ``UPDB`` reads ring
B last, so ``b_ret`` is the bottom-most west port), which means ring B's chord
encloses ``prod`` and the ADDER is outside it. So exactly one pipe has to cross that
chord, and the only place a pipe may cross another is *inside a room*:
:func:`shared_relay_cells` is that room, two men from one ``Y``, ring B turned around
west-to-east through the middle and ``prod`` passed south-to-north across it.
:func:`pipe_bindings` decides which pipe each glyph takes, in ``SPEC.md``'s own
direction, and :data:`DUAL_RELAY_PORTS` records the assignment that leaves both of
``prod``'s ports on one side of the chord — and the round-9 over-claim that grew out of
reading that rule backwards.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..circuit import Circuit, E

__all__ = [
    "ARMS",
    "DUAL_RELAY_PORTS",
    "dual_relay_probe",
    "block_crossings",
    "boundary_clockwise",
    "pipe_bindings",
    "wall_segments",
    "shared_relay_bindings",
    "SHARED_RELAY_MIN_MARGIN",
    "perimeter_order",
    "DUAL_RELAY_IH",
    "DUAL_RELAY_IW",
    "SHARED_RELAY_GLYPHS",
    "SHARED_RELAY_IH",
    "SHARED_RELAY_IW",
    "SHARED_RELAY_PORTS",
    "shared_relay_cells",
    "shared_relay_probe",
    "StreamBlock",
    "UNIT_IH",
    "UNIT_IW",
    "EXPECTED_PIPES",
    "UPDB_BANDS",
    "UPDB_SHIFT",
    "arm_codes",
    "build_stream",
    "build_updb_probe",
    "dual_relay_cells",
    "unit_interior",
    "unit_interior_grid",
    "unit_pipe_count",
    "updb_body",
    "updb_probe_input",
    "updb_probe_model",
]


class StreamError(RuntimeError):
    """The block's geometry did not close, with the constraint that failed."""


# ── UPDB: the one arm whose body does not fit in two hands ────────────────────
#: The shift ``UPDB`` applies, wired into the drawn glyphs and *not* a field of any
#: command word. 18, and not 6, because one shift has to serve both of the arm's
#: callers: the dense weight update wants ``w -= (dz*f >> 12) >> 6`` and floored
#: shifts compose (``(g >> 12) >> 6 == g >> 18``), while the ring-B *write* — the
#: only way the CPU can put a value on ring B at all — sends ``g = 1`` and
#: ``a = (b - v) << 18``, which is an exact multiple of ``2**18`` so the floored
#: shift recovers ``b - v`` losslessly and the write is exact. A program declares
#: the shift it was written against with ``.equ STREAM_LR_SHIFT`` (``asm.py``);
#: ``machine._stream`` reads that ``equ`` and hands it to :func:`build_stream`, which
#: checks the glyphs it is about to draw really implement it (:func:`drawn_shift`) and
#: refuses otherwise. Until round 10 that sentence was a claim with nothing behind it —
#: nothing passed the declared value at all, so ``.equ STREAM_LR_SHIFT 7`` drew 18 while
#: the emulator modelled 7. See the task-4 report and ``mnist_cnn``'s module docstring.
UPDB_SHIFT = 18


def _shift_glyphs(shift: int) -> str:
    """``A >>= shift`` in the fewest glyphs, leaving ``B`` scratch.

    ``}`` is ``A >> B`` *arithmetic*, i.e. floored division by ``2**B``, which is
    what the model's ``>>`` is. Getting the count into ``B`` costs ``M d W`` — and
    that is exactly why ``UPDB`` cannot hold its scalar in a register across the
    shift: a literal writes ``A``, ``W`` then writes ``B``, so loading a constant
    destroys one of the two live values whichever way round it is done. ``B`` still
    holds the count afterwards, so *repeating* the same shift is one further glyph;
    18 is therefore ``M 9 W } }`` and not a five-digit literal.
    """
    if shift < 1:
        raise StreamError(f"UPDB's shift must be at least 1, got {shift}")
    for digit in range(9, 0, -1):
        if shift % digit == 0:
            return f"M{digit}W" + "}" * (shift // digit)
    raise StreamError(f"no single-digit chunk divides a shift of {shift}")  # pragma: no cover


def drawn_shift(body: str) -> int:
    """How far the ``}`` run in a drawn ``UPDB`` body actually shifts.

    Read back off the glyphs rather than remembered, so :func:`build_stream` can check
    that what it is about to draw is what the program declared. A grid that shifts by a
    different amount than the emulator models is arithmetic that differs between tiers
    with nothing to catch it, which is the one failure this project's whole verification
    ladder exists to prevent.
    """
    for i in range(len(body) - 3):
        digit, w = body[i + 1], body[i + 2]
        if body[i] != "M" or not digit.isdigit() or w != "W":
            continue
        run = len(body[i + 3 :]) - len(body[i + 3 :].lstrip("}"))
        if run:
            return int(digit) * run
    raise StreamError(f"no `M<digit>W}}...` shift run in {body!r}")


#: The pipes ``UPDB``'s body touches, in body order — which is row order, which is
#: execution order, because the body is walked down one column one glyph per row.
#: This tuple is the reason the depth-4 unit's incoming pipes are *all* on the west
#: wall: ring A's return is read between the accumulator's and ring B's, and
#: ``ARCH.md`` §7.1's distances only let a reader swap between two walls whose
#: row-slopes differ. A south pipe's distance falls with the row exactly as a west
#: pipe's does above its own row, so a south pipe can only ever win *below* every
#: west pipe the same column reads — never between two of them. North is the mirror
#: image. So the middle read has to be on the same wall as the outer two, and once
#: the accumulator's return is on the west wall there is no reason for anything else
#: to be anywhere else. :func:`_spec4` asserts the drawn arm matches this.
UPDB_BANDS: tuple[str, ...] = ("a_ret", "p1", "a_fwd", "p2", "b_ret", "b_fwd")


def updb_body(shift: int = UPDB_SHIFT) -> str:
    """``UPDB``'s counted-loop body, walked *down* one column, one glyph per row.

    One iteration of ``b = ring_b.pop(); g = p1.pop(); p2.push(g);
    ring_b.push(b - ((a * g) >> shift))`` with two hands and no readable backpack::

        r   ring A's return : A = a, the scalar PUSHA left on ring A
        M   B = a
        r   the accumulator : A = g, one gradient off P1
        W   A = a, B = g       — both live, swapped so `a` can be sent
        s   ring A's fill   : put the scalar straight back, ring A unchanged
        W   A = g, B = a       — swapped back
        s   the accumulator : g goes on to P2 — it circulates, it is not consumed
        *   A = a * g          (B is still a — the multiply does not touch it)
        M9W}}  A >>= shift     (see :func:`_shift_glyphs`; B ends holding 9)
        M   B = (a * g) >> shift
        r   ring B's return : A = b
        -   A = b - ((a * g) >> shift)
        s   ring B's fill   : the updated weight, back where it came from

    The two ``W``s are the price of *interleaving* the two reads — a, g, then push a,
    push g — instead of finishing with ring A before starting on the accumulator. They
    buy the thing the block cannot do without: this order, and only this order among
    the five the arm's semantics allow, lets the whole block be routed with a single
    shared relay (:func:`block_crossings`; the search is in the task-6 report). Two
    glyphs a lap against a block that otherwise does not close.

    The ``r``/``s`` pair on ring A is the same rotate ``MAC`` does on ring B, and it
    is not optional: the scalar has to be re-read every iteration because the shift
    needs both hands (:func:`_shift_glyphs`), so **ring A is UPDB's third
    register**. That makes the drawn arm agree with
    :meth:`~.store.StreamUnit._updb` — which reads a remembered ``_scalar_a`` and
    leaves ``ring_a`` alone — exactly when ring A holds that one scalar and nothing
    else: a rotation of a one-value ring is the identity. That is the precondition
    ``mnist_cnn``'s ``updb_from_acc`` (``PUSHA``, ``UPDB``, ``MAC 0``) creates, and
    its ``aq`` model asserts at emit time, so the two tiers cannot drift apart
    silently. It is the one place the drawn arm is narrower than the model.

    Which pipe each glyph talks to is :data:`UPDB_BANDS`, and the order is *searched*,
    not chosen. Five interleavings satisfy the arm's semantics; a sixth (finish with
    ring A, then the accumulator) is inadmissible because it puts ring A's fill above
    ``resp``, and ``resp`` — the one pipe that leaves the block northward — cuts the
    routing region, so every port above it is severed from every other. Of the five,
    exactly one lets the block close with a single shared relay. See
    :func:`block_crossings` and the enumeration recorded in the task-6 report.
    """
    return "rMrWsWs*" + _shift_glyphs(shift) + "Mr-s"


def updb_probe_model(
    scalar: int, weights: list[int], grads: list[int], *, shift: int = UPDB_SHIFT
) -> list[int]:
    """What :func:`build_updb_probe` must emit: the arm's own send order, per lap.

    The probe's room has one outgoing pipe, so every ``s`` in the body lands in the
    same ``O`` room — which is the point: a wrong glyph order shows up as a wrong
    *interleaving*, not just a wrong number, so a run cannot pass by accident with
    the arithmetic done in the wrong place. The order is :data:`UPDB_BANDS`' own.
    """
    sends = {"p2": lambda g, b: g, "a_fwd": lambda g, b: scalar, "b_fwd": lambda g, b: b}
    out: list[int] = []
    for b, g in zip(weights, grads, strict=True):
        updated = b - ((scalar * g) >> shift)
        out += [sends[band](g, updated) for band in UPDB_BANDS if band in sends]
    return out


def updb_probe_input(scalar: int, weights: list[int], grads: list[int]) -> list[int]:
    """One command word, then one lap of reads per weight, in :data:`UPDB_BANDS` order.

    Feeding the scalar once per lap is not a shortcut around the arm: it is exactly
    what ring A hands it, because the drawn body pops the scalar and pushes it
    straight back every iteration.
    """
    reads = {"p1": lambda g, b: g, "a_ret": lambda g, b: scalar, "b_ret": lambda g, b: b}
    words = [(1 << 4) * len(weights) + 11]  # 16 * n + UPDB's code
    for b, g in zip(weights, grads, strict=True):
        words += [reads[band](g, b) for band in UPDB_BANDS if band in reads]
    return words


def build_updb_probe(shift: int = UPDB_SHIFT) -> list[str]:
    """``UPDB`` alone, in one room with one pipe in and one pipe out.

    Probed before it is placed, for the reason ``dsprelay``'s relay and the store
    selector were: in the unit the arm's six pipe glyphs bind six *different* pipes
    by geometry, and a room holds one ``O``. Here there is exactly one incoming and
    one outgoing pipe, so §7.1 has nothing to choose between and every ``r``/``s``
    binds whatever the layout — which isolates the part that is actually hard (the
    register juggling and the glyph order) from the part that is geometric.

    The input is :func:`updb_probe_input` and the expected output
    :func:`updb_probe_model`, both ordered by :data:`UPDB_BANDS`, so the probe reads
    and writes in exactly the sequence the placed arm's rows impose.
    """
    body = updb_body(shift)
    iw, ih = 12, len(body) + 4
    r = Circuit(iw + 1, ih + 1)

    # The command word, then the argument: `}` by 4 is the depth-4 trie's own
    # `16 * arg + code` decode, floored, so a negative argument would survive.
    r.run(1, 1, "@rM4W}b")
    exit_x, exit_y = r.counted_loop(8, 1, body)
    r.set(exit_x, exit_y, "H")

    g = Circuit(iw + 12, ih + 3)
    ox, oy = 6, 1
    for (x, y), glyph in r.cell.items():
        g.set(ox + x, oy + y, glyph)
    for x in range(-1, iw + 1):
        g.set(ox + x, oy - 1, "+" if x in (-1, iw) else "-")
        g.set(ox + x, oy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(ox - 1, oy + y, "|")
        g.set(ox + iw, oy + y, "|")

    row = oy + 1  # the spawn's row: the room's only two pipes hang off it
    for i, line in enumerate(("+-+", "|I|", "+-+")):
        for j, glyph in enumerate(line):
            g.set(j, row - 1 + i, glyph)
    g.run(3, row, ">>", d=E)

    out_x = ox + iw + 3
    for i, line in enumerate(("+-+", "|O|", "+-+")):
        for j, glyph in enumerate(line):
            g.set(out_x + j, row - 1 + i, glyph)
    g.run(ox + iw + 1, row, ">>", d=E)

    return [line.rstrip() for line in g.rows() if line.strip()]


# ── the unit's row map ───────────────────────────────────────────────────────
#: Interior rows. ``MAIN`` reads a command, a depth-3 trie fans it to eight
#: columns, every arm recovers its argument with the same five glyphs, and the
#: rows below are the *pipe rows*: a glyph sitting on one of these binds that
#: pipe (see the module docstring's rule 1).
R_MAIN = 1
R_TRIE = 2  # rows 2..4
R_ARG = 5  # rows 5..9: `M 8 W / b`
R_A_RET = 11  # west: ring A's return   (MAC pops the scalar here)
R_IN = 13  # west: the input room
R_B_RET = 14  # west: ring B's return
R_RESP = 14  # east: one word back to the CPU
R_A_FWD = 15  # east: ring A's fill
R_B_FWD = 16  # east: ring B's rotate-back
R_PROD = 18  # east: products to the ADDER
R_P1 = 18  # south: partial sums back from the ADDER
R_P2 = 19  # east: partial sums to the ADDER
R_OUT = 20  # east: the output room
R_COLLECT = 23  # every arm rejoins here and walks back to MAIN

UNIT_IH = R_COLLECT
UNIT_IW = 33

#: Trie geometry: eight leaves at ``LEAF0 + 4i``, entry column midway.
LEAF0 = 2
LEAF_PITCH = 4
TRIE_BITS = 3
TRIE_COL = LEAF0 + LEAF_PITCH * ((1 << TRIE_BITS) - 1) // 2  # 16

#: The eight arms, west to east. Order is not free: it is what makes each
#: reader nearest its own incoming pipe (rule 2). ``FWD``/``EMIT`` read the
#: accumulator return on the *south* wall so they go east, where the west wall is
#: far; ``FILLA``/``FILLB``/``DRAINB``/``MAC`` read the west wall so they stay west.
ARMS: tuple[str, ...] = (
    "RDIN",  # r(in) -> s(resp): one input word to the CPU
    "FILLA",  # n x { r(in) -> s(A_fwd) }
    "DRAINB",  # n x { r(B_ret) }: empty the ring between rounds
    "FILLB",  # n x { r(in) -> s(B_fwd) }
    "MAC",  # a = r(A_ret); n x { r(B_ret), s(B_fwd), *, s(prod) }
    "ZEROC",  # n x { 0 -> s(P2) }
    "FWD",  # n x { r(P1) -> s(P2) }: one lap of the accumulator ring
    "EMIT",  # n x { r(P1) -> s(out) }
)


def _bit_of(level: int) -> int:
    return 1 << (level - 1)


def _trie_col(trie_bits: int) -> int:
    return LEAF0 + LEAF_PITCH * ((1 << trie_bits) - 1) // 2


def _leaf_codes(trie_bits: int) -> dict[int, int]:
    """``leaf column -> command code``, read off the trie's own geometry.

    ``x`` turns clockwise on BP's low bit; a man heading *south* turns clockwise
    to the **west**, so a west branch means that bit is 1. The code is therefore
    read off the path, not assigned — which is the same trick ``machine.plan``
    uses on the CPU's trie (there the leaves are rows, here they are columns).
    """
    codes: dict[int, int] = {}

    def walk(level: int, col: int, code: int) -> None:
        step = LEAF_PITCH * (1 << (trie_bits - level)) // 2
        for sign, bit in ((-1, 1), (+1, 0)):
            nxt = col + sign * step
            acc = code | (bit * _bit_of(level))
            if level < trie_bits:
                walk(level + 1, nxt, acc)
            else:
                codes[nxt] = acc

    walk(1, _trie_col(trie_bits), 0)
    return codes


def arm_codes(trie_bits: int = TRIE_BITS) -> dict[str, int]:
    """Command code per arm at one decode width, derived from the trie's geometry.

    Not a table: the codes *are* the leaf columns, so moving a leaf moves the code
    and the tests catch it. A depth-4 trie has sixteen leaves for twelve arms, and
    which four are spare is therefore not free either — the arms have to sit on the
    leaves whose paths spell the codes :attr:`~.store.StreamUnit.CODES` already
    publishes, because ``matmul``'s eight are shipped and the other four are what
    the emulator model has been tested against since Task 3.
    """
    spec = _spec(trie_bits)
    codes = _leaf_codes(trie_bits)
    leaves = sorted(codes)
    if len(leaves) != len(spec.leaves):
        raise StreamError(f"trie has {len(leaves)} leaves for {len(spec.leaves)} slots")
    return {arm: codes[col] for arm, col in zip(spec.leaves, leaves, strict=True) if arm}


#: ``(loop-entry row, body)`` per arm. The body is walked *down* the arm's own
#: column one cell per row, so a blank is a nop that moves the next glyph onto
#: its pipe's row. ``None`` means "no loop": the arm's glyphs sit straight in its
#: leaf column and it walks on to the collector.
_BODIES: dict[str, tuple[int, str] | None] = {
    "RDIN": None,  # r@13 in, s@14 resp — no count, so no loop
    "FILLA": (R_IN - 1, "r s"),  # r@13 in, s@15 A_fwd
    "DRAINB": (R_B_RET - 1, "r"),  # r@14 B_ret
    "FILLB": (R_IN - 1, "r  s"),  # r@13 in, s@16 B_fwd
    "MAC": (R_B_RET - 1, "r s*s"),  # r@14 B_ret, s@16 B_fwd, *@17, s@18 prod
    "ZEROC": (R_P1 - 1, "0s"),  # 0@18, s@19 P2
    "FWD": (R_P1 - 1, "rs"),  # r@18 P1, s@19 P2
    "EMIT": (R_P1 - 1, "r s"),  # r@18 P1, s@20 out
}


@dataclass(frozen=True)
class _Arm:
    """One arm's glyphs, as rows in its own leaf column.

    ``pre`` and ``straight`` sit in the leaf column itself; ``loop`` is a
    :meth:`~..circuit.Circuit.counted_loop` whose body walks down column ``x + 1``.
    A row is a pipe, so these row numbers *are* the arm's bindings.
    """

    arg: bool = True  # recover the argument (and the loop count) from the word
    pre: tuple[tuple[int, str], ...] = ()  # glyphs before the loop
    loop: tuple[int, str] | None = None  # (entry row, body)
    straight: tuple[tuple[int, str], ...] = ()  # glyphs when there is no loop


@dataclass(frozen=True)
class _Spec:
    """Everything one decode width draws, so the widths cannot share a constant.

    ``matmul``'s grid is shipped and judged, so the depth-3 spec is the module's
    original constants verbatim and the depth-4 one is a separate object rather
    than an edit — which is what makes ``machine.build_for("matmul")``
    byte-identical something a test can assert rather than something to argue.
    """

    trie_bits: int
    leaves: tuple[str, ...]  # leaf order west to east; "" is a spare leaf
    arms: tuple[str, ...]  # the named arms, in leaf order
    rows: dict[str, int]  # band -> interior row
    west: tuple[str, ...]  # incoming bands on the west wall
    east: tuple[str, ...]  # outgoing bands on the east wall
    south: tuple[str, ...]  # incoming bands on the south wall (a column, not a row)
    south_col: dict[str, int]
    arg: str  # the argument-recovery glyphs, walked down from R_ARG
    plan: dict[str, _Arm]
    band_at: dict[tuple[str, int], str]
    iw: int
    ih: int
    arg_row: int
    collect: int

    def col(self, i: int) -> int:
        return LEAF0 + LEAF_PITCH * i

    @property
    def cols(self) -> dict[str, int]:
        return {arm: self.col(i) for i, arm in enumerate(self.leaves) if arm}


def _band_at(rows: dict[str, int], west: tuple[str, ...], east: tuple[str, ...]) -> dict:
    """``(glyph, row) -> band``. The row *is* the pipe, so this is the whole map.

    Incoming and outgoing pipes are separate pools (``SPEC.md``: ``r`` looks at
    incoming, ``s`` at outgoing), so an ``r`` row and an ``s`` row may coincide and
    the key has to carry the glyph. Two *incoming* bands on one row, or two
    outgoing, would be a genuine ambiguity — hence the check.
    """
    out: dict[tuple[str, int], str] = {}
    for glyph, bands in (("r", west), ("s", east)):
        for band in bands:
            key = (glyph, rows[band])
            if key in out:
                raise StreamError(f"{band} and {out[key]} both claim {glyph!r} on row {rows[band]}")
            out[key] = band
    return out


#: Depth 3: ``matmul``'s unit, unchanged. ``p1`` comes back on the *south* wall
#: under the two eastern arms that read it, which is what lets ``FWD``/``EMIT`` sit
#: east of everything and the west wall carry only three pipes.
_WEST3 = ("a_ret", "in", "b_ret")
_EAST3 = ("resp", "a_fwd", "b_fwd", "prod", "p2", "out")
_ROWS3 = {
    "a_ret": R_A_RET,
    "in": R_IN,
    "b_ret": R_B_RET,
    "resp": R_RESP,
    "a_fwd": R_A_FWD,
    "b_fwd": R_B_FWD,
    "prod": R_PROD,
    "p1": R_P1,
    "p2": R_P2,
    "out": R_OUT,
}

_SPEC3 = _Spec(
    trie_bits=TRIE_BITS,
    leaves=ARMS,
    arms=ARMS,
    rows=_ROWS3,
    west=_WEST3,
    east=_EAST3,
    south=("p1",),
    south_col={"p1": LEAF0 + LEAF_PITCH * ARMS.index("EMIT") + 1},
    arg="M8W/b",
    plan={
        arm: _Arm(
            arg=arm != "RDIN",
            # The scalar is popped *before* the loop and stays in B for the whole
            # row, so ring A's return is the only pipe read outside a loop body.
            pre=((R_A_RET, "r"), (R_A_RET + 1, "M")) if arm == "MAC" else (),
            loop=_BODIES[arm],
            straight=((R_IN, "r"), (R_RESP, "s")) if _BODIES[arm] is None else (),
        )
        for arm in ARMS
    },
    band_at={
        **_band_at(_ROWS3, _WEST3, _EAST3),
        ("r", R_P1): "p1",  # south wall: a column, but still one designated row
    },
    iw=UNIT_IW,
    ih=UNIT_IH,
    arg_row=R_ARG,
    collect=R_COLLECT,
)


#: Depth 4: twelve arms on sixteen leaves. The leaf *order* is forced — each arm
#: has to land on the leaf whose trie path spells its published code — so the
#: layout's only freedom is the row map, and rule 2 changes shape because of it:
#: ``UPDB`` reads ring A's return, the accumulator and ring B's return in one
#: column, and :func:`updb_body` shows why the accumulator's read sits *between*
#: the other two. A pipe read between two west-wall pipes cannot be on the north or
#: south wall — ``ARCH.md`` §7.1's distances only cross where the row-slopes differ,
#: and a south pipe's distance falls with the row exactly as a west pipe's does
#: above its own row — so at depth 4 **all four incoming pipes are on the west
#: wall**, one row each, and every ``r`` binds its own row for free.
_LEAVES4: tuple[str, ...] = (
    "",  # code 15, spare
    "RDIN",  # 7
    "UPDB",  # 11
    "FILLA",  # 3
    "",  # 13, spare
    "DRAINB",  # 5
    "ROTB",  # 9
    "FILLB",  # 1
    "",  # 14, spare
    "MAC",  # 6
    "RDP",  # 10
    "ZEROC",  # 2
    "",  # 12, spare
    "FWD",  # 4
    "PUSHA",  # 8
    "EMIT",  # 0
)

_WEST4 = ("p1", "in", "a_ret", "b_ret")
_EAST4 = ("resp", "p2", "out", "a_fwd", "b_fwd", "prod")


def _spec4(shift: int) -> _Spec:
    """The depth-4 unit, with ``UPDB``'s body length deciding the lower rows.

    Everything below the accumulator's row is a consequence of
    :func:`updb_body`: ring B's return is however far down that body reads it, and
    ``MAC``'s own loop has to start on the same row because a row *is* a pipe. So
    the rows are computed from the body rather than written beside it, and a
    different shift moves them together instead of silently detaching one.
    """
    body = updb_body(shift)
    pipe_ops = [i for i, ch in enumerate(body) if ch in "rs"]
    if len(pipe_ops) != 6:
        raise StreamError(f"UPDB's body has {len(pipe_ops)} pipe glyphs, expected 6")

    arg = "M4W}b"  # `}` by 4: the depth-4 trie's own `16 * arg + code`, floored
    arg_row = R_TRIE + 4  # the trie is one row deeper than depth 3's
    top = arg_row + len(arg) + 1  # 12: the first row an arm's loop body can reach

    # ``UPDB``'s body needs no padding at this order: its six pipe glyphs already fall
    # on alternating rows, and ``in``, ``resp`` and ``out`` slot into the gaps. ``resp``
    # has to be the topmost *outgoing* row — it is the one pipe that leaves the block
    # northward, so it cuts the routing region and anything above it is severed from
    # everything else (:func:`block_crossings`) — and it also has to sit below ``in``
    # (``RDIN``) and below the accumulator's row (``RDP``), which is what fixes it here.
    updb = body
    rows = {
        "a_ret": top,  # 12  west: ring A's return  (UPDB's scalar, and MAC's)
        "in": top + 1,  # 13  west: the input room
        "p1": top + 2,  # 14  west: partial sums back from the ADDER
        "resp": top + 3,  # 15  east: one word back to the CPU — the topmost outgoing
        "a_fwd": top + 4,  # 16  east: ring A's fill
        "out": top + 5,  # 17  east: the output room
        "p2": top + 6,  # 18  east: partial sums to the ADDER
        "b_ret": top + len(updb) - 3,  # 26  west: ring B's return
        "b_fwd": top + len(updb) - 1,  # 28  east: ring B's rotate-back
        "prod": top + len(updb) + 1,  # 30  east: products to the ADDER
    }
    if [top + i for i in pipe_ops] != [rows[band] for band in UPDB_BANDS]:
        raise StreamError(f"UPDB's body {updb!r} does not land on the row map {rows}")

    def gap(first: str, last: str) -> str:
        """A body that reads ``first``'s pipe and writes ``last``'s, blanks between."""
        return "r" + " " * (rows[last] - rows[first] - 1) + "s"

    plan = {
        # No count, so no loop: one word in, one word straight back out.
        "RDIN": _Arm(arg=False, straight=((rows["in"], "r"), (rows["resp"], "s"))),
        "RDP": _Arm(arg=False, straight=((rows["p1"], "r"), (rows["resp"], "s"))),
        # PUSHA's argument *is* its value, so the arg block is all the work: after
        # `M4W}b` A holds the scalar and one `s` puts it on ring A.
        "PUSHA": _Arm(straight=((rows["a_fwd"], "s"),)),
        "FILLA": _Arm(loop=(rows["in"] - 1, gap("in", "a_fwd"))),
        "FILLB": _Arm(loop=(rows["in"] - 1, gap("in", "b_fwd"))),
        "DRAINB": _Arm(loop=(rows["b_ret"] - 1, "r")),
        "ROTB": _Arm(loop=(rows["b_ret"] - 1, gap("b_ret", "b_fwd"))),
        "MAC": _Arm(
            pre=((rows["a_ret"], "r"), (rows["a_ret"] + 1, "M")),
            loop=(rows["b_ret"] - 1, "r s*s"),
        ),
        "ZEROC": _Arm(loop=(rows["p2"] - 2, "0s")),
        "FWD": _Arm(loop=(rows["p1"] - 1, gap("p1", "p2"))),
        "EMIT": _Arm(loop=(rows["p1"] - 1, gap("p1", "out"))),
        "UPDB": _Arm(loop=(rows["a_ret"] - 1, updb)),
    }

    arms = tuple(arm for arm in _LEAVES4 if arm)
    if set(arms) != set(plan):
        raise StreamError(f"depth-4 leaves {sorted(arms)} against plan {sorted(plan)}")
    # The collector has to clear every loop: a counted loop spans
    # ``entry .. entry + len(body) + 1`` (the test, the body, the turn back up).
    collect = 1 + max(
        (a.loop[0] + len(a.loop[1]) + 1 if a.loop else max(r for r, _ in a.straight))
        for a in plan.values()
    )
    east_edge = max(
        LEAF0 + LEAF_PITCH * i + (2 if arm and plan[arm].loop else 0)
        for i, arm in enumerate(_LEAVES4)
    )
    return _Spec(
        trie_bits=4,
        leaves=_LEAVES4,
        arms=arms,
        rows=rows,
        west=_WEST4,
        east=_EAST4,
        south=(),
        south_col={},
        arg=arg,
        plan=plan,
        band_at=_band_at(rows, _WEST4, _EAST4),
        iw=east_edge + 1,
        ih=collect,
        arg_row=arg_row,
        collect=collect,
    )


def _spec(trie_bits: int = TRIE_BITS, lr_shift: int = UPDB_SHIFT) -> _Spec:
    """The one drawing this module knows for ``trie_bits``, or a loud refusal.

    A depth-3 unit reads a depth-4 program's ``PUSHA`` word as ``EMIT`` and runs to
    completion computing nonsense (``StreamUnit``'s docstring works the aliasing
    out both ways), so an unknown width must never fall back to a known one.
    """
    if trie_bits == TRIE_BITS:
        return _SPEC3
    if trie_bits == 4:
        return _spec4(lr_shift)
    raise StreamError(
        f"this module draws a depth-3 unit (the original eight arms, {list(ARMS)}) "
        f"and a depth-4 one (those eight plus PUSHA, ROTB, RDP and UPDB), but the "
        f"program asked for depth {trie_bits} ({1 << trie_bits} leaves). Widths do "
        "not substitute for one another: at mod-8 a PUSHA word decodes as EMIT and "
        "at mod-16 an existing odd-argument FILLA word decodes as a different arm "
        "entirely, so either substitution produces a machine that runs to "
        "completion and computes the wrong answer."
    )


@dataclass
class Unit:
    """The stream unit's interior, plus where each of its pipes must attach."""

    cells: dict[tuple[int, int], str]
    width: int = UNIT_IW
    height: int = UNIT_IH
    #: band -> interior row on the west wall
    west: dict[str, int] = field(default_factory=dict)
    #: band -> interior row on the east wall
    east: dict[str, int] = field(default_factory=dict)
    #: band -> interior column on the north wall
    north: dict[str, int] = field(default_factory=dict)
    #: band -> interior column on the south wall
    south: dict[str, int] = field(default_factory=dict)
    #: every pipe glyph: (x, y, glyph, band) in interior coordinates
    glyphs: list[tuple[int, int, str, str]] = field(default_factory=list)
    codes: dict[str, int] = field(default_factory=dict)


def unit_interior(trie_bits: int = TRIE_BITS, *, lr_shift: int = UPDB_SHIFT) -> Unit:
    """Lay the unit: MAIN, the decode trie, the arms, the collector.

    One drawing routine for both widths, because a second copy is exactly the place
    a depth-3 fix would fail to reach the depth-4 grid. What differs between them is
    entirely in :class:`_Spec` — the leaf order, the row map, which wall the
    accumulator's return arrives on, and the argument-recovery glyphs.
    """
    spec = _spec(trie_bits, lr_shift)
    c = Circuit(spec.iw + 1, spec.ih + 1)
    glyphs: list[tuple[int, int, str, str]] = []
    trie_col = _trie_col(spec.trie_bits)

    # ── MAIN: the riser lands here, one command word, then the trie ──────────
    c.set(1, R_MAIN, ">")
    c.set(2, R_MAIN, "@")
    c.set(3, R_MAIN, "r")
    glyphs.append((3, R_MAIN, "r", "cmd"))
    c.set(4, R_MAIN, "b")
    c.horizontal(R_MAIN, 4, trie_col)
    c.set(trie_col, R_MAIN, "v")

    # ── decode trie, fanning *sideways*: leaves are columns, not rows ────────
    def trie(level: int, col: int) -> None:
        row = R_TRIE + level - 1
        step = LEAF_PITCH * (1 << (spec.trie_bits - level)) // 2
        c.set(col, row, "x")
        for sign in (-1, +1):
            for d in range(1, step + 1):
                ch = "v" if d == step else ("]" if d == 1 else " ")
                c.set(col + sign * d, row, ch)
            if level < spec.trie_bits:
                trie(level + 1, col + sign * step)

    trie(1, trie_col)

    # ── arms ─────────────────────────────────────────────────────────────────
    for i, arm in enumerate(spec.leaves):
        x = spec.col(i)
        if not arm:
            # A spare leaf. The man still arrives — the trie always turns — so he
            # walks straight to the collector and the command is a no-op instead of
            # a `bad-op` on a blank he was never meant to reach.
            c.vertical(x, spec.arg_row - 1, spec.collect)
            continue
        plan = spec.plan[arm]
        if plan.arg:
            # Every arm but the two that take no argument recovers it the same way:
            # the command word is still in A (the trie only touched BP), so a
            # floored divide by the trie's width — which is why a negative argument
            # survives — and `b` makes the quotient the loop count.
            c.run(x, spec.arg_row, spec.arg, d=(0, 1))
            below = spec.arg_row + len(spec.arg) - 1
        else:
            below = spec.arg_row - 1
        for row, glyph in plan.pre:
            c.vertical(x, below, row)
            c.set(x, row, glyph)
            if glyph in "rs":
                glyphs.append((x, row, glyph, spec.band_at[(glyph, row)]))
            below = row
        for row, glyph in plan.straight:
            c.vertical(x, below, row)
            c.set(x, row, glyph)
            if glyph in "rs":
                glyphs.append((x, row, glyph, spec.band_at[(glyph, row)]))
            below = row
        if plan.loop is None:
            c.vertical(x, below, spec.collect)
            continue
        y0, text = plan.loop
        c.vertical(x, below, y0)
        c.counted_loop(x, y0, text)
        for j, ch in enumerate(text):
            if ch in "rs":
                glyphs.append((x + 1, y0 + 1 + j, ch, spec.band_at[(ch, y0 + 1 + j)]))
        c.set(x + 2, y0, "v")
        c.vertical(x + 2, y0, spec.collect)

    # ── collector: every arm arrives southbound and turns west ───────────────
    east_edge = max(
        spec.col(i) + (2 if arm and spec.plan[arm].loop else 0)
        for i, arm in enumerate(spec.leaves)
    )
    for x in range(2, east_edge + 1):
        c.set(x, spec.collect, "<")
    c.set(1, spec.collect, "^")
    c.vertical(1, spec.collect, R_MAIN)

    cells = {k: v for k, v in c.cell.items() if v != " "}
    return Unit(
        cells=cells,
        width=spec.iw,
        height=spec.ih,
        west={band: spec.rows[band] for band in spec.west},
        east={band: spec.rows[band] for band in spec.east},
        north={"cmd": 3},
        south=dict(spec.south_col),
        glyphs=glyphs,
        codes=arm_codes(trie_bits),
    )


#: Which band a pipe glyph on a given row belongs to. The row *is* the pipe
#: (module docstring, rule 1), so this table is the single place that mapping
#: lives and every arm body is checked against it.
_BAND_AT: dict[tuple[str, int], str] = _SPEC3.band_at


#: How many pipes the unit's own room has: every incoming band, every outgoing band,
#: and ``cmd``. Both widths come to eleven, which is the number the module docstring
#: quotes — depth 4 adds four arms and no pipes, because the new arms reuse the rings
#: that are already there. ``ARCH.md`` §4.4's failure mode is that a stray ``|`` one
#: cell behind an arrowhead *deletes* a pipe silently and ``analyze`` simply reports
#: one fewer, so the count belongs in an assertion and not in an argument.
EXPECTED_PIPES = 11


def unit_pipe_count(trie_bits: int = TRIE_BITS) -> int:
    spec = _spec(trie_bits)
    return len(spec.west) + len(spec.east) + len(spec.south) + 1  # + cmd


def perimeter_order(trie_bits: int = TRIE_BITS, *, lr_shift: int = UPDB_SHIFT) -> list[str]:
    """The unit's ports clockwise from the top of the east wall, down it and up the west.

    ``cmd`` and ``resp`` both run north to the CPU, so no pipe of the block can cross
    the top: the region the block's pipes may use is the unit's perimeter *as an
    interval*, and this is that interval. ``cmd`` is not in it — it comes from outside
    the block — and ``resp`` is, because its climb occupies the east side.
    """
    spec = _spec(trie_bits, lr_shift)
    east = sorted((spec.rows[band], band) for band in spec.east)
    west = sorted((spec.rows[band], band) for band in spec.west)
    # Clockwise: down the east wall, west along the south wall, up the west wall.
    south = sorted((-col, band) for band, col in spec.south_col.items())
    return (
        [band for _row, band in east]
        + [band for _col, band in south]
        + [band for _row, band in reversed(west)]
    )


def block_crossings(
    trie_bits: int = TRIE_BITS,
    *,
    lr_shift: int = UPDB_SHIFT,
    tree: tuple[str, ...] = ("p2", "prod", "p1"),
    pairs: tuple[tuple[str, str], ...] = (("a_fwd", "a_ret"), ("b_fwd", "b_ret")),
    order: Sequence[str] | None = None,
) -> list[tuple[str, str]]:
    """Which ring pairs *cannot* be routed outside the unit. Empty means planar.

    Two things cut the routing region, and both matter:

    * **``resp`` leaves the block northward**, so its pipe is a curve from the unit's
      boundary to the outer boundary: it *splits* the perimeter interval, and a pair
      with one port on each side of it is unroutable at any cost, by any layout. That
      is why ``resp`` has to be the topmost outgoing row and why
      :func:`updb_body`'s other valid order — which would put ring A's fill above
      ``resp`` — cannot be used. This module drew that order once and reverted it;
      the check below is what should have caught it the first time.
    * **The ADDER's legs form a tree** touching the perimeter at ``tree``, cutting
      what is left into sectors. A pair is routable iff both ports fall in one
      sector. Splitting a pipe with a relay changes *neither endpoint*, so no relay,
      leg span, band depth or column allocation can move a pair across a boundary —
      only the perimeter order or the tree can.

    **What this does not decide.** It adjudicates whether a *chord* can be routed —
    whether a ring's two ports lie in one sector — and nothing else. It is necessary
    and **not sufficient**: it cannot see whether the four legs of a single *room* can
    be drawn without crossing each other, which is a separate question and one that
    bit this module. The shared relay's four ports first collided on its west wall
    (``b_ret`` leaving westward across ``prod``'s descent) in a configuration this
    function calls planar, and the fix was to move ``b_ret`` to the room's north wall.
    So a planar verdict here licenses attempting the drawing; it does not promise the
    drawing closes. Trust it exactly that far.

    ``order`` overrides the drawn perimeter, which is what makes this a *search* tool
    rather than a check: candidate row maps can be adjudicated before any of them is
    drawn. Every search must keep the depth-3 block as a control — a configuration
    that reports it non-planar is a bug in this function, not a result.

    ``tree`` is a parameter because growing it is the fix: a *room* is not a point,
    so a relay that also passes another pipe through makes that pipe's ports leaves
    of the same tree, and leaves are adjacent to a tree rather than separated by it.
    Routing ``prod`` through ring B's relay adds ``b_fwd``/``b_ret``; doing the same
    at ring A's adds ``a_fwd``/``a_ret``.
    """
    order = list(order) if order is not None else perimeter_order(trie_bits, lr_shift=lr_shift)
    # A band named in `tree` or `pairs` but missing from `order` used to be dropped, and
    # the function then answered about a *smaller* tree — which reads as "planar" when the
    # question asked was a different one. That is the shape of round 5's false positive,
    # where `prod` fell out of the port set unnoticed, so it is a refusal now.
    missing = sorted({*tree, *(b for pair in pairs for b in pair)}.difference(order))
    if missing:
        raise StreamError(
            f"block_crossings was asked about {missing} but the perimeter order "
            f"{order} does not contain them; a silently smaller tree answers a "
            "different question and reports planar"
        )
    if "resp" in order:
        cut = order.index("resp")
        arcs = [order[:cut], order[cut + 1 :]]
        split = [
            pair
            for pair in pairs
            if not any(set(pair) <= set(arc) for arc in arcs)
        ]
        if split:
            return split
        order = max(arcs, key=len)
    pos = {band: i for i, band in enumerate(order)}
    cuts = sorted(pos[band] for band in tree if band in pos)

    def sector(p: int) -> tuple[int, int]:
        for i, lo in enumerate(cuts):
            hi = cuts[(i + 1) % len(cuts)]
            if (lo < p < hi) if lo < hi else (p > lo or p < hi):
                return (lo, hi)
        raise StreamError(f"{order[p]} is itself one of the tree's leaves {tree}")

    # A port that is itself a leaf of the tree is connected *by* the tree, so it
    # needs no sector of its own — which is exactly what growing the tree buys.
    return [
        pair
        for pair in pairs
        if not set(pair) & set(tree) and sector(pos[pair[0]]) != sector(pos[pair[1]])
    ]


def unit_interior_grid(trie_bits: int = TRIE_BITS, *, lr_shift: int = UPDB_SHIFT) -> list[str]:
    """The unit's room and all eleven of its pipes, each ending in a bare stub room.

    A loadable grid whose *only* content is the thing under test, so ``analyze`` and
    ``route`` answer about the unit and nothing else. Where a pipe attaches is the
    row or column the unit asks for, and that — not the stub's position — is all a
    binding depends on (``SPEC.md``: nearest is measured to the segment touching
    *this* room), so the harness pins exactly the property the block relies on
    without the block's own hundred-cell rings in the way.

    Every leg is a straight run, so no two can cross and a failure here is a
    statement about the row map rather than about the harness.
    """
    spec = _spec(trie_bits, lr_shift)
    unit = unit_interior(trie_bits, lr_shift=lr_shift)
    from .machine import _Grid

    g = _Grid()
    pad = 4  # room for a 2-cell pipe and the stub's own wall
    ux, uy = pad + 2, pad + 2
    g.room(ux, uy, ux + spec.iw + 1, uy + spec.ih + 1)
    g.blit(ux, uy, unit.cells)
    east_x = ux + spec.iw + 2

    # One stub room per wall, spanning that wall, with a pipe per band. A room may
    # own any number of pipes; these have no `@`, so nothing runs and nothing but
    # the parse and the routing is being asked about.
    g.room(0, uy - 1, pad - 1, uy + spec.ih + 2)
    for row in unit.west.values():
        g.draw_pipe([(pad, uy + row), (ux - 1, uy + row)])
    g.room(east_x + pad - 1, uy - 1, east_x + 2 * pad, uy + spec.ih + 2)
    for row in unit.east.values():
        g.draw_pipe([(east_x, uy + row), (east_x + pad - 2, uy + row)])
    g.room(ux - 1, 0, ux + spec.iw + 2, pad - 1)
    for col in unit.north.values():
        g.draw_pipe([(ux + col, pad), (ux + col, uy - 1)])
    south_y = uy + spec.ih + 2
    if unit.south:
        g.room(ux - 1, south_y + pad - 1, ux + spec.iw + 2, south_y + 2 * pad)
        for col in unit.south.values():
            g.draw_pipe([(ux + col, south_y + pad - 2), (ux + col, south_y)])
    return g.rows()


# ── the ADDER: the accumulator ring's adding relay ───────────────────────────
#: ``r(prod) M r(P2) + s(P1)`` in a closed circuit. Padding between the two
#: reads is not decoration: it is what makes each ``r`` strictly nearest its own
#: pipe (``prod`` on the north wall, ``P2`` on the east).
_ADDER = [
    ">rM  r+v",
    "^      s",
    "^@<<<<<<",
]
ADDER_IW = len(_ADDER[0])
ADDER_IH = len(_ADDER)
ADDER_PROD_COL = 2  # north wall: products in
ADDER_P1_COL = ADDER_IW  # north wall: partial sums out
ADDER_P2_ROW = 1  # east wall: partial sums in


def adder_cells() -> dict[tuple[int, int], str]:
    out: dict[tuple[int, int], str] = {}
    for y, row in enumerate(_ADDER, start=1):
        for x, ch in enumerate(row, start=1):
            if ch != " ":
                out[(x, y)] = ch
    return out


# ── a relay: one `r`, one `s`, one value in flight ───────────────────────────
#: ``memory_tape``'s relay verbatim. With exactly one incoming and one outgoing
#: pipe both glyphs bind whatever the geometry, so its pipes may attach anywhere.
_RELAY = [
    " >v",
    " sr",
    " ^<",
]
RELAY_IW = len(_RELAY[0])
RELAY_IH = len(_RELAY)


def relay_cells() -> dict[tuple[int, int], str]:
    out: dict[tuple[int, int], str] = {(1, 1): "@"}
    for y, row in enumerate(_RELAY, start=1):
        for x, ch in enumerate(row, start=1):
            if ch != " ":
                out[(x, y)] = ch
    return out


# ── two relays, one room, one starter ────────────────────────────────────────
# The STREAM block needs two identical persistent relay men, one for ring A and
# one for ring B. They currently live in separate 3x3 rooms. This layout is the
# first production-oriented use of Y: a single starter splits north/south, then
# each child enters one unchanged `r`/`s` relay loop. It is deliberately exposed
# before placement so geometry and runtime semantics can be pinned independently.
#
# Coordinates are interior, 1-based like ``relay_cells``:
#
#     >>v       upper child joins at the extra `>` on the west, so that — like
#     ^sr       `_RELAY` itself — it reaches its `r` before its `s` and never sends
#     ^^<       the 0 it was born holding
#     ^<
#     @Y
#      v
#      >v       lower child is born directly above its loop
#      sr
#      ^<
#
# The upper route is longer, but startup latency is irrelevant: both men reach
# blocking `r` before the CPU can finish filling its command/tape state.
DUAL_RELAY_IW = 3
DUAL_RELAY_IH = 9


def dual_relay_cells() -> dict[tuple[int, int], str]:
    """Two disjoint relay loops seeded by one ``Y`` in one compute room."""
    rows = (
        ">>v",
        "^sr",
        "^^<",
        "^< ",
        "@Y ",
        " v ",
        " >v",
        " sr",
        " ^<",
    )
    if {len(row) for row in rows} != {DUAL_RELAY_IW}:
        raise StreamError("dual relay rows are not fixed-width")
    return {
        (x, y): ch
        for y, row in enumerate(rows, start=1)
        for x, ch in enumerate(row, start=1)
        if ch != " "
    }


#: Where a four-pipe dual relay's pipes attach, as ``band -> (wall, offset)``. Two
#: loops, one room, four pipes — ``ARCH.md`` §2.1's "one room can turn around many
#: rings", which is what this room is for: it turns one ring around *and* passes a
#: second pipe through, so the passed pipe's ports become leaves of the same tree
#: instead of being separated by it (:func:`block_crossings`).
#:
#: §2.1's caution is that a relay shared between rings blocks on the first empty one.
#: That is why this is two *men*, not one man serving four pipes: each loop has its
#: own ``r``/``s`` pair and its own cells, so an empty pipe parks one man and leaves
#: the other cycling. Their steady-state cell sets are disjoint by **layout**, not by
#: timing, so the two can never meet — which matters because ``SPEC.md`` kills both
#: men on a same-cell arrival, silently and with no fatal error.
#:
#: **As written, these four ports do not let ``prod`` cross ring B's chord** — and that
#: is a defect in the *assignment*, not in the room.
#:
#: The property the planarity argument needs is one nobody had stated: a room that turns
#: ring B around *and* passes ``prod`` through must have ``prod`` enter on one side of
#: ring B's chord and leave on the other, because the room is the only place a pipe may
#: cross another. The chord meets the room at ``turn_in`` and ``turn_out``, which split
#: the boundary into two arcs, so ``pass_in`` and ``pass_out`` have to fall in
#: *different* ones. These four do not: reading clockwise they come out ``turn_out``,
#: ``turn_in``, ``pass_out``, ``pass_in``, with both passed ports on one side. The two
#: checks this module *did* run — four pipes bind, with a minimum margin of 5, and no
#: two of the four legs need to cross — are both true of it and neither can see that.
#:
#: **Round 9 over-claimed this, and the over-claim is corrected rather than deleted**,
#: the way §R7.1 is kept as a wrong frame. It said the stacked room could not do it *for
#: any port assignment at any size*. That is false. Enumerating every ordered 4-tuple of
#: attach positions at 3x9 under :func:`pipe_bindings` finds **1238 strictly-binding
#: interleaved assignments**, and ``test_the_stacked_relay_can_interleave_after_all``
#: pins one of them — ``turn_in`` north 2, ``pass_in`` west 3, ``turn_out`` west 7,
#: ``pass_out`` east 8 — verified on the engine, not on the model.
#:
#: What survives is narrower and is what actually decided the drawing: **no assignment
#: with the four ports on four distinct walls binds and interleaves here** — 0 of them,
#: against 2809 for :func:`shared_relay_cells` — and four distinct walls is what
#: :func:`_place4`'s geometry wants, because ring B arrives from the far west, returns
#: east into the unit, ``prod`` climbs from below and ``pass_out`` leaves north into the
#: ADDER. So the crossed room is the one that fits *this* placement; the stacked room is
#: not unusable, and a different placement could well be built on it.
#:
#: The room is kept because it is the smaller demonstration of the ``Y`` idiom the block
#: relies on, and because a corrected claim beside it is worth more than a deletion.
DUAL_RELAY_PORTS = {
    "turn_in": ("north", 3),  # the ring's fill drops onto the upper `r`
    "turn_out": ("north", 2),  # the ring's return climbs straight off the upper `s`
    "pass_in": ("west", 8),  # the passed pipe arrives westward at the lower `r`
    "pass_out": ("south", 2),  # and drops on southward from the lower `s`
}


def dual_relay_probe(upper_driven: bool = True) -> list[str]:
    """The four-pipe dual relay alone, with one loop fed from ``I`` and drained to ``O``.

    The other loop's pipes hang off manless stub rooms, so its ``r`` blocks forever —
    which is the point. §2.1's caution is that a relay shared between rings blocks on
    the first empty one, and this grid is what shows two *men* do not: values keep
    flowing through the driven loop while the idle one is parked on an empty pipe.
    """
    from .machine import _Grid

    g = _Grid()
    rx, ry = 10, 8  # the relay room's north-west wall corner
    g.room(rx, ry, rx + DUAL_RELAY_IW + 1, ry + DUAL_RELAY_IH + 1)
    g.blit(rx, ry, dual_relay_cells())
    south_y = ry + DUAL_RELAY_IH + 1

    def outer(band: str) -> tuple[int, int]:
        """The cell just outside the wall this port attaches to."""
        wall, off = DUAL_RELAY_PORTS[band]
        return {
            "north": (rx + off, ry - 1),
            "south": (rx + off, south_y + 1),
            "west": (rx - 1, ry + off),
        }[wall]

    def feed(band: str, label: str | None) -> None:
        """A source room out beyond ``band``, with a pipe into the relay."""
        wall, _off = DUAL_RELAY_PORTS[band]
        x, y = outer(band)
        if wall == "north":
            g.room(x, y - 4, x + 2, y - 2)
            g.draw_pipe([(x, y - 1), (x, y)])
            spot = (x + 1, y - 3)
        elif wall == "south":
            g.room(x, y + 2, x + 2, y + 4)
            g.draw_pipe([(x, y + 1), (x, y)])
            spot = (x + 1, y + 3)
        else:  # west
            g.room(x - 5, y - 1, x - 3, y + 1)
            g.draw_pipe([(x - 2, y), (x, y)])
            spot = (x - 4, y)
        if label:
            g.put(*spot, label)

    def drain(band: str, label: str | None) -> None:
        """A sink room out beyond ``band``, with a pipe out of the relay."""
        wall, _off = DUAL_RELAY_PORTS[band]
        x, y = outer(band)
        if wall == "north":
            # climb clear of the neighbouring north port's room, then turn west
            g.room(x - 8, y - 7, x - 6, y - 5)
            g.draw_pipe([(x, y), (x, y - 6), (x - 5, y - 6)])
            spot = (x - 7, y - 6)
        elif wall == "south":
            g.room(x - 8, y + 5, x - 6, y + 7)
            g.draw_pipe([(x, y), (x, y + 6), (x - 5, y + 6)])
            spot = (x - 7, y + 6)
        else:  # west
            g.room(x - 4, y - 1, x - 2, y + 1)
            g.draw_pipe([(x, y), (x - 1, y)])
            spot = (x - 3, y)
        if label:
            g.put(*spot, label)

    driven_in, driven_out = ("turn_in", "turn_out") if upper_driven else ("pass_in", "pass_out")
    idle_in, idle_out = ("pass_in", "pass_out") if upper_driven else ("turn_in", "turn_out")
    feed(driven_in, "I")
    drain(driven_out, "O")
    feed(idle_in, None)  # a manless stub: nothing ever sends, so this loop parks
    drain(idle_out, None)
    return g.rows()


# ── which pipe a glyph binds, in SPEC's own direction ─────────────────────────
def wall_segments(iw: int, ih: int) -> dict[tuple[str, int], tuple[int, int]]:
    """``(wall, interior offset) -> the pipe segment cell just outside that wall``.

    Interior coordinates, 1-based like :func:`relay_cells`, so the walls themselves are
    at 0 and ``iw+1``/``ih+1`` and a pipe's own first (or last) cell is one further out.
    That outer cell is what ``SPEC.md`` measures to: "the pipe segment attached to the
    current room".
    """
    out: dict[tuple[str, int], tuple[int, int]] = {}
    for x in range(1, iw + 1):
        out[("north", x)] = (x, -1)
        out[("south", x)] = (x, ih + 2)
    for y in range(1, ih + 1):
        out[("west", y)] = (-1, y)
        out[("east", y)] = (iw + 2, y)
    return out


def boundary_clockwise(iw: int, ih: int) -> list[tuple[str, int]]:
    """Every position a pipe may attach to, clockwise from the north-west corner."""
    return (
        [("north", x) for x in range(1, iw + 1)]
        + [("east", y) for y in range(1, ih + 1)]
        + [("south", x) for x in range(iw, 0, -1)]
        + [("west", y) for y in range(ih, 0, -1)]
    )


def pipe_bindings(
    glyphs: Sequence[tuple[str, tuple[int, int]]],
    ports: Sequence[tuple[str, tuple[int, int]]],
) -> dict[str, tuple[str, int]]:
    """``glyph name -> (the port it binds, the margin to the runner-up)``.

    ``SPEC.md``, "Which pipe do I talk to?", in **its own direction**: a pipe glyph takes
    the *nearest pipe*, Manhattan, measured from the instruction to the pipe segment
    attached to this room, ties broken by that **segment's** reading order (top to
    bottom, left to right).

    The direction matters and getting it backwards is a real bug, which this module made:
    "port ``F`` is nearer glyph ``a`` than glyph ``b``" is a different proposition from
    "glyph ``a`` is nearer port ``F`` than port ``P``", and only the second is what the
    engine evaluates. An earlier version of this helper computed the first, partitioned
    the room's boundary into "arcs" on that basis, and an impossibility claim was built
    on top of it. See :func:`dual_relay_cells` for what that cost.

    ``glyphs`` and ``ports`` must be in one coordinate frame, and ``ports`` are segment
    cells (:func:`wall_segments`). Call it once per pool — ``r`` against the incoming
    pipes, ``s`` against the outgoing — because ``SPEC.md`` keeps those separate. A
    margin of 0 means the binding rests on the tie-break rather than on distance.
    """
    if len(ports) < 2:
        raise StreamError("pipe_bindings compares pipes; give it at least two")
    out: dict[str, tuple[str, int]] = {}
    for name, (gx, gy) in glyphs:
        ranked = sorted(
            (abs(px - gx) + abs(py - gy), py, px, port) for port, (px, py) in ports
        )
        out[name] = (ranked[0][3], ranked[1][0] - ranked[0][0])
    return out


#: The shared relay the block places: two men from one ``Y``, ring B turned around
#: **west to east** through the middle and ``prod`` passed **south to north** across it.
#:
#: The two channels cross at right angles, and that is what makes the four ports
#: **interleave**: clockwise they come out ``pass_out, turn_out, pass_in, turn_in``, so
#: ring B's two separate ``prod``'s two and ``prod`` really does cross the chord inside
#: the room. That is the condition :data:`DUAL_RELAY_PORTS`' assignment misses, and the
#: reason this room exists.
#:
#: Its concrete advantage over the stacked room is narrower than round 9 claimed: with
#: the four ports on **four distinct walls** — which is what :func:`_place4` wants, since
#: ring B arrives from the far west, returns east into the unit, ``prod`` climbs from
#: below and ``pass_out`` leaves north into the ADDER — this room has 2809 strictly
#: binding interleaved assignments and the stacked one has none.
#:
#: The two loops are *nested*, which they have to be: the ring's loop connects a west
#: cell to an east cell and the passed loop a south cell to a north cell, so one has to
#: go round the other. The ring takes the inner ring (rows 3..5, columns 3..7) and the
#: passed pipe the outer frame, and the ``@``/``Y`` sits in the annulus between them so
#: each child walks into its own loop without ever stepping on the other's cells.
#: Both children reach their ``r`` before their ``s``, so neither sends the 0 it was
#: born holding::
#:
#:      v<<<s<<<<      outer loop (prod): the frame. `r` south, `s` north.
#:      v @Y    ^      the starter, in the annulus; Y's children go north and south
#:      v v<<<< ^      inner loop (ring B): rows 3..5, columns 3..7
#:      v r   s ^      `r` west at (3,4), `s` east at (7,4)
#:      v >>>>^ ^
#:      v       ^
#:      >>>>r>>>^
SHARED_RELAY_IW = 9
SHARED_RELAY_IH = 7

#: ``band -> (wall, interior offset)``, each in the middle of its wall.
#:
#: The binding margins are **3, 7, 3, 7** — minimum :data:`SHARED_RELAY_MIN_MARGIN`, and
#: asserted, because an unasserted margin is how the round-9 figure came to be wrong in
#: both value and direction. (It said 5, and said that beat the stacked room's; the
#: stacked room's documented ports are the ones with a minimum of 5. Margin was never the
#: reason to prefer this room — interleaving was.) 3 is comfortable: the tightest margin
#: anywhere else in this block is 1 cell, inside the unit.
SHARED_RELAY_PORTS = {
    "turn_in": ("west", 4),  # ring B's fill arrives eastward at the inner `r`
    "turn_out": ("east", 4),  # ring B's return leaves eastward from the inner `s`
    "pass_in": ("south", 5),  # prod climbs onto the south wall, at the outer `r`
    "pass_out": ("north", 5),  # and leaves northward from the outer `s`
}

#: ``band -> the interior cell that must bind it``, so the probe and the block check the
#: same four facts.
SHARED_RELAY_GLYPHS = {
    "turn_in": ("r", (3, 4)),
    "turn_out": ("s", (7, 4)),
    "pass_in": ("r", (5, 7)),
    "pass_out": ("s", (5, 1)),
}

#: The smallest distance by which any of the four glyphs prefers its own pipe to the
#: other of its kind. Asserted rather than commented (``test_the_shared_relays_ports``).
SHARED_RELAY_MIN_MARGIN = 3


def shared_relay_bindings() -> dict[str, tuple[str, int]]:
    """``band -> (the band each glyph actually binds, its margin)``, by SPEC's rule.

    One call per pool, since ``SPEC.md`` keeps incoming and outgoing pipes separate. The
    block, the probe and the enumeration all go through this, so there is one place where
    "which pipe does this glyph take" is computed.
    """
    seg = wall_segments(SHARED_RELAY_IW, SHARED_RELAY_IH)
    out: dict[str, tuple[str, int]] = {}
    for kind in "rs":
        bands = [b for b, (g, _c) in SHARED_RELAY_GLYPHS.items() if g == kind]
        out |= pipe_bindings(
            [(b, SHARED_RELAY_GLYPHS[b][1]) for b in bands],
            [(b, seg[SHARED_RELAY_PORTS[b]]) for b in bands],
        )
    return out


def shared_relay_cells() -> dict[tuple[int, int], str]:
    """Two nested relay loops seeded by one ``Y``, crossing at right angles."""
    rows = (
        "v<<<s<<<<",
        "v @Y    ^",
        "v v<<<< ^",
        "v r   s ^",
        "v >>>>^ ^",
        "v       ^",
        ">>>>r>>>^",
    )
    if {len(row) for row in rows} != {SHARED_RELAY_IW} or len(rows) != SHARED_RELAY_IH:
        raise StreamError("shared relay rows are not the declared size")
    return {
        (x, y): ch
        for y, row in enumerate(rows, start=1)
        for x, ch in enumerate(row, start=1)
        if ch != " "
    }


def shared_relay_probe(inner_driven: bool = True) -> list[str]:
    """The crossed shared relay alone, with one loop fed from ``I`` and drained to ``O``.

    Same shape of check as :func:`dual_relay_probe`: the idle loop's ``r`` hangs off a
    manless stub, so §2.1's "a shared relay blocks on the first empty ring" would show
    up as no output at all rather than as an argument.
    """
    from .machine import _Grid

    g = _Grid()
    rx, ry = 12, 12  # the relay room's north-west wall corner
    g.room(rx, ry, rx + SHARED_RELAY_IW + 1, ry + SHARED_RELAY_IH + 1)
    g.blit(rx, ry, shared_relay_cells())

    def outer(band: str) -> tuple[int, int]:
        """The cell just outside the wall this port attaches to."""
        wall, off = SHARED_RELAY_PORTS[band]
        return {
            "north": (rx + off, ry - 1),
            "south": (rx + off, ry + SHARED_RELAY_IH + 2),
            "west": (rx - 1, ry + off),
            "east": (rx + SHARED_RELAY_IW + 2, ry + off),
        }[wall]

    def stub(band: str, label: str | None, *, into: bool) -> None:
        """A 3x3 room four cells out along ``band``'s wall, and the two-cell pipe to it.

        Cell 1 out is the pipe's near end, cell 2 its far end, cell 3 the stub's wall
        and cell 4 its interior — the shortest legal pipe (``SPEC.md``: two cells, an
        arrowhead on each end), so nothing about the *leg* can affect the binding.
        """
        wall, _off = SHARED_RELAY_PORTS[band]
        near = outer(band)
        dx, dy = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}[wall]
        far = (near[0] + dx, near[1] + dy)
        home = (near[0] + dx * 3, near[1] + dy * 3)
        g.room(home[0] - 1, home[1] - 1, home[0] + 1, home[1] + 1)
        g.draw_pipe([far, near] if into else [near, far])
        if label:
            g.put(*home, label)

    driven, idle = (("turn", "pass") if inner_driven else ("pass", "turn"))
    stub(f"{driven}_in", "I", into=True)
    stub(f"{driven}_out", "O", into=False)
    stub(f"{idle}_in", None, into=True)  # nothing ever sends, so this loop parks
    stub(f"{idle}_out", None, into=False)
    return g.rows()


@dataclass
class StreamBlock:
    """A placed STREAM block: cells, its two CPU-facing anchors, capacities."""

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    #: the cell a command pipe from the CPU must end on, pointing south
    cmd_cell: tuple[int, int]
    #: the cell the response pipe has climbed to; the caller carries it north
    resp_cell: tuple[int, int]
    ring_a: int  # capacity in values
    ring_b: int
    ring_c: int
    rows_a: int  # serpentine rows each long ring needed
    rows_b: int
    pipes: int  # pipes the block draws (the engine must find exactly these + 1)
    glyphs: list[tuple[int, int, str, str]] = field(default_factory=list)
    codes: dict[str, int] = field(default_factory=dict)
    #: ``name -> (x, y, w, h)`` in block coordinates, for ``Machine.debug_map`` and
    #: the heat map. Without it a profile of this block is 989 anonymous pipe cells.
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


# ── placement ────────────────────────────────────────────────────────────────
# Every column and row below is chosen for *planarity*: fifteen pipes share this
# block and none of them may cross. Two rules do all the work.
#
# **Southbound pipes leaving the unit's east wall turn at columns that decrease
# as their row increases.** A pipe leaving at row R and turning south at column T
# occupies the run ``49..T`` on row R and then the column T below it; a pipe
# leaving further down turns further west, so its run stops short of T. That is
# why the east-wall row order (`resp` topmost, then ring A, ring B, `prod`, `p2`,
# `out`) is not cosmetic — `resp` is the one pipe that climbs *north*, so it must
# be the topmost row or its climb would cross everything below it.
#
# **The three pipes arriving at the west wall come from rooms stacked in the same
# top-to-bottom order as their wall rows** (A's relay above the I room above B's
# relay, for wall rows 12, 14, 15), so their jogs nest instead of crossing.
UX, UY = 14, 1  # the unit room's north-west wall corner
W_JOG = {"a_ret": 12, "in": 10, "b_ret": 11}  # jog column per west-wall pipe
#: Column each east-wall pipe turns at. Decreasing with the row for the five that
#: head *south* (see the note above); ``resp`` is the exception and turns as far
#: **west** as it can, because it heads north and so crosses only rows above its
#: own — where nothing else is. Hugging the block there keeps its climb clear of
#: whatever the caller has placed east of the machine: grazing a room corner is
#: legal (§7.4b) and therefore silent, and the tape's relay sits exactly there.
E_TURN = {"resp": 50, "a_fwd": 66, "b_fwd": 65, "prod": 60, "p2": 56, "out": 51}
RELAY_A_Y, IO_IN_Y, RELAY_B_Y = 5, 11, 15  # west stack, top to bottom
ROOM_X = 5  # west stack's west wall
ADDER_X, ADDER_Y = 53, 30
O_ROOM = (50, 24)  # north-west wall corner of the O room
BAND_B, BAND_A = 36, 42  # first serpentine row of each long ring
LEG_W, LEG_E = 5, 62  # serpentine leg span; the last leg reaches the climb column
CLIMB = {"a": 1, "b": 3}


def _serpentine(
    y0: int, rows: int, climb: int, *, leg_w: int = LEG_W, leg_e: int = LEG_E
) -> list[tuple[int, int]]:
    """Boustrophedon corners, west first and ending west at ``climb``.

    ``rows`` is forced odd by the caller so the last leg is westbound: the ring's
    long pipe then climbs the far-west column, which no other pipe uses, and turns
    east into its relay. Intermediate westward legs stop at ``LEG_W`` so they never
    touch that climb column.
    """
    pts: list[tuple[int, int]] = []
    for i in range(rows):
        y = y0 + i
        last = i == rows - 1
        pts.append(((climb if last else leg_w) if i % 2 == 0 else leg_e, y))
        if not last:
            pts.append((pts[-1][0], y + 1))
    return pts


# ── placement, depth 4: why the ADDER alone cannot do it ─────────────────────
# The topology argument that decides the whole depth-4 block, kept because it is what
# makes the shared relay *necessary* rather than convenient: with the ADDER's three
# legs as the only tree, the block is not planar, and no choice of port rows, leg
# span, band depth or column allocation changes that. :func:`block_crossings`
# computes it and ``test_the_two_refuted_fixes_stay_refuted`` pins it.
#
# **The argument.** ``cmd`` enters and ``resp`` leaves through the north wall, and
# both run to the CPU above, so no pipe can cross the top: the region the block's
# pipes may use is a disk with the unit removed and the north side cut, i.e. an
# *interval* of the unit's perimeter. Label the ports clockwise from the top of the
# east wall, down it, and back up the west wall (:func:`perimeter_order`).
#
# The block must connect:
#
#   * ``p2`` -> ADDER, ``prod`` -> ADDER, ADDER -> ``p1`` — one room, three legs;
#   * ``a_fwd`` -> ring A's relay -> ``a_ret``;
#   * ``b_fwd`` -> ring B's relay -> ``b_ret``;
#   * ``out`` -> the O room and the I room -> ``in``, which are terminals and free.
#
# The ADDER's three legs form a tree touching the perimeter at ``p2``, ``prod`` and
# ``p1``, which cuts the disk into three sectors. A ring's pair is routable **iff
# both of its ports lie in one sector** — and splitting a pipe with a relay does not
# help, because a relay changes neither endpoint: ``a_fwd`` -> relay -> ``a_ret`` is
# still one curve from ``a_fwd`` to ``a_ret``. Nor does any choice of leg span, band
# depth or column allocation, for the same reason.
#
# As drawn, both ring pairs straddle a sector boundary, so **two** crossings are
# forced. Two facts make that unavoidable rather than a numbering accident:
#
#   * ``prod`` is the bottom-most east port, because ``MAC`` must push ``b`` back
#     (``s`` on ring B's fill) *before* it can multiply and send the product, so
#     ``R_B_FWD < R_PROD`` always; and
#   * ``b_ret`` is the bottom-most west port, because ``UPDB`` reads ring B last.
#
# So ``prod`` always sits between ``b_fwd`` and ``b_ret`` in the cyclic order.
#
# **Two things do fix it, and both are cheap.**
#
# 1. :func:`updb_body`'s *other* valid order — reading ring A's return before the
#    accumulator's (``rsMrs*…`` with no padding, the order this module first drew) —
#    puts ``a_fwd`` at the top of the east wall and ``a_ret`` at the bottom of the
#    west, so ring A's pair lands in the sector that wraps the interval's ends and
#    **one of the two crossings disappears**. Both orders compute the same thing and
#    cost the same glyphs, so this is free.
# 2. The remaining crossing goes away if ``prod``'s pipe is *split through ring B's
#    relay* — ``ARCH.md`` §2.1's "one room can turn around many rings", which is what
#    :func:`dual_relay_cells` was exposed for: two men from one ``Y``, so ring B's
#    loop never blocks behind the product stream (§2.1's caution — the product stream
#    is not permanently full, ring B is). The ADDER's tree then touches the perimeter
#    at four points instead of three, ``b_fwd`` and ``b_ret`` become adjacent leaves
#    of it rather than being separated by it, and **the block is planar**.
#
# Both change the block's internals only: no port moves wall, and no port order
# changes except within the unit's own east and west walls, which is the row map the
# unit already owns.
#
# **Two negative results, and they are the load-bearing part.** Each was proposed —
# twice, by different people — and each is refuted by an enumeration over every row map
# the unit's own arms allow: all four incoming ports on the west wall and all six outgoing
# on the east, ``b_ret`` bottom-most west (``UPDB`` reads ring B last) and ``b_fwd`` above
# ``prod`` (``MAC`` pushes ``b`` back before it multiplies). That is **2160** maps, crossed
# with four trees, with the depth-3 block as a control through the same call.
#
# Round 6 quoted these as "0 of 114" from a search that was never landed, so as pinned
# they held only at the single drawn map — which is not what a refutation is for. The
# enumeration is now the test (``test_the_two_refuted_fixes_stay_refuted``), so the
# results are reproducible from the code rather than from a lost script, and the space is
# larger than the one they were originally claimed over:
#
# * **The ADDER alone closes 0 of 2160.** No choice of port rows, leg span, band depth
#   or column allocation makes the block planar without a shared relay. A relay is
#   *necessary*. That is why "give the two bands disjoint leg spans" — and every other
#   capacity-shaped idea — cannot work: the obstruction is not capacity.
# * **Ring A's relay closes 0 of 2160; it has to be ring B's.** Ring A's chord
#   *encloses* ring B's, so passing ``prod`` through ring A's relay leaves ring B
#   crossing instead. The asymmetry is forced rather than incidental: ``UPDB`` reads
#   ring A *before* the accumulator and ring B *after* it, so ring A's two ports end
#   up outside ring B's on the perimeter.
# * **"Each southbound pipe drops down a western column of its own, drop columns
#   rising with the corridor row" is REFUTED — it was never this block's geometry.**
#   This module invented that discipline in round 4 to explain a real collision, it did
#   explain it, and three rounds of prose then treated it as forced. Rendering the
#   *depth-3* block (``manview``) falsified it: depth 3 uses **no western drop columns
#   at all**. Everything that has to cross from the east wall to the west side goes
#   *around the outside* — east to an outer turn column, south to a serpentine band,
#   west along the band, north up a far-west column, into its relay — which is why the
#   ring relays sit beside the unit at its own rows and the returns are short
#   horizontal runs. Only rooms in the *strip* between the unit and the bands (the
#   ADDER, the O room) are reached by a middling turn column descending part-way.
#   The invented rule is what produced the round-7 BLOCKED claim, and that claim is
#   withdrawn. ``prod`` should be routed like a ring *forward* pipe, because that is
#   exactly its job: east wall to a west-side relay. Rules 1 and 3 survive; this one
#   is recorded so it is not re-derived a second time.
# * **A shared relay with its two loops stacked — each loop's ``r`` and ``s`` on one
#   row — does not let ``prod`` cross ring B's chord, and no assignment of its four
#   ports to four *distinct walls* does either.** The property the planarity result
#   needs is a third one nobody had stated: ``prod`` must *enter on one side of ring B's
#   chord and leave on the other*, since the room is the only place a pipe may cross
#   another. The chord meets the room at two points, splitting its boundary into two
#   arcs, so the passed pipe's two ports must fall in different ones. The stacked room's
#   documented ports do not, and the two checks that were run of it — four pipes bind
#   with a minimum margin of 5, no two of the four legs need to cross — are both true
#   and neither can see that.
#
#   **Round 9 stated this far too strongly and the over-claim is corrected here rather
#   than deleted.** It said the stacked room could not do it for *any* assignment at any
#   size. That is false: 1238 assignments at 3x9 bind strictly and interleave, and
#   ``test_the_stacked_relay_can_interleave_after_all`` pins one on the engine. What
#   holds is the four-distinct-wall statement above — 0 against 2809 for
#   :func:`shared_relay_cells` — and four distinct walls is what :func:`_place4`'s
#   geometry wants. The crossed room fits *this* placement; the stacked room is not
#   unusable.
#
#   The over-claim had a cause worth recording: the helper it rested on computed
#   "which glyph is this port nearest to" when ``SPEC.md`` asks "which pipe is this
#   glyph nearest to". Those are different propositions, the second is the engine's, and
#   :func:`pipe_bindings` now computes that one — checked against the engine on 35 real
#   bindings.
#
#   The general lesson, since it is the same one four times: every negative result on
#   this task came from checking a property nobody had thought to state, and the one
#   false result came from checking the converse of the property that mattered. A
#   verified artifact is only verified against the checks it was given.



# ── the withdrawn obstruction, kept so the wrong frame is not reinvented ─────
# The depth-4 block is drawn now (:func:`_place4`). What follows is the obstruction
# this module reported for three rounds instead, and it was **an artefact of a layout
# scheme this module invented, not a property of the block**. It is kept because the
# correction is worth more than the obstruction was, and because ``prod``'s actual
# route in :func:`_place4` is the thing the correction predicted.
#
# **What was claimed.** That rule 2 — "each southbound pipe runs west along a corridor
# row of its own and drops down a column of its own, corridor rows and drop columns
# rising with the turn column" — forces ``prod``'s drop column to be the westmost of
# five, that rule 3 forces ``b_ret``'s climb west of all of them, and that ``b_ret``'s
# jog out of the shared relay therefore always crosses ``prod``'s descent.
#
# **Why that was the wrong frame.** Rendering the *depth-3* block (``manview``) shows
# it uses no western drop columns at all. Everything that has to cross from the east
# wall to the west side goes **around the outside**: east to an outer turn column,
# south to a serpentine band, west along the band, north up a far-west column (1 or 3),
# and into its relay — which is why the two ring relays sit beside the unit at its own
# rows and the returns are short horizontal runs. Only the rooms in the *strip* between
# the unit and the bands (the ADDER, the O room) are reached by a middling turn column
# descending part-way. The westbound-corridor discipline was this module's own
# invention and it is what created the collision; the block's own idiom does not have
# it.
#
# **What that means for ``prod``.** ``prod`` has to get from the east wall to the shared
# relay on the west side, which is exactly what a ring *forward* pipe does. So it
# should be routed like one — east, down, west along a band-like row, up a far-west
# column of its own, into the relay — and then there is no western descent for
# ``b_ret``'s exit to cross, because ``b_ret``'s exit is a short horizontal run into
# the unit's west wall like the other two returns. The far west then needs three climb
# columns (ring A's forward, ring B's forward, ``prod``) rather than two.
#
# That is what :func:`_place4` does, and it is what closed it. Rule 1 (turn columns
# fall as the east-wall row rises) and rule 3 (each incoming pipe's own climb column,
# ordered by the wall row it feeds) both survive; rule 2 as stated does not, and is
# recorded here only so it is not reinvented. What the correction did *not* predict is
# that the relay this module had already built does not, at the ports it documents, let
# ``prod`` cross ring B's chord — see :data:`DUAL_RELAY_PORTS` for that, the fourth
# refuted constraint, and for the round-9 over-claim that came with it.
#
# ── placement constants, depth 4 ─────────────────────────────────────────────
# The depth-3 idiom, transcribed. Nothing crosses from the east wall to the west side
# through a *western* drop column (the refuted rule 2); everything that has to change
# sides goes round the outside — east to its own turn column, south past the unit,
# west along a corridor of its own, north up a far-west column, into its room.
#
# **Rule 1 survives**: turn columns fall as the east-wall row rises, so the pipe that
# leaves highest runs furthest east before turning south and the five never braid.
# ``resp`` is the exception and hugs the block, because it climbs *north* and the only
# rows it crosses are the ones above its own, where nothing else is.
#
# **The corridors nest the other way round**: a pipe with a *smaller* turn column has
# to turn west at a row *above* one with a larger turn column, or the outer pipe's
# westbound leg cuts the inner pipe's descent. So north to south below the unit:
# ``prod``'s corridor, ring B's band, ``p2``'s corridor, ring A's band. ``out`` never
# goes west at all — like depth 3 it drops into the O room in the east strip.
#
# **Rule 3 survives**: each far-west climb column is ordered so a leg spanning
# ``[climb, its room]`` excludes every climb west of it. ``a`` is westmost because ring
# A's chord encloses everything, then ``p2``, then ``b``.
#
# North to south in the west channel: ring A's relay (``a_ret`` at wall row 13), the I
# room (``in`` at 14), ``p1``'s boustrophedon and the ADDER (``p1`` at 15), the shared
# relay (``b_ret`` at 27). The ADDER sits *above* the shared relay so ``pass_out`` is a
# two-cell climb between them, and ``p1`` — which needs a whole row of C — takes its
# length from a boustrophedon in the channel rather than from a fold under the unit.
UX4, UY4 = 34, 1
#: Far-west climb column per pipe that has to come back up the west side. Ring A's is
#: westmost because its chord encloses the whole assembly (:func:`block_crossings`).
CLIMB4 = {"a": 1, "p2": 2, "b": 3}
LEG_W4 = 5  # both bands' intermediate west legs stop here, clear of every climb
RELAY_A4 = (5, 2)  # ring A's relay: `a_fwd` in at its west wall, `a_ret` out east
IO_IN4 = (5, 8)  # the I room
ADDER4 = (6, 16)  # `p2` in at its west wall, `pass_out` up into its south, `p1` out east
RELAY_B4 = (7, 23)  # the shared relay: ring B west-to-east, `prod` south-to-north
#: Jog column for the two west-wall pipes that come from rooms above their own row.
#: ``in``'s is west of ``a_ret``'s, which is what makes the two jogs nest — the same
#: ordering depth 3 uses, for the same reason.
J4 = {"in": 10, "a_ret": 11}
P1_ROWS4 = (15, 21)  # p1's boustrophedon, north to south; its last leg is row 15
P1_LEG_W4 = 19  # its west limit: one column clear of p1's own descent at 17
PROD_ROW4 = 36  # prod's corridor: below the unit's south wall, above ring B's band
BAND_B4 = 38  # ring B's band, immediately below prod's corridor


def build_stream(
    *,
    a_slots: int,
    b_slots: int,
    c_slots: int,
    trie_bits: int = TRIE_BITS,
    lr_shift: int = UPDB_SHIFT,
) -> StreamBlock:
    """Place the unit, its ADDER, both ring relays and the I/O rooms.

    ``a_slots``/``b_slots`` are the values each long ring must hold — ``N*M`` and
    ``M*K`` at the problem's maximum, plus one, since a ring is briefly holding one
    more value than it stores (the same +1 ``memory_tape`` needs). The serpentine
    grows a row at a time until the pipes are long enough; capacity *is* length
    (``SPEC.md``: a pipe is a FIFO whose capacity equals its cell count).

    ``trie_bits`` picks the drawing, and a width this module cannot *place* is
    refused rather than approximated. Refusing is the point: handing a depth-4
    program a depth-3 trie reads its ``PUSHA`` as ``EMIT`` and its ``ROTB`` as
    ``FILLB``, and the machine then runs to completion computing nonsense — the one
    outcome a builder is allowed to prevent by refusing to build. Both widths are
    now drawn and placed; a third is still refused, and the depth-3 unit still
    refuses a depth-4 program (:func:`_spec`).

    ``lr_shift`` is ``UPDB``'s shift, drawn into the arm's glyphs, and it is checked
    rather than trusted: the glyphs about to be drawn are read back
    (:func:`drawn_shift`) and a disagreement is a refusal. A depth-3 unit has no
    ``UPDB`` at all, so asking one for a shift is refused too — the program wants
    arithmetic that width cannot do, and drawing it anyway is the silent
    grid-versus-emulator divergence this check exists to stop.
    """
    from .machine import MachineError

    spec = _spec(trie_bits, lr_shift)  # refuses a width this module cannot draw
    if spec.trie_bits == TRIE_BITS:
        if lr_shift != UPDB_SHIFT:
            raise StreamError(
                f"the program declares .equ STREAM_LR_SHIFT {lr_shift}, but a depth-3 "
                "unit has no UPDB arm to shift with — it draws matmul's original eight "
                "arms. Declare `.unit stream4` for a program that updates weights."
            )
    else:
        got = drawn_shift(updb_body(lr_shift))
        if got != lr_shift:
            raise StreamError(
                f"UPDB's drawn glyphs {updb_body(lr_shift)!r} shift by {got}, not the "
                f"{lr_shift} the program declares with .equ STREAM_LR_SHIFT. Drawing "
                "this would make the grid and the emulator disagree about the weight "
                "update with nothing to catch it."
            )
    place = _place if spec.trie_bits == TRIE_BITS else _place4

    # ``rows_a`` outer, because the block's height is set by ring A's band — it is
    # the lowest thing in the block, so ring B's extra rows are free until they
    # push past it (``band_a`` in :func:`_place`).
    for rows_a in range(1, 16, 2):
        for rows_b in range(1, 16, 2):
            try:
                blk = place(a_slots, b_slots, c_slots, rows_a, rows_b, lr_shift)
            except MachineError:
                continue
            if blk.ring_a >= a_slots and blk.ring_b >= b_slots:
                return blk
    raise MachineError(f"no serpentine holds {a_slots} + {b_slots} values; widen the band")


def _place4(
    a_slots: int, b_slots: int, c_slots: int, rows_a: int, rows_b: int, lr_shift: int = UPDB_SHIFT
) -> StreamBlock:
    """The depth-4 block: twelve arms, a shared relay, and ``prod`` routed as a forward.

    Same idiom as :func:`_place` and a different set of rooms, because the depth-4 row
    map moves the accumulator's return from the unit's *south* wall to its **west** wall
    and puts ring B's return 12 rows below everything else. Three consequences:

    * ``p1`` arrives on the west wall, so the ADDER moves into the west channel, and
      ``p2`` — not ``p1`` — is the accumulator leg that goes the long way round.
    * ``prod`` is routed like a ring forward (east, south, west, north) into the shared
      relay's south wall, and leaves the relay's north wall two cells into the ADDER.
    * ``p1`` gets its capacity from a boustrophedon in the channel between the ADDER and
      the unit, rather than from depth 3's fold under the unit's south wall.
    """
    from .machine import MachineError, _Grid

    spec = _spec(4, lr_shift)
    unit = unit_interior(4, lr_shift=lr_shift)
    g = _Grid()
    iw, ih = spec.iw, spec.ih
    ux, uy = UX4, UY4
    east_x = ux + iw + 2  # first free column east of the unit's east wall
    row = {band: uy + r for band, r in {**unit.west, **unit.east}.items()}

    # ── the unit room ────────────────────────────────────────────────────────
    g.room(ux, uy, ux + iw + 1, uy + ih + 1)
    g.blit(ux, uy, unit.cells)

    # ── turn columns: falling as the east-wall row rises (rule 1) ────────────
    order = sorted(spec.east, key=lambda b: row[b])  # resp, a_fwd, out, p2, b_fwd, prod
    turn = {band: east_x + 2 * (len(order) - i) for i, band in enumerate(order)}
    turn["resp"] = east_x + 1  # climbs north, so it hugs the block instead

    # ── the west channel: ring A's relay, the I room, the ADDER, the relay ───
    rax, ray = RELAY_A4
    g.room(rax, ray, rax + RELAY_IW + 1, ray + RELAY_IH + 1)
    g.blit(rax, ray, relay_cells())
    iox, ioy = IO_IN4
    g.room(iox, ioy, iox + 2, ioy + 2)
    g.put(iox + 1, ioy + 1, "I")
    adx, ady = ADDER4
    g.room(adx, ady, adx + ADDER_IW + 1, ady + ADDER_IH + 1)
    g.blit(adx, ady, adder_cells())
    rbx, rby = RELAY_B4
    g.room(rbx, rby, rbx + SHARED_RELAY_IW + 1, rby + SHARED_RELAY_IH + 1)
    g.blit(rbx, rby, shared_relay_cells())

    npipes = 0
    laid = 0  # cells this routine believes it drew, against the cells that exist

    def pipe(points: list[tuple[int, int]]) -> int:
        nonlocal npipes, laid
        npipes += 1
        n = g.draw_pipe(points)
        laid += n
        return n

    # ── the two jogs: rooms above their own wall row, jogging down to it ─────
    a_ret = pipe(
        [
            (rax + RELAY_IW + 2, ray + 1),
            (J4["a_ret"], ray + 1),
            (J4["a_ret"], row["a_ret"]),
            (ux - 1, row["a_ret"]),
        ]
    )
    pipe(
        [
            (iox + 3, ioy + 1),
            (J4["in"], ioy + 1),
            (J4["in"], row["in"]),
            (ux - 1, row["in"]),
        ]
    )

    # ── resp: the topmost east row, so its climb crosses nothing ─────────────
    resp_x = turn["resp"]
    npipes += 1
    laid += g.draw_pipe([(east_x, row["resp"]), (resp_x, row["resp"]), (resp_x, 1)])
    g.put(resp_x, 0, "^")  # the caller carries it north from here

    # ── the accumulator ring: p2 the long way, prod through the shared relay ─
    # `p2` must clear ring B's band before it turns west, or its westbound leg cuts
    # ring B's descent; `prod` must turn west *above* the band for the same reason
    # applied the other way round. That ordering is the whole reason `p2` is the leg
    # that goes round and `p1` the one that stays in the channel.
    band_b_last = BAND_B4 + rows_b - 1
    p2_row = band_b_last + 2
    band_a = p2_row + 2
    leg_e = turn["b_fwd"] - 1  # both bands stop west of ring B's own descent
    p2 = pipe(
        [
            (east_x, row["p2"]),
            (turn["p2"], row["p2"]),
            (turn["p2"], p2_row),
            (CLIMB4["p2"], p2_row),
            (CLIMB4["p2"], ady + 1),
            (adx - 1, ady + 1),
        ]
    )
    pipe(
        [
            (east_x, row["prod"]),
            (turn["prod"], row["prod"]),
            (turn["prod"], PROD_ROW4),
            (rbx + SHARED_RELAY_PORTS["pass_in"][1], PROD_ROW4),
            (rbx + SHARED_RELAY_PORTS["pass_in"][1], rby + SHARED_RELAY_IH + 2),
        ]
    )
    pass_col = rbx + SHARED_RELAY_PORTS["pass_out"][1]
    pipe([(pass_col, rby - 1), (pass_col, ady + ADDER_IH + 2)])

    # `p1` holds a whole row of C, so it takes its length from a boustrophedon in the
    # channel rather than from depth 3's fold under the unit.
    #
    # It leaves the ADDER's *east* wall, so ``SPEC.md``'s first parse rule applies with
    # teeth: a pipe starts with the arrowhead whose **backward** cell is on the source
    # room's border, so the first cell has to step straight east before it may turn.
    # Drawn with the turn on the attach cell itself the whole pipe silently fails to
    # parse — ``analyze`` reports one fewer and nothing else complains — which is
    # exactly ARCH.md §4.4's family, and is why the pipe count is asserted.
    #
    # Then: down column ``p1_desc``, and every leg stops at ``P1_LEG_W4`` two columns
    # east of it, so the descent and the leg ends are never adjacent without being
    # consecutive. The row count is odd, so the first leg and the last both run east
    # and the last one carries straight on into the unit's west wall.
    p1_top, p1_bot = P1_ROWS4
    p1_desc = adx + ADDER_IW + 3
    p1_east = ux - 3
    p1_pts: list[tuple[int, int]] = [(p1_desc - 1, ady + 2), (p1_desc, ady + 2)]
    if p1_bot != ady + 2:
        p1_pts.append((p1_desc, p1_bot))
    for i, y in enumerate(range(p1_bot, p1_top - 1, -1)):
        p1_pts.append((p1_east if i % 2 == 0 else P1_LEG_W4, y))
        if y > p1_top:
            p1_pts.append((p1_pts[-1][0], y - 1))
    p1_pts.append((ux - 1, p1_top))
    p1 = pipe(p1_pts)

    # ── the long rings: east wall -> band -> far-west climb -> its relay ─────
    b_fwd = pipe(
        [
            (east_x, row["b_fwd"]),
            (turn["b_fwd"], row["b_fwd"]),
            (turn["b_fwd"], BAND_B4),
            *_serpentine(BAND_B4, rows_b, CLIMB4["b"], leg_w=LEG_W4, leg_e=leg_e),
            (CLIMB4["b"], row["b_ret"]),
            (rbx - 1, row["b_ret"]),
        ]
    )
    b_ret = pipe([(rbx + SHARED_RELAY_IW + 2, row["b_ret"]), (ux - 1, row["b_ret"])])
    a_fwd = pipe(
        [
            (east_x, row["a_fwd"]),
            (turn["a_fwd"], row["a_fwd"]),
            (turn["a_fwd"], band_a),
            *_serpentine(band_a, rows_a, CLIMB4["a"], leg_w=LEG_W4, leg_e=leg_e),
            (CLIMB4["a"], ray + 3),
            (rax - 1, ray + 3),
        ]
    )

    # ── the output room, in the east strip like depth 3 ──────────────────────
    ox, oy = turn["out"] - 1, row["out"] + 2
    g.room(ox, oy, ox + 2, oy + 2)
    g.put(ox + 1, oy + 1, "O")
    pipe([(east_x, row["out"]), (turn["out"], row["out"]), (turn["out"], oy - 1)])

    # Two pipes may not share a cell — and ``_Grid.put`` only catches it when the two
    # glyphs *differ*, so a horizontal leg crossing another horizontal leg (both ``-``)
    # is silent and produces a machine that routes values into the wrong room. Every
    # cell this routine laid must therefore be distinct.
    if len(g.drawn) != laid:
        raise MachineError(
            f"{laid} pipe cells drawn but only {len(g.drawn)} distinct: "
            f"{laid - len(g.drawn)} cells are shared by two pipes"
        )
    if min(p1, p2) < c_slots:
        raise MachineError(
            f"the accumulator ring holds {min(p1, p2)} values, {c_slots} needed; "
            "widen p1's boustrophedon (P1_ROWS4) or move the unit east (UX4)"
        )

    rows = g.rows()
    width = max(len(r) for r in rows)
    regions = {
        "unit": (ux, uy, iw + 2, ih + 2),
        "adder": (adx, ady, ADDER_IW + 2, ADDER_IH + 2),
        "relay:A": (rax, ray, RELAY_IW + 2, RELAY_IH + 2),
        "relay:B": (rbx, rby, SHARED_RELAY_IW + 2, SHARED_RELAY_IH + 2),
        "io:I": (iox, ioy, 3, 3),
        "io:O": (ox, oy, 3, 3),
        "ring:A": (CLIMB4["a"], band_a, leg_e, rows_a),
        "ring:B": (CLIMB4["b"], BAND_B4, leg_e, rows_b),
        "ring:C": (P1_LEG_W4, p1_top, p1_east - P1_LEG_W4, p1_bot - p1_top + 1),
    }
    return StreamBlock(
        cells=g.c,
        width=width,
        height=len(rows),
        cmd_cell=(ux + unit.north["cmd"], uy - 1),
        resp_cell=(resp_x, 0),
        ring_a=a_fwd + a_ret,
        ring_b=b_fwd + b_ret,
        ring_c=min(p1, p2),
        rows_a=rows_a,
        rows_b=rows_b,
        regions=regions,
        pipes=npipes,
        glyphs=[(ux + x, uy + y, gl, band) for x, y, gl, band in unit.glyphs],
        codes=unit.codes,
    )


def _place(
    a_slots: int, b_slots: int, c_slots: int, rows_a: int, rows_b: int, _lr_shift: int = UPDB_SHIFT
) -> StreamBlock:
    """The depth-3 block, exactly as ``matmul`` has shipped it. ``_lr_shift`` is
    accepted and ignored: a depth-3 trie has no ``UPDB`` leaf to shift with."""
    from .machine import MachineError, _Grid

    unit = unit_interior()
    g = _Grid()
    east_x = UX + UNIT_IW + 2  # first free column east of the unit's east wall
    band_a = BAND_A + max(0, rows_b - 5)  # A's band starts below B's

    # ── the unit room ────────────────────────────────────────────────────────
    g.room(UX, UY, UX + UNIT_IW + 1, UY + UNIT_IH + 1)
    g.blit(UX, UY, unit.cells)

    # ── the west stack: ring A's relay, the I room, ring B's relay ───────────
    for y in (RELAY_A_Y, RELAY_B_Y):
        g.room(ROOM_X, y, ROOM_X + RELAY_IW + 1, y + RELAY_IH + 1)
        g.blit(ROOM_X, y, relay_cells())
    g.room(ROOM_X, IO_IN_Y, ROOM_X + 2, IO_IN_Y + 2)
    g.put(ROOM_X + 1, IO_IN_Y + 1, "I")

    relay_east = ROOM_X + RELAY_IW + 2  # first cell east of a relay's east wall
    npipes = 0

    def pipe(points: list[tuple[int, int]]) -> int:
        nonlocal npipes
        npipes += 1
        return g.draw_pipe(points)

    # ring returns and the input, jogging so the three never cross
    a_ret = pipe(
        [
            (relay_east, RELAY_A_Y + 1),
            (W_JOG["a_ret"], RELAY_A_Y + 1),
            (W_JOG["a_ret"], UY + R_A_RET),
            (UX - 1, UY + R_A_RET),
        ]
    )
    pipe(
        [
            (ROOM_X + 3, IO_IN_Y + 1),
            (W_JOG["in"], IO_IN_Y + 1),
            (W_JOG["in"], UY + R_IN),
            (UX - 1, UY + R_IN),
        ]
    )
    b_ret = pipe(
        [
            (relay_east, RELAY_B_Y + 1),
            (W_JOG["b_ret"], RELAY_B_Y + 1),
            (W_JOG["b_ret"], UY + R_B_RET),
            (UX - 1, UY + R_B_RET),
        ]
    )

    # ── the response pipe: the topmost east row, so its climb crosses nothing ─
    resp_x = E_TURN["resp"]
    pipe_cells = [(east_x, UY + R_RESP), (resp_x, UY + R_RESP), (resp_x, 0)]
    npipes += 1
    resp_len = g.draw_pipe(pipe_cells[:-1] + [(resp_x, 1)])
    g.put(resp_x, 0, "^")  # the caller carries it north from here
    resp_len += 1

    # ── the long rings: east wall -> band serpentine -> far-west climb -> relay
    a_fwd = pipe(
        [
            (east_x, UY + R_A_FWD),
            (E_TURN["a_fwd"], UY + R_A_FWD),
            (E_TURN["a_fwd"], band_a),
            *_serpentine(band_a, rows_a, CLIMB["a"]),
            (CLIMB["a"], RELAY_A_Y + 3),
            (ROOM_X - 1, RELAY_A_Y + 3),
        ]
    )
    b_fwd = pipe(
        [
            (east_x, UY + R_B_FWD),
            (E_TURN["b_fwd"], UY + R_B_FWD),
            (E_TURN["b_fwd"], BAND_B),
            *_serpentine(BAND_B, rows_b, CLIMB["b"]),
            (CLIMB["b"], RELAY_B_Y + 3),
            (ROOM_X - 1, RELAY_B_Y + 3),
        ]
    )

    # ── the ADDER and the accumulator ring ───────────────────────────────────
    g.room(ADDER_X, ADDER_Y, ADDER_X + ADDER_IW + 1, ADDER_Y + ADDER_IH + 1)
    g.blit(ADDER_X, ADDER_Y, adder_cells())
    pipe(
        [
            (east_x, UY + R_PROD),
            (E_TURN["prod"], UY + R_PROD),
            (E_TURN["prod"], ADDER_Y - 1),
        ]
    )
    p2 = pipe(
        [
            (east_x, UY + R_P2),
            (E_TURN["p2"], UY + R_P2),
            (E_TURN["p2"], ADDER_Y - 1),
        ]
    )
    # P1 folds under the unit: it has to hold a whole row of C, and the direct run
    # from the ADDER to the south wall is far shorter than that.
    p1 = pipe(
        [
            (ADDER_X - 1, ADDER_Y + 2),
            (UX + 24, ADDER_Y + 2),
            (UX + 24, UY + UNIT_IH + 3),
            (UX + unit.south["p1"], UY + UNIT_IH + 3),
            (UX + unit.south["p1"], UY + UNIT_IH + 2),
        ]
    )

    # ── the output room ──────────────────────────────────────────────────────
    ox, oy = O_ROOM
    g.room(ox, oy, ox + 2, oy + 2)
    g.put(ox + 1, oy + 1, "O")
    pipe(
        [
            (east_x, UY + R_OUT),
            (E_TURN["out"], UY + R_OUT),
            (E_TURN["out"], oy - 1),
        ]
    )

    if E_TURN["out"] != ox + 1:
        raise MachineError("the output pipe must drop into the O room's own column")
    if min(p1, p2) < c_slots:
        # The accumulator ring is short and fixed, so this is a build-time error
        # rather than something the serpentine search can grow out of: both legs
        # have to hold a whole row of C or the ADDER blocks mid-row and the unit
        # blocks behind it, which on the real machine is a silent hang.
        raise MachineError(
            f"the accumulator ring holds {min(p1, p2)} values, {c_slots} needed; "
            "lengthen P1's fold or P2's descent"
        )

    rows = g.rows()
    width = max(len(r) for r in rows)
    regions = {
        "unit": (UX, UY, UNIT_IW + 2, UNIT_IH + 2),
        "adder": (ADDER_X, ADDER_Y, ADDER_IW + 2, ADDER_IH + 2),
        "relay:A": (ROOM_X, RELAY_A_Y, RELAY_IW + 2, RELAY_IH + 2),
        "relay:B": (ROOM_X, RELAY_B_Y, RELAY_IW + 2, RELAY_IH + 2),
        "io:I": (ROOM_X, IO_IN_Y, 3, 3),
        "io:O": (O_ROOM[0], O_ROOM[1], 3, 3),
        "ring:A": (CLIMB["a"], band_a, LEG_E, rows_a),
        "ring:B": (CLIMB["b"], BAND_B, LEG_E, rows_b),
    }
    return StreamBlock(
        cells=g.c,
        width=width,
        height=len(rows),
        cmd_cell=(UX + unit.north["cmd"], UY - 1),
        resp_cell=(resp_x, 0),
        ring_a=a_fwd + a_ret,
        ring_b=b_fwd + b_ret,
        ring_c=min(p1, p2),
        rows_a=rows_a,
        rows_b=rows_b,
        regions=regions,
        pipes=npipes,
        glyphs=[(UX + x, UY + y, gl, band) for x, y, gl, band in unit.glyphs],
        codes=unit.codes,
    )
