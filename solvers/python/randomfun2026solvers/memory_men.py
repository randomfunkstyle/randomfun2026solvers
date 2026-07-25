#!/usr/bin/env python3
"""Memory whose storage is *little men*, not a rotating pipe tape.

The tape designs in this repo store ``n`` values as ``n`` values circulating in a
ring of pipes, so every access waits for its slot to come round: ``8n`` ticks for
a full lap (``memory_ast``), ``105 + 8.3n`` for the LM-1 STORE block. That cost is
algorithmic, not geometric — no layout fixes it.

This module stores each value in a **resident man's off hand**. One 6x6 room per
value, the man circling a 12-cell loop forever:

    +----+       command 0  = READ  -> `W s M`: value to A, send it, restore B
    |>rXv|       command 1  = WRITE -> `r W`  : take the next token into B
    |  rW|
    |^W<s|       B holds the cell's value, A is scratch. B starts at 0, which is
    |^@M<|       exactly the problem's "every cell starts at 0" — a man-memory
    +----+       needs **no initialisation pass** at all.

Access is then addressing, not waiting: 12 ticks in the cell whichever cell it is.
What replaces the rotation cost is the *walk* — a router man peeling off at lane
``addr`` — and that is a geometry problem, which layout search can attack.

Two register facts make the whole design work, and both are why it is shaped like
this rather than like a chain:

* **A storage cell cannot do arithmetic.** Its value sits in ``B`` and every
  arithmetic glyph reads ``B``, so a cell can never decrement a countdown and pass
  it on. A chain of storage cells that self-routes does not exist; routing has to
  be done by rooms whose hands are free.
* **A router needs no incoming pipe but its parent's.** Answers go *forward* to a
  collector, never back to the router, so every ``r`` in a router binds to the one
  pipe from its parent — including the ``r`` that fetches a WRITE's value at the
  far end of the lane. That is what lets the packet stay the problem's own
  ``0 addr`` / ``1 addr value`` and need no dummy tokens or fixed-length framing.

The wire protocol is therefore ``lm1.store``'s verbatim, so a block built here
drops into an LM-1 machine in place of ``tape_block``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .circuit import Circuit
from .man_debug import DebugMap

__all__ = [
    "CELL",
    "CELL_READ_TICKS",
    "CELL_WRITE_TICKS",
    "Line",
    "Tree",
    "build_line",
    "build_tree",
    "cell_at",
    "collector_rows",
    "main",
    "router_rows",
]

# ── overlay palette: one colour per kind of room, so a screenshot reads at a
# glance which band is routing and which is storage.
C_IO = "#e879f9"
C_HEAD = "#38bdf8"
C_MID = "#a78bfa"
C_LEAF = "#fb923c"
C_STORE = "#22c55e"
C_COLL = "#facc15"
C_CMD = "#60a5fa"
C_ANS = "#34d399"

#: The storage cell's interior, verified on the engine (see module docstring).
#:
#: ``X`` branches on the command word: 0 (READ) walks straight into column 3,
#: 1 (WRITE) turns clockwise into column 2. The lanes rejoin on the way back to
#: ``r``, and they must rejoin *late*: the READ lane's ``M`` restores ``B`` from
#: ``A``, and a WRITE arriving there would undo its own write, so the WRITE lane
#: cuts west one row early along row 2.
CELL: tuple[str, ...] = (
    ">rXv",
    "  rW",
    "^W<s",
    "^@M<",
)

#: Measured on the engine by differencing runs of k operations: the read loop is
#: 12 cells (``r X v W s M < < @ ^ ^ >``) and the write loop 8.
CELL_READ_TICKS = 12
CELL_WRITE_TICKS = 8

#: Cell geometry: interior 4x4, so 6x6 with walls. The command pipe lands on the
#: north wall above interior column 0 (nearest both ``r``s), the answer pipe
#: leaves the south wall below interior column 3 (under the ``s``).
CELL_W, CELL_H = 6, 6
CELL_IN_COL = 1  # offset from the room's west wall to the command port
CELL_OUT_COL = 4  # offset to the answer port

#: Lane pitch in the router room. Six columns is what a cell is wide, and keeping
#: the pitch equal to the cell pitch is what makes every command pipe two cells
#: long — which is also what keeps every answer's latency equal (see ``Line``).
PITCH = 6

#: Columns of router preamble west of lane 0: ``> r M r b W``.
PREAMBLE = 6


def cell_at(grid: Circuit, x: int, y: int) -> None:
    """Stamp one storage cell with its walls, north-west interior corner at (x,y)."""
    for i in range(-1, CELL_W - 1):
        grid.set(x + i, y - 1, "+" if i in (-1, CELL_W - 2) else "-")
        grid.set(x + i, y + CELL_H - 2, "+" if i in (-1, CELL_W - 2) else "-")
    for j in range(CELL_H - 2):
        grid.set(x - 1, y + j, "|")
        grid.set(x + CELL_W - 2, y + j, "|")
    for j, row in enumerate(CELL):
        for i, glyph in enumerate(row):
            if glyph != " ":
                grid.set(x + i, y + j, glyph)


@dataclass(frozen=True)
class Line:
    """One router with ``n`` cells hung off its south wall — the "long chain".

    The router man's cycle *is* the cost model. He reads ``op`` and ``addr`` from
    the input pipe, loads ``BP = addr``, then walks east along the trunk: at each
    lane ``d`` turns him clockwise while ``BP > 0`` (a 6-cell bypass that spends
    one ``m``), and lets him walk straight through when ``BP`` hits zero. So the
    walk out is 8 ticks per bypassed lane and the walk home 6, and the whole
    design lives or dies on how many lanes a request has to pass.

    Answers do not come back to the router. Each cell sends its value forward into
    a collector room whose man loops ``R``/``s`` into the output pipe, so the
    router never blocks on memory and the packet needs no dummy value token.

    **Why that is order-safe here.** ``R`` takes from whichever pipe is ready, so
    ordering is only preserved while answers cannot overtake each other. In this
    layout every command pipe is 2 cells and every answer pipe is 2 cells, the
    cell service time is a constant 12 ticks, and the router issues requests in
    order at least 18 ticks apart — more than a cell or the collector needs — so
    no two answers are ever in flight at once. A layout with unequal pipe lengths
    loses that guarantee and needs the router to collect the answers itself.
    """

    n: int
    rows: tuple[str, ...]
    width: int
    height: int
    #: The sidecar the generator emits alongside the grid (see ``DEBUGGING.md``):
    #: the grid carries no comments, so this is the only record of what a cell is.
    debug: DebugMap | None = field(default=None, compare=False, repr=False)

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def source(self) -> str:
        return "\n".join(self.rows)


def build_line(n: int) -> Line:
    """Build a complete standalone ``memory`` program with ``n`` man-cells.

    Reads the problem's own stream (``0 addr`` / ``1 addr value``) on the input
    pipe and emits one word per READ, so the result is verifiable directly
    against ``tasks/problems/memory.json`` for any ``n``.
    """
    if n < 1:
        raise ValueError("a memory needs at least one cell")

    # ── router room ──────────────────────────────────────────────────────────
    # interior rows: 0 trunk, 1 bypass, 2 lane send, 3 lane branch, 4 return
    iw = PREAMBLE + PITCH * n
    ih = 5
    rx, ry = 6, 1  # interior north-west corner in grid coordinates
    grid = Circuit(rx + iw + 12, ry + ih + 4 + CELL_H + 4 + 6)

    def put(x: int, y: int, glyph: str) -> None:
        grid.set(rx + x, ry + y, glyph)

    # preamble: A=op -> B=op, A=addr -> BP=addr, then A=op again for the lanes
    for i, glyph in enumerate(">rMrbW"):
        put(i, 0, glyph)
    put(0, 4, "^")  # the return path climbs back into the preamble
    # Spawn on the return row: the man walks east into lane 0's `<`, is turned
    # round, and enters the preamble the same way every later request does.
    put(1, 4, "@")

    for j in range(n):
        x = PREAMBLE + PITCH * j
        # trunk: peel straight ahead when BP == 0, bypass clockwise while BP > 0
        put(x, 0, "d")
        put(x + 1, 0, "v")
        put(x + PITCH - 1, 0, ">")
        # bypass: one decrement per lane skipped, then climb back to the trunk
        put(x, 1, ">")
        put(x + 2, 1, "m")
        put(x + PITCH - 1, 1, "^")
        # lane: send the op word to this cell, then branch on it
        put(x + 1, 2, "s")
        put(x + 1, 3, "X")
        # WRITE turns clockwise (west): take the value off the input pipe and
        # send it after the op. `r` is unambiguous — the router's only incoming
        # pipe is the input one.
        put(x, 3, "r")
        put(x - 1, 3, "s")
        put(x - 2, 3, "v")
        # READ walks straight through into the return row; WRITE merges into it.
        put(x + 1, 4, "<")
        put(x - 2, 4, "<")

    for i in range(-1, iw + 1):
        grid.set(rx + i, ry - 1, "+" if i in (-1, iw) else "-")
        grid.set(rx + i, ry + ih, "+" if i in (-1, iw) else "-")
    for j in range(ih):
        grid.set(rx - 1, ry + j, "|")
        grid.set(rx + iw, ry + j, "|")

    # ── input room, feeding the router's west wall on the preamble row ────────
    iy = ry
    for j, row in enumerate(("+-+", "|I|", "+-+")):
        for i, glyph in enumerate(row):
            grid.set(rx - 6 + i, iy - 1 + j, glyph)
    grid.set(rx - 3, iy, ">")
    grid.set(rx - 2, iy, ">")

    # ── cells, one per lane, two cells of pipe below the router ───────────────
    # two rows of pipe between the router's south wall and the cell's north wall:
    # a pipe's first cell must sit against the source room and its last must point
    # into the destination, so the minimum legal gap is 2.
    cy = ry + ih + 4
    for j in range(n):
        x = rx + PREAMBLE + PITCH * j + 1
        cell_at(grid, x, cy)
        grid.set(x, ry + ih + 1, "v")
        grid.set(x, ry + ih + 2, "v")

    # ── collector: `R` takes whichever answer is ready, `s` goes to output ────
    by = cy + CELL_H - 1 + 3
    bw = PREAMBLE + PITCH * n
    for j in range(n):
        x = rx + PREAMBLE + PITCH * j + 1 + CELL_OUT_COL - CELL_IN_COL
        grid.set(x, by - 3, "v")
        grid.set(x, by - 2, "v")
    for i, glyph in enumerate("@>Rv"):
        grid.set(rx + i, by + 0, glyph)
    for i, glyph in enumerate(" ^s<"):
        if glyph != " ":
            grid.set(rx + i, by + 1, glyph)
    for i in range(-1, bw + 1):
        grid.set(rx + i, by - 1, "+" if i in (-1, bw) else "-")
        grid.set(rx + i, by + 2, "+" if i in (-1, bw) else "-")
    for j in range(2):
        grid.set(rx - 1, by + j, "|")
        grid.set(rx + bw, by + j, "|")

    # output room, hung off the collector's west wall under the `s`
    grid.set(rx - 2, by + 1, "<")
    grid.set(rx - 3, by + 1, "<")
    for j, row in enumerate(("+-+", "|O|", "+-+")):
        for i, glyph in enumerate(row):
            grid.set(rx - 6 + i, by + j, glyph)

    # ── the sidecar: names for a grid that cannot carry comments ──────────────
    dbg = DebugMap(f"man-memory line n={n}")
    dbg.region(
        "input",
        rx - 6,
        iy - 1,
        3,
        3,
        note="the problem's own stream: `0 addr` (READ) / `1 addr value` (WRITE)",
        color=C_IO,
    )
    dbg.region(
        "router",
        rx,
        ry,
        iw,
        ih,
        note=(
            "preamble `>rMrbW`: B=op, BP=addr, A=op. Then one walk east: `d` bypasses "
            "a lane while BP>0 (8 ticks) and peels into it at BP==0, so lane j serves "
            "address j and one operation costs 22+14*addr ticks."
        ),
        color=C_MID,
    )
    dbg.lane(
        "input-pipe",
        [(rx - 3, iy), (rx - 1, iy)],
        kind="pipe",
        expect="op addr [value]",
        color=C_CMD,
    )
    for j in range(n):
        x = rx + PREAMBLE + PITCH * j + 1
        dbg.region(
            f"cell addr {j}",
            x - 1,
            cy - 1,
            CELL_W,
            CELL_H,
            note=(
                f"the resident man for address {j}: his B *is* the stored value (0 until "
                f"first WRITE, so no initialisation pass). READ 12 ticks, WRITE 8."
            ),
            color=C_STORE,
        )
        dbg.lane(
            f"cmd addr {j}",
            [(x, ry + ih + 1), (x, cy - 1)],
            kind="pipe",
            expect="op [value]",
            color=C_CMD,
        )
        ox = x + CELL_OUT_COL - CELL_IN_COL
        dbg.lane(
            f"answer addr {j}",
            [(ox, by - 3), (ox, by - 1)],
            kind="pipe",
            expect="the value, on READ only",
            color=C_ANS,
        )
    dbg.region(
        "collector",
        rx,
        by,
        bw,
        2,
        note=(
            "`R` takes whichever answer pipe is ready, `s` forwards it. Safe because the "
            "router issues requests >=18 ticks apart and every pipe here is 2 cells, so "
            "no two answers are ever in flight."
        ),
        color=C_COLL,
    )
    dbg.lane(
        "output-pipe",
        [(rx - 2, by + 1), (rx - 4, by + 1)],
        kind="pipe",
        expect="one word per READ",
        color=C_ANS,
    )
    dbg.region(
        "output",
        rx - 6,
        by - 1,
        3,
        3,
        note="one word per READ, in stream order",
        color=C_IO,
    )

    rows = [row.rstrip() for row in grid.rows()]
    while rows and not rows[-1]:
        rows.pop()
    width = max(len(row) for row in rows)
    return Line(n=n, rows=tuple(rows), width=width, height=len(rows), debug=dbg)


# ── the tree: one router per level, cells at the leaves ──────────────────────
#
# Lane pitch inside a router. Four columns is the minimum the lane code fits in
# (`X` plus the three-glyph WRITE tail west of it), and it is only achievable
# because a child's command pipe leaves the wall under the lane's **`d`**, not
# under its `s`: with the port a column further east the WRITE tail's `s` is
# equidistant from its own lane's pipe and its western neighbour's, ties break by
# reading order, and every WRITE lands in the wrong cell.
LANE_PITCH = 4


def router_rows(
    k: int,
    *,
    block: int | None = None,
    pitch: int = LANE_PITCH,
    merge_head: bool = False,
) -> tuple[list[str], list[int]]:
    """A lane-walking router: ``k`` children, one command pipe out per child.

    Returns the interior rows and the port column of each lane.

    ``block=None`` builds a **leaf** router, whose children are storage cells: it
    receives ``addr op [value]``, uses ``addr`` as the lane index and relays
    ``op [value]`` to that cell. Otherwise it is a **mid** router whose children
    each cover ``block`` addresses: one ``/`` splits the address into
    ``(child, local)`` — quotient to ``A``, remainder to ``B``, both in one glyph —
    and it relays ``local op [value]``.

    Every ``r`` here binds to the one pipe from the parent, because answers never
    come back to a router (they go forward to a collector). That is what lets the
    WRITE value be fetched at the far end of the lane, and it is the reason the
    packet needs no dummy tokens.
    """
    if k < 1:
        raise ValueError("a router needs at least one lane")
    if pitch < LANE_PITCH:
        raise ValueError(f"lane pitch must be at least {LANE_PITCH}")
    mid = block is not None
    if merge_head and mid:
        raise ValueError("a head-merged router addresses cells, so it has no block size")
    if merge_head:
        # The problem's own stream is `op addr [value]`, op first, while a router
        # deeper in a tree is fed `addr op [value]` by the head that reordered it.
        # Merging the head in means reading both here — B parks op across the walk,
        # `b` takes the address into BP, and `W` brings op back to A for the lane —
        # which also deletes the lane's `r`, and with it one whole row.
        pre = ">rMrbW"
    elif mid:
        lit = str(block) if block < 10 else f"`{block}`"
        pre = ">rM" + lit + "W/bW"
    else:
        pre = ">rb"
    x0 = len(pre)
    # lane rows: [s(local)] [r(op)] s(op) X ... return
    r_local = 2 if mid else None
    r_op = None if merge_head else (3 if mid else 2)
    r_send = 2 if merge_head else r_op + 1
    r_x = r_send + 1
    r_ret = r_x + 1
    ih = r_ret + 1
    iw = x0 + pitch * k
    rows = [[" "] * iw for _ in range(ih)]

    def put(x: int, y: int, glyph: str) -> None:
        if rows[y][x] not in (" ", glyph):
            raise ValueError(f"router collision at ({x},{y}): {rows[y][x]!r} vs {glyph!r}")
        rows[y][x] = glyph

    for i, glyph in enumerate(pre):
        put(i, 0, glyph)
    put(0, r_ret, "^")
    put(x0 - 1, r_ret, "@")

    ports: list[int] = []
    for j in range(k):
        x = x0 + pitch * j
        # The port sits under the peel dive, not under the lane's `s`: that is what
        # keeps the WRITE tail's `s` strictly nearer its own lane's pipe than its
        # western neighbour's, which a tie would resolve the wrong way.
        #
        # At the minimum pitch there is no slack for even that: the WRITE tail's `s`
        # sits two columns west of its lane, so a port at `x + 1` is *equidistant*
        # from this lane's pipe and the previous one's, the tie breaks by reading
        # order, and every WRITE lands one cell too far west. Pull the port onto the
        # `d` itself, which is strictly nearer for both sends at any pitch.
        ports.append(x + 1 if pitch >= 6 else x)
        put(x, 0, "d")  # BP > 0 -> bypass clockwise; BP == 0 -> peel straight on
        put(x + 1, 0, "v")
        put(x + pitch - 1, 0, ">")
        put(x, 1, ">")
        put(x + 2, 1, "m")
        put(x + pitch - 1, 1, "^")
        if r_local is not None:
            put(x + 1, r_local, "s")  # the child's local address
        if r_op is not None:
            put(x + 1, r_op, "r")  # op, off the parent pipe
        put(x + 1, r_send, "s")
        put(x + 1, r_x, "X")  # 0 = READ: straight on; 1 = WRITE: clockwise, west
        put(x, r_x, "r")  # WRITE: the value, still off the parent pipe
        put(x - 1, r_x, "s")
        put(x - 2, r_x, "v")
        put(x + 1, r_ret, "<")
        put(x - 2, r_ret, "<")
    return ["".join(row) for row in rows], ports


#: The head: the only room that talks to the problem's I/O. It turns the
#: stream's ``op addr [value]`` into the tree's ``addr op [value]`` and, on a
#: READ, waits for the answer before starting the next operation.
#:
#: The wait is not conservatism, it is what makes the answers ordered. Latency
#: down the tree depends on how far each router walks, so two READs in flight can
#: finish out of order and the collector, which takes whichever answer is ready,
#: would emit them swapped. Serialising here costs one round trip per READ and
#: needs no equal-length pipes anywhere.
HEAD_ROWS: tuple[str, ...] = (
    ">rMrsWsXrsv",
    " v sr  <@ v",
    "^<<<<<<<<<<",
)
#: interior port cells: (input, answer) in / (root, output) out
HEAD_IN = ((1, 0), (8, 2))
HEAD_OUT = ((5, 0), (10, 1))


def collector_rows(n_in: int) -> tuple[list[str], list[tuple[int, int]]]:
    """``R``/``s`` in a six-cell loop: forward whichever answer arrived.

    ``R`` takes from any incoming pipe, so a collector needs no pipe affinity at
    all — which is why the answer path can be a chain of these next to the cells
    they serve instead of ``n`` long pipes converging on one room.
    """
    iw = max(4, n_in)
    rows = [list("@>Rv".ljust(iw)), list(" ^s<".ljust(iw))]
    ports = [(i, 0) for i in range(n_in)]
    return ["".join(r) for r in rows], ports


#: Pipe glyphs by step direction. Every cell of a pipe drawn here carries an
#: arrowhead rather than a ``-``/``|`` body: straight-through arrowheads are legal
#: and it makes bends impossible to get wrong.
_ARROW = {(1, 0): ">", (-1, 0): "<", (0, 1): "v", (0, -1): "^"}


def draw_pipe(grid: Circuit, path: list[tuple[int, int]]) -> int:
    """Draw a pipe along ``path``; the last point is the destination *wall* cell.

    Returns the pipe's length in cells (its capacity).
    """
    for i in range(len(path) - 1):
        (x, y), (nx, ny) = path[i], path[i + 1]
        step = (nx - x, ny - y)
        if step not in _ARROW:
            raise ValueError(f"pipe step {step} from {path[i]} is not a unit move")
        grid.set(x, y, _ARROW[step])
    return len(path) - 1


def _corners(path: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Reduce a cell-by-cell pipe path to the vertices ``DebugMap.lane`` wants.

    The overlay is derived from the *same* list the pipe was drawn from, so an
    annotated pipe can never point somewhere the grid does not.
    """
    out = [path[0]]
    for prev, cur, nxt in zip(path, path[1:], path[2:], strict=False):
        if (cur[0] - prev[0], cur[1] - prev[1]) != (nxt[0] - cur[0], nxt[1] - cur[1]):
            out.append(cur)
    out.append(path[-1])
    return out


def _room(grid: Circuit, x: int, y: int, rows: list[str] | tuple[str, ...]) -> None:
    """Stamp a room's interior at (x,y) and draw its walls."""
    iw = max(len(r) for r in rows)
    ih = len(rows)
    for i in range(-1, iw + 1):
        grid.set(x + i, y - 1, "+" if i in (-1, iw) else "-")
        grid.set(x + i, y + ih, "+" if i in (-1, iw) else "-")
    for j in range(ih):
        grid.set(x - 1, y + j, "|")
        grid.set(x + iw, y + j, "|")
    for j, row in enumerate(rows):
        for i, glyph in enumerate(row):
            if glyph != " ":
                grid.set(x + i, y + j, glyph)


def _io_room(grid: Circuit, x: int, y: int, glyph: str) -> None:
    """A 3x3 I/O room whose interior cell is at (x,y)."""
    for j, row in enumerate(("+-+", f"|{glyph}|", "+-+")):
        for i, ch in enumerate(row):
            grid.set(x - 1 + i, y - 1 + j, ch)


@dataclass(frozen=True)
class Tree:
    """A two-level man-memory: one mid router over ``k1`` leaf routers.

    ``k1`` blocks of ``k2`` cells each, so ``n = k1 * k2`` addresses. The mid
    router divides the address once (``/`` gives quotient and remainder in one
    glyph) and relays ``local op [value]`` to the block that owns it; the leaf
    router walks to the cell. Two shorter walks replace one long one, which is
    the whole point: a line's walk is ``14 * addr`` ticks, a tree's is
    ``14 * (addr // k2) + 14 * (addr % k2)`` plus one pipe crossing.
    """

    k1: int
    k2: int
    rows: tuple[str, ...]
    width: int
    height: int
    #: Named rooms, addresses and pipes for the grid below (see ``DEBUGGING.md``).
    debug: DebugMap | None = field(default=None, compare=False, repr=False)

    @property
    def n(self) -> int:
        return self.k1 * self.k2

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def source(self) -> str:
        return "\n".join(self.rows)


def build_tree(k1: int, k2: int) -> Tree:
    """Build a complete standalone ``memory`` program as a two-level tree.

    ``k1`` blocks of ``k2`` cells. The layout is deliberately planar rather than
    packed: the mid router's command pipes fan out down a west corridor, one
    column each, and the collector chain runs down an east one, so no pipe ever
    crosses another and the whole thing can be checked by construction. Packing it
    tighter is a placement problem — the cost model this measures does not change.
    """
    if k1 < 1 or k2 < 1:
        raise ValueError("both fan-outs must be positive")
    leaf_rows, leaf_ports = router_rows(k2, pitch=PITCH)
    mid_rows, mid_ports = router_rows(k1, block=k2, pitch=PITCH)
    leaf_iw = max(len(r) for r in leaf_rows)
    mid_iw = max(len(r) for r in mid_rows)
    coll_rows, _ = collector_rows(1)
    coll_iw = leaf_iw

    bx = k1 + 3
    hy = 6  # head interior rows hy..hy+2
    ry = 13  # mid router interior rows ry..ry+6
    # +2, not +1: a pipe's first cell must sit against the source wall, so every
    # command pipe needs at least one vertical cell before it turns west.
    ty0 = ry + len(mid_rows) + 2
    by0 = ty0 + k1 + 1
    pitch_y = 23
    east = bx + max(leaf_iw, mid_iw, coll_iw) + 2

    grid = Circuit(east + k1 + 8, by0 + pitch_y * k1 + 8)

    _io_room(grid, bx + 1, 1, "I")
    _room(grid, bx, hy, list(HEAD_ROWS))
    _room(grid, bx, ry, mid_rows)
    _io_room(grid, bx + 15, hy + 1, "O")

    # ── the sidecar. Everything a reader of the bare ASCII cannot recover: which
    # room is what, and above all which physical cell holds which address.
    dbg = DebugMap(f"man-memory tree k1={k1} k2={k2} (n={k1 * k2})")
    dbg.region(
        "input",
        bx,
        0,
        3,
        3,
        note="the problem's own stream: `0 addr` (READ) / `1 addr value` (WRITE)",
        color=C_IO,
    )
    dbg.region(
        "head",
        bx,
        hy,
        max(len(r) for r in HEAD_ROWS),
        len(HEAD_ROWS),
        note=(
            "the only room that talks to I/O. Swaps the stream's `op addr` into the "
            "tree's `addr op [value]`, and on a READ waits for the answer before "
            "starting the next operation — that serialisation is what keeps answers "
            "ordered when latency down the tree differs per address. Ports: input and "
            "answer arrive on the north wall (over interior cols 1 and 8), the command "
            "leaves the south wall under col 5, the answer leaves east on row 1."
        ),
        color=C_HEAD,
    )
    dbg.region(
        "output",
        bx + 14,
        hy,
        3,
        3,
        note="one word per READ, in stream order",
        color=C_IO,
    )
    dbg.region(
        "mid router",
        bx,
        ry,
        mid_iw,
        len(mid_rows),
        note=(
            f"one `/` splits the address: quotient (the block, 0..{k1 - 1}) drives BP, "
            f"remainder (the local address, 0..{k2 - 1}) is relayed on. Then a single "
            f"walk east: `d` bypasses a lane while BP>0 and peels into it at BP==0, so "
            f"lane j owns addresses j*{k2}..j*{k2}+{k2 - 1}."
        ),
        color=C_MID,
    )
    dbg.circle(
        "`/` address split",
        bx + mid_rows[0].index("/"),
        ry,
        2,
        note=(
            f"`>rM{k2}W/bW`: A=addr, B={k2}, then `/` leaves quotient in A and remainder "
            f"in B in one glyph; `b` loads BP=quotient and `W` brings the remainder back "
            f"to A for the lane's `s`. addr = block*{k2} + local."
        ),
        color=C_MID,
    )
    for j in range(k1):
        dbg.region(
            f"mid lane {j} -> addr {j * k2}..{j * k2 + k2 - 1}",
            bx + mid_ports[j] - 1,
            ry,
            PITCH,
            len(mid_rows),
            note=(
                f"BP=={j} peels here; the command pipe leaves the south wall under this "
                f"lane's `v` and runs to the leaf router {k1 - 1 - j} blocks down "
                f"(lane j feeds block k1-1-j, which is what keeps the fan-out planar)."
            ),
            color=C_MID,
        )

    in_pipe = [(bx + 1, 3), (bx + 1, 4), (bx + 1, hy - 1)]
    root_pipe = [(bx + 5, hy + 4), (bx + 5, hy + 5), (bx + 5, ry - 1)]
    out_pipe = [(bx + 12, hy + 1), (bx + 13, hy + 1), (bx + 14, hy + 1)]
    draw_pipe(grid, in_pipe)  # I -> head
    draw_pipe(grid, root_pipe)  # head -> mid
    draw_pipe(grid, out_pipe)  # head -> O
    dbg.lane(
        "input-pipe",
        _corners(in_pipe),
        kind="pipe",
        expect="op addr [value]",
        color=C_CMD,
    )
    dbg.lane(
        "root command",
        _corners(root_pipe),
        kind="pipe",
        expect="addr op [value]",
        color=C_CMD,
    )
    dbg.lane(
        "output-pipe",
        _corners(out_pipe),
        kind="pipe",
        expect="one word per READ",
        color=C_ANS,
    )

    mid_bottom = ry + len(mid_rows)
    prev_coll: tuple[int, int] | None = None
    # Lane j feeds the block (k1-1-j) rows down. That reversal is what makes the
    # fan-out planar: the deepest block gets the westmost corridor column and the
    # earliest turn row, so no command pipe ever has to cross another.
    for pos in range(k1):
        lane = k1 - 1 - pos
        by = by0 + pitch_y * pos
        _room(grid, bx, by, leaf_rows)
        ty, cx = ty0 + lane, 1 + lane
        cmd_path = (
            [(bx + mid_ports[lane], y) for y in range(mid_bottom + 1, ty)]
            + [(x, ty) for x in range(bx + mid_ports[lane], cx - 1, -1)]
            + [(cx, y) for y in range(ty + 1, by + 1)]
            + [(x, by) for x in range(cx + 1, bx)]
        )
        draw_pipe(grid, cmd_path)
        dbg.region(
            f"leaf router block {lane} (addr {lane * k2}..{lane * k2 + k2 - 1})",
            bx,
            by,
            leaf_iw,
            len(leaf_rows),
            note=(
                f"`>rb` loads BP = the local address, then the same walk east: lane i "
                f"peels to the cell holding addr {lane * k2}+i. Fed by mid lane {lane}; "
                f"every `r` in here (including the WRITE value at the far end of the "
                f"lane) binds to that one incoming pipe, which is why the packet needs no "
                f"dummy tokens."
            ),
            color=C_LEAF,
        )
        dbg.lane(
            f"cmd block {lane}",
            _corners(cmd_path),
            kind="pipe",
            expect="local op [value]",
            color=C_CMD,
        )
        # leaf router -> its cells, and the cells -> this block's collector
        for i in range(k2):
            cell_x = bx + leaf_ports[i] - 1
            cell_y = by + len(leaf_rows) + 4
            addr = lane * k2 + i
            cell_at(grid, cell_x, cell_y)
            cell_cmd = [(cell_x, cell_y - 3), (cell_x, cell_y - 2), (cell_x, cell_y - 1)]
            draw_pipe(grid, cell_cmd)
            out_x = cell_x + CELL_OUT_COL - CELL_IN_COL
            cell_ans = [(out_x, cell_y + 5), (out_x, cell_y + 6), (out_x, cell_y + 7)]
            draw_pipe(grid, cell_ans)
            dbg.region(
                f"cell addr {addr}",
                cell_x - 1,
                cell_y - 1,
                CELL_W,
                CELL_H,
                note=(
                    f"address {addr} = mid lane {lane} * {k2} + leaf lane {i}. The "
                    f"resident man's B *is* the stored value — 0 until the first WRITE, so "
                    f"there is no initialisation pass. READ {CELL_READ_TICKS} ticks, "
                    f"WRITE {CELL_WRITE_TICKS}."
                ),
                color=C_STORE,
            )
            dbg.lane(
                f"cmd addr {addr}",
                _corners(cell_cmd),
                kind="pipe",
                expect="op [value]",
                color=C_CMD,
            )
            dbg.lane(
                f"answer addr {addr}",
                _corners(cell_ans),
                kind="pipe",
                expect="the value, on READ only",
                color=C_ANS,
            )
        cy = by + len(leaf_rows) + 4 + CELL_H + 2
        _room(grid, bx, cy, [r.ljust(coll_iw) for r in coll_rows])
        dbg.region(
            f"collector block {lane}",
            bx,
            cy,
            coll_iw,
            len(coll_rows),
            note=(
                f"`R` takes whichever pipe is ready — this block's {k2} cells, plus the "
                f"chain from the block above — and `s` forwards it south. No pipe affinity "
                f"needed, which is why the answer path is a chain beside the cells rather "
                f"than {k1 * k2} long pipes converging on one room."
            ),
            color=C_COLL,
        )
        if prev_coll is not None:
            px, py = prev_coll
            ex = east + pos - 1
            chain = (
                # in on interior row 0, out from row 1: a collector's own outgoing
                # stub and its predecessor's incoming one would otherwise want the
                # same cell east of the wall.
                [(x, py) for x in range(px, ex + 1)]
                + [(ex, y) for y in range(py + 1, cy)]
                + [(x, cy) for x in range(ex, bx + coll_iw - 1, -1)]
            )
            draw_pipe(grid, chain)
            dbg.lane(
                f"answer chain block {lane + 1} -> block {lane}",
                _corners(chain),
                kind="pipe",
                expect="an answer from any block above",
                color=C_ANS,
            )
        prev_coll = (bx + coll_iw + 1, cy + 1)

    # last collector -> the head's answer port on its north wall
    px, py = prev_coll
    ex = east + k1
    last = (
        [(x, py) for x in range(px, ex + 1)]
        + [(ex, y) for y in range(py - 1, 3, -1)]
        + [(x, 4) for x in range(ex - 1, bx + 8 - 1, -1)]
        + [(bx + 8, hy - 1)]
    )
    draw_pipe(grid, last)
    dbg.lane(
        "answers -> head",
        _corners(last),
        kind="pipe",
        expect="every READ answer, the full height of the grid",
        color=C_ANS,
    )

    rows = [row.rstrip() for row in grid.rows()]
    while rows and not rows[-1]:
        rows.pop()
    width = max(len(row) for row in rows)
    return Tree(k1=k1, k2=k2, rows=tuple(rows), width=width, height=len(rows), debug=dbg)


# ── measured cost model ──────────────────────────────────────────────────────
#
# Every number below was produced by differencing engine runs of k identical
# operations (``scratch``-style harness: k = 4 vs 12, divided by 8), never derived.
# They are the input a placer or a sweep needs, so they live here as data.
#
# A single cell, driven straight off the input pipe:
#     READ  12 ticks   WRITE  8 ticks     6x6 room, no initialisation pass
#
# ``build_line(n)`` — one router, n cells on its south wall, collector below:
#     READ = WRITE = 22 + 14 * addr        (exact, all n; 14 = 8 out + 6 home)
#     grid = (6n + 13) x 21
#
# ``build_tree(k1, k2)`` — mid router over k1 leaf blocks of k2 cells:
#     WRITE ~= 44 + 14 * (addr // k2 + addr % k2)      (fire-and-forget)
#     READ  ~= WRITE + answer path, and the answer path dominates in the planar
#              layout built here because it runs the full height of the grid.
#: Measured (k1, k2) -> (grid w, h, READ at addr 0, READ at addr n-1, WRITE at n-1).
TREE_MEASURED: dict[tuple[int, int], tuple[int, int, int, int, int]] = {
    (2, 2): (30, 69, 175, 209, 44),
    (3, 4): (39, 93, 229, 287, 60),
    (4, 4): (46, 117, 291, 399, 72),
    (4, 8): (65, 117, 305, 415, 116),
    (6, 6): (62, 165, 407, 597, 100),
}

#: ``build_line(n)`` measured: n -> (grid w, h, READ/WRITE at addr 0, at addr n-1).
LINE_MEASURED: dict[int, tuple[int, int, int, int]] = {
    1: (19, 21, 22, 22),
    2: (25, 21, 22, 36),
    4: (37, 21, 22, 64),
    8: (61, 21, 22, 120),
    16: (109, 21, 22, 232),
}


def line_ticks(addr: int) -> int:
    """Ticks for one operation at ``addr`` in a ``build_line`` memory (exact)."""
    return 22 + 14 * addr


def tape_ticks(n: int) -> float:
    """The LM-1 STORE tape's cost per access, for comparison (``ARCH.md`` §4.1)."""
    return 105 + 8.3 * n


# ── CLI: the grid and its sidecars, in one invocation ────────────────────────
#
# The house rule from ``littleman/DEBUGGING.md``: a generated `.man` carries no
# comments, so the generator is the only thing that knows what a cell means and it
# must emit the overlay at the same moment it emits the ASCII. Writing the three
# files separately is how an overlay drifts from its grid.
def main(argv: Sequence[str] | None = None) -> int:
    """``--line N`` or ``--tree K1 K2``, written to ``--man``/``--html``/``--json``."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--line", type=int, metavar="N", help="one router with N cells")
    what.add_argument(
        "--tree",
        type=int,
        nargs=2,
        metavar=("K1", "K2"),
        help="a mid router over K1 leaf blocks of K2 cells (n = K1*K2)",
    )
    ap.add_argument("--man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write the labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args(argv)

    built: Line | Tree
    built = build_line(args.line) if args.line is not None else build_tree(*args.tree)
    rows = list(built.rows)
    if args.man:
        args.man.write_text(built.source() + "\n", encoding="utf-8")
    assert built.debug is not None  # every builder emits its own map
    if args.html:
        built.debug.write_html(rows, args.html)
    if args.json:
        built.debug.write_json(args.json)
    if not (args.man or args.html or args.json):
        print(built.source())
    else:
        print(f"{built.width} x {built.height}, footprint {built.footprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
