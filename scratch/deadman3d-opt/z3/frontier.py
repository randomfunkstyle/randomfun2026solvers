"""The joint (mem_pad x rom_touch_drop x squash_band) frontier.

Three synthesis rules, each *checked against a real capture* before it is used
(``--validate``), so the sweep below is a projection of measured geometry rather
than a model of the builder's arithmetic:

  R1  ``mem_pad`` d>=0: the 22 MEM glyphs move d columns in x; nothing else moves.
  R2  ``rom_touch_drop`` d: ``touches["rom"]`` moves d rows south; nothing else.
  R3  ``squash_band`` d: every glyph AND every touch **except ``in``** moves d
      rows north. ``in`` is pinned to ``(in_x, CY - 1)`` -- the CPU's *north*
      wall, which the squash does not move -- so a squash walks the whole machine
      up past a stationary input pipe. That asymmetry is the only reason binding
      is not invariant under the squash, and it is what refuses ``mem_pad`` -1 at
      the corridor-preserving squash.

R1 breaks below ``mem_pad`` 0 and the break is the interesting part: ``_flat_lane``
pushes a band's first glyph out with ``while x < target``, so a ``mem_x`` under a
lane's natural column is ignored *for that lane*. Below 0 the band stops being a
column and becomes ragged, six of the nine MEM lanes gain and three do not, and
``mem_pad`` -2 is byte-identical to -1. So the sweep uses a captured pad -1
geometry rather than synthesising one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bind import decide, geom, load, margins  # noqa: E402

BASE = {"pad": 1, "drop": 11, "squash": 6}


def synth(g0, t0, dpad=0, ddrop=0, dsquash=0):
    g = [(x + (dpad if b == "mem" else 0), y - dsquash, gl, b) for x, y, gl, b in g0]
    t = {
        n: (x, y - dsquash) if n != "in" else (x, y)
        for n, (x, y) in t0.items()
    }
    t["rom"] = (t["rom"][0], t["rom"][1] + ddrop)
    return g, t


def validate(recs):
    base = next(r for r in recs if r["knobs"] == {"pad": 1})
    g0, t0 = geom(base)
    ok = True
    print("=== synthesis rules vs real captures ===", flush=True)
    for r in recs:
        if "glyphs" not in r:
            continue
        kn = r["knobs"]
        pad = kn.get("pad", BASE["pad"])
        if pad < 0 or "ranks" in kn or "in_west" in kn:
            continue
        g, t = synth(
            g0, t0,
            dpad=pad - BASE["pad"],
            ddrop=kn.get("drop", BASE["drop"]) - BASE["drop"],
            dsquash=kn.get("squash", BASE["squash"]) - BASE["squash"],
        )
        gr, tr = geom(r)
        gm = "glyphs OK" if g == list(gr) else "GLYPH MISMATCH"
        tm = "touches OK" if t == tr else f"TOUCH MISMATCH {[(n, t[n], tr[n]) for n in t if t[n] != tr.get(n)]}"
        if "MISMATCH" in gm + tm:
            ok = False
        print(f"  {str(kn):50} {gm}, {tm}", flush=True)
    print(f"  ==> synthesis is {'EXACT' if ok else 'WRONG -- do not trust the sweep'}",
          flush=True)
    return g0, t0, ok


def frontier(g0, t0, neg=None):
    print("\n=== joint frontier: does (pad, drop, squash) bind? ===", flush=True)
    print("   eff = drop - squash is the ROM corridor; the shipped machine is eff 5.",
          flush=True)
    print("   mem_x = lane_x0(10) + max(prefixes)(1) + pad, and is what a MEM lane walks.\n",
          flush=True)
    hdr = f"  {'pad':>4} {'mem_x':>6} {'drop':>5} {'squash':>7} {'eff':>4}  verdict"
    print(hdr, flush=True)
    print("  " + "-" * (len(hdr) - 2), flush=True)
    rows = []
    for pad in (2, 1, 0):
        for eff in (5,):
            for squash in range(0, 15):
                drop = eff + squash
                g, t = synth(g0, t0, dpad=pad - BASE["pad"],
                             ddrop=drop - BASE["drop"], dsquash=squash - BASE["squash"])
                bad = decide(g, t)
                m = margins(g, t)
                rows.append((pad, 10 + 1 + pad, drop, squash, eff, not bad,
                             m[0] if m else None, bad[0] if bad else None))
    for pad, mx, drop, squash, eff, good, m, b in rows:
        v = f"BINDS (min-slack {m[0]}, {m[3]!r} at ({m[1]},{m[2]}) {m[4]} vs {m[5]})" if good \
            else f"refused: '{b[2]}'@({b[0]},{b[1]}) wants {b[3]}: {b[4]}"
        print(f"  {pad:>4} {mx:>6} {drop:>5} {squash:>7} {eff:>4}  {v}", flush=True)
    if neg is not None:
        print("\n  --- mem_pad -1 (ragged band: 6 of 9 lanes at mem_x 10, 3 at 11) ---",
              flush=True)
        gn, tn = geom(neg)
        nb = neg["knobs"]
        for squash in range(6, 13):
            drop = 5 + squash
            g, t = synth(gn, tn, ddrop=drop - nb.get("drop", 11),
                         dsquash=squash - nb.get("squash", 6))
            bad = decide(g, t)
            m = margins(g, t)
            v = f"BINDS (min-slack {m[0]})" if not bad else \
                f"refused: '{bad[0][2]}'@({bad[0][0]},{bad[0][1]}) wants {bad[0][3]}: {bad[0][4]}"
            print(f"  {-1:>4} {'10/11':>6} {drop:>5} {squash:>7} {5:>4}  {v}", flush=True)


if __name__ == "__main__":
    recs = []
    for p in sys.argv[1:]:
        recs += load(p)
    g0, t0, ok = validate(recs)
    neg = next((r for r in recs if r["knobs"].get("pad") == -1 and "glyphs" in r), None)
    frontier(g0, t0, neg)
