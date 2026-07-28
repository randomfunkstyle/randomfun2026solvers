"""Region / extent probe for the taped tier under registry overrides.

usage: probe6.py [rom_rows|-] [banks csv|-] [bank_order csv|-]
"""
import sys

from randomfun2026solvers.lm1 import machine as M

rr = sys.argv[1] if len(sys.argv) > 1 else "-"
banks = sys.argv[2] if len(sys.argv) > 2 else "-"
order = sys.argv[3] if len(sys.argv) > 3 else "-"
if rr != "-":
    M.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] = {"rom_rows": int(rr)}
if banks != "-":
    M.TAPED_BANKS["deadman-3d"] = tuple(int(x) for x in banks.split(","))
if order != "-":
    M.TAPED_BANK_ORDER[("deadman-3d", "taped")] = tuple(int(x) for x in order.split(","))

m = M.build_for("deadman-3d", store="taped")
print(f"{m.width}x{m.height}")
rows = m.rows
widest = max(range(len(rows)), key=lambda i: len(rows[i]))
print(f"widest row {widest} len {len(rows[widest])}")
for y, r in enumerate(rows):
    if len(r) >= m.width - 1:
        print(f"  row {y}: len {len(r)}  tail {r[-40:]!r}")
print("--- regions (x, y, w, h), east edge:")
for name, (x, y, w, h) in sorted(m.regions.items(), key=lambda kv: -(kv[1][0] + kv[1][2])):
    print(f"  {name:24s} x={x:4d} y={y:4d} w={w:4d} h={h:4d}  east={x+w:4d} south={y+h:4d}")
