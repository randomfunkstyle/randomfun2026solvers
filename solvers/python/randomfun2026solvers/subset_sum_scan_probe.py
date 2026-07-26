#!/usr/bin/env python3
"""The one number `subset_sum_mitm`'s feasibility rests on, measured not modelled.

The meet-in-the-middle design's whole cost is `2^hL * (2^hR + 1)` comparisons of a
word of ring `B` against a query — 1,052,672 of them at `n = 20`, whatever the
input.  Against a 15,000,000-tick cap that is a machine only if a comparison
costs about five ticks.  This probe is the smallest grid that does exactly that
comparison, so the figure can be measured on the engine instead of argued.

**What it computes.** Input is `k`, then `k` values, then a query `q`; output is
`1` if `q` is one of the values and `0` if not.  The values live in a pipe ring
biased by one (`v + 1`, so no stored word is ever `0`) behind a `-1` sentinel.

**The gadget being measured.**  The query stays in `B` — the only register a
receive does not clobber — and the station walks

    r  s  ~  X

`~` is XOR, and it touches neither `B` nor the sign of a non-negative result, so
one `X` resolves all three outcomes at once::

    A == 0   straight   the query is in the ring
    A  > 0   clockwise  keep scanning
    A  < 0   ccw        that was the -1 sentinel: not present

Laid as a two-column clockwise loop, five cells a side, the two turning corners
are both `X` and one lap carries **two** words.  Ten cells, two words.  The
sentinel is re-sent *before* the branch, so a failed pass leaves the ring exactly
where it started and the next query needs no realignment — which is what makes
the real machine able to run one scan per candidate mask.

The claim under test is that a pass over `k` values costs `5k` ticks plus a
constant, so the tick count is measured at several `k` and the slope reported.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit
from randomfun2026solvers.dataflow_relay import relay
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

__all__ = ["IH", "IW", "RING_CAP", "build", "expected", "worker"]

#: Worker interior.
IW, IH = 24, 19

#: North-wall anchor columns.  Every pipe hangs off the **north** wall, so the
#: `y` term of the Manhattan distance is common to all of them and "nearest pipe"
#: collapses to "nearest column" at every row — the one-dimensional rule a block
#: that moves down a row cannot silently break.  Both midpoints land on 11.
#: The ring's forward pipe is the *western* of the two so that its climb out of
#: the room never has to cross the return pipe's run back in.
IN_COL, OUT_COL, FWD_COL, RET_COL = 3, 6, 16, 19

#: Guarded splits, two columns inside the real midpoint on each side.
R_SPLIT = 9
S_SPLIT = 9

#: Words the ring must be able to hold: the values, the sentinel, and one free
#: cell, or a send blocks behind its own backlog and the machine deadlocks.
RING_CAP = 72


def _in(c: Circuit, x: int, y: int) -> None:
    if x > R_SPLIT:
        raise ValueError(f"r at ({x},{y}) binds to the ring, not input")
    c.set(x, y, "r")


def _out(c: Circuit, x: int, y: int) -> None:
    if x > S_SPLIT:
        raise ValueError(f"s at ({x},{y}) binds to the ring, not output")
    c.set(x, y, "s")


def _rr(c: Circuit, x: int, y: int) -> None:
    if x <= R_SPLIT:
        raise ValueError(f"r at ({x},{y}) binds to input, not the ring")
    c.set(x, y, "r")


def _rs(c: Circuit, x: int, y: int) -> None:
    if x <= S_SPLIT:
        raise ValueError(f"s at ({x},{y}) binds to output, not the ring")
    c.set(x, y, "s")


def worker() -> Circuit:
    """Fill the ring, read the query, scan, answer."""
    c = Circuit(IW, IH, strict_corridors=True)

    # ══ INIT: BP = k, B = 1 (the bias, and the only constant the fill needs) ══
    c.set(1, 0, "@")
    c.horizontal(0, 1, IN_COL)
    _in(c, IN_COL, 0)                       # A = k
    c.run(IN_COL + 1, 0, "b1M")             # BP = k; A = 1; B = 1
    c.set(7, 0, "v")
    c.set(7, 1, "<")
    c.horizontal(1, 7, 0)
    c.set(0, 1, "v")

    # ══ FILL: k x { A = v; A = v + 1; push } ═════════════════════════════════
    c.set(0, 2, ">")                        # merge: entry from above, loop from below
    c.set(1, 2, " ")
    c.set(2, 2, "d")                        # BP > 0 -> south into the body
    c.set(2, 3, "m")
    c.set(2, 4, ">")
    _in(c, IN_COL, 4)                       # A = v
    c.run(IN_COL + 1, 4, "+")               # A = v + 1  (B = 1 survives r)
    c.horizontal(4, IN_COL + 1, 14)
    _rs(c, 14, 4)                           # push the biased value
    c.set(15, 4, "v")
    c.set(15, 5, "<")
    c.horizontal(5, 15, 0)
    c.set(0, 5, "^")
    c.vertical(0, 5, 2)

    # ══ SENTINEL and QUERY ═══════════════════════════════════════════════════
    c.set(3, 2, " ")
    c.run(4, 2, "1N")                       # A = -1
    c.horizontal(2, 5, 14)
    _rs(c, 14, 2)                           # the sentinel closes the ring
    c.horizontal(2, 14, 20)
    c.set(20, 2, "v")
    c.vertical(20, 2, 6)
    c.set(20, 6, "<")
    c.horizontal(6, 20, IN_COL)
    _in(c, IN_COL, 6)                       # A = q     (walked westward)
    c.run(IN_COL - 1, 6, "+M", d=(-1, 0))   # A = q + 1; B = q + 1
    c.set(0, 6, "v")
    c.set(0, 7, ">")
    c.horizontal(7, 0, 15)
    c.set(15, 7, "v")
    c.vertical(15, 7, 10)

    # ══ SCAN: ten cells, two words a lap ═════════════════════════════════════
    #   (15,10) v   enter heading south
    #   (15,11) r   (15,12) s   (15,13) ~   (15,14) X   corner: cw = west
    #   (14,14) ^
    #   (14,13) r   (14,12) s   (14,11) ~   (14,10) X   corner: cw = east
    c.set(15, 10, "v")
    _rr(c, 15, 11)
    _rs(c, 15, 12)
    c.set(15, 13, "~")
    c.set(15, 14, "X")
    c.set(14, 14, "^")
    _rr(c, 14, 13)
    _rs(c, 14, 12)
    c.set(14, 11, "~")
    c.set(14, 10, "X")

    # ── FOUND, north lane (from the top-left X going straight) ───────────────
    c.set(14, 9, "<")
    c.horizontal(9, 14, 7)
    c.run(7, 9, "1", d=(-1, 0))
    _out(c, OUT_COL, 9)
    c.set(OUT_COL - 1, 9, "H")

    # ── FOUND, south lane (from the bottom-right X going straight) ───────────
    c.set(15, 15, " ")
    c.set(15, 16, "<")
    c.horizontal(16, 15, 7)
    c.run(7, 16, "1", d=(-1, 0))
    _out(c, OUT_COL, 16)
    c.set(OUT_COL - 1, 16, "H")

    # ── NOT FOUND, west lane (top-left X, counter-clockwise) ─────────────────
    c.set(13, 10, "<")
    c.horizontal(10, 13, 0)
    c.set(0, 10, "v")
    c.set(0, 11, ">")
    c.horizontal(11, 0, 5)
    c.run(5, 11, "0")
    _out(c, OUT_COL, 11)
    c.set(OUT_COL + 1, 11, "H")

    # ── NOT FOUND, east lane (bottom-right X, counter-clockwise) ─────────────
    c.set(16, 14, "v")
    c.vertical(16, 14, 18)
    c.set(16, 18, "<")
    c.horizontal(18, 16, 7)
    c.run(7, 18, "0", d=(-1, 0))
    _out(c, OUT_COL, 18)
    c.set(OUT_COL - 1, 18, "H")
    return c


def build() -> list[str]:
    """Worker room, input/output rooms, relay, and the ring's two pipes."""
    w = worker()
    g = Circuit(72, IH + 9)
    wx, wy = 1, 8
    stamp(g, wx, wy, w.rows())
    walls(g, wx, wy, IW, IH)

    for col, label, into in ((IN_COL, "I", True), (OUT_COL, "O", False)):
        x = wx + col
        stamp(g, x - 1, 0, ["+-+", f"|{label}|", "+-+"])
        draw_pipe(g, [(x, 3), (x, wy - 2)] if into else [(x, wy - 2), (x, 3)])

    # The relay sits far east and both ring pipes take the long way round: the
    # ring has to hold every value plus the sentinel plus one free cell or a send
    # blocks behind its own backlog and the machine deadlocks *silently*.
    rx, ry = 62, 6
    stamp(g, rx, ry, relay(6, 4))
    fwd, ret = wx + FWD_COL, wx + RET_COL
    n_fwd = draw_pipe(g, [(fwd, wy - 2), (fwd, 1), (58, 1), (58, 8), (rx - 1, 8)])
    n_ret = draw_pipe(g, [(rx - 1, 10), (30, 10), (30, 3), (ret, 3), (ret, wy - 2)])
    if n_fwd + n_ret < RING_CAP:
        raise ValueError(f"ring holds {n_fwd + n_ret} words, need >= {RING_CAP}")
    return [r.rstrip() for r in g.rows() if r.strip()]


def expected(values: list[int], query: int) -> list[int]:
    return [1 if query in values else 0]


def debug_map() -> DebugMap:
    dbg = DebugMap("subset-sum scan probe — 'is q in the ring', 5 ticks a word")
    dbg.region(
        "scan station",
        1 + 14,
        8 + 10,
        2,
        5,
        note="r s ~ X twice a lap; X at both corners resolves hit / keep / sentinel.",
        tags=["compute"],
    )
    dbg.region(
        "fill loop",
        1,
        8 + 2,
        16,
        4,
        note="BP = k; every value is pushed biased by one so no stored word is 0.",
        tags=["setup"],
    )
    dbg.scenario(
        "miss on a full ring",
        "8 11 12 13 14 15 16 17 18 999",
        0,
        4000,
        watch=["scan station"],
        note="One full pass, ending on the sentinel, then emit 0.",
    )
    return dbg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--man", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    args = parser.parse_args(argv)

    rows = build()
    dbg = debug_map()
    for path in (args.man, args.html, args.json):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dbg.write_html(rows, args.html)
    dbg.write_json(args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
