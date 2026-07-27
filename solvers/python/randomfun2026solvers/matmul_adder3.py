#!/usr/bin/env python3
"""The ADDER, ports on ONE wall, with `cmd` as its **topmost** port.

`matmul_adder2` had the right idea and two faults, both found on the engine:

* **its spine did not close.** Each phase peeled off east and dropped a ``>`` onto
  the spine row; walking back west along that same row the man hits those ``>``
  glyphs and is steered east again, forever. The return leg needs a row of its own.
* **`cmd` below `prod` makes the machine unroutable.** MAIN sends `prod` and `cmd`
  from its east wall and both descend to the ADDER. The upper of two descending
  pipes must turn further east (its horizontal leg would otherwise cross the
  other's vertical), so `cmd` — the lower one at MAIN — descends *west* of `prod`.
  Its westward approach to the ADDER then crosses `prod`'s vertical unless it
  arrives **above** where `prod` does. So `cmd` has to be the ADDER's top port.

Port rows, and what pins each one:

    cmd 1   (2 = where every riser reads it)   prod 4   cin 6   cout 8   out 9

The accumulate body walks down one column, so its glyph order *is* its row order,
and `r(prod) M r(cin) + s(cout)` pins prod < cin < cout two rows apart. `out` sits
directly under `cout` so the emit body is `r(cin) . . s(out)`. `cmd` goes to row 1
and every riser reads it from row 2, where it is 1 away from `cmd` and 2 from
`prod` — nearest, with no tie to resolve.

One phase occupies five columns::

      x    x+1  x+2  x+3  x+4
   1  >    b    v
   2  r         .
   3            >    d              <- counted_loop entry (top row per phase)
   4            m    r(prod)
   ...
   9            ^    <
  11  ^         .    .    >         <- spine, walked EAST between phases
  12  ^         .    .    .         <- return, walked WEST after the last phase
"""

from __future__ import annotations

from .circuit import Circuit

__all__ = ["CIN", "CMD", "COUT", "IH", "IW", "OUT", "PHASES", "PORTS_IN", "PORTS_OUT", "PROD", "cells"]

CMD, PROD, CIN, COUT, OUT = 1, 4, 6, 8, 9
READ = 2                       # the row every riser reads `cmd` from
# The emit loop's bottom row **is** the spine. The spine is only ever walked from one
# phase's drop column to the next phase's riser, and those are adjacent columns, so
# the loop's two turn glyphs on that row are never stepped on. That is a row off the
# ADDER, and the ADDER's bottom is what the O room and ring B's band sit under.
SPINE, RET = 10, 11
IH = 11

PORTS_IN = {"cmd": CMD, "prod": PROD, "cin": CIN}
PORTS_OUT = {"cout": COUT, "out": OUT}

#: (name, loop top row, body). Each body walks down its own column, one glyph per
#: row, so every glyph binds the port on its row.
#: Bodies are derived from the port rows: a body's length **is** the span it covers,
#: and its cycle is 2*(len+2) ticks. `out` directly under `cin` is what makes the emit
#: body two glyphs instead of four — 8 ticks per output value rather than 12.
PHASES = (
    ("seed", PROD - 1, "r" + " " * (COUT - PROD - 1) + "s"),
    ("accumulate", PROD - 1, "rMr+s"),
    ("emit", CIN - 1, "r" + " " * (OUT - CIN - 1) + "s"),
)

IW = 2 + 5 * len(PHASES)       # '@' column plus five per phase


def cells() -> dict[tuple[int, int], str]:
    c = Circuit(IW + 2, IH + 2)
    c.set(1, SPINE, "@")
    x = 2
    first = x
    for _, top, body in PHASES:
        c.set(x, SPINE, "^")
        c.blanks(x, SPINE - 1, SPINE - READ - 1, d=(0, -1))
        c.set(x, READ, "r")        # BP <- this phase's count word, off `cmd`
        c.set(x, CMD, ">")
        c.set(x + 1, CMD, "b")
        c.set(x + 2, CMD, "v")
        c.blanks(x + 2, CMD + 1, top - CMD - 1, d=(0, 1))
        ex, ey = c.counted_loop(x + 2, top, body)
        assert (ex, ey) == (x + 4, top)
        c.set(x + 4, top, "v")
        c.blanks(x + 4, top + 1, SPINE - top - 1, d=(0, 1))
        c.set(x + 4, SPINE, ">")   # ... and east along the spine to the next riser
        x += 5
    last = x - 1                   # the emit phase's drop column
    c.cell[(last, SPINE)] = "v"    # ... continues south instead of east
    c.set(last, RET, "<")
    c.blanks(last - 1, RET, last - first - 1, d=(-1, 0))
    c.set(first, RET, "^")
    # the eastward spine: blanks between one phase's drop column and the next riser
    for k in range(first, last):
        if c.get(k, SPINE) == " ":
            c.set(k, SPINE, " ")
    return dict(c.cell)


if __name__ == "__main__":
    cc = Circuit(IW + 2, IH + 2)
    for (x, y), ch in cells().items():
        cc.set(x, y, ch)
    print(cc.ruler())
