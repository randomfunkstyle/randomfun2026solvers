"""Decide captured geometries, and test the pad-shift hypothesis.

The hypothesis worth a lot of build time: ``mem_pad`` enters the geometry
*only* as ``mem_x = lane_x0 + max(prefixes) + mem_pad``, so a pad one smaller
should move the MEM band's glyphs one column west and nothing else -- except
the CPU's own width ``W``, hence the east-wall touches at ``CX + W + 2``.
If that holds exactly, ONE build decides every pad.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bind import decide, geom, load, margins, ties  # noqa: E402


def key(rec):
    return tuple(sorted(rec["knobs"].items()))


def show(recs):
    for r in recs:
        if "glyphs" not in r:
            print(f"  {r['knobs']}: {r.get('error')}", flush=True)
            continue
        g, t = geom(r)
        bad = decide(g, t)
        m = margins(g, t)
        tie = ties(g, t)
        print(
            f"  {r['knobs']}  {r['w']}x{r['h']}  "
            f"{'BINDS' if not bad else 'REFUSED'}  "
            f"min-slack={m[0][0] if m else '-'}  ties={len(tie)}",
            flush=True,
        )
        for b in bad[:4]:
            print(f"      violation: '{b[2]}' at ({b[0]},{b[1]}) wants {b[3]}: {b[4]}", flush=True)
        for tt in tie:
            print(f"      tie: '{tt[2]}' ({tt[0]},{tt[1]}) {tt[3]} vs {tt[4]} at {tt[5]} -> {tt[6]}",
                  flush=True)


def diff_geom(a, b):
    """How do two captured geometries differ?"""
    ga, ta = geom(a)
    gb, tb = geom(b)
    print(f"  glyph counts {len(ga)} vs {len(gb)}", flush=True)
    if len(ga) == len(gb):
        moved = [(x, y, gl, bd, gb[i][0] - x, gb[i][1] - y)
                 for i, (x, y, gl, bd) in enumerate(ga) if gb[i][:2] != (x, y)]
        by_band: dict = {}
        for x, y, gl, bd, dx, dy in moved:
            by_band.setdefault((bd, gl, dx, dy), 0)
            by_band[(bd, gl, dx, dy)] += 1
        print(f"  {len(moved)}/{len(ga)} glyphs moved:", flush=True)
        for k2, n in sorted(by_band.items()):
            print(f"      {k2[0]:16} '{k2[1]}'  d=({k2[2]:+d},{k2[3]:+d})  x{n}", flush=True)
        same = [(x, y, gl, bd) for i, (x, y, gl, bd) in enumerate(ga) if gb[i][:2] == (x, y)
                and gb[i][2:] != (gl, bd)]
        if same:
            print(f"      !! {len(same)} glyphs changed band/kind in place", flush=True)
    for n in sorted(set(ta) | set(tb)):
        if ta.get(n) != tb.get(n):
            print(f"      touch {n:12} {ta.get(n)} -> {tb.get(n)}", flush=True)


if __name__ == "__main__":
    recs = []
    for p in sys.argv[1:]:
        recs += load(p)
    print(f"=== {len(recs)} captured geometries ===", flush=True)
    show(recs)
    if len(recs) >= 2:
        print("\n=== geometry diff, first vs each ===", flush=True)
        for r in recs[1:]:
            print(f"  {recs[0]['knobs']} -> {r['knobs']}", flush=True)
            if "glyphs" in r and "glyphs" in recs[0]:
                diff_geom(recs[0], r)
