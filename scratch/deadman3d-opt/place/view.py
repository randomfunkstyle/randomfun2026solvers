#!/usr/bin/env python3
"""Print a window of the cached shipped grid, with column/row rulers.

    python3 view.py X0 Y0 X1 Y1
    python3 view.py --region cpu:seek:flush [pad]
"""

from __future__ import annotations

import sys

from build import ensure


def show(rows, x0, y0, x1, y1) -> None:
    hdr = " " * 6
    print(hdr + "".join(str(x // 100 % 10) for x in range(x0, x1 + 1)))
    print(hdr + "".join(str(x // 10 % 10) for x in range(x0, x1 + 1)))
    print(hdr + "".join(str(x % 10) for x in range(x0, x1 + 1)))
    for y in range(y0, y1 + 1):
        r = rows[y] if 0 <= y < len(rows) else ""
        print(f"{y:5d} " + "".join(r[x] if x < len(r) else " " for x in range(x0, x1 + 1)))


def main() -> int:
    rows, regions = ensure()
    a = sys.argv[1:]
    if a and a[0] == "--region":
        name = a[1]
        pad = int(a[2]) if len(a) > 2 else 2
        x, y, w, h = regions[name]
        show(rows, x - pad, y - pad, x + w - 1 + pad, y + h - 1 + pad)
    else:
        show(rows, *(int(v) for v in a[:4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
