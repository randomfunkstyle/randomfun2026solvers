#!/usr/bin/env python3
"""Delete a grid row by *relocating* the corridor bend it carries.

The repair pass got 85x96 down to 77x89 and then stalled. What stops it is always
the same shape: a row holds exactly two glyphs, a bend pair like `v` at column
x1 and `<` at column x2, and the cells between them are the corridor's horizontal
run. Moving the pair one row up fails because that row is not free between x1 and
x2.

But the run does not have to go one row up — it can go to *any* row where columns
x1..x2 are clear. The two vertical legs are blanks a man walks straight through,
so lengthening one and shortening the other changes nothing but timing. So this
tries every target row for the whole set of blockers at once, which is the move
that actually relocates a corridor rather than nudging its corners.

Cheap: ~89 target rows per candidate row, a verify is ~0.14s and a broken grid is
rejected in ~2ms, so a full sweep is well under a minute.
"""
from __future__ import annotations

import itertools
import sys
import time
from pathlib import Path

from . import optimize

CAP = 200_000
SLUG = "matmul"


def factor(rows):
    w = max((len(r) for r in rows), default=0)
    return max(w, len(rows)) ** 2, w, len(rows)


def measure(rows):
    try:
        res = optimize.verify("\n".join(rows) + "\n", SLUG, tick_cap=CAP)
    except Exception:
        return False, 0.0
    return bool(res.passed), float(res.avg_ticks)


def at(rows, x, y):
    return rows[y][x] if 0 <= y < len(rows) and 0 <= x < len(rows[y]) else " "


def put(rows, x, y, ch):
    r = rows[y].ljust(x + 1)
    rows[y] = (r[:x] + ch + r[x + 1:]).rstrip()


def blockers(rows, y, axis="row"):
    if axis == "row":
        return [(x, ch) for x, ch in enumerate(rows[y]) if ch not in (" ", "|")]
    return [(k, at(rows, y, k)) for k in range(len(rows)) if at(rows, y, k) not in (" ", "-")]


def candidates(rows, y):
    """Grids with row y gone and its blockers put back somewhere legal."""
    blk = blockers(rows, y)
    if not blk or len(blk) > 4:
        return
    base = rows[:y] + rows[y + 1:]

    # (a) the whole set relocated to one common row — a corridor move
    for target in range(len(base)):
        if all(at(base, x, target) == " " for x, _ in blk):
            cand = list(base)
            for x, ch in blk:
                put(cand, x, target, ch)
            yield cand, f"all->row {target}"

    # (b) each blocker nudged independently, or dropped as redundant
    opts = []
    for x, ch in blk:
        here = [t for t in (y - 1, y, y - 2, y + 1)
                if 0 <= t < len(base) and at(base, x, t) == " "]
        opts.append([(x, ch, t) for t in here] + [(x, ch, None)])
    for combo in itertools.product(*opts):
        cand = list(base)
        ok = True
        for x, ch, t in combo:
            if t is None:
                continue
            if at(cand, x, t) != " ":
                ok = False
                break
            put(cand, x, t, ch)
        if ok:
            yield cand, f"nudge {[t for _, _, t in combo]}"


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    budget = float(sys.argv[3]) if len(sys.argv) > 3 else 1800.0
    global SLUG
    SLUG = sys.argv[4] if len(sys.argv) > 4 else "matmul"
    rows = src.read_text(encoding="utf-8").rstrip("\n").split("\n")
    ok, t = measure(rows)
    assert ok, "baseline does not pass"
    f, w, h = factor(rows)
    best = f * t
    print(f"baseline {w}x{h} factor {f:,} avg {t:,.0f} score {best:,.0f}", flush=True)

    t0 = time.time()
    changed = True
    while changed and time.time() - t0 < budget:
        changed = False
        for y in sorted(range(len(rows)), key=lambda k: len(blockers(rows, k))):
            if time.time() - t0 > budget:
                break
            hit = None
            for cand, how in candidates(rows, y):
                good, tt = measure(cand)
                if not good:
                    continue
                nf, nw, nh = factor(cand)
                if nf * tt < best - 1e-9 and (hit is None or nf * tt < hit[1]):
                    hit = (cand, nf * tt, how, tt, nf, nw, nh)
            if hit is None:
                continue
            rows, best, how, t, f, w, h = hit
            print(f"  row {y:>3} deleted, {how:<16} {w}x{h} factor {f:,} "
                  f"avg {t:,.0f} score {best:,.0f} [{time.time()-t0:.0f}s]", flush=True)
            dst.write_text("\n".join(rows) + "\n", encoding="utf-8")
            changed = True
            break

    f, w, h = factor(rows)
    ok, t = measure(rows)
    print(f"\nfinal {w}x{h} factor {f:,} avg {t:,.0f} score {f*t:,.0f} "
          f"[{time.time()-t0:.0f}s]", flush=True)
    dst.write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
