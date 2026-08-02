#!/usr/bin/env python3
"""Enumerate the shipped machine's rooms and men, hottest first.

``Machine.regions`` names the CPU's insides but draws one flat box over the whole
taped store, which is where most of the traffic is.  The engine's own room parser
does not have that blind spot -- every worker is a man in a room, so *the room
list is the block list*, and it is derived from the grid rather than from a
generator's opinion about it.

For each room this prints the man's total ticks, his blocked ticks, his lap count
(ticks standing on the ``@``, which he crosses exactly once a lap) and hence his
ticks per lap.  That last number is the "actual" column of the floor-gap table,
measured rather than counted off a listing.

    python3 rooms_of.py [--min-ticks N]
"""

from __future__ import annotations

import sys

from build import ensure
from heat import TOTAL, load


def rooms():
    """``[(x0, y0, x1, y1, kind)]`` from the engine's own parser."""
    rows, _ = ensure()
    sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers"
                       "/.claude/worktrees/compactor/solvers/python")
    from randomfun2026solvers.fast_littleman import FastLittleman

    g = FastLittleman("\n".join(rows))
    return [(r.min[0], r.min[1], r.max[0], r.max[1], r.kind) for r in g.rooms]


def main() -> int:
    rows, _ = ensure()
    d = load()
    heat, wait = d["heat"], d["wait"]
    out = []
    for x0, y0, x1, y1, kind in rooms():
        cells = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        t = sum(heat.get(c, 0) for c in cells)
        b = sum(wait.get(c, 0) for c in cells)
        men = [c for c in cells
               if c[1] < len(rows) and c[0] < len(rows[c[1]]) and rows[c[1]][c[0]] == "@"]
        laps = sum(heat.get(c, 0) for c in men)
        out.append((t, b, laps, x0, y0, x1, y1, kind, men))
    out.sort(key=lambda r: -r[0])
    print(f"{'room':>26s} {'kind':>8s} {'ticks':>13s} {'%run':>7s} "
          f"{'%blk':>6s} {'laps':>10s} {'t/lap':>7s}  man")
    for t, b, laps, x0, y0, x1, y1, kind, men in out:
        if t < 1000:
            continue
        walk = t - b
        print(f"{f'({x0},{y0})-({x1},{y1})':>26s} {kind:>8s} {t:13,} "
              f"{100 * t / TOTAL:7.3f} {100 * b / max(1, t):6.1f} {laps:10,} "
              f"{walk / max(1, laps):7.2f}  {men[:1]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
