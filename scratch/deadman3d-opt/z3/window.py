"""For one captured geometry, the whole (drop, squash) window that binds.

The picture the sweeps kept missing: the band's westmost MEM ``r`` is squeezed
between two rivals that move in *opposite* directions.

* ``rom`` attaches on the west wall at ``CY + centre + drop``. Raising the drop
  walks it south, away from the band's rows, and that is what buys a pad column
  (two drop rows a column: the pad moves the glyph one west, which costs one
  against ``rom`` and one against ``mem_resp``).
* ``in`` attaches on the CPU's **north** wall at ``(in_x, CY - 1)``, and the
  squash does not move the north wall. So a squash walks every glyph and every
  other touch *north past a stationary* ``in``, and eventually ``in`` steals the
  same ``r``.

Holding ``drop - squash`` (the ROM corridor, which is what the tour prices)
therefore does not hold the binding: it slides the band up into ``in``. The
window below is where both rivals are beaten at once.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bind import decide, geom, load, margins  # noqa: E402
from frontier import synth  # noqa: E402


def window(rec, drops, squashes, dpads=(0,)):
    g0, t0 = geom(rec)
    kn = rec["knobs"]
    d0, s0 = kn.get("drop", 11), kn.get("squash", 6)
    for dpad in dpads:
        print(f"\n  pad {kn.get('pad', 1) + dpad}   "
              f"rows = rom_touch_drop, cols = squash_band  ('.' binds)", flush=True)
        print("        squash " + " ".join(f"{s:3d}" for s in squashes), flush=True)
        for d in drops:
            cells = []
            for s in squashes:
                g, t = synth(g0, t0, dpad=dpad, ddrop=d - d0, dsquash=s - s0)
                bad = decide(g, t)
                if not bad:
                    cells.append("  .")
                else:
                    who = bad[0][4]
                    nearest = who[0][0] if isinstance(who, list) else "?"
                    cells.append(f"  {'r' if nearest == 'rom' else 'i' if nearest == 'in' else 'q'}")
            print(f"  drop {d:3d}    " + " ".join(cells), flush=True)
        print("        ('r' = rom steals it, 'i' = in steals it)", flush=True)


if __name__ == "__main__":
    recs = []
    for p in sys.argv[1:]:
        recs += load(p)
    for r in recs:
        if "glyphs" not in r:
            continue
        print(f"\n=== base capture {r['knobs']} ({r['w']}x{r['h']}) ===", flush=True)
        window(r, range(9, 22), range(4, 15))
