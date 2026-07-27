"""`reverse-a-list` as a pair ring: 2.14x fewer rotations in the same box.

## Why the shipped ring is slow

`reverse_list_ast` stores one value per ring slot and, to emit the last value of
a `k`-long ring, rotates `k-1` values.  That is `n(n-1)/2` rotations — **120 at
`n = 16`** — and rotations are the whole cost of the machine.

## The pair ring

A little man durably holds one value in `B`, so he can push a *pair* into the
ring **already reversed**: `r M r s W s` sends `x1` then `x0`.  Reading that
pair back out later yields `x1, x0` — the reversal of a two-element block is
free at load time.  The emit phase then only has to reverse the order of the
`g = n/2` pairs, and it never has to reach further than *two* slots from the
head.  Rotations drop to `g(g-1) = n(n-2)/4`, **56 at `n = 16`**.

The ring carries its own header: its head slot holds `V`, the number of values
still stored.  One emit step is

    r          V            read the header
    b m m      BP = V-2     rotations for this step
    -          A = V-2      (B = 2)
    X          three-way branch on V-2
    s          re-send the header
    <rotate>   BP times { r ; s }
    r s r s    read the surfaced pair and send both to the output pipe

`V` walks `n, n-2, …` down to `2` (even `n`) or `1` (odd `n`), and the step
leaves the ring exactly where it found it — nothing is ever rotated back to a
canonical position, and `V == 0` both ends the round and leaves the ring empty
for the next one.

## Odd `n` costs one glyph, not a code path

Group the values as `(x0), (x1,x2), (x3,x4), …` when `n` is odd, so the
**singleton is the group loaded first** and therefore the group emitted last.
`A = V-2` is then `-1` exactly on that final step, and `X` — which turns
clockwise on positive, anticlockwise on negative and goes straight on zero — is
a free three-way branch: `V>2` and `V==2` both run the ordinary two-value emit,
`V==1` takes a short arm that sends a literal `0` as the header and emits one
value.  The prologue picks the grouping with `/`:

    2 M r s / b W X

`/` yields `n>>1` in `A` and `n&1` in `B` **in one glyph**, so `g` and the
parity arrive together; `W` brings the parity into `A` for the `X` that decides
whether one value is stored on its own before the pair loop starts.

## Cost

Per round, with `g = n>>1`:

    load    ~4 ticks a value        (r M r s W s + loop control, per pair)
    rotate  ~4 ticks a rotation     g(g-1) rotations
    emit    ~6 ticks a pair

which is `~2g^2 + 14g + c` against the shipped `4n^2 + 34n + 7`.  Over the eight
public cases that is an average near 220 ticks against the measured 1012.75 of
`solutions/reverse-a-list/000000000513410_reverse-a-list.man`.

## The judge runs twenty cases; we measure eight

**A local average is not a score.**  The three archived submissions carry both
numbers, and the private cases are consistently the longer ones:

    judge avgTicks   local public avg   ratio
        1585              1012.75        1.565
        1589              1016.75        1.562
        1589              1016.75        1.562

:data:`PRIVATE_TICK_RATIO` is that 1.563.  Multiply a local public average by it
before squaring a side against a leaderboard number, or a projection lands ~36%
optimistic — which is the difference between clearing the live 118,401 and tying
it.  At the pair ring's ~220 local average the judge should see ~344, so

    side 18 -> 111,500   a tie, not a win
    side 16 ->  88,100
    side 15 ->  77,400

**Side 16 is the first side that is actually worth submitting.**  The ratio is
measured on a rotation machine and both the numerator and denominator scale with
`n^2`, so it transfers to the pair ring; re-measure it if the algorithm's shape
ever stops being quadratic.

## Top five is out of reach for this design, and that is a result

The I/O floor is `2n + c` a round because the judge withholds the next list
until the current one is printed, so the eight public cases cannot average below
~35 ticks.  `13,764 / 35 = 396` puts the leader's side at **19 or less** with a
near-linear machine.  Top five (17,508) needs side 14 at `t ~ 89`, or side 12 at
`t ~ 122` — under any rotation curve, including this one.  **The pair ring
cannot reach top five; only a linear machine can.**  Five linear designs were
priced and rejected, and none should be re-tried without new information:

* **One pipe per value (the 9n comb).**  Genuinely `9n + c`, ~143 local average,
  because the drainer's eastward traverse hides under the slower load.  But
  sixteen anchors force ~22 columns while loader (7 rows) + pipes + drainer
  (9 rows) force ~20 — side 22, ~69k local, no better than the pair ring for far
  more machine.
* **Pairs into eight pipes.**  The pair work needs ~11 rows a column, so the
  width halves and the height doubles.  Area and `max(w, h)` both unchanged.
* **Arithmetic packing.**  Values are +-1e6 = 21 bits, so only three fit a 64-bit
  word.  `w = w*K + x` has three live values against `A`, `B` and a *write-only*
  `BP`, and `r` always clobbers `A` — so the accumulator must occupy `B` at the
  receive, which evicts `K`.  The base has to be reloaded every pair and costs
  more than the packing saves.
* **Men as storage.**  Parked men release **FIFO, never LIFO**: creation order is
  monotone (the right child keeps the parent's slot, the left child is the newest
  runner), and a blocked `s` resolves in creation order, so position on the grid
  does not decide who sends first.  This is a substrate fact, not a fact about
  reversal.
* **Staggered pipe lengths so `R` sees them ready in reverse.**  Lengths must
  fall faster than the loader's per-value cost, which is ~750 pipe cells.  And
  crossing sixteen pipes to make *reading order* reverse is not planar, so the
  cheap version does not exist either.

## Where the glyphs may stand

Every pipe in `reverse_list_ast`'s worker anchors on the room's **east** wall,
so the `|Δx|` term is identical for all four and the binding depends only on the
row.  In the 10x11 worker's own coordinates:

    rows 0-4    r = input    s = ring      load
    rows 5-8    r = ring     s = ring      rotate
    rows 9-10   r = ring     s = output    emit

which is exactly where that machine puts its blocks: its load loop's body sits
on row 2, its rotate loop's body on row 8, and its emit `r`/`s` pair on row 9.

There is no band where `r` is the input pipe and `s` is the output pipe, which
is exactly why the odd value must not be sent straight to the output: it goes
into the ring as the first group instead.  :func:`zone_map` computes the bands
for any anchor set — moving one anchor off the east wall makes the `x` term stop
cancelling and turns the bands into quadrants.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Geom", "binding", "zone_map", "AST_WORKER", "PRIVATE_TICK_RATIO", "judge_score"]

#: The judge averages twenty cases; `scoring.score_program` averages the eight
#: public ones, and the private twelve are longer.  Measured from the three
#: archived `reverse-a-list` submissions, whose `.descr` files record both the
#: judge's `avgTicks` and our local public average — 1585/1012.75, 1589/1016.75
#: and 1589/1016.75, which agree to 0.2%.
PRIVATE_TICK_RATIO = 1.563


def judge_score(side: int, local_avg_ticks: float) -> float:
    """What the leaderboard should show for a grid we measured locally.

    Comparing a raw local average against a leaderboard number is how a
    regression gets reported as a win: it is ~36% optimistic on this problem.
    """
    return side * side * local_avg_ticks * PRIVATE_TICK_RATIO


@dataclass
class Geom:
    """Worker-room interior size and each pipe's anchor cell.

    Anchors are given in interior coordinates, so ``(-1, k)`` is the west wall,
    ``(w, k)`` the east wall, ``(k, -1)`` the north wall and ``(k, h)`` the
    south wall.  The anchor is the pipe cell that touches the room, which is
    what "nearest" is measured to.
    """

    w: int = 14
    h: int = 12
    a_input: tuple[int, int] = (-1, 5)
    a_ring_in: tuple[int, int] = (14, 5)
    a_ring_out: tuple[int, int] = (7, -1)
    a_output: tuple[int, int] = (7, 12)


#: `reverse_list_ast`'s worker: interior 10x11, all four anchors on the east
#: wall, which is what collapses its binding map to horizontal bands.
AST_WORKER = Geom(
    w=10,
    h=11,
    a_input=(10, -1),
    a_ring_out=(10, 7),
    a_ring_in=(10, 9),
    a_output=(10, 10),
)


def _dist(g: Geom, x: int, y: int) -> dict[str, int]:
    return {
        name: abs(x - ax) + abs(y - ay)
        for name, (ax, ay) in (
            ("input", g.a_input),
            ("ring_in", g.a_ring_in),
            ("ring_out", g.a_ring_out),
            ("output", g.a_output),
        )
    }


def binding(g: Geom, x: int, y: int) -> tuple[str, str]:
    """(what `r` reads at this cell, what `s` writes at this cell).

    Ties break by the pipes' reading order over the segment attached to the
    room — top to bottom, then left to right — so the anchor nearer the top-left
    wins an equal distance.
    """
    d = _dist(g, x, y)
    order = {"input": g.a_input, "ring_in": g.a_ring_in, "ring_out": g.a_ring_out, "output": g.a_output}

    def pick(a: str, b: str) -> str:
        if d[a] != d[b]:
            return a if d[a] < d[b] else b
        return a if (order[a][1], order[a][0]) <= (order[b][1], order[b][0]) else b

    return pick("input", "ring_in"), pick("ring_out", "output")


def zone_map(g: Geom) -> str:
    """One character a cell: which of the four block families may stand there."""
    code = {
        ("input", "ring_out"): "L",  # load: read input, store to the ring
        ("ring_in", "ring_out"): "R",  # rotate: ring to ring
        ("ring_in", "output"): "E",  # emit: ring to output
        ("input", "output"): "T",  # input straight to output
    }
    return "\n".join(
        "".join(code[binding(g, x, y)] for x in range(g.w)) for y in range(g.h)
    )


if __name__ == "__main__":  # pragma: no cover - a layout probe
    print("quadrant anchors (two incoming on side walls, two outgoing on ends):")
    print(zone_map(Geom()))
    print()
    print("reverse_list_ast's worker (all four anchors on the east wall):")
    print(zone_map(AST_WORKER))
