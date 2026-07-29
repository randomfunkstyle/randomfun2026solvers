"""Does a taller taped block build at some other fold? build-only.

usage: sweep6_tall.py <plan csv> <order csv> <rom_rows csv>
"""
import sys

from randomfun2026solvers.lm1 import machine as M

plan = tuple(int(x) for x in sys.argv[1].split(","))
order = tuple(int(x) for x in sys.argv[2].split(","))
rows = [int(x) for x in sys.argv[3].split(",")]
M.TAPED_BANKS["deadman-3d"] = plan
M.TAPED_BANK_ORDER[("deadman-3d", "taped")] = order
for rr in rows:
    M.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] = {"rom_rows": rr}
    try:
        m = M.build_for("deadman-3d", store="taped")
        print(f"  rr={rr:3d}  {m.width}x{m.height}  max={max(m.width,m.height)}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  rr={rr:3d}  FAIL {str(exc)[:80]}", flush=True)
