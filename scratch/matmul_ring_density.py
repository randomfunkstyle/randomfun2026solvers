#!/usr/bin/env python3
"""Can a storage pipe serpentine at ~100% cell density?

`B` packed is 96 words and `A` packed 43; if a pipe can fold back on itself
every row then storage costs ~1 cell a word and the whole machine fits in a
box of side ~28.  If bends cost, it does not.
"""
import sys

sys.path.insert(0, "/Users/oleg/projects/randomfun2026solvers/.claude/worktrees/"
                   "agent-a408bcddfaf92d05c/solvers/python")
from randomfun2026solvers.fast_littleman import FastLittleman

W, H = 20, 16


def build(rows_of_serpent=6, span=12):
    g = [[" "] * W for _ in range(H)]

    def box(x0, y0, x1, y1):
        for x in range(x0, x1 + 1):
            g[y0][x] = g[y1][x] = "-"
        for y in range(y0, y1 + 1):
            g[y][x0] = g[y][x1] = "|"
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            g[y][x] = "+"

    # source room, top left; sink room, bottom left
    box(0, 0, 4, 4)
    g[1][1], g[1][2], g[1][3] = "@", "9", "s"
    g[2][1], g[2][2], g[2][3] = "^", "<", "<"
    box(0, 11, 4, 15)
    g[12][1], g[12][2] = "@", "r"

    # the serpentine leaves the source room's east wall at y=2
    x, y, d = 5, 2, "E"
    g[y][x] = ">"
    for i in range(rows_of_serpent):
        for _ in range(span):
            x += 1 if d == "E" else -1
            g[y][x] = "-"
        g[y][x] = "v"          # turn south
        y += 1
        d = "W" if d == "E" else "E"
        g[y][x] = "<" if d == "W" else ">"
    # run west back to the sink room's east wall
    while x > 5:
        x -= 1
        g[y][x] = "-"
    g[y][x] = "v"
    while y < 13:
        y += 1
        g[y][x] = "|"
    g[y][x] = "<"              # forward cell (4, y) is the sink's border
    return ["".join(r) for r in g]


def main():
    rows = build()
    src = "\n".join(rows)
    try:
        lm = FastLittleman(src)
    except Exception as exc:
        print("load error:", exc)
        print(src)
        return
    print(f"rooms={len(lm.rooms)} pipes={len(lm.pipes)}")
    for p in lm.pipes:
        print(f"  pipe {p.id}: {len(p.path)} cells, room{p.src}->room{p.dst}")
    box_cells = W * H
    cap = sum(len(p.path) for p in lm.pipes)
    print(f"capacity {cap} words in a {W}x{H}={box_cells} box")
    print(src)


if __name__ == "__main__":
    main()
