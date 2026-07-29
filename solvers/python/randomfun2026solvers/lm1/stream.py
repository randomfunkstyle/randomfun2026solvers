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

The depth-4 *unit* is drawn and checked (:func:`unit_interior_grid` renders it
with all eleven pipes for ``analyze``/``route`` to answer about). The *block*
around it is not placed yet — see the note above :func:`build_stream`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..circuit import Circuit, E

__all__ = [
    "ARMS",
    "DUAL_RELAY_IH",
    "DUAL_RELAY_IW",
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
#: the shift it was written against with ``.equ STREAM_LR_SHIFT`` (``asm.py``) and
#: :func:`build_stream` refuses to draw a different one rather than silently doing
#: different arithmetic. See the task-4 report and ``mnist_cnn``'s module docstring.
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
UPDB_BANDS: tuple[str, ...] = ("p1", "p2", "a_ret", "a_fwd", "b_ret", "b_fwd")


def updb_body(shift: int = UPDB_SHIFT) -> str:
    """``UPDB``'s counted-loop body, walked *down* one column, one glyph per row.

    One iteration of ``b = ring_b.pop(); g = p1.pop(); p2.push(g);
    ring_b.push(b - ((a * g) >> shift))`` with two hands and no readable backpack::

        r   the accumulator : A = g, one gradient off P1
        s   the accumulator : g goes on to P2 — it circulates, it is not consumed
        M   B = g
        r   ring A's return : A = a, the scalar PUSHA left on ring A
        s   ring A's fill   : put it straight back, so the ring is unchanged
        *   A = a * g          (B is still g — the multiply does not touch it)
        M9W}}  A >>= shift     (see :func:`_shift_glyphs`; B ends holding 9)
        M   B = (a * g) >> shift
        r   ring B's return : A = b
        -   A = b - ((a * g) >> shift)
        s   ring B's fill   : the updated weight, back where it came from

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

    Which pipe each glyph talks to is :data:`UPDB_BANDS`, and it is forced by the
    row map rather than chosen: the accumulator is read first because ``resp`` has
    to be the topmost *outgoing* row and it sits below ``in``, which sits below the
    accumulator's own row.
    """
    return "rsMrs*" + _shift_glyphs(shift) + "Mr-s"


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
    p1 = arg_row + len(arg) + 1  # 12: the first row an arm's loop body can reach

    # ``UPDB``'s body, padded so its three reads and three writes land on their
    # rows. Two blanks after the accumulator's read: ``resp`` has to be the topmost
    # *outgoing* row (it is the one pipe that climbs north, so anything above it
    # would cross its climb), and ``resp`` sits below ``in``, which sits below the
    # accumulator — so ``p2`` cannot be the row immediately under ``p1``.
    updb = body[:1] + "  " + body[1:]
    rows = {
        "p1": p1,  # 12  west: partial sums back from the ADDER
        "in": p1 + 1,  # 13  west: the input room
        "resp": p1 + 2,  # 14  east: one word back to the CPU
        "p2": p1 + 3,  # 15  east: partial sums to the ADDER
        "out": p1 + 4,  # 16  east: the output room
        "a_ret": p1 + 5,  # 17  west: ring A's return
        "a_fwd": p1 + 6,  # 18  east: ring A's fill
        "b_ret": p1 + len(updb) - 3,  # 26  west: ring B's return
        "b_fwd": p1 + len(updb) - 1,  # 28  east: ring B's rotate-back
        "prod": p1 + len(updb) + 1,  # 30  east: products to the ADDER
    }
    if [p1 + i for i in [pipe_ops[0], *(o + 2 for o in pipe_ops[1:])]] != [
        rows[band] for band in UPDB_BANDS
    ]:
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
        "UPDB": _Arm(loop=(rows["p1"] - 1, updb)),
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
#     >>v       upper child joins at the extra `>` on the west
#     ^sr
#     ^<<
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
        "^<<",
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


def _serpentine(y0: int, rows: int, climb: int) -> list[tuple[int, int]]:
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
        pts.append(((climb if last else LEG_W) if i % 2 == 0 else LEG_E, y))
        if not last:
            pts.append((pts[-1][0], y + 1))
    return pts


# ── placement, depth 4: the unit is drawn, the block is not placed ───────────
# The depth-4 *unit* is drawn and all twenty-eight of its pipe glyphs are checked
# against the engine's own ``route`` (:func:`unit_interior_grid`). The block around
# it — the ADDER, the ring relays, the I/O rooms and the pipes between them — is not
# placed. The constraint that stops it is recorded here rather than in a report,
# because two rounds of work have narrowed it from "P1 cannot cross the block" to
# something much smaller, and the next attempt should start from the narrow version.
#
# **Three rules the depth-4 block does close**, and they are the reusable part:
#
# 1. Southbound pipes leaving the east wall must turn at columns that *fall* as
#    their row rises, or a lower pipe's eastward run crosses a higher one's descent.
#    With ``resp`` climbing north from the topmost row, that fixes the order
#    ``prod`` < ``b_fwd`` < ``a_fwd`` < ``out`` < ``p2``.
# 2. Each then runs west along a corridor row of its own, and if the corridor rows
#    *and* the columns they drop down at rise in the same order as the turn columns,
#    the five nest exactly: pipe *j*'s corridor is below pipe *i*'s, so it passes
#    *i*'s descent above where *i* turns west, and *i*'s drop column is west of *j*'s
#    corridor, so *j* never crosses it. **The destination depths are then free** —
#    the non-obvious consequence, and what lets the ADDER's room span the three
#    intermediate drop columns: those all terminate above it.
# 3. Each of the four west-wall rows is a corridor too — a pipe arriving on one runs
#    east along it — so every source turns north in a column of its own, ordered by
#    the wall row it feeds (the pipe climbing highest goes furthest west). A source
#    whose room sits directly beside its climb has a *one-cell* leg, and a one-cell
#    leg cannot cross anything: that is how the ring returns get past every drop.
#
# **P1's crossing is solved, by ARCH.md §2.1's own pattern.** ``SPEC.md`` forbids a
# pipe looping back to its own room, so a ring always needs two rooms and a
# turnaround room is something this block already knows how to place. The
# accumulator's return is *structurally* the topmost pipe on the west wall (``UPDB``
# reads it before both ring returns and a body's rows rise with its execution order)
# while the ADDER that feeds it hangs off the bottom of the east wall, so P1 has to
# cross the block diagonally — which a single pipe cannot, because it would have to
# be both the diagonal and the final east-running corridor. Splitting it at a relay
# **dedicated to P1** makes ``p1b`` a short straight run into the topmost west row
# and frees ``p1a`` to take any route, since it no longer has to land on a corridor
# row. Dedicated, not shared: §2.1 allows one room to turn around many rings but only
# while they are permanently full, and the accumulator ring drains by design
# (``ZEROC`` seeds P2, ``MAC`` drains it, ``EMIT`` and ``RDP`` drain P1), so a shared
# relay would block on the first empty ring. With one ``r`` and one ``s`` it never
# chooses, and is exactly as transparent as more pipe would have been.
#
# **What is still open is smaller, and is not P1.** It is the two rings' *bands*.
# Each band is entered from a drop column west of it (rule 2) and left toward its
# relay, and the two bands are at different depths by construction. With both sharing
# one leg span — which is what makes each ring's capacity maximal per row — the
# shallower ring's exit column is live at the deeper ring's entry row, or the deeper
# ring's entry jog crosses the shallower one's exit, depending on which way round the
# bands and relays are ordered. Six orderings of (band depth, relay depth, jog
# column) were tried; each closes one direction and breaks the other.
#
# The fix is local and is a *capacity trade*, which is why it is a decision and not a
# derivation: give the two bands **disjoint leg spans** (halving each ring's cells per
# row, so ring B needs roughly twice the rows for ``mnist``'s 856 values), or give
# each ring its own serpentine *column* band so the two never share a row at all.
# Both change the block's footprint, so it belongs with Task 7's placement pass.
#
# Until then :func:`build_stream` refuses depth 4, for the same reason it refuses
# depth 5: a block it cannot draw correctly must not be approximated.


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
    outcome a builder is allowed to prevent by refusing to build. The depth-4
    *unit* is drawn and checked (:func:`unit_interior`, :func:`unit_interior_grid`);
    what is not drawn is the block around it, for the reason recorded above
    :func:`build_stream`.

    ``lr_shift`` is ``UPDB``'s shift, drawn into the arm's glyphs. It has to match
    the ``.equ STREAM_LR_SHIFT`` the program declares, because a unit built with a
    different one is wrong arithmetic with nothing to catch it.
    """
    from .machine import MachineError

    spec = _spec(trie_bits, lr_shift)  # refuses a width this module cannot draw
    if spec.trie_bits != TRIE_BITS:
        raise StreamError(
            f"the depth-{spec.trie_bits} unit is drawn ({len(spec.arms)} arms, "
            f"{unit_pipe_count(spec.trie_bits)} pipes, every glyph checked against the "
            "engine's own route) but the block around it is not placed yet. P1's own "
            "crossing is solved — a turnaround relay dedicated to it splits the leg — "
            "and what is still open is that the two rings' bands cannot both be "
            "entered from the west and left toward their relays while sharing one leg "
            "span. See the note above build_stream: the fix is disjoint leg spans or a "
            "column band per ring, which is a capacity trade and so a placement "
            "decision for Task 7 rather than a derivation."
        )

    # ``rows_a`` outer, because the block's height is set by ring A's band — it is
    # the lowest thing in the block, so ring B's extra rows are free until they
    # push past it (``band_a`` in :func:`_place`).
    for rows_a in range(1, 16, 2):
        for rows_b in range(1, 16, 2):
            try:
                blk = _place(a_slots, b_slots, c_slots, rows_a, rows_b, lr_shift)
            except MachineError:
                continue
            if blk.ring_a >= a_slots and blk.ring_b >= b_slots:
                return blk
    raise MachineError(f"no serpentine holds {a_slots} + {b_slots} values; widen the band")


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
