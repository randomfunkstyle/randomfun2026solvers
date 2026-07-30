"""What actually moves when a lever moves — the data the geometry model is fitted to.

Prints, for the shipped point and one step along each axis, the captured touches
and glyphs *as a function of ``mem_pad``*, so the model can be derived rather
than guessed.  Run once; :mod:`hz_geom` encodes what it finds.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hz_core as H  # noqa: E402

AXES = [
    ("squash_band", [5, 6, 8, 9, 12]),
    ("rom_touch_drop", [0, 5, 6, 8, 9, 12]),
    ("rom_rows", [118, 120, 125, 130]),
    ("store_dy", [8, 9, 11, 12]),
    ("store_dx", [-2, -1, 1, 2]),
    ("seek_slab_pitch", [10, 12]),
    ("store_cols", [15, 17, 19, 20]),
    ("straight_trie", [False]),
    ("folded_lanes", [False]),
    ("tucked_drops", [False]),
    ("seek_taken_drop_east", [False]),
    ("seek_tight_struct_drops", [False]),
    ("lane_pitch", [2]),
]


def summary(c, base_pads=None):
    if not c.pads:
        return f"no pad reached §7.1; early={sorted(set(c.early.values()))[:1]}"
    out = [f"pads_seen={min(c.pads)}..{max(c.pads)}({len(c.pads)}) good={c.good}"]
    # how does the geometry move with the pad itself?
    if base_pads is not None:
        p0 = min(set(c.pads) & set(base_pads), default=None)
        if p0 is not None:
            g1, t1 = c.pads[p0]
            g0, t0 = base_pads[p0]
            dt = {n: (t1[n][0] - t0[n][0], t1[n][1] - t0[n][1])
                  for n in t0 if n in t1}
            out.append(f"@pad{p0} dtouch={{{', '.join(f'{n}:{d}' for n, d in dt.items() if d != (0,0)) or 'none'}}}")
            s0 = {(x, y): f"{gl}{b}" for x, y, gl, b in g0}
            s1 = {(x, y): f"{gl}{b}" for x, y, gl, b in g1}
            if s0 == s1:
                out.append("glyphs=same")
            else:
                shifts = set()
                for dxy in ((0, -12), (0, -10), (0, -5), (0, -2), (0, -1), (0, 1),
                            (0, 2), (0, 5), (0, 10), (0, 12), (-1, 0), (1, 0)):
                    if {(x + dxy[0], y + dxy[1]): v for (x, y), v in s0.items()} == s1:
                        shifts.add(dxy)
                out.append(f"glyphs=rigid{sorted(shifts)}" if shifts
                           else f"glyphs=RESHAPED(+{len(set(s1)-set(s0))}/-{len(set(s0)-set(s1))})")
    return "  ".join(out)


def main():
    base = H.shipped()
    print("shipped:", base.label(H.P()) or "(== P() defaults)", flush=True)
    print("       ", base, flush=True)
    b = H.capture(base)
    print(f"\nbase: {summary(b)}  ({b.secs:.1f}s)")
    if b.pads:
        pad0 = b.good[0] if b.good else min(b.pads)
        gl, tc = b.pads[pad0]
        print(f"touches @pad{pad0}:", {n: v for n, v in sorted(tc.items())})
        # how the geometry moves with the pad
        for pad in sorted(b.pads)[:6]:
            g2, t2 = b.pads[pad]
            dt = {n: (t2[n][0] - tc[n][0], t2[n][1] - tc[n][1]) for n in tc}
            same = ({(x, y) for x, y, _, _ in g2} == {(x, y) for x, y, _, _ in gl})
            print(f"   pad={pad:2d} dtouch="
                  f"{ {n: d for n, d in dt.items() if d != (0,0)} } glyphs_same={same}")

    for name, vals in AXES:
        for v in vals:
            c = H.capture(H.bump(base, **{name: v}))
            print(f"\n{name}={v}: {summary(c, b.pads)}", flush=True)
            if not c.binds:
                print(f"     why: {c.reason[:200]}")


if __name__ == "__main__":
    main()
