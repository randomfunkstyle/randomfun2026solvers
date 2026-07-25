#!/usr/bin/env python3
"""A memory cell that is *only a man and a loop* — no room of its own.

``memory_men`` gives every value a 6x6 room, so 36 cells and two pipes buy one
stored value. That is backwards: rooms are for *pipe boundaries*, and a value does
not need one. ``Y`` is the only way to put more than one man in a room (SPEC), and
it means ``n`` values can share a single room with no walls between them at all.

Four things had to be true for that to work, and all four are checked against the
engine in ``tests/test_memory_men_field.py``:

1. **A man blocked on ``s`` parks, and the pipe he is blocked on holds his value.**
   So a READ is not an interaction with the man — it is an ``r`` on his pipe from
   the other side, and he immediately refills it. Storage is the pipe; the man is
   the refresh circuit. (Probed: parked at his ``s`` with ``A = 5`` and the pipe
   holding 5, stable for hundreds of ticks; drained, he re-sends forever.)

2. **Pipe affinity is per-position, so men sharing one room still get private
   pipes.** Three cells in one room, residents born by ``Y`` off the input stream,
   parked holding 11, 22 and 33, each in its own pipe. This is what makes a field
   of cells possible rather than a queue.

3. **A moving man does NOT kill a parked one — he blocks behind him.** This
   contradicts the obvious reading of SPEC's collision rules ("ordinary same-cell
   arrivals ... kill the participants"): those are *arrivals*, two men moving into
   one cell on one tick. A stationary man is not arriving, so the mover simply
   never gets in. Verified stable to 200 ticks with both men alive.

   That kills the tempting write protocol — walk into the resident to evict him,
   then ``Y`` a replacement carrying the new value. It cannot be done.

4. **``q`` polls without blocking.** ``BP = q`` is the number of values waiting on
   the nearest incoming pipe and it consumes nothing, so a resident can ask "is
   there a write for me?" every lap and branch on the answer. Every other way of
   learning about a pipe (``r``/``R``/``U``) blocks, and a cell that blocks stops
   refreshing.

So the cell needs no eviction and no ``Y`` on the write path:

    v r<      write branch: BP > 0, take the value, rejoin the loop
    >sqav     s refills the read pipe (and parks here while it is full)
    ^@  <     q asks whether a write is waiting; a turns to it if so

``A`` holds the value, ``B`` and ``BP`` are scratch. ``A`` starts at 0, which is the
problem's "every cell starts at 0" for free — a man-field needs no initialisation
pass. READ is ``r`` on the cell's read pipe; WRITE is ``s`` into its write pipe.
Neither costs the memory a single tick of its own: **all the cost moves to
addressing**, which is now reaching pipe ``k`` from outside rather than walking a
lane inside.
"""

from __future__ import annotations

from .circuit import Circuit
from .memory_men import _io_room, _room, draw_pipe

__all__ = [
    "FIELD_CELL",
    "PARK_CELL",
    "build_park_probe",
    "build_field_probe",
    "build_q_cell_probe",
    "onehot_cell_rows",
    "build_onehot_probe",
]

#: The complete cell: 5x3 interior, ten glyphs, two pipes, no walls of its own.
#: The write pipe lands on the north wall above ``q``; the read pipe leaves the
#: south wall under ``s``. Both bind by position, so a field of these in one room
#: gets one private pipe pair each.
FIELD_CELL: tuple[str, ...] = (
    "v r< ",
    ">sqav",
    "^@  <",
)

#: The read-only half: the smallest loop that can contain an ``s`` at all. Four
#: corners of a 3x2 pinwheel must be turn glyphs, which leaves exactly two free
#: cells — one of them is the ``s`` the man parks on.
PARK_CELL: tuple[str, ...] = (
    ">sv",
    "^ <",
)


def build_park_probe(*, drain: bool) -> str:
    """One parked cell. With ``drain`` a collector empties its pipe forever.

    Undrained, the man must be *stationary* on his ``s`` with the value sitting in
    the pipe. Drained, the output must be his value repeated — the refresh loop.
    """
    body = ["@5>sv   ", "  ^ <   "]
    g = Circuit(26, 16)
    rx, ry = 1, 1
    _room(g, rx, ry, body)
    # interior rows ry..ry+1, south wall at ry+2, so the pipe starts at ry+3
    sx = rx + 3
    draw_pipe(g, [(sx, ry + 3), (sx, ry + 4), (sx, ry + 5)])
    cy = ry + 6
    if drain:
        _room(g, rx + 2, cy, ["@>rv", " ^s<"])
        draw_pipe(g, [(rx + 7, cy + 1), (rx + 8, cy + 1), (rx + 9, cy + 1)])
        _io_room(g, rx + 10, cy + 1, "O")
    else:
        _room(g, rx + 2, cy, ["    ", "    "])
    return _trim(g)


def build_field_probe(n: int) -> str:
    """``n`` cells in ONE room, residents born by ``Y`` off the input stream.

    The spawner walks east; at each site it reads a value and splits. The south
    child lands on that site's pinwheel and parks on its ``s`` holding the value;
    the north child runs the return lane and carries on to the next site. Proves
    per-position pipe affinity for men sharing a room.
    """
    pitch = 5
    rows = [[" "] * (1 + pitch * n + 1) for _ in range(4)]
    for j in range(n):
        x = 1 + pitch * j
        rows[1][x] = "r"
        rows[1][x + 1] = "Y"
        rows[0][x + 1] = ">"
        rows[0][x + 4] = "v"
        rows[1][x + 4] = ">"
        rows[2][x + 1] = ">"
        rows[2][x + 2] = "s"
        rows[2][x + 3] = "v"
        rows[3][x + 3] = "<"
        rows[3][x + 1] = "^"
    rows[1][0] = "@"
    rows[1][1 + pitch * (n - 1) + 4] = "H"  # the spawner stops after the last site
    body = ["".join(r) for r in rows]
    iw = len(body[0])

    g = Circuit(iw + 20, 24)
    rx, ry = 6, 4
    _room(g, rx, ry, body)
    _io_room(g, rx - 5, ry + 1, "I")
    draw_pipe(g, [(rx - 3, ry + 1), (rx - 2, ry + 1), (rx - 1, ry + 1)])
    sink_y = ry + 8
    for j in range(n):
        sx = rx + 1 + pitch * j + 2
        draw_pipe(g, [(sx, y) for y in range(ry + 5, sink_y)])
    _room(g, rx, sink_y, [" " * iw, " " * iw])
    return _trim(g)


def build_q_cell_probe() -> str:
    """One :data:`FIELD_CELL` with a write pipe in and a read pipe out.

    Feeding it one value must produce ``0`` (its free initial state) followed by
    that value repeated forever: proof that ``q`` lets a cell accept a write
    without ever blocking its own refresh.
    """
    g = Circuit(34, 24)
    rx, ry = 8, 6
    _room(g, rx, ry, list(FIELD_CELL))
    _io_room(g, rx + 2, ry - 5, "I")
    draw_pipe(g, [(rx + 2, ry - 3), (rx + 2, ry - 2), (rx + 2, ry - 1)])
    draw_pipe(g, [(rx + 1, ry + 4), (rx + 1, ry + 5), (rx + 1, ry + 6)])
    cy = ry + 7
    _room(g, rx, cy, ["@>rv", " ^s<"])
    draw_pipe(g, [(rx + 5, cy + 1), (rx + 6, cy + 1), (rx + 7, cy + 1)])
    _io_room(g, rx + 8, cy + 1, "O")
    return _trim(g)


def _trim(g: Circuit) -> str:
    rows = [row.rstrip() for row in g.rows()]
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows)


# ── the broadcast decoder: addressing without a walk ─────────────────────────
#
# `R` reads from ANY incoming pipe, so n senders reach one room in O(1) with one
# glyph and no addressing — a many-to-one teleport, which is why the collector is
# free. `S` is its mirror: it writes A into EVERY outgoing pipe at once. Together
# they mean a request need not be *routed* to a cell at all — it can be shouted at
# all of them, with each cell deciding for itself.
#
# The obvious way to do that does not work. A cell's value lives in `B`, and every
# comparison glyph (`-`, `~`, `%`, `{`, ...) reads `B`, so a cell holding a value
# literally cannot compare a broadcast address against its own index.
#
# Broadcasting **one-hot** does work. The router builds `1 << addr` in a single
# glyph (`{`), and cell j tests bit j with `b` `]`*j `x` — all three touch only
# `BP`, so `B` is never disturbed. `x` always turns, so the test is a clean two-way
# branch with no third case to merge. Probed for j = 0, 1, 3 against every address:
# each cell answers for exactly its own and stays silent otherwise, and what it
# answers is the value that was in `B` all along.
#
# Cost: j shifts in cell j, all cells testing *in parallel*, so an access is
# bounded by the slowest cell (~n ticks) rather than by a walk (10 ticks per lane
# passed). Area: the shift chains are O(n^2/2) glyphs — 136 for n = 16.


def onehot_cell_rows(j: int, *, value_probe: bool = False) -> list[str]:
    """Cell ``j``'s decode: does the broadcast word have bit ``j`` set?

    With ``value_probe`` the cell is a self-contained test — it loads 7 into ``B``
    and sends it when selected, which is what proves the decode leaves ``B`` alone.
    """
    if j < 0:
        raise ValueError("cell index must not be negative")
    code = ("@7M" if value_probe else "@") + "r" + "b" + "]" * j + "x"
    w = len(code)
    return [
        " " * (w - 1) + "H",  # bit clear -> counter-clockwise: not this cell
        code,
        " " * (w - 1) + "W",  # bit set -> clockwise: mine. W brings the value to A
        " " * (w - 1) + "s",
        " " * (w - 1) + "H",
    ]


def build_onehot_probe(j: int) -> str:
    """One decode cell wired I to O: feed ``1 << addr`` and see whether it answers."""
    rows = onehot_cell_rows(j, value_probe=True)
    w = max(len(r) for r in rows)
    g = Circuit(w + 22, 18)
    rx, ry = 6, 2
    _room(g, rx, ry, [r.ljust(w) for r in rows])
    _io_room(g, rx - 5, ry + 1, "I")
    draw_pipe(g, [(rx - 3, ry + 1), (rx - 2, ry + 1), (rx - 1, ry + 1)])
    sx = rx + w - 1
    draw_pipe(g, [(sx, ry + 6), (sx, ry + 7), (sx, ry + 8)])
    _io_room(g, sx, ry + 9, "O")
    return _trim(g)
