#!/usr/bin/env python3
"""Every ``mem_pad`` the answer collapse is tried at, and how each one fails.

``build_for`` reports only the *last* pad's error, which reads like one hard
obstruction when it may be forty different near-misses.  This forces the pad
(``MEM_PAD`` makes ``pads`` a single-element list) and collects all of them, so
the decline can name the constraint instead of quoting a symptom.

    python scratch/deadman3d-opt/hires_answer_pads.py [dx]
"""
from __future__ import annotations

import collections
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

    dx = int(argv[0]) if argv else -19
    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)

    for roof in (True, False):
        M.STORE_ANSWER_WEST.add(KEY)
        (M.STORE_REQUEST_REACH.add if roof else M.STORE_REQUEST_REACH.discard)(KEY)
        M.TIER_LAYOUT[KEY] = {"store_offset": (dx, 0)}
        tally: collections.Counter[str] = collections.Counter()
        placed = []
        for pad in range(0, 40):
            M.MEM_PAD[SLUG] = pad
            try:
                m = M.build_for(SLUG, program=prog, store="taped")
                placed.append((pad, m.width, m.height))
            except Exception as exc:  # noqa: BLE001
                tally[str(exc).replace("no pad pair makes every pipe bind; last: ", "")] += 1
            finally:
                M.MEM_PAD.pop(SLUG, None)
        print(f"dx={dx} roof={int(roof)}: {len(placed)}/40 pads placed")
        for pad, w, h in placed:
            print(f"    pad {pad}: {w}x{h}")
        for msg, n in tally.most_common():
            print(f"    x{n:<3} {msg}")
        M.STORE_ANSWER_WEST.discard(KEY)
        M.STORE_REQUEST_REACH.add(KEY)
        M.TIER_LAYOUT.pop(KEY, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
