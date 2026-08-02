#!/usr/bin/env python3
"""Apply the proved reroutes to the built grid and re-check it statically.

This does **not** run a tour — that is the user's loop.  What it does do is the
part a tour cannot: confirm that the patched grid still *parses* (``lm.mjs
analyze`` is the reference loader, so a load error here is a load error there),
that the CPU man's walk graph still has no terminal state, that every glyph the
old graph executed is still executed, and that the arm walks are shorter by
exactly the predicted number of cells.

    uv run python scratch/deadman3d-opt/live/validate_patch.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cpu_live as L  # noqa: E402

LM = REPO / "littleman" / "lm.mjs"
GRID = Path("/tmp/d3hires-taped/grid.man")
META = Path("/tmp/d3hires-taped/meta.json")
PATCHED = Path("/tmp/d3hires-taped/grid-early-turn.man")

#: (x, y, new glyph, note).  Each is a turn column moved west; the old ``^``
#: is left in place and simply stops being reachable.
PATCH = [
    (18, 174, "^", "BRZ neg riser 24 -> 18 (base+9 -> base+3)"),
    (29, 176, "^", "BRN zero riser 32 -> 29 (base+6 -> base+3)"),
]


def spawn_and_graph(grid: list[str], regions: dict):
    cpu = [v for k, v in regions.items() if k.startswith("cpu:")]
    box = (min(v[0] for v in cpu) - 2, min(v[1] for v in cpu) - 2,
           max(v[0] + v[2] for v in cpu) + 4, max(v[1] + v[3] for v in cpu) + 4)
    return L.build(grid, L.find_spawn(grid, box))


def arm_walk(w: L.WalkGraph, start: L.Node, home_x: int, limit: int = 400) -> int:
    """Cells walked from ``start`` until the man is west of ``home_x``.

    Follows the unique successor; a branch on the way means the arm is not a
    straight-line return and the length is not well defined, which is reported
    as ``-1``.
    """
    n, seen = start, 0
    while seen < limit:
        outs = w.succ.get(n, ())
        if len(outs) != 1:
            return -1
        n = outs[0]
        seen += 1
        if n[0] <= home_x:
            return seen
    return -1


def main() -> int:
    grid = L.load(GRID)
    meta = json.loads(META.read_text())
    regions = {k: tuple(v) for k, v in meta["regions"].items()}
    before = spawn_and_graph(grid, regions)

    patched = list(grid)
    for x, y, ch, note in PATCH:
        old = patched[y][x]
        patched[y] = patched[y][:x] + ch + patched[y][x + 1:]
        print(f"patch ({x},{y}) {old!r} -> {ch!r}   {note}", flush=True)
    PATCHED.write_text("\n".join(patched) + "\n")

    out = subprocess.run([str(LM), "analyze", str(PATCHED), "--json"],
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        print("\nLOAD ERROR from the reference analyser:", flush=True)
        print((out.stderr or out.stdout)[:800], flush=True)
        return 1
    an = json.loads(out.stdout)
    print(f"\nlm.mjs analyze: OK — {len(an.get('rooms') or [])} rooms, "
          f"{len(an.get('pipes') or [])} pipes, "
          f"{len(an.get('displays') or [])} display(s)", flush=True)

    after = spawn_and_graph(patched, regions)
    print(f"\nwalk graph {len(before.succ):,} -> {len(after.succ):,} nodes; "
          f"terminals {len(before.terminal)} -> {len(after.terminal)}", flush=True)
    if after.terminal:
        print(f"   !! terminals: {sorted(after.terminal)[:8]}", flush=True)

    def work(w):
        return {(x, y) for (x, y, _h) in w.succ if w.at(x, y) not in ". @"}

    lost = work(before) - work(after)
    gained = work(after) - work(before)
    print(f"executed non-nop cells: {len(work(before))} -> {len(work(after))}", flush=True)
    print(f"   no longer executed: {sorted(lost)}", flush=True)
    print(f"   newly executed:     {sorted(gained)}", flush=True)

    print("\narm walk lengths (cells from the slab's X to the fetch column):", flush=True)
    for label, node in (("BRZ neg (ACC<0)", (17, 174, "E")),
                        ("BRZ pos (ACC>0)", (17, 176, "E")),
                        ("BRN zero (ACC==0)", (28, 176, "E")),
                        ("BRN pos (ACC>0)", (28, 177, "E"))):
        a = arm_walk(before, node, 10)
        b = arm_walk(after, node, 10)
        print(f"   {label:<20} {a:>4} -> {b:>4}   saves {a - b} ticks/execution",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
