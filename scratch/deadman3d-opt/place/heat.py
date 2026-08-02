#!/usr/bin/env python3
"""Read the cached exact profile and answer "how often was this cell executed".

The profile is taken at ``stride=1``, so ``heat[(x,y)]`` is the exact number of
ticks a man stood on that cell over the whole 21-round tour, and ``wait[(x,y)]``
is the blocked subset.  The difference is what matters here:

    executions(cell) = heat - wait

because a blocked man stands still without firing the glyph.  That distinction is
the whole difference between "this loop is too long" (walk) and "this loop is
waiting for something else" (wait) -- and a floor-gap analysis that conflates the
two will confidently propose shortening a loop that is 90 % idle.

    python3 heat.py --cell X Y
    python3 heat.py --box X0 Y0 X1 Y1
    python3 heat.py --region NAME
"""

from __future__ import annotations

import pickle
import sys
from functools import lru_cache
from pathlib import Path

PKL = Path("/tmp/compactor/heat-1-21.pkl")
TOTAL = 85_522_204


@lru_cache(maxsize=1)
def load() -> dict:
    if not PKL.exists():
        raise SystemExit(f"{PKL} missing: run `python3 prof.py 1 21` (~40 s)")
    with PKL.open("rb") as fh:
        return pickle.load(fh)


def stats(cells) -> tuple[int, int]:
    """``(ticks, executions)`` summed over ``cells``."""
    d = load()
    h, w = d["heat"], d["wait"]
    t = sum(h.get(c, 0) for c in cells)
    b = sum(w.get(c, 0) for c in cells)
    return t, t - b


def box(x0, y0, x1, y1):
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def dump(cells) -> None:
    d = load()
    rows, h, w = d["rows"], d["heat"], d["wait"]
    for c in cells:
        g = rows[c[1]][c[0]] if c[1] < len(rows) and c[0] < len(rows[c[1]]) else " "
        t, b = h.get(c, 0), w.get(c, 0)
        if t:
            print(f"  {str(c):>12s} {g!r}  ticks {t:12,}  blocked {b:12,} "
                  f"({100 * b / t:5.1f}%)  exec {t - b:12,}  "
                  f"{100 * t / TOTAL:6.3f}% of run", flush=True)


def main() -> int:
    a = sys.argv[1:]
    if a[0] == "--cell":
        dump([(int(a[1]), int(a[2]))])
    elif a[0] == "--box":
        cells = box(*(int(v) for v in a[1:5]))
        dump(cells)
        t, e = stats(cells)
        print(f"  TOTAL ticks {t:,} ({100 * t / TOTAL:.3f}% of run), exec {e:,}")
    elif a[0] == "--region":
        x, y, w, h = load()["regions"][a[1]]
        cells = box(x, y, x + w - 1, y + h - 1)
        dump(cells)
        t, e = stats(cells)
        print(f"  TOTAL ticks {t:,} ({100 * t / TOTAL:.3f}% of run), exec {e:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
