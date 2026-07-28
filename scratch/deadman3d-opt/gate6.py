"""Native tick gate for the taped tier under registry overrides.

usage: gate6.py <n_commands|full> [rom_rows|-] [banks csv|-] [bank_order csv|-]
"""
import sys
import time

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M

ncmd = sys.argv[1]
rr = sys.argv[2] if len(sys.argv) > 2 else "-"
banks = sys.argv[3] if len(sys.argv) > 3 else "-"
order = sys.argv[4] if len(sys.argv) > 4 else "-"

if rr != "-":
    M.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] = {"rom_rows": int(rr)}
if banks != "-":
    M.TAPED_BANKS["deadman-3d"] = tuple(int(x) for x in banks.split(","))
if order != "-":
    M.TAPED_BANK_ORDER[("deadman-3d", "taped")] = tuple(int(x) for x in order.split(","))

t0 = time.time()
m = M.build_for("deadman-3d", store="taped")
src = "\n".join(m.rows)
print(
    f"rr={M.SEEK_TIER_LAYOUT[('deadman-3d','taped')]} "
    f"banks={M.TAPED_BANKS['deadman-3d']} "
    f"order={M.TAPED_BANK_ORDER.get(('deadman-3d','taped'))}"
)
print(f"  dims {m.width}x{m.height}  max={max(m.width, m.height)}  build {time.time()-t0:.1f}s", flush=True)

walk = d3.WALK if ncmd == "full" else d3.WALK[: int(ncmd)]
case = d3.cases_json(walk)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]
t0 = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=8_000_000_000)
print(
    f"  cmds={len(walk)} rounds={len(frames)} ticks={res.step:,} "
    f"passed={res.passed} fatal={res.fatal} ({time.time()-t0:.1f}s)",
    flush=True,
)
