#!/usr/bin/env python3
"""A ``Y``-parallel dataflow ``matmul``: primitives, and the ADDER room.

Design: ``docs/superpowers/specs/2026-07-26-matmul-y-parallel-design.md``.

The baseline to beat is ``tasks/solutions/matmul-5818b2cc.man`` — 85x96 (9,216)
at ~38.4k judged ticks, score 354M. A profile of its 16x16x16 case puts 37.6% of
every tick in two grid rows: one man walking an 18-cell multiply-accumulate body
and then an 18-cell empty return, at 31.9 ticks per multiply-accumulate. Both
halves of that are addressable, and this module holds the two primitives that
address them.

## 1. ``men_on_a_cycle`` — why ``Y`` is the tick lever

A man moves **exactly one value per lap** of his cycle. Measured on the reference
wasm with a 54-cell cycle: 53.65 ticks per value. Put ``P`` men on the same cycle
and throughput becomes ``cycle / P``, linear to at least 8 men — 1/2/3/4/6/8 men
give 53.65 / 26.75 / 17.98 / 13.50 / 9.20 / 7.12 ticks per value.

**FIFO order survives this**, which is the whole reason it is usable: men on a
1-D cycle cannot overtake each other, so they pass every ``r`` cell and every
``s`` cell in the same fixed rotational order. The sequence of reads and the
sequence of writes are therefore the same permutation, whatever the blocking.

``seed_chain`` is the placement that gets ``P`` men onto one cycle. Each ``Y``'s
north child rides a riser up into the cycle, and its south child carries the
chain one row lower and one column east, so no riser ever crosses another
corridor. Every man joins upstream of the ``r``, so none can emit a value it
never read.

## 2. ``counted_cycle`` — the shape every counted loop takes here

A clockwise rectangle: west leg north, top row east, east leg south, bottom row
west. Two constraints fix where the ``d`` test goes, and between them they leave
exactly one legal layout.

* The test must come **after** all the pipe work in a lap. Otherwise the
  peel-off tick has already consumed a value it will never use — a wrong answer
  that looks like an arithmetic bug.
* A man arriving at the bottom-west corner heading west turns clockwise to north
  to stay in the cycle and goes straight on west to leave. So the *entry* cannot
  also arrive there: it would be turned east into the middle of the room.

Entry therefore joins one cell up the west leg, at a ``^`` that is a no-op for
the circulating man (already heading north) and a turn for the arriving one. That
single cell is what makes the shape close.

**A count of zero runs one lap.** The test is at the end, so ``BP = 0`` at entry
still performs a full body before peeling off. ``matmul`` guarantees
``2 <= N, M, K <= 16``, so the counts this machine builds — ``K``, ``(M-1)*K``
and ``N*M`` — are never zero, and the cheaper end-tested shape is safe. Anything
reusing this primitive with a possibly-zero count needs a guard.

## 3. Ports bind by row

All incoming pipes land on the west wall and all outgoing leave the east wall, so
the x-distance to a wall is identical for every rival and "nearest" collapses to
"on the nearest row" — a glyph binds its pipe wherever it sits horizontally.
That is what lets a cycle be narrow and still reach three different pipes.

Which row each pipe gets is *not* free, because the pipes must not cross. A pipe
approaching a wall from **below** climbs its own column and turns east at its
terminal row, so of two such pipes the one ending **higher** must climb the
**wester** column; approaching from **above** the rule inverts. In the ADDER,
MAIN sits below and feeds ``prod`` and ``cmd`` while ring C's relay sits above
and feeds ``cin`` — so ``cin`` has to be the topmost terminal, where its descent
crosses nobody's horizontal leg, and ``prod`` sits above ``cmd``.

One more rule, learned by tripping over it: a pipe's **first** cell must point
away from its source room, so a pipe leaving an east wall has to run at least one
cell east before it may turn. Turning on the first cell aims the arrowhead north
and its backward cell is then no longer the wall, which the loader rejects.

## 4. The ADDER

A fused multiply-accumulate needs three live values — the scalar ``a``, the
operand ``b``, the running sum ``c`` — and a man has only A and B, since BP
cannot be read. Splitting the add into its own room is what dissolves that:
MAIN's cycle is ``r(b) s(b) * s(prod)`` with ``a`` sitting untouched in B, and
the ADDER's is ``r(prod) M r(cin) + s(cout)``. Neither room ever holds three.

Ring C never passes through MAIN, so there is no forward pass — the accumulator
just recirculates. (The ``STREAM`` block pays a whole ``FWD K`` command per
``MAC K`` precisely because its unit sits inside ring C.) Three counted phases
per row of C, each preceded by one count word off ``cmd``:

    r(cmd) b ;  K x       { r(prod)              s(cout) }   # t=0: seed ring C
    r(cmd) b ;  (M-1)*K x { r(prod) M r(cin)  +  s(cout) }   # t=1..M-1
    r(cmd) b ;  K x       { r(cin)               s(out)  }   # emit one row of C

Seeding on the t=0 pass is what removes a zeroing phase, and emitting from here
is what keeps MAIN off ring C. Three sends per row of C, at most 48 per round.
"""

from __future__ import annotations

from .circuit import N, S, Circuit, Collision
from .lm1.machine import MachineError, _Grid

__all__ = [
    "ADDER_IH",
    "ADDER_IW",
    "A_CIN",
    "A_CMD",
    "A_COUT",
    "A_OUT",
    "A_PROD",
    "MAIN_ROWS",
    "SENTINEL",
    "Serpentine",
    "adder_cells",
    "build_adder_probe",
    "counted_cycle",
    "seed_chain",
]


# ── the ADDER's wall rows (interior, 1-based) ────────────────────────────────
A_CIN, A_PROD, A_CMD = 1, 2, 6  #: west wall: ring C's return, products, counts
A_COUT, A_OUT = 4, 5  #: east wall: ring C's forward, the output room
A_SPINE, A_MID, A_BACK = 9, 8, 7  #: corridors: start/return, p1->p2, p2->p3
ADDER_IW, ADDER_IH = 27, 9

#: Where each phase's cycle and its `r(cmd) b` riser sit. The riser is the column
#: immediately west of its cycle, and a phase's cycle spans the rows its pipes
#: need: seed touches prod/cout, accumulate also touches cin (so it reaches up to
#: row 1), emit touches cin/out.
_PHASES = (
    ("seed", 2, (3, 7), A_PROD, A_COUT),
    ("accumulate", 11, (12, 16), A_CIN, A_COUT),
    ("emit", 20, (21, 25), A_CIN, A_OUT),
)


def _writer(cells: dict[tuple[int, int], str], what: str):
    """A ``put`` that treats a blank as compatible with anything.

    Corridors and cycles deliberately share cells — a `^` on a cycle's west leg
    is a no-op for the man already heading north and a turn for the man arriving
    from the west — so blanks must merge silently while real collisions raise.
    """

    def put(x: int, y: int, ch: str) -> None:
        old = cells.get((x, y))
        if old is not None and old != ch and old != " " and ch != " ":
            raise MachineError(f"{what} collision at {(x, y)}: {old!r} vs {ch!r}")
        if old in (None, " ") or ch != " ":
            cells[(x, y)] = ch

    return put


def counted_cycle(
    put,
    cols: tuple[int, int],
    top: int,
    bottom: int,
    body: dict[object, str],
) -> None:
    """A clockwise rectangle with the `d` test at its bottom-west corner.

    ``body`` maps cells to glyphs, keyed by column (meaning "on the top row") or
    by an explicit ``(x, y)``. Unnamed cells are blanks the man walks through
    keeping his heading. See the module docstring for why the test and the entry
    sit where they do — and note the count-of-zero caveat.
    """
    x0, x1 = cols
    if x1 - x0 < 2 or bottom - top < 1:
        raise MachineError(f"cycle {cols} rows {top}..{bottom} is too small to close")
    put(x0, bottom, "d")  # west -> north stays in; straight on west leaves
    put(x0, bottom - 1, "^")  # entry: no-op northbound, a turn for the arrival
    for y in range(top + 1, bottom - 1):
        put(x0, y, " ")
    put(x0, top, ">")
    put(x1, top, "v")
    put(x1, bottom, "<")
    for x in range(x0 + 1, x1):
        put(x, top, " ")
        put(x, bottom, " ")
    for y in range(top + 1, bottom):
        put(x1, y, " ")
    for key, ch in body.items():
        x, y = key if isinstance(key, tuple) else (key, top)
        put(x, y, ch)


def adder_cells() -> dict[tuple[int, int], str]:
    """The ADDER's interior: three counted phases sharing five wall rows."""
    cells: dict[tuple[int, int], str] = {}
    put = _writer(cells, "adder")

    # seed — r(prod) on the top row, s(cout) on the way back west
    counted_cycle(put, (3, 7), A_PROD, A_COUT,
                  {4: "r", (5, A_COUT): "s", (4, A_COUT): "m"})
    # accumulate — the prod read sits on the *west leg*, at its own row, so `M`
    # and the cin read land on the top row in the order the arithmetic needs:
    # A=prod, B=prod, A=cin, A=cin+prod.
    counted_cycle(put, (12, 16), A_CIN, A_COUT,
                  {(12, A_PROD): "r", 13: "M", 14: "r",
                   (15, A_COUT): "+", (14, A_COUT): "s", (13, A_COUT): "m"})
    # emit — one row of C straight to the output room
    counted_cycle(put, (21, 25), A_CIN, A_OUT,
                  {22: "r", (23, A_OUT): "s", (22, A_OUT): "m"})

    # ── the spine, and one `r(cmd) b` riser per phase ─────────────────────────
    put(1, A_SPINE, "@")
    corridors = (A_SPINE, A_MID, A_BACK)
    for (_, riser, cols, top, bottom), corridor in zip(_PHASES, corridors, strict=True):
        entry = bottom - 1
        put(riser, corridor, "^")
        for y in range(A_CMD + 1, corridor):
            put(riser, y, " ")
        put(riser, A_CMD, "r")
        put(riser, A_CMD - 1, "b")
        for y in range(entry + 1, A_CMD - 1):
            put(riser, y, " ")
        put(riser, entry, ">")
        _ = cols, top

    # Each phase peels off westward along its own `d` row, drops down a column of
    # its own and runs east to the next phase's riser. The drop columns descend
    # west-to-east so the three corridors nest instead of crossing.
    for (_, riser, cols, _top, bottom), corridor, nxt in (
        (_PHASES[0], A_MID, _PHASES[1][1]),
        (_PHASES[1], A_BACK, _PHASES[2][1]),
    ):
        drop = riser - 1
        for x in range(drop + 1, cols[0]):
            put(x, bottom, " ")
        put(drop, bottom, "v")
        for y in range(bottom + 1, corridor):
            put(drop, y, " ")
        put(drop, corridor, ">")
        for x in range(drop + 1, nxt + 1):
            put(x, corridor, " ")

    # emit peels off west, drops east of the accumulate riser and returns along
    # the spine to phase one — the round loop, with no jump and no ROM.
    _, riser, cols, _, bottom = _PHASES[2]
    drop = riser - 2
    for x in range(drop + 1, cols[0]):
        put(x, bottom, " ")
    put(drop, bottom, "v")
    for y in range(bottom + 1, A_SPINE):
        put(drop, y, " ")
    put(drop, A_SPINE, "<")
    for x in range(2, drop):
        put(x, A_SPINE, " ")
    put(2, A_SPINE, "^")
    return cells


# ── the gauge: a loop count that costs one glyph and no registers ────────────
# `q` sets BP to the number of values in the nearest incoming pipe. Point a pipe
# into a room and never read it, and it fills to exactly what was put in and stays
# there — so `q` is a *repeatable* read of a constant, and unlike `b` it touches
# neither A nor B.
#
# That is what makes the parallel worker possible. Its cycle is
# `r(b) s(b) * s(prod)` with the scalar sitting in B for the whole inner loop, so
# there is no register left to load a count from: `b` would clobber A and the
# scalar is in B. `q` reads K/P off a gauge without disturbing either.
#
# Measured on the reference wasm (`test_a_gauge_reports_its_own_occupancy`):
# feeding 1/2/3/5 values into a 5-cell gauge reports 1/2/3/5, and feeding 7
# reports 5 — it saturates at the pipe's capacity, which is also its length
# (SPEC.md). So a gauge for a value up to V needs V cells, and matmul's counts are
# all <= 16, which is why this is cheap here and would not be for N*M.
GAUGE_SETTLE = 28  #: nop cells walked before `q`, so the pipe has settled first


def gauge_probe(capacity: int = 5) -> str:
    """A grid whose output length *is* what `q` saw, for pinning the semantics."""
    room_t, room_b, room_r = 8, 15, 38
    w, h = room_r + 8, room_b + 1
    g = [[" "] * w for _ in range(h)]

    def put(x: int, y: int, ch: str) -> None:
        g[y][x] = ch

    def txt(x: int, y: int, text: str) -> None:
        for i, ch in enumerate(text):
            g[y][x + i] = ch

    txt(2, 0, "+-+"), txt(2, 1, "|I|"), txt(2, 2, "+-+")
    for y in range(3, 3 + capacity):
        put(3, y, "v")
    txt(0, room_t, "+" + "-" * (room_r - 1) + "+")
    for y in range(room_t + 1, room_b):
        put(0, y, "|"), put(room_r, y, "|")
    txt(0, room_b, "+" + "-" * (room_r - 1) + "+")

    put(1, 9, "@")
    put(GAUGE_SETTLE + 1, 9, "q")
    x, y = GAUGE_SETTLE + 2, 9
    put(x, y, ">"), put(x + 1, y, "d")            # counted loop: emit `1` per unit
    put(x + 1, y + 1, "1"), put(x + 1, y + 2, "s")
    put(x + 1, y + 3, "<"), put(x, y + 3, "^")
    put(x, y + 1, "m"), put(x, y + 2, " ")
    put(x + 2, y, "H")
    put(room_r + 1, y + 2, ">"), put(room_r + 2, y + 2, ">")
    txt(room_r + 3, y + 1, "+-+"), txt(room_r + 3, y + 2, "|O|"), txt(room_r + 3, y + 3, "+-+")
    return "\n".join("".join(r).rstrip() for r in g) + "\n"


# ── seeding P men onto one cycle ─────────────────────────────────────────────
def seed_chain(put, start: tuple[int, int], men: int, join_row: int, pitch: int = 1) -> None:
    """Place ``men`` runners onto the cycle whose south row is ``join_row``.

    A descending chain of ``Y`` splits: each split's north child rides a riser up
    to ``join_row``, and its south child carries the chain two rows lower and
    ``pitch`` columns east. Risers therefore only ever occupy rows *above* the
    chain row that spawned them, at columns *west* of every later chain row, so
    none of them can cross. ``men - 1`` splits plus the chain's own last man make
    ``men``.
    """
    if men < 1:
        raise MachineError(f"{men} men is not a machine")
    x0, y0 = start
    put(x0, y0, "@")
    col, row = x0, y0
    for k in range(men - 1):
        col, row = x0 + 2 + pitch * k, y0 + 2 * k
        put(col, row, "Y")
        for y in range(join_row + 1, row):
            put(col, y, " ")
        put(col, join_row, "<")
        put(col, row + 2, ">")
    last = col + (pitch if men > 1 else 3)
    put(last, row + (2 if men > 1 else 0), "^")
    for y in range(join_row + 1, row + 2):
        put(last, y, " ")
    put(last, join_row, "<")


# ── the parallel worker ──────────────────────────────────────────────────────
# One of P independent workers. Each owns a contiguous slice of C's columns, so
# nothing is shared with its peers and there is no FIFO-ordering hazard between
# rooms — the reason this beats P men on one cycle, which needs the count split
# and every man holding the same scalar.
#
# Per (i, t) it does K/P multiply-accumulates:
#
#     r(a_in)  M  q(gauge)   then  K/P x { r(b_ret) s(b_fwd) * s(prod) }
#
# `M` parks the scalar in B, where it survives the whole inner loop; `q` reads
# K/P off the gauge without touching A or B (which `b` could not do); and the
# product leaves through `prod` so the room never holds three live values. The
# accumulate is the ADDER's job.
#
# The row order is what keeps the loop short: `counted_loop` walks a body down a
# column one glyph per row, so `rs*s` lands r on b_ret, s on b_fwd, the `*` on a
# spare row and s on prod, in exactly that order — and the order is forced,
# because `b` has to go back into ring B before `*` overwrites it.
W_A_IN, W_B_RET, W_GAUGE = 2, 3, 7   #: west wall
W_B_FWD, W_PROD = 4, 6               #: east wall; row 5 is the spare for `*`
WORKER_IW, WORKER_IH = 8, 8


def worker_cells() -> dict[tuple[int, int], str]:
    """A worker's interior: fetch the scalar, read the count, run the MAC loop."""
    c = Circuit(WORKER_IW + 2, WORKER_IH + 2)
    c.set(1, W_A_IN, "@")
    c.set(2, W_A_IN, "r")          # the scalar, from the previous worker or CTRL
    c.set(3, W_A_IN, "M")          # ... parked in B for the whole inner loop
    c.set(4, W_A_IN, "v")
    for y in range(W_A_IN + 1, W_GAUGE):
        c.set(4, y, " ")
    c.set(4, W_GAUGE, "q")         # BP = K/P, touching neither A nor B
    c.set(4, W_GAUGE + 1, ">")
    c.set(5, W_GAUGE + 1, "^")
    for y in range(W_A_IN + 1, W_GAUGE + 1):
        c.set(5, y, " ")
    c.set(5, W_A_IN, ">")
    ex, _ = c.counted_loop(6, W_A_IN, "rs*s")
    c.set(ex, W_A_IN, "^")         # exit, then back over the top to the fetch
    c.set(ex, W_A_IN - 1, "<")
    for x in range(3, ex):
        c.set(x, W_A_IN - 1, " ")
    c.set(2, W_A_IN - 1, "v")
    return {k: v for k, v in c.cell.items()}


# ── the ADDER's standalone probe ─────────────────────────────────────────────
M_IN, M_PROD, M_CMD = 2, 3, 5
MAIN_IW, MAIN_IH = 16, 5

MAIN_AT, ADDER_AT, RELAY_AT = (6, 22), (30, 7), (46, 1)
I_AT, O_AT = (0, 23), (64, 11)
#: prod's terminal is the higher of the two, so it climbs the wester column.
PROD_CLIMB, CMD_CLIMB = 26, 27
#: `cout` turns east of its own wall (see the module docstring's first-cell rule);
#: `cin` drops west of the ADDER's west wall, or it would cross the room.
COUT_TURN, CIN_DROP = 61, 28


def _stub_driver_cells() -> dict[tuple[int, int], str]:
    """Three counts into ``cmd``, then every later value into ``prod``, forever.

    Unrolled on purpose: a counted loop would need its ``b`` to fire once and its
    literal to be harmless on every lap, which for three sends is more machinery
    than laying them out flat.
    """
    cells: dict[tuple[int, int], str] = {}
    put = _writer(cells, "driver")
    put(1, M_IN, "@")
    for zig in range(3):
        x = 2 + 4 * zig
        put(x, M_IN, "r")
        put(x + 1, M_IN, "v")
        put(x + 1, M_CMD, ">")
        put(x + 2, M_CMD, "s")
        put(x + 3, M_CMD, "^")
        put(x + 3, M_IN, ">")
        for y in range(M_IN + 1, M_CMD):
            put(x + 1, y, " ")
            put(x + 3, y, " ")
    put(14, M_IN, "r")
    put(15, M_IN, "v")
    put(15, M_PROD, "<")
    put(14, M_PROD, "s")
    put(13, M_PROD, "^")
    return cells


def build_adder_probe() -> tuple[str, dict[str, int]]:
    """The ADDER, ring C, a relay and a stub driver: the grid and its capacities.

    The driver turns one input stream into the ADDER's whole protocol, so a case
    is just ``[K, (M-1)*K, K, *products]`` and the expected output is the
    column-wise sums.
    """
    g = _Grid()
    mx, my = MAIN_AT
    ax, ay = ADDER_AT
    rx, ry = RELAY_AT

    g.room(mx, my, mx + MAIN_IW + 1, my + MAIN_IH + 1)
    g.blit(mx, my, _stub_driver_cells())
    g.room(ax, ay, ax + ADDER_IW + 1, ay + ADDER_IH + 1)
    g.blit(ax, ay, adder_cells())
    g.room(rx, ry, rx + 4, ry + 4)
    g.blit(rx, ry, {(1, 1): "@", (2, 1): ">", (3, 1): "v",
                    (2, 2): "s", (3, 2): "r", (2, 3): "^", (3, 3): "<"})
    for corner, label in ((I_AT, "I"), (O_AT, "O")):
        g.room(corner[0], corner[1], corner[0] + 2, corner[1] + 2)
        g.put(corner[0] + 1, corner[1] + 1, label)

    caps = {
        "in": g.draw_pipe([(I_AT[0] + 3, I_AT[1] + 1), (mx - 1, my + M_IN)]),
        "prod": g.draw_pipe([(mx + MAIN_IW + 2, my + M_PROD), (PROD_CLIMB, my + M_PROD),
                             (PROD_CLIMB, ay + A_PROD), (ax - 1, ay + A_PROD)]),
        "cmd": g.draw_pipe([(mx + MAIN_IW + 2, my + M_CMD), (CMD_CLIMB, my + M_CMD),
                            (CMD_CLIMB, ay + A_CMD), (ax - 1, ay + A_CMD)]),
        "cout": g.draw_pipe([(ax + ADDER_IW + 2, ay + A_COUT), (COUT_TURN, ay + A_COUT),
                             (COUT_TURN, ry + 2), (rx + 5, ry + 2)]),
        "cin": g.draw_pipe([(rx - 1, ry + 2), (CIN_DROP, ry + 2),
                            (CIN_DROP, ay + A_CIN), (ax - 1, ay + A_CIN)]),
        "out": g.draw_pipe([(ax + ADDER_IW + 2, ay + A_OUT), (O_AT[0] - 1, O_AT[1] + 1)]),
    }
    return "\n".join(g.rows()) + "\n", caps


def adder_case(k: int, m: int, products: list[list[int]]) -> tuple[list[int], list[int]]:
    """``(input, expected)`` for one row of C: ``m`` passes of ``k`` products."""
    if len(products) != m or any(len(row) != k for row in products):
        raise ValueError(f"expected {m} rows of {k} products")
    values = [k, (m - 1) * k, k, *(v for row in products for v in row)]
    return values, [sum(row[j] for row in products) for j in range(k)]


# ── MAIN ─────────────────────────────────────────────────────────────────────
# MAIN's row map is the whole trick to keeping it small. `circuit.counted_loop`
# lays a loop body *down a column*, one glyph per row, so a blank in the body is
# a no-op that moves the next glyph onto its pipe's row — the same idiom
# `stream.py` uses for its arms. Pick the row order to match the order each loop
# body needs its pipes in, and every loop comes out as short as its body:
#
#   fill A   `rs`     rows 3,4      r(in)   s(a_fwd)
#   fill B   `r  s`   rows 3..6     r(in)         s(b_fwd)
#   the MAC  `rs*s`   rows 5..8     r(b_ret) s(b_fwd) * s(prod)
#   drain B  `r`      row 5         r(b_ret)
#
# The MAC's order is forced — `b` has to go back into ring B *before* `*`
# overwrites it — so b_ret < b_fwd < prod with the `*` between them. Both fills
# read `in`, so `in` sits above all of them. Everything else is free.
MAIN_ROWS: dict[str, int] = {
    "in": 3,       # west
    "a_fwd": 4,    # east
    "b_ret": 5,    # west
    "b_fwd": 6,    # east
    "prod": 8,     # east
    "a_ret": 9,    # west
    "rk_ret": 10,  # west
    "rk_fwd": 11,  # east
    "rm_ret": 12,  # west
    "rm_fwd": 13,  # east
    "rn_ret": 14,  # west
    "rn_fwd": 15,  # east
    "cmd": 16,     # east
}
MAIN_TOP, MAIN_BOT = 2, 17  # the serpentine's turn rows; 1 and 18 stay corridors
MAIN_IH = 18

#: Ring A's end marker. Entries are -99..99, so any |value| > 99 separates — but
#: the test has to leave the scalar in B, where the MAC needs it, so it may only
#: touch A and BP. `b` copies A into BP and seven `]` shifts arithmetically:
#: 128>>7 == 1 while 99>>7 == 0 and -99>>7 == -1, so `d` turns for the marker and
#: for nothing else. 128 is also buildable in A alone (`2 M` then six `*`), which
#: avoids a backtick literal and the row/column pairing hazards that come with it.
SENTINEL = 128
_SENTINEL_BUILD = "2M******"
_SENTINEL_TEST = "b]]]]]]]"


class Serpentine:
    """Lay a straight-line op sequence as a vertical serpentine.

    The man walks one column from `top` to `bot` (or back), so every pipe row is
    reachable exactly once per pass. An op whose row lies behind the current
    heading starts a new pass one column east, which costs two cells and one
    column; an op with no row rides whatever cell comes next, which is what makes
    the arithmetic between pipe operations free.
    """

    def __init__(self, c: Circuit, x: int, y: int, top: int, bot: int, heading=S) -> None:
        self.c, self.x, self.y, self.top, self.bot, self.d = c, x, y, top, bot, heading

    def _step(self) -> tuple[int, int]:
        """The next cell in the current heading, turning east if the band ends.

        `top` and `bot` are reserved for turn glyphs, so an op may only land
        strictly between them. Letting an op sit *on* an edge leaves no cell in
        that column for the turn that follows it, and the turn then collides with
        the op — which is what this used to do.
        """
        nxt = self.y + self.d[1]
        if not self.top < nxt < self.bot:
            self._turn()
            nxt = self.y + self.d[1]
        return self.x, nxt

    def _turn(self) -> None:
        """Walk to the band edge, step one column east, reverse the heading."""
        edge = self.bot if self.d == S else self.top
        step = self.d[1]
        for fill in range(self.y + step, edge, step):
            self.c.set(self.x, fill, " ")
        self.c.set(self.x, edge, ">")
        self.d = N if self.d == S else S
        self.x += 1
        self.c.turn(self.x, edge, self.d)
        self.y = edge

    def op(self, glyph: str, row: int | None = None) -> None:
        if row is None:
            x, y = self._step()
            self.c.set(x, y, glyph)
            self.y = y
            return
        if not self.top < row < self.bot:
            raise Collision(
                f"row {row} is on a band edge ({self.top}/{self.bot}); those are "
                "reserved for turns, so no op may sit there"
            )
        if (row - self.y) * self.d[1] <= 0:
            self._turn()
        while self.y + self.d[1] != row:
            x, y = self._step()
            self.c.set(x, y, " ")
            self.y = y
        self.c.set(self.x, row, glyph)
        self.y = row

    def ops(self, glyphs: str) -> None:
        for ch in glyphs:
            self.op(ch)

    def park(self, row: int) -> tuple[int, int]:
        """Walk to one cell short of `row` and hand (x, row) to the next block.

        A `counted_loop` placed there owns the `>` on that cell, which turns the
        arriving man east into the loop; it hands him back two columns on, still
        on `row`.
        """
        if (row - self.y) * self.d[1] <= 0:
            self._turn()
        while self.y + self.d[1] != row:
            x, y = self._step()
            self.c.set(x, y, " ")
            self.y = y
        return self.x, row

    def resume(self, x: int, y: int, heading=S) -> None:
        """Pick the man up at (x, y) heading east and turn him vertical again."""
        self.c.turn(x, y, heading)
        self.x, self.y, self.d = x, y, heading


if __name__ == "__main__":  # pragma: no cover - a look at the grid
    grid, caps = build_adder_probe()
    print(grid)
    print("pipe capacities:", caps, "-> ring C holds", min(caps["cout"], caps["cin"]))
