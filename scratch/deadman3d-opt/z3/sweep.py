"""Joint (pad x rom-drop) sweep on a *synthesised* geometry, decided exactly.

Two facts, both measured against real captures rather than assumed:

* ``mem_pad`` enters the grid only through ``mem_x``, so a pad delta moves the
  22 MEM glyphs that many columns in x and touches **nothing** else --
  ``analyse.py`` diffs pad 0/1/2/3 and finds exactly that;
* ``rom_touch_drop`` enters only through ``touches["rom"] = (CX-1, CY + centre +
  drop)``, so a drop delta moves that one attach in y and nothing else
  (``machine.py:6627``; ``fetch_y`` at 5937 is the corridor it draws, not a glyph).

So one captured geometry decides the whole (pad, drop) plane in milliseconds,
and builds are spent only on confirming the frontier.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bind import decide, geom, load, margins  # noqa: E402


def synth(glyphs, touches, dpad=0, ddrop=0, dresp=0, dreq=0, din=(0, 0)):
    """Shift MEM glyphs by `dpad` in x, `rom` by `ddrop` in y, etc."""
    g = [
        (x + (dpad if b == "mem" else 0), y, gl, b) for x, y, gl, b in glyphs
    ]
    t = dict(touches)
    if ddrop:
        t["rom"] = (t["rom"][0], t["rom"][1] + ddrop)
    if dresp:
        t["mem_resp"] = (t["mem_resp"][0], t["mem_resp"][1] + dresp)
    if dreq:
        t["mem_req"] = (t["mem_req"][0], t["mem_req"][1] + dreq)
    if din != (0, 0) and "in" in t:
        t["in"] = (t["in"][0] + din[0], t["in"][1] + din[1])
    return g, t


def main():
    recs = load(sys.argv[1])
    base = next(r for r in recs if r["knobs"].get("pad") == 1)
    g0, t0 = geom(base)
    BASE_PAD, BASE_DROP = 1, base["knobs"].get("drop", 11)
    print(f"base: pad={BASE_PAD} drop={BASE_DROP} rom={t0['rom']} "
          f"mem_resp={t0['mem_resp']} in={t0['in']}", flush=True)

    mem_x = min(x for x, _, _, b in g0 if b == "mem")
    print(f"mem band's west column at pad {BASE_PAD}: {mem_x}  "
          f"(so mem_x = {mem_x} - CX)", flush=True)

    print("\n=== pad x rom_touch_drop: '.' binds, digit = #violations ===", flush=True)
    drops = list(range(BASE_DROP - 6, BASE_DROP + 15))
    pads = list(range(0, 6))
    print("      drop " + " ".join(f"{d:3d}" for d in drops), flush=True)
    for pad in pads:
        row = []
        for d in drops:
            g, t = synth(g0, t0, dpad=pad - BASE_PAD, ddrop=d - BASE_DROP)
            bad = decide(g, t)
            row.append("  ." if not bad else f"{len(bad):3d}")
        print(f"  pad {pad}  " + " ".join(row), flush=True)

    print("\n=== the binding frontier: lowest pad per drop, and what blocks below ===",
          flush=True)
    for d in drops:
        floor = None
        for pad in range(0, 12):
            g, t = synth(g0, t0, dpad=pad - BASE_PAD, ddrop=d - BASE_DROP)
            if not decide(g, t):
                floor = pad
                break
        if floor is None:
            print(f"  drop {d:3d}: no pad 0..11 binds", flush=True)
            continue
        g, t = synth(g0, t0, dpad=floor - BASE_PAD, ddrop=d - BASE_DROP)
        m = margins(g, t)
        below = decide(*synth(g0, t0, dpad=floor - 1 - BASE_PAD, ddrop=d - BASE_DROP))
        blk = below[0] if below else None
        print(
            f"  drop {d:3d}: floor pad {floor}  min-slack {m[0][0]} "
            f"({m[0][3]!r} at ({m[0][1]},{m[0][2]}) {m[0][4]} vs {m[0][5]})"
            + (f"   | pad {floor - 1} blocked by '{blk[2]}'@({blk[0]},{blk[1]})"
               f" wants {blk[3]}: {blk[4]}" if blk else ""),
            flush=True,
        )


if __name__ == "__main__":
    main()
