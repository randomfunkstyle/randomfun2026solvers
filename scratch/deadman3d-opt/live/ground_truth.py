#!/usr/bin/env python3
"""Check the liveness model against the three facts we already know by measurement.

A model that cannot reproduce known-good ground truth is worse than no model,
because it answers instantly and wrongly.  These are the three facts the brief
pins:

1. ``b`` at (29,175) **is** transparent to the BRN riser — measured, taped
   140,379,566 -> 140,377,226.
2. On a return path ``B`` is live (it carries the emulated ACC) while ``A`` and
   ``BP`` are dead, so ``M``, ``W``, ``/`` and the pipe glyphs are **not**
   transparent there, and turn glyphs never are.
3. The three risers at (10,194)/(10,195)/(10,196) have one column of westward
   slack with an all-``.`` column above them.

    uv run python scratch/deadman3d-opt/live/ground_truth.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cpu_live as L  # noqa: E402

GRID = Path("/tmp/d3hires-taped/grid.man")
META = Path("/tmp/d3hires-taped/meta.json")


def main() -> int:
    grid = L.load(GRID)
    meta = json.loads(META.read_text())
    regions = {k: tuple(v) for k, v in meta["regions"].items()}
    # The CPU room is the one holding the fetch; its spawn is the CPU man's.
    cpu = [v for k, v in regions.items() if k.startswith("cpu:")]
    x0 = min(v[0] for v in cpu)
    x1 = max(v[0] + v[2] for v in cpu)
    y0 = min(v[1] for v in cpu)
    y1 = max(v[1] + v[3] for v in cpu)
    spawn = None
    for y in range(y0 - 2, y1 + 2):
        for x in range(x0 - 2, x1 + 2):
            if grid[y][x] == "@":
                spawn = (x, y, "E")
    if spawn is None:
        raise SystemExit("no CPU spawn found")
    print(f"CPU spawn at {spawn}", flush=True)

    w = L.build(grid, spawn)
    print(f"walk graph: {len(w.succ):,} nodes, {len(w.terminal)} terminal", flush=True)
    if w.terminal:
        # Terminals are either H or a spurious branch arm that ran into a wall.
        kinds = {}
        for (x, y, h) in sorted(w.terminal):
            kinds.setdefault(w.at(x, y), []).append((x, y, h))
        for g, ns in sorted(kinds.items()):
            print(f"   terminal on {g!r}: {len(ns)}  e.g. {ns[:4]}", flush=True)

    print("\n── fact 1: `b` at (29,175) is transparent to the BRN riser ──", flush=True)
    # Proposal: turn north at x=29 on row 176 instead of x=32, landing on the
    # same westward collector at row 170.
    col, turn_row, collector = 29, 176, 170
    cells = [(col, y) for y in range(turn_row - 1, collector, -1)]
    join = (col, collector, "W")
    if join not in w.live_in:
        print(f"   !! join node {join} is not on the walk graph", flush=True)
        return 1
    live_after = w.live_in[join]
    print(f"   live at the join {join}: {{{','.join(sorted(live_after)) or ''}}}", flush=True)
    rows = L.walk_back(w, cells, live_after, "N")
    allok = True
    for (x, y), g, live, v in rows:
        allok &= bool(v)
        print(f"   ({x},{y}) {g!r:<4} "
              f"live_after={{{','.join(sorted(live)) or ''}}}"
              f"  {'OK ' if v else 'NO '} {v.reason}", flush=True)
    print(f"   => vertical run transparent: {allok}", flush=True)
    # And the cell we would overwrite with `^` must be ours alone.
    others = w.headings_on(col, turn_row)
    print(f"   headings standing on ({col},{turn_row}) today: {sorted(others)} "
          f"(glyph {w.at(col, turn_row)!r})", flush=True)

    print("\n── fact 2: on a return path B is live, A and BP are dead ──", flush=True)
    cx, cy, cw_, ch = regions["cpu:return:collector"]
    bad = []
    for x in range(cx, cx + cw_):
        n = (x, cy, "W")
        if n in w.live_in:
            lv = w.live_in[n]
            if lv != frozenset({"B"}):
                bad.append((x, lv))
    print(f"   collector row {cy}, x {cx}..{cx+cw_-1}: "
          f"{'all live_in == {B}' if not bad else 'EXCEPTIONS ' + str(bad)}", flush=True)
    probe = max((n for n in w.succ if n[1] == cy and n[2] == "W"), key=lambda n: n[0])
    lv = w.live_in[probe]
    print(f"   probing {probe}, live = {{{','.join(sorted(lv))}}}", flush=True)
    for g in "MW/rsRSUHb.mq]<>^vXxda":
        v = L.transparent(g, lv, "W")
        print(f"   {g!r:<4} on the collector: {'TRANSPARENT' if v else 'no '} — {v.reason}",
              flush=True)

    print("\n── fact 3: the seek risers at (10,194..196) ──", flush=True)
    for y in (194, 195, 196):
        n = (10, y, "N")
        print(f"   ({10},{y}) glyph={w.at(10,y)!r} on-graph={n in w.succ} "
              f"live_in={{{','.join(sorted(w.live_in.get(n, frozenset())))}}}", flush=True)
    # one column of westward slack: is column 9 free above them?
    for y in (193, 194, 195, 196, 197):
        print(f"   col 9 row {y}: {w.at(9,y)!r} occupied={w.occupied(9,y)}  "
              f"col 10 row {y}: {w.at(10,y)!r} occupied={w.occupied(10,y)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
