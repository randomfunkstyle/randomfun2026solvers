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

`route-check` is **not** a sound check on how many pipes exist, only on where the
drawn ones go: the runtime mints a pipe at every arrowhead whose backward cell is
a wall, and both it and ``lm.mjs analyze`` fold a stray one into its neighbour.
:func:`_mouths` counts them the runtime's way and :func:`build` calls it, which
is what caught a seventh pipe leaving the worker's **east** wall the first time
the rings were routed into the void.

## What it costs, and why the cost is a measurement

The search examines every candidate pair at most once, so it costs
`2^hL * (2^hR + 1) = 1,052,672` element comparisons at `n = 20` **whatever the
input**.  Engine-measured on the finished grid, the ceiling is **6,329,884
ticks** (`6,329,912` including the walk out to the no-solution lane) — twenty
values, all even, an odd target, so nothing is satisfiable and every left mask
is tried against all 257 words of ring B.  That is 42% of the 15,000,000 cap.
The seven public cases run 115,496 to 1,223,406 and average 288,792 at `92x153`.

Two facts hold the cost model up, and both were re-measured here rather than
inherited:

* **A scanned word costs 5.000 ticks, and a nearly full ring costs no more than
  an empty one.**  The probe measures the slope with 101 words in a 109-cell
  ring — 93% full — and gets exactly 5.000 at every spacing.  The engine shifts a
  pipe as one train, not one gap per tick, so ring B can be sized to its contents
  plus slack instead of to a throughput argument.
* **`r + 1 <= 0` must never reach the scan.**  The sentinel separates "keep
  going" from "not present" by the sign of `b ^ q`, and for `q < -1` the value
  `-1 ^ q` is *positive* — the pass would circle ring B forever.  One `X` on the
  residual is therefore load-bearing, not economy.  (The `totalR` bound the
  earlier design carried *was* pure economy and is gone: the `2^hL * (2^hR + 1)`
  ceiling already assumes every lap scans.)

`ONE` is gone from ring V for the same reason — `B = 1` comes from a digit glyph
— which is why the ring is `n + 7` words and not `n + 9`.

## Where the ticks are, measured rather than guessed

`subset_sum_mitm.walk` charges the machine word for word, and running it over the
seven public cases plus twelve random `n = 20` cases drawn inside the stated
constraints (`1 <= v <= 99999`, `t` uniform in 10%..60% of the sum) splits the
cost cleanly:

* **the ring-B scan is ~70% of a big case** — 141,131 of the near-total case's
  scanned words, at five ticks each;
* **phase 3 is ~15%** and is nearly the whole of a small case: it walks up to 256
  right masks at ~520 ticks a lap, which is why every `n <= 14` case lands
  between 115k and 165k ticks whatever else it does;
* the per-lap ring-V rotation is the rest.

Two prunes were priced against that and only one is worth anything:

* **the `totalR` upper bound is worth 3.4% and is not worth a ring word.**  Over
  the nineteen cases the scanned-word total goes 3,368,780 -> 3,252,616.  The
  low-side test the grid already does (`r + 1 <= 0` never reaches the scan) is
  what does the work; it alone takes the same total from 7,828,758 down to
  3,368,780.  Measured, so it is not re-tried.
* **sorting ring B and stopping at the first word past the residual is worth
  2.2x** on the same total, 3,368,780 -> 1,541,415.  That is the one real tick
  lever left, and its price is a sorted-doubling build: eight merge passes,
  1,022 merge steps.  At the 129 ticks a merge step measured in
  :mod:`subset_sum_merge_probe` it costs 132k a case and eats the gain; the
  question that decides it is whether a merge step can be drawn at ~30.

## No backticks

The only constants the machine needs are `1`, `8`, `256 = 1 << 8` and `2^hL`,
and every one is reachable from a single digit glyph, so the grid holds no
numeric literal at all and the vertical-pairing load error cannot arise.
"""

from __future__ import annotations

from randomfun2026solvers.circuit import E, N, Circuit
from randomfun2026solvers.subset_sum_mitm import public_cases

__all__ = [
    "BANDS",
    "debug_map",
    "main",
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
BANDS: dict[str, tuple[int, int]] = {"io": (0, 11), "v": (14, 24), "b": (27, 45)}

#: Worker interior.  ``IH`` is set by :data:`NOSOL_ROW` — the deepest thing in the
#: room is the no-solution emit, and everything below the last code block is the
#: corridor that reaches it.  One row of slack past that row is all it needs.
IW, IH = 46, 146


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

EAST_COL, WEST_COL = 38, 13


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

#: The worker sits five rows down, not twenty-four.  Everything above it is a
#: **turn band**: each of the six pipes leaves the north wall, climbs its own
#: column and turns east or west on a row of its own.  Four ring runs nest by
#: distance from the wall — the easternmost anchor turns lowest, so no run ever
#: crosses a column whose riser is still climbing — and the I/O rooms take the
#: three rows the runs do not reach, west of column 20.
#:
#: The rings themselves live **east of the worker**, in the 33x147 rectangle the
#: render showed empty for the room's whole height.  That is free area: the score
#: charges ``max(w, h)`` and the worker room is more than twice as tall as it is
#: wide, so every cell of pipe moved out of the band above it is a row off the
#: charged side and every cell moved into the void beside it is free.
#:
#:     81x169   area2 28,561      ring B serpentined across a 19-row north band
#:     91x153   area2 23,409      both rings in the void, north band down to 5
#:
#: A ring may be much longer than the words it carries: what a long ring costs is
#: latency, not ticks, and the lap only stalls if the ring is longer than the man
#: takes to walk one lap (``cells > words * ticks-per-word``).  Ring V carries 28
#: words at five ticks each, so it is safe to about 140 cells; ring B carries 257,
#: so it is safe to well over a thousand.
WX, WY = 1, 6
GW, GH = 92, WY + IH + 1

#: The four ring anchors, in worker-interior columns, paired with the row each
#: one turns on.  Sorted east to west: the easternmost turns nearest the wall.
_TURN_ROW = {BFWD_COL: 3, BRET_COL: 2, VFWD_COL: 1, VRET_COL: 0}


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
    if stage in ("p2", "full"):
        _link(c, exit_, E, 22, (14, P2_HEAD))
        _phase2(c)
        _nosol(c)
        if stage == "p2":
            _hit_probe(c)
            return c
        _phase3(c)
        _emit(c)
        return c
    if stage == "loadb":
        _link(c, exit_, E, 22, (14, 24))
        _spill_b(c, 24)
        return c
    raise ValueError(f"unknown stage {stage!r}")


#: Every room's outer rectangle, `(x, y, w, h)`, in the order :func:`build` draws
#: them.  Taken from the floor plan and not from the glyphs, because `|` and `-`
#: are a pipe's body as well as a wall and the finished grid cannot tell them
#: apart — which is exactly what lets a phantom pipe hide.
def _boxes() -> list[tuple[int, int, int, int]]:
    return [
        (WX - 1, WY - 1, IW + 2, IH + 2),               # the worker
        (WX + IN_COL - 1, 0, 3, 3),                     # I
        (WX + OUT_COL - 1, 0, 3, 3),                    # O
        (54, 6, 8, 6),                                  # ring V's relay
        (49, 20, 8, 6),                                 # ring B's relay
    ]


def _mouths(rows: list[str], *, expect: int) -> None:
    """Count pipe mouths the way the **runtime** does, and refuse a surprise.

    An arrowhead whose backward cell is a room border is the start of a pipe
    whether or not one was meant.  `route-check.mjs` and `lm.mjs analyze` both
    fold a stray one into its neighbour and report the pipes that were drawn;
    the engine builds it.  Six is the whole design: I, O, and two per ring.
    """
    from randomfun2026solvers.brackets_men import pipe_mouths, wall_cells

    found = pipe_mouths([r.rstrip() for r in rows], wall_cells(_boxes()))
    if len(found) != expect:
        listed = ", ".join(f"{ch!r}@{c}" for c, ch in sorted(found))
        raise ValueError(f"{len(found)} pipe mouths, wanted {expect}: {listed}")


def build(stage: str = "full") -> list[str]:
    """Worker room, I/O rooms, two relays and the four ring pipes.

    The whole of the grid outside the worker room is drawn here, and all of it
    is either the five-row turn band above the room or the void east of it.
    """
    from randomfun2026solvers.dataflow_relay import relay
    from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

    g = Circuit(GW, GH)
    stamp(g, WX, WY, worker(stage).rows())
    walls(g, WX, WY, IW, IH)

    # ── I/O: two rooms in the band's top three rows, pipes on the bottom two ─
    stamp(g, WX + IN_COL - 1, 0, ["+-+", "|I|", "+-+"])
    draw_pipe(g, [(WX + IN_COL, 3), (WX + IN_COL, WY - 2)])
    stamp(g, WX + OUT_COL - 1, 0, ["+-+", "|O|", "+-+"])
    draw_pipe(g, [(WX + OUT_COL, WY - 2), (WX + OUT_COL, 3)])

    vf, vr_, bf, br_ = (WX + col for col in (VFWD_COL, VRET_COL, BFWD_COL, BRET_COL))
    tvf, tvr = _TURN_ROW[VFWD_COL], _TURN_ROW[VRET_COL]
    tbf, tbr = _TURN_ROW[BFWD_COL], _TURN_ROW[BRET_COL]
    wall = WY - 2                       # the last band row, against the north wall

    # ── ring V: relay just east of the room, 97 cells for 28 words ───────────
    #
    # Descent columns nest the same way the turn rows do — ``BFWD 49 < BRET 50 <
    # VFWD 51 < VRET 52`` — because a run on row `y` may only cross a descent
    # column whose own turn row is *below* `y`.  `Circuit.set` refuses an
    # overwrite, so a crossing that was got wrong is a build error rather than a
    # silently re-steered pipe.
    #
    # **Column 48 is reserved and must stay empty.**  It is the column against
    # the worker's east wall, so an east-pointing arrowhead there is the mouth of
    # a seventh pipe leaving the worker — the runtime builds it, `route-check`
    # and `analyze` both fold it away, and every `s` in the `b` band would then
    # bind by a distance that includes an east-wall anchor.  The first draft of
    # this block descended at 48 and :func:`_mouths` is what found it.
    stamp(g, 54, 6, relay(6, 4))                        # cols 54..61, rows 6..11
    n = draw_pipe(g, [(vf, wall), (vf, tvf), (51, tvf), (51, 13), (57, 13), (57, 12)])
    n += draw_pipe(g, [(53, 8), (52, 8), (52, tvr), (vr_, tvr), (vr_, wall)])
    if n < V_CAP:
        raise ValueError(f"ring V holds {n} words, need >= {V_CAP}")

    # ── ring B: 257 stored sums serpentined down the void, five rows of it ───
    stamp(g, 49, 20, relay(6, 4))                       # cols 49..56, rows 20..25
    m = draw_pipe(g, [(bf, wall), (bf, tbf), (49, tbf), (49, 17), (52, 17), (52, 19)])
    ret = [(57, 22)]
    ret += _serpentine(58, 90, [22, 24, 26, 28, 30])[1:]
    ret += [(91, 30), (91, 16), (50, 16), (50, tbr), (br_, tbr), (br_, wall)]
    m += draw_pipe(g, ret)
    if m < B_CAP:
        raise ValueError(f"ring B holds {m} words, need >= {B_CAP}")
    _mouths(g.rows(), expect=6)
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
P2_HEAD, P2_MASK, P2_PEEL, P2_ROT, P2_TEST = 23, 29, 35, 40, 44
#: The scan gets a band of its own, forty rows clear of everything else.  The
#: four lanes it fans out into were what defeated the first attempt at a tight
#: layout; there is no area pressure on this problem, so they get room.
SCAN_TOP, SCAN_SOUTH, SCAN_NORTH = 50, 35, 34
MISS_ROW, HIT_ROW, HIT_COL, P3_HEAD = 46, 55, 12, 56


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

    # ── the residual, and the only test that has to happen before the scan ───
    y = P2_TEST
    c.run(14, y, "+N")                      # A = -(t+1) + s, negated: r + 1
    c.set(16, y, "M")                       # B = r + 1, the scan's query
    vr(c, 17, y)                            # the old RR, discarded
    c.set(18, y, "W")                       # A = r + 1 again, B = the old RR
    vs(c, 19, y)                            # RR = r + 1
    c.set(20, y, "M")                       # B = r + 1
    c.set(21, y, "X")                       # r+1 > 0 -> scan; otherwise next lap
    # `q <= 0` must never reach the scan: `-1 ^ q` goes *positive* for q < -1, so
    # the sentinel would stop separating "keep going" from "not present" and the
    # pass would circle ring B forever.
    c.set(21, y - 1, "<")                   # r + 1 < 0
    c.set(22, y, "^")                       # r + 1 == 0
    c.set(22, y - 1, "<")
    c.horizontal(y - 1, 21, LOOP_COL)
    c.set(LOOP_COL, y - 1, "^")

    _scan(c)


def _scan(c: Circuit) -> None:
    """The probe's gadget, and the four lanes it fans out into.

    Ten cells, two words a lap, five ticks a word — measured, not modelled, and
    re-measured here at 101 words in a 109-cell ring, which is what says a nearly
    full ring costs no more than an empty one: the engine shifts a pipe as one
    train, not one gap per tick.  `~` leaves `B` alone and both operands are
    non-negative, so one `X` separates all three outcomes at once::

        A == 0   straight   the residual is a right-half sum
        A  > 0   turn       keep scanning
        A  < 0   turn back  that was the sentinel: not present

    Every lane leaves the band before turning, and the only cells two lanes ever
    share are blanks, which a man walks straight through.
    """
    south, north, top = SCAN_SOUTH, SCAN_NORTH, SCAN_TOP
    # Entry comes down a column east of every lane and turns in at the top of the
    # south side; the north side's `X` turns into the same cell, so the loop and
    # the way in share one glyph instead of needing a junction.
    c.set(21, P2_TEST + 1, ">")
    c.horizontal(P2_TEST + 1, 21, south + 2)
    c.set(south + 2, P2_TEST + 1, "v")
    c.vertical(south + 2, P2_TEST + 1, top - 1)
    c.set(south + 2, top - 1, "<")
    c.set(south + 1, top - 1, " ")
    c.set(south, top - 1, "v")

    br(c, south, top)
    bs(c, south, top + 1)
    c.set(south, top + 2, "~")              # A = b ^ q; B untouched, so q survives
    c.set(south, top + 3, "X")
    c.set(north, top + 3, "^")
    br(c, north, top + 2)
    bs(c, north, top + 1)
    c.set(north, top, "~")
    c.set(north, top - 1, "X")

    # ── not found: both lanes climb clear of the band before turning west ────
    # They have to: a westbound run at ring level would be crossed by the found
    # lanes' descent, and a corridor crossing a *turn* glyph re-steers the man.
    # Above the band the only cells they share with anything are blanks.
    c.set(north - 1, top - 1, "^")
    c.vertical(north - 1, top - 1, MISS_ROW)
    c.set(north - 1, MISS_ROW, "<")
    c.horizontal(MISS_ROW, north - 1, LOOP_COL)
    c.set(LOOP_COL, MISS_ROW, "^")
    c.set(south + 1, top + 3, "^")
    c.vertical(south + 1, top + 3, MISS_ROW + 1)
    c.set(south + 1, MISS_ROW + 1, "<")
    c.horizontal(MISS_ROW + 1, south + 1, LOOP_COL)
    c.set(LOOP_COL, MISS_ROW + 1, "^")
    for row in range(P2_HEAD + 1, MISS_ROW + 2):
        c.set(LOOP_COL, row, "^")

    # ── found: west below the not-found rows, then down the hit column ───────
    c.set(north, top - 2, "<")
    c.horizontal(top - 2, north, HIT_COL)
    c.set(HIT_COL, top - 2, "v")
    c.set(south, top + 4, "v")
    c.vertical(south, top + 4, HIT_ROW)
    c.set(south, HIT_ROW, "<")
    c.horizontal(HIT_ROW, south, HIT_COL)
    c.set(HIT_COL, HIT_ROW, "v")
    for row in range(top - 1, P3_HEAD):     # blanks, not `v`: the not-found rows
        if c.free(HIT_COL, row):            # cross this column and must not turn
            c.set(HIT_COL, row, " ")
    c.set(HIT_COL, P3_HEAD, ">")


#: Where the exhausted lane and the no-solution answer live: far east of every
#: lane, far south of every block.  Nothing else is within ten columns of it.
#:
#: ``NOSOL_ROW`` **sets the machine's charged side**, because the room is taller
#: than it is wide and everything below the last code block is corridor reaching
#: this row.  So does every row constant above it: the phases are stacked, and
#: pulling any one up carries the whole stack with it.
#:
#: The stack has been compacted greedily, top down, each constant pulled up as
#: far as it will build **and still pass every public case on the engine**.  Two
#: passes, because the first held :data:`SCAN_TOP` and ``P2_HEAD`` fixed:
#:
#:     81x202   area2 40,804   avg 292,126   worst 6,329,912
#:     81x179   area2 32,041   avg 290,418   worst 6,315,279   pass 1
#:     81x169   area2 28,561   avg 288,808   worst 6,267,839   pass 2
#:
#: Ticks improve rather than trade, because a row removed from a corridor is also
#: a row the man does not walk — and *which* corridor decides by how much.  The
#: trailing walk below the last block was 51 rows for 42 ticks; pass 1's phase
#: rows were 23 for 14,633 (636 a row); pass 2's were 10 for 47,440 (**4,744 a
#: row**), because ``SCAN_TOP`` and ``HIT_ROW`` sit deepest inside the per-lap
#: walk and there are 4,096 laps.
#:
#: **`SCAN_TOP` was the whole of the second pass.**  Pass 1 could close only 5 of
#: the 17 rows between ``MISS_ROW`` and ``HIT_ROW`` before the hit lane met phase
#: 2's ``X`` at (35, 63) — and that ``X`` is the scan band, which pass 1 never
#: moved.  Dropping ``SCAN_TOP`` 60 -> 50 freed ``HIT_ROW`` for 8 more rows.  The
#: comment that band carried, that it is "forty rows clear of everything else"
#: with "no area pressure on this problem", was true when written and had become
#: the binding constraint.
#:
#: **Verifying in the loop is not optional, and it cost exactly one row.**  A
#: build-only greedy reaches ``NOSOL_ROW`` 154 and the grid assembles cleanly at
#: 81x178 — then fails three of the seven cases (`tiny warm up`,
#: `last-index-required`, `near-total-sum, 20 values`).  South of the emit block
#: the corridor is unobstructed but the answer stops binding the ``O`` pipe, and
#: nothing about the geometry says so.
#:
#: **This row is now the whole of the charged side, and cells no longer bound it.**
#: With both rings moved out of the band above the room and into the void beside
#: it, the grid is `92x153` and every one of those 153 rows is the room plus its
#: two walls plus the five-row turn band.  The cell-count floor says the same
#: thing from the other direction::
#:
#:     I room 9 + O room 9 + two turnarounds 48 + ring V 30 + ring B 262 = 358
#:     worker room 48 x 148                                             = 7,104
#:     total                                                              7,462   -> side >= 87
#:
#: So 87 is what the *area* allows and 153 is what the *row count* forces: the
#: gap is entirely the worker's 146 interior rows, of which roughly half are the
#: one-row corridor every :func:`_link` spends turning the man around.  Nothing
#: outside the room can pay that back.  The two ways in are (a) a boustrophedon
#: stack, where the westbound return leg carries the next block's glyphs instead
#: of being blank — that removes the corridor row *and* its 25-column walk, but
#: every gadget with an `X`/`x`/`d` turn has to be redrawn mirrored, because
#: chirality is relative to the heading; or (b) two worker rooms side by side
#: with ring V threaded through both, which needs a baton word and an idle relay
#: loop in whichever room is not the active phase.  Width is free either way:
#: the grid is 92 wide against a 153 side.
NOSOL_COL, NOSOL_ROW = 44, 145


def _nosol(c: Circuit) -> None:
    """`C` reached zero with no hit: every left mask has been tried.  Emit `0`."""
    c.horizontal(P2_HEAD, 17, NOSOL_COL)
    c.set(NOSOL_COL, P2_HEAD, "v")
    c.vertical(NOSOL_COL, P2_HEAD, NOSOL_ROW)
    c.set(NOSOL_COL, NOSOL_ROW, "<")
    c.horizontal(NOSOL_ROW, NOSOL_COL, 8)
    c.set(8, NOSOL_ROW, "0")
    c.set(7, NOSOL_ROW, " ")
    out(c, OUT_COL, NOSOL_ROW)
    c.set(OUT_COL - 1, NOSOL_ROW, "H")


def _hit_probe(c: Circuit) -> None:
    """Stage `p2`: answer `1` if phase 2 found a residual and `0` if it did not.

    Phase 2 is the block that decides whether the machine answers at all, and
    "does a subset exist" is exactly the question it settles.  Checking it on its
    own separates a broken search from a broken read-out, which is the pair the
    earlier builds could never tell apart.
    """
    c.set(WEST_COL, P3_HEAD, "1")
    c.set(WEST_COL + 1, P3_HEAD, "v")
    c.set(WEST_COL + 1, P3_HEAD + 1, "<")
    c.horizontal(P3_HEAD + 1, WEST_COL + 1, OUT_COL)
    out(c, OUT_COL, P3_HEAD + 1)
    c.set(OUT_COL - 1, P3_HEAD + 1, "H")


#: Phase 3's rows.  It is phase 2's lap with `CR` driving it, so it reuses every
#: block; only the prologue and the final comparison differ.
P3_MASK, P3_SKIP, P3_PEEL, P3_TEST = 61, 67, 71, 76


def _phase3(c: Circuit) -> None:
    """One lap per right-half mask, lexicographic, first hit wins.

    Phase 2 proved the residual is a right-half sum, so this always terminates on
    a match.  The accumulator starts at **1**, not 0, because `RR` holds `r + 1`:
    biasing both sides is one glyph and saves the comparison a correction.
    """
    y = P3_HEAD
    c.run(14, y, "1M")                      # A = 1, B = 1
    vr(c, 16, y)                            # C and G belong to phase 2
    vs(c, 17, y)
    vr(c, 18, y)
    vs(c, 19, y)
    vr(c, 20, y)                            # A = CR
    c.set(21, y, "X")                       # CR > 0 -> south; == 0 is unreachable
    c.set(22, y, "H")
    c.set(21, y + 1, "<")
    c.set(20, y + 1, "-")                   # A = CR - 1 = this lap's counter
    c.set(19, y + 1, "v")
    c.set(19, y + 2, ">")
    vs(c, 20, y + 2)                        # send the counter back: CR = cr
    c.set(21, y + 2, "M")                   # B = cr
    vr(c, 22, y + 2)                        # A = GR = 256
    vs(c, 23, y + 2)                        # GR goes straight back
    c.run(24, y + 2, "+b1M0")               # BP = cr + 256, A = 0, B = 1
    _link(c, (29, y + 2), E, P3_MASK - 2, (17, P3_MASK))

    _bits(c, 20, P3_MASK, shift=True)
    c.run(21, P3_MASK, "}b")                # BP = the mask; B is already 1
    _link(c, (23, P3_MASK), E, P3_SKIP - 2, (19, P3_SKIP))

    _rot(c, VRET_COL, P3_SKIP)              # skip the left values, stop on MB
    _link(c, (22, P3_SKIP - 1), N, P3_PEEL - 2, (18, P3_PEEL))

    _peel_sum(c, VRET_COL, P3_PEEL)         # B = 1 + the right half's sum
    _link(c, (22, P3_PEEL - 1), N, P3_TEST - 2, (14, P3_TEST))

    y = P3_TEST
    vr(c, 14, y)                            # A = RR = r + 1
    vs(c, 15, y)
    c.set(16, y, "-")                       # A = (r+1) - (1+sum) = r - sum
    c.set(17, y, "X")                       # 0 -> east, the winning right mask
    c.set(17, y - 1, "<")                   # > 0 and < 0 alike: try the next one
    c.horizontal(y - 1, 17, LOOP_COL)
    c.set(LOOP_COL, y - 1, "^")
    c.set(17, y + 1, "<")
    c.horizontal(y + 1, 17, LOOP_COL)
    c.set(LOOP_COL, y + 1, "^")
    for row in range(P3_HEAD + 1, y + 2):
        c.set(LOOP_COL, row, "^")
    c.set(LOOP_COL, P3_HEAD, ">")


#: Emit's rows.  Three laps: count the bits and answer `k`, then the left half's
#: chosen values, then the right half's.  Left before right **is** increasing
#: index order, which is why no combined mask and no output buffer are needed.
E1_HEAD, E1_COUNT, E1_EMIT, E1_MB, E1_MT = 80, 83, 89, 94, 98
E2_HEAD, E2_MASK, E2_ROT, E2_PEEL, E2_MT, E2_RR = 103, 106, 112, 116, 121, 125
E3_HEAD, E3_MASK, E3_SKIP, E3_PEEL = 128, 131, 137, 142


def _emit(c: Circuit) -> None:
    """Read the answer back out of the ring the search left behind.

    `C` and `CR` hold the winning counters, and a population count is invariant
    under bit reversal, so `k` is `popcount(C) + popcount(CR)` with no reversal
    at all — and the two fit in one backpack as `C * 256 + CR`, because `C < 2^12`
    and `CR < 256` cannot overlap.  That is one lap, and the two emit laps after
    it each reverse one counter and peel one half.
    """
    _link(c, (18, P3_TEST), E, E1_HEAD - 2, (14, E1_HEAD))
    y = E1_HEAD
    vr(c, 14, y)                            # A = C
    vs(c, 15, y)
    c.run(16, y, "M8W{M")                   # B = C << 8
    vr(c, 21, y)                            # G, rotated
    vs(c, 22, y)
    vr(c, 23, y)                            # A = CR
    vs(c, 24, y)
    c.run(25, y, "+b1M0")                   # BP = C*256 + CR, A = 0, B = 1
    _link(c, (30, y), E, E1_COUNT - 2, (17, E1_COUNT))

    _bits(c, 20, E1_COUNT, shift=False)     # A = the number of chosen indices
    _link(c, (21, E1_COUNT), E, E1_EMIT - 2, (14, E1_EMIT))
    y = E1_EMIT
    c.set(14, y, "v")
    c.set(14, y + 1, "<")
    c.horizontal(y + 1, 14, OUT_COL)
    out(c, OUT_COL, y + 1)                  # answer k
    c.set(OUT_COL - 1, y + 1, "v")
    c.set(OUT_COL - 1, y + 2, ">")
    c.horizontal(y + 2, OUT_COL - 1, VRET_COL)
    vr(c, VRET_COL, y + 2)                  # GR, rotated
    vs(c, VRET_COL + 1, y + 2)
    _link(c, (VRET_COL + 2, y + 2), E, E1_MB - 2, (19, E1_MB))
    _rot(c, VRET_COL, E1_MB)                # on to MB
    _link(c, (22, E1_MB - 1), N, E1_MT - 2, (19, E1_MT))
    _rot(c, VRET_COL, E1_MT)                # on to MT
    _link(c, (22, E1_MT - 1), N, E2_HEAD - 3, (14, E2_HEAD))

    # ── lap 2: reverse C and emit the left half's chosen values ──────────────
    y = E2_HEAD
    c.run(14, y, "1M")
    vr(c, 16, y)                            # RR, the last word of lap 1
    vs(c, 17, y)
    vr(c, 18, y)                            # A = C
    vs(c, 19, y)
    c.set(20, y, "M")                       # B = C
    vr(c, 21, y)                            # A = G
    vs(c, 22, y)
    c.run(23, y, "+b1M0")                   # BP = C + 2^hL, A = 0, B = 1
    _link(c, (28, y), E, E2_MASK - 2, (17, E2_MASK))
    _bits(c, 20, E2_MASK, shift=True)
    c.run(21, E2_MASK, "}b")                # BP = the left mask
    _link(c, (23, E2_MASK), E, E2_ROT - 2, (14, E2_ROT))
    y = E2_ROT
    vr(c, 14, y)                            # CR and GR, rotated
    vs(c, 15, y)
    vr(c, 16, y)
    vs(c, 17, y)
    _link(c, (18, y), E, E2_PEEL - 3, (18, E2_PEEL))
    _peel_emit(c, VRET_COL, E2_PEEL, OUT_COL)
    _link(c, (22, E2_PEEL - 1), N, E2_MT - 2, (19, E2_MT))
    _rot(c, VRET_COL, E2_MT)                # on to MT
    _link(c, (22, E2_MT - 1), N, E2_RR - 2, (14, E2_RR))
    y = E2_RR
    vr(c, 14, y)                            # RR, rotated
    vs(c, 15, y)
    _link(c, (16, y), E, E3_HEAD - 2, (14, E3_HEAD))

    # ── lap 3: reverse CR and emit the right half's chosen values ────────────
    y = E3_HEAD
    c.run(14, y, "1M")
    vr(c, 16, y)                            # C and G, rotated
    vs(c, 17, y)
    vr(c, 18, y)
    vs(c, 19, y)
    vr(c, 20, y)                            # A = CR
    vs(c, 21, y)
    c.set(22, y, "M")                       # B = CR
    vr(c, 23, y)                            # A = GR = 256
    vs(c, 24, y)
    c.run(25, y, "+b1M0")                   # BP = CR + 256, A = 0, B = 1
    _link(c, (30, y), E, E3_MASK - 2, (17, E3_MASK))
    _bits(c, 20, E3_MASK, shift=True)
    c.run(21, E3_MASK, "}b")                # BP = the right mask
    _link(c, (23, E3_MASK), E, E3_SKIP - 2, (19, E3_SKIP))
    _rot(c, VRET_COL, E3_SKIP)              # skip the left values, stop on MB
    _link(c, (22, E3_SKIP - 1), N, E3_PEEL - 3, (18, E3_PEEL))
    _peel_emit(c, VRET_COL, E3_PEEL, OUT_COL)
    c.set(22, E3_PEEL - 1, "H")             # MT: every chosen value is out


def debug_map() -> "DebugMap":
    """The overlay that says what each band of the grid is for.

    A generated `.man` carries no comments, so this is the only thing that knows
    a cell's meaning; it is written in the same invocation as the grid so the two
    cannot drift.
    """
    from randomfun2026solvers.man_debug import DebugMap

    dbg = DebugMap("subset-sum — meet in the middle on two pipe rings")
    ox, oy = WX, WY - 2                     # two blank rows are stripped on render
    dbg.region("load", ox, oy, IW, 22,
               note="Read n, derive hL = n-8, lay ring V's header GL,GL,256,256, "
                    "append the values with MB between the halves, and double "
                    "ring B by each right value until it holds all 256 sums.",
               tags=["setup"])
    dbg.region("phase 2 — left masks", ox, oy + P2_HEAD, IW, P2_TEST - P2_HEAD + 4,
               note="One lap per left mask, counter walked DOWN so bitrev(C) "
                    "visits index sets in lexicographic order. Peel, reach MT, "
                    "turn the sum into r+1, write RR, scan.",
               tags=["compute"])
    dbg.region("scan station", ox + SCAN_NORTH, oy + SCAN_TOP - 1, 2, 5,
               note="r s ~ X twice a lap: ten cells, two words, five ticks a "
                    "word measured on the engine. A==0 hit, A>0 keep going, "
                    "A<0 sentinel. Never entered with r+1 <= 0.",
               tags=["compute", "hot"])
    dbg.region("phase 3 — right masks", ox, oy + P3_HEAD, IW, P3_TEST - P3_HEAD + 3,
               note="The same lap driven by CR, comparing the biased right-half "
                    "sum against RR.",
               tags=["compute"])
    dbg.region("emit", ox, oy + E1_HEAD, IW, E3_PEEL - E1_HEAD + 4,
               note="popcount(C*256 + CR) answers k without a bit reversal, then "
                    "one lap emits the left half's chosen values and one the "
                    "right — which is already increasing index order.",
               tags=["output"])
    name, values, target, _ = public_cases()[1]
    dbg.scenario(name, " ".join(str(v) for v in [len(values), *values, target]),
                 0, 4000, watch=["phase 2 — left masks"],
                 note="Two subsets sum to 300; the lex pin picks {0,1}.")
    return dbg


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--man", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--stage", default="full",
                        choices=("full", "p2", "load", "loadb"),
                        help="'load'/'loadb' spill a ring instead of solving; "
                             "'p2' answers only whether a subset exists")
    args = parser.parse_args(argv)

    rows = build(args.stage)
    dbg = debug_map()
    for path in (args.man, args.html, args.json):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dbg.write_html(rows, args.html)
    dbg.write_json(args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
