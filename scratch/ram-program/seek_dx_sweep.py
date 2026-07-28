"""Build-only store-dx x rom_rows sweep for the taped seek drum.

usage: seek_dx_sweep.py <tier> <rom_rows csv> <store dx csv> [mem_pad]
"""
import sys
import time

from randomfun2026solvers.lm1 import machine as M

tier = sys.argv[1]
rows_list = [int(x) for x in sys.argv[2].split(",")]
dxs = [int(x) for x in sys.argv[3].split(",")]
pad = int(sys.argv[4]) if len(sys.argv) > 4 else 22
M.SEEK_MEM_PAD["deadman-3d"] = pad

best = None
for rr in rows_list:
    for dx in dxs:
        M.SEEK_TIER_LAYOUT[("deadman-3d", tier)] = {
            "rom_rows": rr, "store_offset": (dx, 0)}
        t = time.time()
        try:
            m = M.build_for("deadman-3d", store=tier, seek=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  rr={rr:3d} dx={dx:4d}  FAIL {str(exc)[:80]}")
            continue
        w, h = max(len(r) for r in m.rows), len(m.rows)
        mark = ""
        if best is None or max(w, h) < best[0]:
            best = (max(w, h), rr, dx, w, h)
            mark = "  <-- best"
        print(f"  rr={rr:3d} dx={dx:4d}  {w}x{h}  max={max(w,h)}  "
              f"skew={abs(w-h)/max(w,h)*100:.1f}%  ({time.time()-t:.0f}s){mark}")
print("best:", best)
