#!/usr/bin/env python3
"""Per-room slack: how many of a man's ticks did no work, measured.

The floor theorem says a lap costs ``n_ops + 4 - n_turning_ops``.  Every term in
that is countable off the shipped grid *if* you know how often each cell fired,
and the stride-1 profile knows exactly that.  So for each room:

``op``
    ticks spent on cells that do work -- anything that is not a steer, a nop or
    a wall.  This is the part no placement can remove.
``steer``
    ticks spent on ``< > ^ v``.  A closed lap has to buy four of these per lap
    and gets a discount for every self-turning op (``X d a x``) on it; everything
    beyond that is **slack**, and slack is what a relayout deletes.
``nop``
    ticks spent on ``.`` and blanks.  These are pure distance -- a man walking
    from one op to the next across ground that does nothing.  Every one of them
    is slack, without exception.
``blocked``
    ticks standing still on a pipe glyph.  Not slack at all: shortening a walk
    that is 85 % blocked moves the blocking, not the total.  Reported separately
    for exactly that reason.

The ``laps`` column is supplied per block rather than inferred, because "how many
closed circuits did he complete" is a question about the program's control flow
and the profile only sees cells.  Where it is left at ``None`` the ``nop`` column
alone is quoted, which is a lower bound on the slack and needs no assumption.

    python3 slack.py
"""

from __future__ import annotations

from build import ensure
from heat import TOTAL, load
from rooms_of import rooms

STEER = set("<>^vV")
NOP = set(". ")
WALL = set("+-|=:")
TURNING = set("Xdax")
#: Cells that can block.  ``H`` cannot but is not a worker glyph either.
PIPEOP = set("srRSUq")


def classify(rows, cells):
    """``{class: ticks}`` plus the turning-op tick count, over ``cells``."""
    d = load()
    heat, wait = d["heat"], d["wait"]
    out = {"op": 0, "steer": 0, "nop": 0, "turning": 0, "blocked": 0, "total": 0}
    for c in cells:
        x, y = c
        t = heat.get(c, 0)
        if not t:
            continue
        g = rows[y][x] if x < len(rows[y]) else " "
        if g in WALL or g == "@":
            # `@` is a spawn marker and a nop; walls are never stood on
            g = "."
        b = wait.get(c, 0)
        out["total"] += t
        out["blocked"] += b
        walked = t - b
        if g in STEER:
            out["steer"] += walked
        elif g in NOP:
            out["nop"] += walked
        else:
            out["op"] += walked
            if g in TURNING:
                out["turning"] += walked
    return out


def main() -> int:
    rows, _ = ensure()
    out = []
    for x0, y0, x1, y1, _kind in rooms():
        cells = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        c = classify(rows, cells)
        if c["total"] < 10_000:
            continue
        out.append(((x0, y0, x1, y1), c))
    out.sort(key=lambda r: -(r[1]["total"] - r[1]["blocked"]))
    print(f"{'room':>24s} {'walked':>12s} {'op':>12s} {'steer':>11s} "
          f"{'nop':>11s} {'turn-op':>10s} {'blocked':>12s}")
    tw = to = ts = tn = 0
    for box, c in out:
        w = c["total"] - c["blocked"]
        tw += w
        to += c["op"]
        ts += c["steer"]
        tn += c["nop"]
        print(f"{str(box):>24s} {w:12,} {c['op']:12,} {c['steer']:11,} "
              f"{c['nop']:11,} {c['turning']:10,} {c['blocked']:12,}", flush=True)
    print(f"{'(sum)':>24s} {tw:12,} {to:12,} {ts:11,} {tn:11,}")
    print(f"\nwalked ticks are {100 * tw / TOTAL:.1f} man-runs of the {TOTAL:,}-tick tour; "
          f"nop ticks alone are {tn:,} ({100 * tn / TOTAL:.2f} % of one)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
