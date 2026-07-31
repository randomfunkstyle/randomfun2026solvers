"""Why is ``mem_pad`` what it is? Report every pad the search rejects, verbatim.

``build_for`` sweeps ``pads = range(0, 40)`` and keeps the first that assembles,
so the shipped ``mem_pad`` is already a floor **for whatever reason each smaller
pad failed**. The reason matters: a §7.1 binding failure is a geometry fact that
only moves when a wall or a pipe touch moves; anything else might be a bug.

Also reports what each pad would have been worth, so the floor is priced.

usage: padfloor.py [men-v3|taped ...]
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402
from dropcols import SHARE  # noqa: E402


def main():
    d3, hires, M, prog = setup()
    for store in (sys.argv[1:] or ["men-v3", "taped"]):
        print(f"\n===== {store} =====")
        seen = []
        orig = M._assemble

        def spy(*a, **k):
            pad = a[5]
            try:
                out = orig(*a, **k)
            except M.MachineError as e:
                seen.append((pad, "FAIL", str(e)))
                raise
            seen.append((pad, "ok", ""))
            return out

        M._assemble = spy
        try:
            m = M.build_for(SLUG, program=prog, store=store)
        finally:
            M._assemble = orig
        for pad, ok, msg in seen:
            print(f"  mem_pad={pad:<3} {ok:<4} {msg[:180]}")
        good = [p for p, ok, _ in seen if ok == "ok"]
        print(f"  -> shipped mem_pad = {min(good) if good else None}")

        # price one column of mem_pad: only lanes that carry a MEM band move.
        R = m.regions
        tx, ty, tw, th = R["cpu:trie"]
        lane_x0 = tx + tw
        lanes = {n.split(":")[-1]: b for n, b in R.items() if n.startswith("cpu:lane:")}
        mem_share = 0.0
        for name, (bx, by, bw, bh) in lanes.items():
            drop = next((x for x in range(lane_x0, m.width) if m.rows[by][x] == "v"), None)
            if drop is None:
                continue
            text = m.rows[by][lane_x0:drop]
            # a MEM lane is one whose glyph run is pushed east by a `.` gap; the
            # `s` that lands on ``mem_x`` is the first glyph after the gap.
            if "." in text and any(c not in ". " for c in text[text.index("."):]):
                mem_share += SHARE.get(name, 0.0)
        print(f"  lanes whose drop would move with mem_x: share {mem_share:.4f} "
              f"-> {2*mem_share:.3f} cells/instr per column of pad")


if __name__ == "__main__":
    main()
