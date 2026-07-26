#!/usr/bin/env python3
"""`subset-sum` as a meet-in-the-middle ring machine — the grid.

The algorithm, its cost and the reasons behind every register convention are
:mod:`randomfun2026solvers.subset_sum_mitm`; the five-ticks-a-word scan it is
priced on is measured by :mod:`randomfun2026solvers.subset_sum_scan_probe`.
This module is only the machine.

## Two rings, six pipes

* **ring V** — one lap per candidate mask, `n + 7` words::

      [ C, G, CR, GR, v_0 .. v_(hL-1), MB, v_hL .. v_(n-1), MT, RR ]

      C  = left mask counter, walked down from 2^hL
      G  = 2^hL           the guard bit that makes the bit reversal self-timing
      CR = right mask counter, walked down from 256
      GR = 256            the same guard for the right half
      MB = -1             boundary: ends the left peel, starts the right one
      MT = -(t + 1)       terminator, and the target, in one word
      RR = r + 1          the residual the current lap left behind

  `C` and `CR` hold **one more** than the counter a lap uses: a lap reads the
  word, subtracts one, sends the difference back, and the same subtraction is the
  exhaustion test.  The lap that wins therefore leaves the ring holding exactly
  the winning counter, which is what lets the emit phase recompute the mask from
  the ring instead of finding a register to keep it in.

* **ring B** — every right-half subset sum, biased by one so no stored word is
  ever `0`, behind a `-1` sentinel.  `2^8 + 1 = 257` words.

`hR` is fixed at 8, so `256`, the peel width and ring B's whole geometry are
compile-time constants and `hL = n - 8` is the only thing the machine derives
from the input.

## Pipe binding is one-dimensional

All six pipes hang off the worker's **north** wall, so the `y` term of "nearest
by Manhattan distance" is common to every one of them and binding collapses to
*nearest column*.  :data:`BANDS` names the three safe column ranges and every
`r`/`s` this module places goes through :func:`_op`, which refuses a column
outside the band it was asked for.  ``tests/test_subset_sum_grid.py`` re-derives
the binding independently from ``route-check.mjs``, which reports the pipe each
op actually resolves to — a mis-bound `s` is otherwise completely silent.

## What is built, and what is not

Built and **verified on the reference engine for all seven public cases**:
`INIT`, `LEFTVALS`, `RIGHTVALS` with its ring-B doubling pass, and `TAIL` — the
`load` stage spills ring V and the `loadb` stage spills ring B, and both match
the Python oracle word for word.

Built but not yet wired to a stage: `PHASE2`'s prologue, guarded bit reversal,
peel and rotate-to-`MT`.  **Not built:** phase 2's `MT`/`RR` test and scan
station, `PHASE3`, `EMIT` and the no-solution lane.  `worker("full")` therefore
raises rather than returning half a machine.

## No backticks

The only constants the machine needs are `1`, `8`, `256 = 1 << 8` and `2^hL`,
and every one is reachable from a single digit glyph, so the grid holds no
numeric literal at all and the vertical-pairing load error cannot arise.
"""

from __future__ import annotations

from randomfun2026solvers.circuit import E, N, Circuit

__all__ = [
    "BANDS",
    "BFWD_COL",
    "BRET_COL",
    "IN_COL",
    "OUT_COL",
    "VFWD_COL",
    "VRET_COL",
    "build",
    "worker",
]

# ── north-wall anchor columns, worker-local ──────────────────────────────────
IN_COL, OUT_COL = 3, 6
VRET_COL, VFWD_COL = 20, 21
BRET_COL, BFWD_COL = 30, 31

#: Safe column ranges per target, inclusive.  A column outside every band is
#: *forbidden*, not merely undocumented: the midpoints between anchors are
#: 11.5/13.5 (io|v) and 25/26 (v|b), and a tie is resolved by reading order,
#: which is not something a block should be allowed to depend on.
BANDS: dict[str, tuple[int, int]] = {"io": (0, 11), "v": (14, 24), "b": (27, 39)}

#: Worker interior.
IW, IH = 40, 78


def _op(c: Circuit, x: int, y: int, glyph: str, band: str) -> None:
    """Place `r` or `s` at (x,y), refusing any column outside `band`."""
    lo, hi = BANDS[band]
    if not lo <= x <= hi:
        raise ValueError(
            f"{glyph!r} at ({x},{y}) is outside the {band!r} band {lo}..{hi}; "
            "it would bind to another pipe"
        )
    c.set(x, y, glyph)


def rin(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "r", "io")


def out(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "s", "io")


def vr(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "r", "v")


def vs(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "s", "v")


def br(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "r", "b")


def bs(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "s", "b")


# ══════════════════════════════ reusable blocks ═══════════════════════════════
#
# Every block is entered heading EAST and leaves its exit cell heading east or
# north; each returns the cell the man occupies on the way out, so a caller can
# chain blocks with `Circuit.route` and never has to know their internals.


def _bits(c: Circuit, xd: int, y: int, *, shift: bool) -> tuple[int, int]:
    """The backpack-decomposition loop: `d` `{` `x` `+` `]`, six cols by four rows.

    Entered at ``(xd - 3, y)`` heading east with ``BP`` holding the value to
    decompose, ``A`` the accumulator and ``B = 1``.  Each pass shifts the
    accumulator left (``shift``), adds the low bit of ``BP`` and halves ``BP``;
    the loop ends when ``BP`` reaches zero, which for a guarded counter
    ``c + 2^w`` is after exactly ``w + 1`` passes whatever ``c``'s leading zeros.

    With ``shift`` the result is ``bitrev(c) * 2 + 1``; without it, the number of
    one bits — the same walk, which is why the emit phase's population count is
    this block with the ``{`` left out.
    """
    c.set(xd - 3, y, ">")                   # merge: entry and both lanes
    c.horizontal(y, xd - 3, xd)
    c.set(xd, y, "d")                       # BP > 0 -> clockwise, south
    c.set(xd, y + 1, "{" if shift else " ")
    c.set(xd, y + 2, "x")                   # low bit: 1 -> west, 0 -> east
    # bit 1: add one, halve, straight back up into the merge
    c.set(xd - 1, y + 2, "+")
    c.set(xd - 2, y + 2, "]")
    c.set(xd - 3, y + 2, "^")
    c.set(xd - 3, y + 1, " ")
    # bit 0: halve, then round the outside and up the same column
    c.set(xd + 1, y + 2, "]")
    c.set(xd + 2, y + 2, "v")
    c.set(xd + 2, y + 3, "<")
    c.horizontal(y + 3, xd + 2, xd - 3)
    c.set(xd - 3, y + 3, "^")
    return xd + 1, y                        # heading east, BP = 0


def _rot(c: Circuit, xr: int, y: int) -> tuple[int, int]:
    """Rotate ring V until a **negative** word comes out; re-send it and leave.

    ``A`` ends holding the marker, already returned to the ring, and ``B`` and
    ``BP`` are untouched — `r`, `s` and `X` leave both alone, which is what lets
    this run between a peel's two halves without a spare register.  Entered at
    ``(xr - 1, y)`` heading east; leaves ``(xr + 2, y - 1)`` heading north.
    """
    c.set(xr - 1, y, ">")
    vr(c, xr, y)
    vs(c, xr + 1, y)
    c.set(xr + 2, y, "X")                   # value > 0 -> south, marker < 0 -> north
    c.set(xr + 2, y + 1, "<")
    c.horizontal(y + 1, xr + 2, xr - 1)
    c.set(xr - 1, y + 1, "^")
    return xr + 2, y - 1


def _peel_head(c: Circuit, xr: int, y: int) -> None:
    """The three cells every peel shares: read, put back, branch on the sign.

    Re-sending *before* the test is what lets one `X` end the loop: the marker
    goes back into the ring on its way past, so the ring stays aligned whichever
    way the man leaves.  Values are `1..99999` and both markers are negative, so
    `X` never goes straight and the loop has no third exit.
    """
    c.set(xr - 2, y, ">")                   # merge: entry and both lanes
    c.set(xr - 1, y, " ")
    vr(c, xr, y)
    vs(c, xr + 1, y)
    c.set(xr + 2, y, "X")                   # value -> south, marker -> north
    c.set(xr + 2, y + 1, "x")               # mask low bit: 1 -> west, 0 -> east


def _peel_tail(c: Circuit, xr: int, y: int) -> None:
    """The bit-0 lane: halve the mask and come back up the merge column.

    Both lanes climb ``xr - 2``; the bit-1 lane joins it at row ``y+1`` and the
    bit-0 lane a row lower, so the two `^` cells are the whole merge.
    """
    c.set(xr + 3, y + 1, "]")
    c.set(xr + 4, y + 1, "v")
    c.set(xr + 4, y + 2, "<")
    c.horizontal(y + 2, xr + 4, xr - 2)
    c.set(xr - 2, y + 2, "^")


def _peel_sum(c: Circuit, xr: int, y: int) -> tuple[int, int]:
    """Peel ring V's values against the mask, accumulating the taken ones in B.

    ``A`` is the word, ``B`` the running sum and ``BP`` the mask; nothing else is
    live.  Entered at ``(xr - 2, y)`` heading east, leaves ``(xr + 2, y - 1)``
    heading north with ``A`` holding the marker that ended it.
    """
    _peel_head(c, xr, y)
    c.set(xr + 1, y + 1, "+")               # bit 1: A = v + sum ...
    c.set(xr, y + 1, "M")                   # ... and B = the new sum
    c.set(xr - 1, y + 1, "]")
    c.set(xr - 2, y + 1, "^")
    _peel_tail(c, xr, y)
    return xr + 2, y - 1


def _peel_emit(c: Circuit, xr: int, y: int, xout: int) -> tuple[int, int]:
    """Peel ring V's values against the mask, **emitting** the taken ones.

    The output pipe is fifteen columns west of the ring's, so the bit-1 lane
    walks out to it and comes back a row lower; the bit-0 lane crosses that
    return leg going the other way, which is safe because every cell they share
    is a blank.  Same entry and exit as :func:`_peel_sum`.
    """
    _peel_head(c, xr, y)
    c.horizontal(y + 1, xr + 1, xout)
    out(c, xout, y + 1)                     # emit the chosen value
    c.set(xout - 1, y + 1, "]")
    c.set(xout - 2, y + 1, "v")
    c.set(xout - 2, y + 2, ">")
    c.horizontal(y + 2, xout - 2, xr - 2)
    c.set(xr - 2, y + 2, "^")
    _peel_tail(c, xr, y)
    return xr + 2, y - 1


# ══════════════════════════════ block plumbing ════════════════════════════════
#
# Blocks are stacked down the room and every one is entered heading **east** from
# the same descent column, so a link is always the same four legs: east out of
# the block, south down the east column, west along a spare row, south down the
# west column into the next block's entry row.  Nothing ever walks west across a
# block's own row, which is the mistake that silently re-steers a man.

EAST_COL, WEST_COL = 34, 13


def _link(
    c: Circuit,
    frm: tuple[int, int],
    heading: tuple[int, int],
    y_link: int,
    to: tuple[int, int],
) -> None:
    """Route a man leaving `frm` with `heading` into the entry cell `to`."""
    x0, y0 = frm
    xt, yt = to
    c.route(
        (x0, y0),
        heading,
        [(EAST_COL, y0), (EAST_COL, y_link), (WEST_COL, y_link)],
        (WEST_COL, yt),
        E,
    )
    c.horizontal(yt, WEST_COL, xt)


# ══════════════════════════════ the machine ═══════════════════════════════════


def _init(c: Circuit) -> tuple[int, int]:
    """Read `n`, derive `hL`, and lay ring V's header and ring B's seed.

    ``r M 8 W - b M 1 {`` is the whole of the geometry the machine derives from
    its input: ``A = n``, ``B = n``, ``A = 8``, swap, ``A = hL``, park it in the
    backpack for the value loop, and shift a one up to ``GL = 2^hL``.  The header
    then goes out as ``GL, GL, 256, 256`` — a counter and its guard are the same
    word — and ring B starts as ``[1, -1]``: the empty right-hand subset, biased,
    behind its sentinel.
    """
    c.set(0, 0, ">")
    c.set(1, 0, "@")
    rin(c, IN_COL, 0)                       # A = n
    c.run(IN_COL + 1, 0, "M8W-bM1{")        # BP = hL, A = GL = 1 << hL
    c.horizontal(0, IN_COL + 9, VRET_COL)
    vs(c, VRET_COL, 0)                      # C  = GL
    vs(c, VFWD_COL, 0)                      # G  = GL
    _link(c, (VFWD_COL + 1, 0), E, 1, (14, 2))

    c.run(14, 2, "8M1{")                    # A = 256, B = 8
    vs(c, 18, 2)                            # CR = 256
    vs(c, 19, 2)                            # GR = 256
    c.set(20, 2, "1")
    c.horizontal(2, 20, 27)
    bs(c, 27, 2)                            # ring B = [1]
    c.run(28, 2, "1N")
    bs(c, 30, 2)                            # ring B = [1, -1]
    return 31, 2


def _leftvals(c: Circuit, y: int) -> tuple[int, int]:
    """`hL` times: read a value and append it to ring V.

    The input pipe is eleven columns west of the ring's, so the body walks out to
    it and comes back on the row below; `m` sits on the *body* rather than the
    return leg so the return row stays a plain corridor another block can merge
    into.
    """
    c.set(WEST_COL, y, ">")                 # merge: entry and the return leg
    c.set(WEST_COL + 1, y, "d")             # BP > 0 -> south into the body
    c.set(WEST_COL + 1, y + 1, "<")
    c.horizontal(y + 1, WEST_COL + 1, IN_COL)
    c.set(WEST_COL - 1, y + 1, "m")
    rin(c, IN_COL, y + 1)                   # A = v
    c.set(IN_COL - 1, y + 1, "v")
    c.set(IN_COL - 1, y + 2, ">")
    c.horizontal(y + 2, IN_COL - 1, VRET_COL)
    vs(c, VRET_COL, y + 2)                  # append it to ring V
    c.set(VRET_COL + 1, y + 2, "v")
    c.set(VRET_COL + 1, y + 3, "<")
    c.horizontal(y + 3, VRET_COL + 1, WEST_COL)
    c.set(WEST_COL, y + 3, "^")
    c.vertical(WEST_COL, y + 3, y)
    return WEST_COL + 2, y


def _boundary(c: Circuit, y: int) -> tuple[int, int]:
    """Close the left half with `MB = -1` and arm the eight-value right loop."""
    c.run(14, y, "1N")
    c.horizontal(y, 15, VRET_COL)
    vs(c, VRET_COL, y)                      # MB
    c.run(VRET_COL + 1, y, "8b")            # BP = 8, the fixed right-half width
    return VRET_COL + 3, y


def _rightvals(c: Circuit, y: int) -> tuple[int, int]:
    """Eight times: append the value to ring V **and** double ring B by it.

    The doubling pass is the inner loop: every stored sum is re-sent unchanged
    and again with `v` added, so one pass turns `k` subset sums into `2k` and
    eight passes turn `[1]` into all 256.  `v` lives in ``B`` and the outer count
    in ``BP``, and the pass touches neither — `r`, `s`, `X` and `+` leave both
    alone, which is why one backpack is enough for two nested loops.
    """
    c.set(WEST_COL, y, ">")
    c.set(WEST_COL + 1, y, "d")
    c.set(WEST_COL + 1, y + 1, "<")
    c.horizontal(y + 1, WEST_COL + 1, IN_COL)
    c.set(WEST_COL - 1, y + 1, "m")
    rin(c, IN_COL, y + 1)                   # A = v
    c.run(IN_COL - 1, y + 1, "Mv", d=(-1, 0))   # B = v, then down a row
    c.set(1, y + 2, ">")
    c.horizontal(y + 2, 1, VRET_COL)
    vs(c, VRET_COL, y + 2)                  # append v to ring V

    # ── the doubling pass over ring B ────────────────────────────────────────
    c.horizontal(y + 2, VRET_COL, 28)
    br(c, 28, y + 2)
    bs(c, 29, y + 2)                        # every stored sum goes back first
    c.set(30, y + 2, "X")                   # sum > 0 -> south, sentinel -> north
    c.set(30, y + 3, "+")                   # A = w + v
    bs(c, 30, y + 4)
    c.set(30, y + 5, "<")
    c.horizontal(y + 5, 30, 27)
    c.set(27, y + 5, "^")
    c.vertical(27, y + 5, y + 2)
    c.set(27, y + 2, ">")

    # ── end of pass: climb out and merge into the outer loop's test ──────────
    c.vertical(30, y + 2, y - 1)
    c.set(30, y - 1, "<")
    return WEST_COL + 2, y


def _tail(c: Circuit, y: int) -> tuple[int, int]:
    """Read `t` and close ring V with `MT = -(t+1)` and `RR = 0`.

    `MT` carries the target already negated and biased because the peel ends on
    it anyway: `A = -(t+1)`, `B = s` and one `+` then one `N` is `r + 1`, the
    form the scan wants, with no separate word for the target.
    """
    c.set(14, y, "v")
    c.set(14, y + 1, "<")
    c.horizontal(y + 1, 14, IN_COL)
    rin(c, IN_COL, y + 1)                   # A = t
    c.run(IN_COL - 1, y + 1, "M1v", d=(-1, 0))
    c.set(0, y + 2, ">")
    c.run(1, y + 2, "+N")                   # A = -(t + 1)
    c.horizontal(y + 2, 2, VRET_COL)
    vs(c, VRET_COL, y + 2)                  # MT
    c.set(VRET_COL + 1, y + 2, "0")
    vs(c, VRET_COL + 2, y + 2)              # RR = 0
    return VRET_COL + 3, y + 2


def _spill(c: Circuit, y: int) -> tuple[int, int]:
    """Copy ring V words to the output until a marker goes past, marker included.

    Only stage `load` uses it: the header, the values and the two markers can
    then be read straight off the engine before anything searches them, and a
    wrong word is visible where it is made instead of as a wrong answer six
    phases later.
    """
    c.set(WEST_COL, y, ">")
    vr(c, 14, y)
    vs(c, 15, y)
    c.set(16, y, "v")
    c.set(16, y + 1, "<")
    c.horizontal(y + 1, 16, OUT_COL)
    out(c, OUT_COL, y + 1)
    c.set(OUT_COL - 1, y + 1, "v")
    c.set(OUT_COL - 1, y + 2, ">")
    c.horizontal(y + 2, OUT_COL - 1, 17)
    c.set(17, y + 2, "X")                   # value -> south and round again
    c.set(17, y + 3, "<")
    c.horizontal(y + 3, 17, WEST_COL)
    c.set(WEST_COL, y + 3, "^")
    c.vertical(WEST_COL, y + 3, y)
    c.set(17, y + 1, ">")                   # marker -> north and out
    return 18, y + 1


def _serpentine(x0: int, x1: int, rows: list[int]) -> list[tuple[int, int]]:
    """Points of a boustrophedon through `rows`, alternating east and west."""
    pts: list[tuple[int, int]] = []
    for i, y in enumerate(rows):
        a, b = (x0, x1) if i % 2 == 0 else (x1, x0)
        pts.extend([(a, y), (b, y)])
    return pts


#: Words each ring must hold, plus one free cell: a ring exactly as long as its
#: contents blocks a send behind its own backlog and deadlocks in silence.
V_CAP, B_CAP = 30, 262

WX, WY = 1, 24
GW, GH = 82, WY + IH + 1


def worker(stage: str = "full") -> Circuit:
    """The whole worker room, block by block, top to bottom."""
    c = Circuit(IW, IH)
    exit_ = _init(c)
    _link(c, exit_, E, 3, (14, 4))
    exit_ = _leftvals(c, 4)
    _link(c, exit_, E, 8, (14, 9))
    exit_ = _boundary(c, 9)
    _link(c, exit_, E, 10, (14, 11))
    exit_ = _rightvals(c, 11)
    _link(c, exit_, E, 17, (14, 19))
    exit_ = _tail(c, 19)
    if stage == "load":
        _link(c, exit_, E, 22, (14, 24))
        exit_ = _spill(c, 24)
        _link(c, exit_, E, 29, (14, 31))
        exit_ = _spill(c, 31)
        _link(c, exit_, E, 36, (14, 38))
        vr(c, 14, 38)
        c.set(15, 38, "v")
        c.set(15, 39, "<")
        c.horizontal(39, 15, OUT_COL)
        out(c, OUT_COL, 39)
        c.set(OUT_COL - 1, 39, "H")
        return c
    if stage == "loadb":
        _link(c, exit_, E, 22, (14, 24))
        _spill_b(c, 24)
        return c
    raise NotImplementedError(stage)


def build(stage: str = "full") -> list[str]:
    """Worker room, I/O rooms, two relays and the four ring pipes."""
    from randomfun2026solvers.dataflow_relay import relay
    from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

    g = Circuit(GW, GH)
    stamp(g, WX, WY, worker(stage).rows())
    walls(g, WX, WY, IW, IH)

    stamp(g, WX + IN_COL - 1, 18, ["+-+", "|I|", "+-+"])
    draw_pipe(g, [(WX + IN_COL, 21), (WX + IN_COL, 22)])
    stamp(g, WX + OUT_COL - 1, 18, ["+-+", "|O|", "+-+"])
    draw_pipe(g, [(WX + OUT_COL, 22), (WX + OUT_COL, 21)])

    # ── ring V: relay west, both pipes clear of the north band ──────────────
    stamp(g, 8, 10, relay(6, 4))
    n = draw_pipe(g, [(WX + VFWD_COL, 22), (WX + VFWD_COL, 8), (12, 8), (12, 9)])
    n += draw_pipe(g, [(12, 16), (12, 18), (WX + VRET_COL, 18), (WX + VRET_COL, 22)])
    if n < V_CAP:
        raise ValueError(f"ring V holds {n} words, need >= {V_CAP}")

    # ── ring B: 257 stored sums, so the two pipes nest instead of crossing ───
    stamp(g, 30, 2, relay(6, 4))
    m = draw_pipe(g, [(WX + BFWD_COL, 22), (WX + BFWD_COL, 20), (80, 20), (80, 4), (38, 4)])
    ret = [(33, 8), (33, 10)]
    ret += _serpentine(78, 35, [10, 12, 14, 16, 18])[1:]
    ret += [(WX + BRET_COL, 18), (WX + BRET_COL, 22)]
    m += draw_pipe(g, ret)
    if m < B_CAP:
        raise ValueError(f"ring B holds {m} words, need >= {B_CAP}")
    rows = [r.rstrip() for r in g.rows()]
    while rows and not rows[0].strip():
        rows.pop(0)
    while rows and not rows[-1].strip():
        rows.pop()
    return rows


def _spill_b(c: Circuit, y: int) -> None:
    """Stage `loadb`: pour ring B out of the output pipe and halt on the sentinel.

    The doubling pass is the one block whose output no later phase can be read
    back from, so it gets its own stage: 256 biased right-half sums followed by
    the `-1` that closes the ring.
    """
    c.set(WEST_COL, y, ">")
    c.horizontal(y, WEST_COL, 28)
    br(c, 28, y)
    bs(c, 29, y)
    c.set(30, y, "v")
    c.set(30, y + 1, "<")
    c.horizontal(y + 1, 30, OUT_COL)
    out(c, OUT_COL, y + 1)
    c.set(OUT_COL - 1, y + 1, "v")
    c.set(OUT_COL - 1, y + 2, ">")
    c.horizontal(y + 2, OUT_COL - 1, 31)
    c.set(31, y + 2, "X")                   # sum -> south and round again
    c.set(31, y + 3, "<")
    c.horizontal(y + 3, 31, WEST_COL)
    c.set(WEST_COL, y + 3, "^")
    c.vertical(WEST_COL, y + 3, y)
    c.set(31, y + 1, "H")                   # sentinel -> stop


#: Where phase 2's four "try the next mask" lanes all end up: a single column the
#: man climbs back to the prologue on.  Every lane turns west onto its own row
#: and then north on this column, so the merge is two `^` cells and not a
#: junction that has to be reasoned about.
LOOP_COL = 11

#: Phase 2's rows.  Named because six blocks and nine lanes have to agree about
#: them and an off-by-one row is a silently re-steered man, not a crash.
P2_HEAD, P2_MASK, P2_PEEL, P2_ROT, P2_TEST = 24, 31, 37, 42, 46
P2_SCAN, P2_SKIP, P2_MISS, P2_HIT = 50, 45, 44, 57


def _phase2(c: Circuit) -> None:
    """One lap per left-half mask, in lexicographic order, first hit wins.

    The lap reads `C`, subtracts one and sends the difference back — so the ring
    holds the winning counter when the search stops — guards it with `G`, reverses
    its bits into the backpack, peels the left values against it, rotates on to
    `MT` to turn the sum into `r + 1`, writes that into `RR` and scans ring B for
    it.  `A`, `B` and `BP` carry the ring word, the running sum and the mask, in
    that order, and nothing else is ever live.
    """
    y = P2_HEAD
    c.set(LOOP_COL, y, ">")                 # the loop-back lanes rejoin here
    c.set(LOOP_COL + 1, y, " ")
    c.set(WEST_COL, y, ">")
    c.run(14, y, "1M")                      # A = 1, B = 1
    vr(c, 16, y)                            # A = C
    c.set(17, y, "X")                       # C == 0 -> east, exhausted; C > 0 -> south
    c.set(17, y + 1, "<")
    c.set(16, y + 1, "-")                   # A = C - 1 = this lap's counter
    c.set(15, y + 1, "v")
    c.set(15, y + 2, ">")
    vs(c, 16, y + 2)                        # send the counter back: C = c
    c.set(17, y + 2, "M")                   # B = c
    vr(c, 18, y + 2)                        # A = G
    vs(c, 19, y + 2)                        # G goes straight back
    c.run(20, y + 2, "+b")                  # BP = c + 2^hL, the guarded counter
    c.set(22, y + 2, "v")
    c.set(22, y + 3, "<")
    c.horizontal(y + 3, 22, WEST_COL + 1)
    c.set(WEST_COL + 1, y + 3, "v")
    c.set(WEST_COL + 1, y + 4, ">")
    vr(c, 16, y + 4)                        # CR and GR are not this phase's
    vs(c, 17, y + 4)
    vr(c, 18, y + 4)
    vs(c, 19, y + 4)
    c.run(20, y + 4, "1M0")                 # A = 0, B = 1: the reversal's state
    _link(c, (23, y + 4), E, P2_MASK - 1, (17, P2_MASK))

    _bits(c, 20, P2_MASK, shift=True)
    c.run(21, P2_MASK, "}b0M")              # BP = the mask, A = 0, B = 0
    _link(c, (25, P2_MASK), E, P2_PEEL - 2, (18, P2_PEEL))

    _peel_sum(c, VRET_COL, P2_PEEL)         # B = the left half's sum, A = MB
    _link(c, (22, P2_PEEL - 1), N, P2_ROT - 2, (19, P2_ROT))

    _rot(c, VRET_COL, P2_ROT)               # skip the right values, stop on MT
    _link(c, (22, P2_ROT - 1), N, P2_TEST - 2, (14, P2_TEST))
