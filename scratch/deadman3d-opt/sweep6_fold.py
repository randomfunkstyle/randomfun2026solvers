"""Build-only rom_rows re-sweep for the taped tier after the answer-path collapse.

usage: sweep6_fold.py <rom_rows csv or lo:hi:step>
"""
import sys
import time

from randomfun2026solvers.lm1 import machine as M

spec = sys.argv[1]
if ":" in spec:
    lo, hi, step = (int(x) for x in spec.split(":"))
    rows = list(range(lo, hi + 1, step))
else:
    rows = [int(x) for x in spec.split(",")]

for rr in rows:
    M.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] = {"rom_rows": rr}
    t = time.time()
    try:
        m = M.build_for("deadman-3d", store="taped")
    except Exception as exc:  # noqa: BLE001
        print(f"  rr={rr:3d}  FAIL {type(exc).__name__}: {str(exc)[:100]}", flush=True)
        continue
    w, h = m.width, m.height
    print(f"  rr={rr:3d}  {w}x{h}  max={max(w, h)}  area={w*h:,}  ({time.time()-t:.0f}s)", flush=True)
