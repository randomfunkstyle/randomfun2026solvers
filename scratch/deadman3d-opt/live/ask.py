#!/usr/bin/env python3
"""Ask the transparency question about one cell, or print the whole map.

    uv run python scratch/deadman3d-opt/live/ask.py 29 175        # one cell
    uv run python scratch/deadman3d-opt/live/ask.py --map         # the CPU map

The map marks each cell of the CPU room with what a *crossing* man may do there:

    .   nop — free in every direction
    N S E W   transparent only to a man travelling that way (an idempotent steer)
    #   opaque: it writes a live register, moves a pipe value, turns, or halts
    ?   not walked by the CPU man at all, so there is no liveness to quote

"Live" is taken over **every** path through the cell, which is the conservative
reading: a cell the map calls opaque may still be transparent to one specific
path, and :mod:`reroute` re-asks the question per path when it matters.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cpu_live as L  # noqa: E402
from reroute import load_graph  # noqa: E402


def cell_report(w: L.WalkGraph, x: int, y: int) -> None:
    g = w.at(x, y)
    hs = sorted(w.headings_on(x, y))
    print(f"({x},{y}) glyph {g!r}   walked with headings {hs or 'never'}")
    if g != " " and L.is_instruction(g):
        eff = L.effect(g)
        print(f"   effect: needs {sorted(eff.needs) or '-'}, writes "
              f"{sorted(eff.writes) or '-'}, heading class {eff.heading}")
    for h in L.CW:
        n = (x, y, h)
        live = w.live_out.get(n)
        if live is None:
            # No path stands here heading h, so quote the union over all paths.
            live = frozenset().union(*(w.live_out[(x, y, k)] for k in hs)) if hs else frozenset()
            note = "(no path here; live = union over the paths that do)"
        else:
            note = ""
        v = L.transparent(g, live, h)
        print(f"   heading {h}: live_after={{{','.join(sorted(live)) or ''}}}  "
              f"{'TRANSPARENT' if v else 'opaque     '} — {v.reason} {note}")


def map_report(w: L.WalkGraph, regions: dict) -> None:
    cpu = [v for k, v in regions.items() if k.startswith("cpu:")]
    x0, x1 = min(v[0] for v in cpu) - 1, max(v[0] + v[2] for v in cpu) + 1
    y0, y1 = min(v[1] for v in cpu) - 1, max(v[1] + v[3] for v in cpu) + 1
    print("     " + "".join(str(x // 10 % 10) if x % 5 == 0 else " " for x in range(x0, x1)))
    print("     " + "".join(str(x % 10) for x in range(x0, x1)))
    for y in range(y0, y1):
        line = []
        for x in range(x0, x1):
            hs = sorted(w.headings_on(x, y))
            if not hs:
                line.append("?")
                continue
            live = frozenset().union(*(w.live_out[(x, y, h)] for h in hs))
            g = w.at(x, y)
            ok = [h for h in L.CW if L.transparent(g, live, h)]
            line.append("." if len(ok) == 4 else ("#" if not ok else ok[0]))
        print(f"{y:>4} " + "".join(line))
    print("\n  '.' free   'N/S/E/W' one-way only   '#' opaque   '?' not walked")


def main(argv: list[str]) -> int:
    w, regions = load_graph()
    if "--map" in argv:
        map_report(w, regions)
        return 0
    if len(argv) >= 2:
        cell_report(w, int(argv[0]), int(argv[1]))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
