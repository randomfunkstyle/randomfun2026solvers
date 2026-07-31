"""Gate ``TRIE_SLACK_ROWS`` on men-v3 at 21 rounds, re-derived post-tie-relaxation.

The lever was parked at **+1.97%** when the pad floor was 3 and the row took it to
4. It is now 2 without the row and 3 with it, so ``mem_x = lane_x0 + prefixes +
pad`` is **21 either way** -- the trie's column is handed straight back to the
pad, exactly as before, only one number lower. So the prediction is unchanged in
kind: only the 45.8% of instructions with no MEM band gain a column
(~0.92 cells/instr), against one more band row.

Needs two companions, both forced rather than chosen:
  * ``INPUT_NORTH_WEST`` 9 -> 8 (it is a distance west of ``lane_x0``);
  * ``store_offset`` dy 10 -> 9 (the added row de-levels the adapter's request
    leg: `store wall 159 vs adapter 158`; dy 7..13 swept, 9 is the only one).
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/tmp/menprobe")
from common import setup, tour, run, SLUG  # noqa: E402

INSTR = 880_332
BASE = 80_342_861  # men-v3 with the reading-order clause + INPUT_NORTH_WEST 9
KEY = (SLUG, "men-v3")


def main():
    d3, hires, M, prog = setup()
    M.TRIE_SLACK_ROWS[KEY] = (20,)
    M.INPUT_NORTH_WEST[KEY] = 8
    lay = dict(M.TIER_LAYOUT[KEY])
    lay["store_offset"] = (lay["store_offset"][0], 9)
    M.TIER_LAYOUT[KEY] = lay
    inp, frames = tour(hires, 21)
    t = time.time()
    m = M.build_for(SLUG, program=prog, store="men-v3")
    R = m.regions
    tx, ty, tw, th = R["cpu:trie"]
    print(f"built {m.width}x{m.height} lane_x0={tx+tw} mem_pad={m.mem_pad} "
          f"({time.time()-t:.0f}s)", flush=True)
    res = run(m, inp, frames, "men-v3+slack")
    print(f"    t/instr={res.frame_ticks[-1]/INSTR:.4f}   vs {BASE:,}: "
          f"{100*(res.frame_ticks[-1]-BASE)/BASE:+.3f}%")


if __name__ == "__main__":
    main()
