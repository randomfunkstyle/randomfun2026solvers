#!/usr/bin/env python3
"""Why ``STORE_ANSWER_WEST`` cannot be placed on ``deadman-3d_hires``.

The decline was inherited from a sweep taken *before* hires had a
``TIER_LAYOUT store_offset`` at all, and the offset that
:data:`machine.STORE_REQUEST_REACH` now needs moves the store block west by
exactly the kind of distance the collapse wanted — so it is worth re-asking.

The two windows do intersect, and only just:

* the collector's wall is ``(CX + W + 4) - tx_pre`` and ``tx_pre`` carries
  ``store_dx``, so it is ``-18 - store_dx`` and the guard wants ``>= 1``:
  **dx <= -19**;
* the roof needs request column ``101 + dx`` inside the adapter floor
  ``81..92``: **dx in -20..-9**.

Intersection: ``dx in {-19, -20}``.  Both fail.  This reports *what* fails and,
more usefully, whether the failure moves when ``dx`` does — because the target
column is named in **machine** coordinates, and if the collision does not move
then no store offset can ever fix it.

    python scratch/deadman3d-opt/hires_answer.py [dx ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
KEY = (SLUG, "taped")


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    offsets = [int(a) for a in argv] or [-19, -20, -21, -25, -30, -40, -60]
    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)

    # What the shipped machine looks like, so the collision has a context.
    M.TAPED_FEED_TELEPORT.add(KEY)
    M.STORE_REQUEST_REACH.add(KEY)
    ship = M.build_for(SLUG, program=prog, store="taped")
    print(f"shipped: {ship.width}x{ship.height}, "
          f"{sum(r.count('@>Rv') for r in ship.rows)} forward-only rooms")
    col = 82
    occupied = [(y, ship.rows[y][col]) for y in range(len(ship.rows))
                if col < len(ship.rows[y]) and ship.rows[y][col] not in " "]
    runs: list[tuple[int, int, str]] = []
    for y, ch in occupied:
        if runs and runs[-1][1] == y - 1 and runs[-1][2] == ch:
            runs[-1] = (runs[-1][0], y, ch)
        else:
            runs.append((y, y, ch))
    print(f"column {col} of the shipped grid, by run:")
    for a, b, ch in runs:
        print(f"    rows {a:>3}..{b:<3} {ch!r}")

    print("\nSTORE_ANSWER_WEST, per offset (roof on where the window allows):")
    for dx in offsets:
        for roof in (True, False):
            if roof and not -20 <= dx <= -9:
                continue
            M.STORE_ANSWER_WEST.add(KEY)
            M.TIER_LAYOUT[KEY] = {"store_offset": (dx, 0)}
            (M.STORE_REQUEST_REACH.add if roof else M.STORE_REQUEST_REACH.discard)(KEY)
            try:
                m = M.build_for(SLUG, program=prog, store="taped")
                print(f"  dx={dx:>4} roof={int(roof)}: PLACED {m.width}x{m.height}, "
                      f"{sum(r.count('@>Rv') for r in m.rows)} forward-only rooms")
            except Exception as exc:  # noqa: BLE001
                print(f"  dx={dx:>4} roof={int(roof)}: {exc}")
            finally:
                M.STORE_ANSWER_WEST.discard(KEY)
                M.TIER_LAYOUT.pop(KEY, None)
                M.STORE_REQUEST_REACH.add(KEY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
