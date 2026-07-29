"""Round-gated tick gate for the staggered lane band.

usage: stagger_gate.py <pitch> <store_dy> <n_commands|full>

Sets only the two knobs this experiment owns — `LANE_PITCH` and the taped tier's
`store_offset` dy — and leaves every other registry alone.
"""

import sys
import time

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M

pitch = int(sys.argv[1])
store_dy = int(sys.argv[2])
ncmd = sys.argv[3]

KEY = ("deadman-3d", "taped")
if pitch != 2:
    M.LANE_PITCH[KEY] = pitch
if store_dy:
    layout = dict(M.TIER_LAYOUT.get(KEY, {}))
    dx, dy = layout.get("store_offset", (0, 0))
    layout["store_offset"] = (dx, dy + store_dy)
    M.TIER_LAYOUT[KEY] = layout

t0 = time.time()
m = M.build_for("deadman-3d", store="taped")
src = "\n".join(m.rows)
w, h = max(len(r) for r in m.rows), len(m.rows)
print(f"pitch={pitch} store_dy={store_dy:+d}  {w}x{h}  build {time.time() - t0:.1f}s",
      flush=True)

walk = d3.WALK if ncmd == "full" else d3.WALK[: int(ncmd)]
case = d3.cases_json(walk)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]
t0 = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=4_000_000_000)
print(f"  rounds={len(frames)} ticks={res.step:,} passed={res.passed} "
      f"fatal={res.fatal} ({time.time() - t0:.1f}s)", flush=True)
