"""Build-only dimension sweep for the seek drum on both deadman-3d tiers.

usage: seek_sweep.py <tier> <rom_rows csv> <mem_pad csv>
"""
import sys
import time

from randomfun2026solvers.lm1 import machine as M

tier = sys.argv[1]
rows_list = [int(x) for x in sys.argv[2].split(",")]
pads = [int(x) for x in sys.argv[3].split(",")]

best = None
for rr in rows_list:
    for pad in pads:
        M.SEEK_TIER_LAYOUT[("deadman-3d", tier)] = {"rom_rows": rr}
        M.SEEK_MEM_PAD["deadman-3d"] = pad
        t = time.time()
        try:
            m = M.build_for("deadman-3d", store=tier, seek=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  rr={rr:3d} pad={pad:3d}  FAIL {type(exc).__name__}: {str(exc)[:90]}")
            continue
        w, h = max(len(r) for r in m.rows), len(m.rows)
        mark = ""
        if best is None or max(w, h) < best[0]:
            best = (max(w, h), rr, pad, w, h)
            mark = "  <-- best"
        print(f"  rr={rr:3d} pad={pad:3d}  {w}x{h}  max={max(w,h)}  "
              f"skew={abs(w-h)/max(w,h)*100:.1f}%  ({time.time()-t:.0f}s){mark}")
print("best:", best)
