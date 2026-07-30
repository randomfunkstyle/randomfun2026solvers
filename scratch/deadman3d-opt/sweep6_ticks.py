"""Tick curve over rom_rows for the taped tier (native engine).

usage: sweep6_ticks.py <n_commands|full> <rom_rows csv>
"""
import sys
import time

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M

ncmd = sys.argv[1]
rows = [int(x) for x in sys.argv[2].split(",")]
if len(sys.argv) > 3 and sys.argv[3] != "-":
    M.TAPED_BANKS["deadman-3d"] = tuple(int(x) for x in sys.argv[3].split(","))
if len(sys.argv) > 4 and sys.argv[4] != "-":
    M.TAPED_BANK_ORDER[("deadman-3d", "taped")] = tuple(int(x) for x in sys.argv[4].split(","))

walk = d3.WALK if ncmd == "full" else d3.WALK[: int(ncmd)]
case = d3.cases_json(walk)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]
print(f"cmds={len(walk)} rounds={len(frames)}", flush=True)

base = None
for rr in rows:
    M.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] = {"rom_rows": rr}
    t0 = time.time()
    m = M.build_for("deadman-3d", store="taped")
    src = "\n".join(m.rows)
    try:
        res = FastLittleman(src).run(inp, frames=frames, max_ticks=8_000_000_000)
    except Exception as exc:  # noqa: BLE001
        print(
            f"  rr={rr:3d}  {m.width}x{m.height} max={max(m.width,m.height):3d}  "
            f"RUNFAIL {type(exc).__name__}: {str(exc)[:80]}",
            flush=True,
        )
        continue
    if base is None:
        base = res.step
    print(
        f"  rr={rr:3d}  {m.width}x{m.height} max={max(m.width,m.height):3d}  "
        f"ticks={res.step:>13,}  {100*(res.step-base)/base:+.2f}%  "
        f"passed={res.passed} fatal={res.fatal}  ({time.time()-t0:.0f}s)",
        flush=True,
    )
