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

__all__ = ["Geom", "binding", "zone_map", "AST_WORKER"]


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
