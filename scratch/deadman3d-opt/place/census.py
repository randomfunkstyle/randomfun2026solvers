#!/usr/bin/env python3
"""Rank every named region by ticks, attributing each cell to exactly one box.

Naively summing the region boxes double-counts badly -- ``cpu:drops`` is a band
laid across every lane row, ``rom`` is half the machine -- so a cell is charged to
the **smallest** box containing it, which is ``lm1.profile._region_of``'s rule and
the only one that adds to 100 %.

Cells in no box at all are reported as ``unattributed``, because on this machine
that bucket is not small: the taped store's banks and gates are drawn inside one
``tape`` region with no internal structure, and they are most of the traffic.

    python3 census.py [--min PERCENT]
"""

from __future__ import annotations

import sys
from collections import defaultdict

from heat import TOTAL, load


def attribute() -> dict[str, tuple[int, int]]:
    d = load()
    boxes = sorted(
        ((w * h, n, x, y, w, h) for n, (x, y, w, h) in d["regions"].items()),
        key=lambda t: t[0],
    )
    heat, wait = d["heat"], d["wait"]
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for (cx, cy), v in heat.items():
        name = "unattributed"
        for _a, n, x, y, w, h in boxes:
            if x <= cx < x + w and y <= cy < y + h:
                name = n
                break
        o = out[name]
        o[0] += v
        o[1] += wait.get((cx, cy), 0)
    return {k: (v[0], v[1]) for k, v in out.items()}


def main() -> int:
    lo = 0.0
    if "--min" in sys.argv:
        lo = float(sys.argv[sys.argv.index("--min") + 1])
    own = attribute()
    print(f"{'region':36s} {'%run':>8s} {'ticks':>14s} {'%blocked':>9s}")
    tot = 0
    for n, (t, b) in sorted(own.items(), key=lambda kv: -kv[1][0]):
        tot += t
        if 100 * t / TOTAL < lo:
            continue
        print(f"{n:36s} {100 * t / TOTAL:8.3f} {t:14,} "
              f"{100 * b / max(1, t):9.1f}", flush=True)
    print(f"{'(sum)':36s} {100 * tot / TOTAL:8.3f} {tot:14,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
