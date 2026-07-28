"""Native round-gated tick gate for a deadman-3d machine variant.

usage: gate.py <tier> <n_commands|full> [rom_rows] [banks csv] [skip_batch] [mem_pad]
"""
import sys
import time

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M

tier = sys.argv[1]
ncmd = sys.argv[2]
if len(sys.argv) > 3 and sys.argv[3] != "-":
    M.ROM_ROWS["deadman-3d"] = int(sys.argv[3])
if len(sys.argv) > 4 and sys.argv[4] != "-":
    M.TAPED_BANKS["deadman-3d"] = tuple(int(x) for x in sys.argv[4].split(","))
if len(sys.argv) > 5 and sys.argv[5] != "-":
    M.TAPED_SKIP_BATCH["deadman-3d"] = int(sys.argv[5])
if len(sys.argv) > 6 and sys.argv[6] != "-":
    M.MEM_PAD["deadman-3d"] = int(sys.argv[6])

t0 = time.time()
m = M.build_for("deadman-3d", store=tier)
src = "\n".join(m.rows)
w, h = max(len(r) for r in m.rows), len(m.rows)
print(f"tier={tier} rom_rows={M.ROM_ROWS['deadman-3d']} banks={M.TAPED_BANKS['deadman-3d']} "
      f"sb={M.TAPED_SKIP_BATCH['deadman-3d']} pad={M.MEM_PAD['deadman-3d']}")
print(f"  dims {w}x{h}  max={max(w, h)}  build {time.time()-t0:.1f}s")

walk = d3.WALK if ncmd == "full" else d3.WALK[: int(ncmd)]
case = d3.cases_json(walk)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]
t0 = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=4_000_000_000)
print(f"  rounds={len(frames)} ticks={res.step:,} passed={res.passed} fatal={res.fatal} "
      f"({time.time()-t0:.1f}s)")
