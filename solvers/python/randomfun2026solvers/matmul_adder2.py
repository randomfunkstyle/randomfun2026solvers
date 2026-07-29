#!/usr/bin/env python3
"""The ADDER re-derived with every port on ONE wall.

The first ADDER split its ports west/east, which meant `prod` and `cmd` had to reach
it from the wrong side and could not both be routed. Mirroring it is not an option --
flipping an arrowhead reverses its pipe's flow, so a mirror inverts the dataflow
graph rather than reflecting it, and `@` always spawns east regardless.

So: all five ports on one wall. Binding is then by row, and a `counted_loop` body
walks down a column one glyph per row -- so the body's row order *is* the order it
touches ports, and the accumulate phase pins the whole map:

    r(prod) M r(cin) + s(cout)   ->   prod < cin < cout, with a spare row each side

    prod 2   (M) 3   cin 4   (+) 5   cout 6   out 7   cmd 9

    seed        `r   s`   r(prod)@2 ... s(cout)@6
    accumulate  `rMr+s`   r(prod)@2  M@3  r(cin)@4  +@5  s(cout)@6
    emit        `r  s`    r(cin)@4  ...  s(out)@7

Not yet verified on the engine: its probe needs the stub driver rewired, because
the driver's sends and the ADDER's ports now both face east and cannot be joined
by a straight pipe. The layout and the bindings are checked.
"""

from __future__ import annotations

from .circuit import Circuit, E, S

__all__ = ["PHASES", "PORTS_IN", "PORTS_OUT", "cells"]

PROD, CIN, COUT, OUT, CMD = 2, 4, 6, 7, 9
PORTS_IN = {"prod": PROD, "cin": CIN, "cmd": CMD}
PORTS_OUT = {"cout": COUT, "out": OUT}
SPINE, IW, IH = 11, 30, 12

#: (loop top row, body) per phase. Each body is walked down its own column, so its
#: glyphs land on consecutive rows and each binds the port on its row.
PHASES = (("seed", 1, "r   s"), ("accumulate", 1, "rMr+s"), ("emit", 3, "r  s"))


def cells() -> dict[tuple[int, int], str]:
    c = Circuit(IW + 2, IH + 2)
    c.set(1, SPINE, "@")
    x = 2
    exits = []
    for name, top, body in PHASES:
        # riser: up from the spine, reading this phase's count word off `cmd`
        c.set(x, SPINE, "^")
        c.set(x, CMD, "r")
        c.set(x, CMD - 1, "b")
        for y in range(top + 1, CMD - 1):
            c.set(x, y, " ")
        c.set(x, top, ">")
        ex, ey = c.counted_loop(x + 1, top, body)
        exits.append((ex, ey, name))
        # peel off east, then back down to the spine for the next phase
        c.set(ex, ey, "v")
        for y in range(ey + 1, SPINE):
            c.set(ex, y, " ")
        c.set(ex, SPINE, ">")
        x = ex + 1
    # after emit, walk back west along the spine to phase one
    for k in range(2, x):
        if c.get(k, SPINE) == " ":
            c.set(k, SPINE, " ")
    return {k: v for k, v in c.cell.items()}
