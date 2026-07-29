#!/usr/bin/env python3
"""Is a counted loop laid as a rectangle cheaper than as a row?

Both machines run the same 12-cell body 9 times.  The row version is the shape
`matmul_grid` builds: body east, then a return corridor west.  The rectangle
walks a closed perimeter, so the return leg *is* the body.
"""
import sys

sys.path.insert(0, "/Users/oleg/projects/randomfun2026solvers/.claude/worktrees/"
                   "agent-a408bcddfaf92d05c/solvers/python")
from randomfun2026solvers.fast_littleman import FastLittleman


def blank(w, h):
    g = [[" "] * w for _ in range(h)]
    for x in range(w):
        g[0][x] = g[h - 1][x] = "-"
    for y in range(h):
        g[y][0] = g[y][w - 1] = "|"
    g[0][0] = g[0][w - 1] = g[h - 1][0] = g[h - 1][w - 1] = "+"
    return g


def rect(trips=9):
    """A 6x4 perimeter: 16 cells, 12 of them body, 4 corners."""
    w, h = 13, 8
    g = blank(w, h)
    put = lambda x, y, c: g[y].__setitem__(x, c)
    put(1, 2, "@"); put(2, 2, str(trips)); put(3, 2, "b")
    # top row x=4..9 at y=2 -- x=4 is the NW corner, x=9 the `d`
    put(4, 2, ">")
    for x in (5, 6, 7):
        put(x, 2, ".")
    put(8, 2, "m")
    put(9, 2, "d")
    # right column x=9, y=3..5 -- y=5 is the SE corner
    put(9, 3, "."); put(9, 4, "."); put(9, 5, "<")
    # bottom row y=5, x=8..5 body, x=4 the SW corner
    for x in (8, 7, 6, 5):
        put(x, 5, ".")
    put(4, 5, "^")
    # left column x=4, y=4..3
    put(4, 4, "."); put(4, 3, ".")
    put(10, 2, "H")   # BP==0 falls straight through the `d`
    return ["".join(r) for r in g]


def row(trips=9):
    """Body east in one row, then a return corridor: the current shape."""
    w, h = 22, 6
    g = blank(w, h)
    put = lambda x, y, c: g[y].__setitem__(x, c)
    put(1, 2, "@"); put(2, 2, str(trips)); put(3, 2, "b")
    put(4, 2, ">")
    for x in range(5, 15):
        put(x, 2, ".")
    put(15, 2, "m")
    put(16, 2, "d")          # 12 body cells: x=5..16
    put(16, 3, "<")          # clockwise -> south, then west home
    for x in range(5, 16):
        put(x, 3, "<")
    put(4, 3, "^")
    put(17, 2, "H")
    return ["".join(r) for r in g]


def probe(rows, label, body, trips):
    try:
        res = FastLittleman("\n".join(rows)).run([])
    except Exception as exc:                       # pragma: no cover - probe
        print(f"{label}: load error {exc}")
        print("\n".join(rows))
        return
    if res.fatal:
        print(f"{label}: fatal {res.fatal} at {res.fatal_pos}")
        print("\n".join(rows))
        return
    lap = (res.step - 4) / trips
    print(f"{label}: ticks={res.step} lap={lap:.1f} for a {body}-cell body "
          f"-> {lap / body:.2f}x")


if __name__ == "__main__":
    probe(rect(), "rectangle", 12, 9)
    probe(row(), "row      ", 12, 9)
