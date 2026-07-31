"""Every pipe touch the CPU is checked against, and every glyph's margin.

The pad floor is a statement about *touches*, so this is the map any east-side
idea has to be argued against: which touch pins which glyph, and by how much.
Margin is ``second-nearest - nearest`` under the engines' key (distance, then
attach cell in reading order), so **margin 0 is a tie** and the sign of a tie is
which attach reads first.

usage: touches.py [men-v3|taped ...]
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402

CAP: dict = {}


def main():
    d3, hires, M, prog = setup()
    Band = M.Band
    strict = M.check_bindings

    for store in (sys.argv[1:] or ["men-v3"]):
        CAP.clear()
        # ``build_for`` tries **all 40 pads** and keeps the smallest footprint, so
        # "the last check_bindings call" is pad 39, not the pad that shipped. Key
        # the capture by pad and select on ``m.mem_pad`` afterwards.
        orig_asm = M._assemble
        cur = {}

        def asm(*a, **k):
            cur["pad"] = a[5]
            return orig_asm(*a, **k)

        def spy(glyphs, touches):
            strict(glyphs, touches)          # a failing trial never gets past this
            CAP[cur["pad"]] = (list(glyphs), dict(touches))
        M._assemble, M.check_bindings = asm, spy
        try:
            m = M.build_for(SLUG, program=prog, store=store)
        finally:
            M._assemble, M.check_bindings = orig_asm, strict

        glyphs, touches = CAP[m.mem_pad]
        print(f"\n===== {store}: {m.width}x{m.height} mem_pad={m.mem_pad} =====")
        incoming = {"rom", "in", "mem_resp", Band.STREAM_RESP}
        print("  touches (attach cell, i.e. the segment on this room's wall):")
        for n, (x, y) in sorted(touches.items(), key=lambda kv: (kv[1][1], kv[1][0])):
            print(f"    {str(n):14s} ({x},{y})  {'IN ' if n in incoming else 'OUT'}")

        rows = []
        for x, y, glyph, band in glyphs:
            want = ("mem_req" if glyph == "s" else "mem_resp") if band == Band.MEM else band
            rivals = {
                n: abs(px - x) + abs(py - y)
                for n, (px, py) in touches.items()
                if (n in incoming) == (glyph == "r")
            }
            if want not in rivals or len(rivals) < 2:
                continue
            order = sorted(rivals, key=lambda n: (rivals[n], touches[n][1], touches[n][0]))
            margin = rivals[order[1]] - rivals[order[0]]
            rows.append((margin, x, y, glyph, want, order[1], rivals[want], rivals[order[1]]))
        rows.sort()
        print(f"\n  tightest bindings ({len(rows)} glyphs checked):")
        print(f"    {'margin':>6} {'glyph':>7} {'wants':<14} {'rival':<14} {'d(want)':>7} {'d(rival)':>8}")
        for mg, x, y, g, want, riv, dw, dr in rows[:12]:
            note = "  <-- TIE, won on reading order" if mg == 0 else ""
            print(f"    {mg:>6} {g!r} ({x},{y})  {str(want):<14} {str(riv):<14} "
                  f"{dw:>7} {dr:>8}{note}")


if __name__ == "__main__":
    main()
