"""What does ``lane_x0 = 9`` cost at the pad now that ties are decidable?

``TRIE_SLACK_ROWS`` reaches ``lane_x0`` 9 by opening one blank band row, and was
parked at **+1.97%** because the pad floor rose 3 -> 4: ``mem_x = lane_x0 +
prefixes + pad``, so the column the trie gives back the pad takes straight away
and only the non-MEM lanes (0.458 of instructions) gain -- against a whole extra
band row.

With the reading-order clause the floor without the row is **2**. This reports
the floor *with* it, which decides the trade:

  * floor 2  -> mem_x moves west too, every lane gains a column (2.436 t/instr),
               and only the added row is against it;
  * floor 3  -> mem_x is unchanged, only non-MEM lanes gain (~0.92 cells/instr),
               and the added row almost certainly still eats it.

Build-only; no tour.

usage: slackpad.py [rank ...]
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402

KEY = (SLUG, "men-v3")


def probe(M, prog, ranks, in_west=None, dy=None):
    # ``INPUT_NORTH_WEST`` is a distance *west of* ``lane_x0``, so every column the
    # trie gives back this has to give back too or the I room walks off the north
    # wall. Shipped 9 at lane_x0 10; 8 at lane_x0 9.
    if in_west is not None:
        M.INPUT_NORTH_WEST[KEY] = in_west
    # the added band row de-levels the store's request leg; TRIE_SLACK_ROWS' own
    # note says store_offset dy is the unique compensation (shipped (-2, 10)).
    if dy is not None:
        lay = dict(M.TIER_LAYOUT[KEY])
        dx, dy0 = lay["store_offset"]
        lay["store_offset"] = (dx, dy)
        M.TIER_LAYOUT[KEY] = lay
    if ranks is None:
        M.TRIE_SLACK_ROWS.pop(KEY, None)
    else:
        M.TRIE_SLACK_ROWS[KEY] = ranks
    seen = []
    orig = M._assemble

    def spy(*a, **k):
        pad = a[5]
        try:
            out = orig(*a, **k)
        except M.MachineError as e:
            seen.append((pad, str(e)))
            raise
        seen.append((pad, None))
        return out

    M._assemble = spy
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
    except M.MachineError as e:
        return None, None, [(None, str(e))]
    finally:
        M._assemble = orig
    return m, min(p for p, e in seen if e is None), [t for t in seen if t[1]]


def main():
    d3, hires, M, prog = setup()
    base_dy = M.TIER_LAYOUT[KEY]["store_offset"][1]
    cases = [(None, 9, base_dy)]
    cases += [((20,), 8, base_dy + d) for d in range(-3, 4)]
    for ranks, w, dy in cases:
        m, pad, rejects = probe(M, prog, ranks, w, dy)
        tag = ("none" if ranks is None else str(ranks)) + f"/w{w}/dy{dy}"
        if m is None:
            print(f"TRIE_SLACK_ROWS={tag:<8} BUILD FAILED: {rejects[0][1][:140]}")
            continue
        R = m.regions
        tx, ty, tw, th = R["cpu:trie"]
        lanes = [b for n, b in R.items() if n.startswith("cpu:lane:")]
        print(f"TRIE_SLACK_ROWS={tag:<8} lane_x0={tx+tw} mem_pad={pad} "
              f"grid={m.width}x{m.height} band_rows={max(b[1] for b in lanes) - min(b[1] for b in lanes) + 1}")
        for p, e in rejects:
            print(f"    pad={p}: {e[:150]}")


if __name__ == "__main__":
    main()
