"""Native tick gate for the taped tier, with an optional ROM-lap inflation.

usage: gate.py <n_commands|full|tour> [data_w_delta] [rom_rows]
"""
import sys
import time

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1 import seekrom as SR

ncmd = sys.argv[1]
delta = int(sys.argv[2]) if len(sys.argv) > 2 else 0
rr = sys.argv[3] if len(sys.argv) > 3 else "-"
if rr != "-":
    M.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] = {"rom_rows": int(rr), "store_offset": (-20, 0)}

if delta:
    # Inflate every token by `delta` blank cells: a pure lap-length control.
    _tc = SR.token_cells
    SR.token_cells = lambda w: " " * delta + _tc(w)

t0 = time.time()
m = M.build_for("deadman-3d", store="taped")
src = "\n".join(m.rows)
rx, ry, rw, rh = m.regions["rom"]
print(
    f"delta={delta} rr={rr}  dims {m.width}x{m.height}  rom {rw}x{rh}@({rx},{ry})"
    f"  rows={m.rom_rows}  lap~{(rw-16)*m.rom_rows}  build {time.time()-t0:.1f}s",
    flush=True,
)

if ncmd == "tour":
    from pathlib import Path
    txt = Path("littleman/examples/deadman-3d_tour.input.txt").read_text()
    rounds = [r.strip().split() for r in txt.split("/")]
    inp = " / ".join(" ".join(r) for r in rounds)
    frames = None
    raise SystemExit("tour path needs frames; use tour6.py")

walk = d3.WALK if ncmd == "full" else d3.WALK[: int(ncmd)]
case = d3.cases_json(walk)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]
t0 = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=8_000_000_000)
print(
    f"  cmds={len(walk)} ticks={res.step:,} passed={res.passed} fatal={res.fatal}"
    f" ({time.time()-t0:.1f}s)",
    flush=True,
)
