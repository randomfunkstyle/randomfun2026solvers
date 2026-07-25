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
2. **Incoming pipes attach where their readers are**: the ring returns and the
   input on the west wall (their readers are the western arms), ``cmd`` on the
   north wall beside ``MAIN``, and the accumulator's return on the *south* wall
   under the two eastern arms that read it. Rows are then chosen so each reader
   is strictly nearest its own — the tightest margin in the block is one cell
   (``MAC``'s ``r`` on ``B_ret`` against ``in``), which is why this is asserted
   rather than argued.

Arms are laid out west to east as trie leaves and their bodies run *down* their
own column, so "which row a glyph is on" is a free variable the layout uses to
satisfy rule 1 — padding a body with blanks moves a pipe glyph onto its row.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..circuit import Circuit

__all__ = [
    "ARMS",
    "StreamBlock",
    "UNIT_IH",
    "UNIT_IW",
    "arm_codes",
    "unit_interior",
]


class StreamError(RuntimeError):
    """The block's geometry did not close, with the constraint that failed."""


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


def arm_codes() -> dict[str, int]:
    """Command code per arm, derived from the trie's own geometry.

    ``x`` turns clockwise on BP's low bit; a man heading *south* turns clockwise
    to the **west**, so a west branch means that bit is 1. The code is therefore
    read off the path, not assigned — which is the same trick ``machine.plan``
    uses on the CPU's trie (there the leaves are rows, here they are columns).
    """
    codes: dict[int, int] = {}

    def walk(level: int, col: int, code: int) -> None:
        step = LEAF_PITCH * (1 << (TRIE_BITS - level)) // 2
        for sign, bit in ((-1, 1), (+1, 0)):
            nxt = col + sign * step
            acc = code | (bit * _bit_of(level))
            if level < TRIE_BITS:
                walk(level + 1, nxt, acc)
            else:
                codes[nxt] = acc

    walk(1, TRIE_COL, 0)
    leaves = sorted(codes)
    if len(leaves) != len(ARMS):
        raise StreamError(f"trie has {len(leaves)} leaves for {len(ARMS)} arms")
    return {arm: codes[col] for arm, col in zip(ARMS, leaves, strict=True)}


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


def unit_interior() -> Unit:
    """Lay the unit: MAIN, the decode trie, eight arms, the collector."""
    c = Circuit(UNIT_IW + 1, UNIT_IH + 1)
    glyphs: list[tuple[int, int, str, str]] = []
    cols = {arm: LEAF0 + LEAF_PITCH * i for i, arm in enumerate(ARMS)}

    # ── MAIN: the riser lands here, one command word, then the trie ──────────
    c.set(1, R_MAIN, ">")
    c.set(2, R_MAIN, "@")
    c.set(3, R_MAIN, "r")
    glyphs.append((3, R_MAIN, "r", "cmd"))
    c.set(4, R_MAIN, "b")
    c.horizontal(R_MAIN, 4, TRIE_COL)
    c.set(TRIE_COL, R_MAIN, "v")

    # ── decode trie, fanning *sideways*: leaves are columns, not rows ────────
    def trie(level: int, col: int) -> None:
        row = R_TRIE + level - 1
        step = LEAF_PITCH * (1 << (TRIE_BITS - level)) // 2
        c.set(col, row, "x")
        for sign in (-1, +1):
            for d in range(1, step + 1):
                ch = "v" if d == step else ("]" if d == 1 else " ")
                c.set(col + sign * d, row, ch)
            if level < TRIE_BITS:
                trie(level + 1, col + sign * step)

    trie(1, TRIE_COL)

    # ── arms ─────────────────────────────────────────────────────────────────
    for arm in ARMS:
        x = cols[arm]
        body = _BODIES[arm]
        if arm != "RDIN":
            # Every looping arm recovers its argument the same way: the command
            # word is still in A (the trie only touched BP), so `M 8 W /` divides
            # it by eight — floored, which is why a negative argument survives —
            # and `b` makes it the loop count.
            c.run(x, R_ARG, "M8W/b", d=(0, 1))
        if arm == "MAC":
            # The scalar is popped *before* the loop and stays in B for the whole
            # row, so ring A's return is the only pipe read outside a loop body.
            c.set(x, R_A_RET, "r")
            glyphs.append((x, R_A_RET, "r", "a_ret"))
            c.set(x, R_A_RET + 1, "M")
        if body is None:
            c.set(x, R_IN, "r")
            glyphs.append((x, R_IN, "r", "in"))
            c.set(x, R_RESP, "s")
            glyphs.append((x, R_RESP, "s", "resp"))
            c.vertical(x, R_ARG - 1, R_IN)
            c.vertical(x, R_IN, R_RESP)
            c.vertical(x, R_RESP, R_COLLECT)
            continue
        y0, text = body
        c.vertical(x, R_ARG + 4 if arm != "MAC" else R_A_RET + 1, y0)
        c.counted_loop(x, y0, text)
        for i, ch in enumerate(text):
            if ch in "rs":
                glyphs.append((x + 1, y0 + 1 + i, ch, _BAND_AT[(ch, y0 + 1 + i)]))
        c.set(x + 2, y0, "v")
        c.vertical(x + 2, y0, R_COLLECT)

    # ── collector: every arm arrives southbound and turns west ───────────────
    east_edge = max(cols[a] + (0 if _BODIES[a] is None else 2) for a in ARMS)
    for x in range(2, east_edge + 1):
        c.set(x, R_COLLECT, "<")
    c.set(1, R_COLLECT, "^")
    c.vertical(1, R_COLLECT, R_MAIN)

    cells = {k: v for k, v in c.cell.items() if v != " "}
    return Unit(
        cells=cells,
        west={"a_ret": R_A_RET, "in": R_IN, "b_ret": R_B_RET},
        east={
            "a_fwd": R_A_FWD,
            "b_fwd": R_B_FWD,
            "prod": R_PROD,
            "p2": R_P2,
            "out": R_OUT,
            "resp": R_RESP,
        },
        north={"cmd": 3},
        south={"p1": cols["EMIT"] + 1},
        glyphs=glyphs,
        codes=arm_codes(),
    )


#: Which band a pipe glyph on a given row belongs to. The row *is* the pipe
#: (module docstring, rule 1), so this table is the single place that mapping
#: lives and every arm body is checked against it.
_BAND_AT: dict[tuple[str, int], str] = {
    ("r", R_A_RET): "a_ret",
    ("r", R_IN): "in",
    ("r", R_B_RET): "b_ret",
    ("r", R_P1): "p1",
    ("s", R_A_FWD): "a_fwd",
    ("s", R_B_FWD): "b_fwd",
    ("s", R_PROD): "prod",
    ("s", R_P2): "p2",
    ("s", R_OUT): "out",
    ("s", R_RESP): "resp",
}


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


def build_stream(*, a_slots: int, b_slots: int, c_slots: int) -> StreamBlock:
    """Place the unit, its ADDER, both ring relays and the I/O rooms.

    ``a_slots``/``b_slots`` are the values each long ring must hold — ``N*M`` and
    ``M*K`` at the problem's maximum, plus one, since a ring is briefly holding one
    more value than it stores (the same +1 ``memory_tape`` needs). The serpentine
    grows a row at a time until the pipes are long enough; capacity *is* length
    (``SPEC.md``: a pipe is a FIFO whose capacity equals its cell count).
    """
    from .machine import MachineError

    # ``rows_a`` outer, because the block's height is set by ring A's band — it is
    # the lowest thing in the block, so ring B's extra rows are free until they
    # push past it (``band_a`` in :func:`_place`).
    for rows_a in range(1, 16, 2):
        for rows_b in range(1, 16, 2):
            try:
                blk = _place(a_slots, b_slots, c_slots, rows_a, rows_b)
            except MachineError:
                continue
            if blk.ring_a >= a_slots and blk.ring_b >= b_slots:
                return blk
    raise MachineError(f"no serpentine holds {a_slots} + {b_slots} values; widen the band")


def _place(
    a_slots: int, b_slots: int, c_slots: int, rows_a: int, rows_b: int
) -> StreamBlock:
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
        pipes=npipes,
        glyphs=[(UX + x, UY + y, gl, band) for x, y, gl, band in unit.glyphs],
        codes=unit.codes,
    )
