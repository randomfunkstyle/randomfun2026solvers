"""The 8-command gate run off the CHECKED-IN grid, not a fresh build_for.

A build-time knob that is right in `build_for` and wrong on disk is the one
failure the tick numbers above would not show, so the shipped number is taken
from the file the repo actually carries.
"""
import sys
import time
from pathlib import Path

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman

MAN = Path("littleman/examples/deadman-3d_taped.man")
ncmd = int(sys.argv[1]) if len(sys.argv) > 1 else 8
src = MAN.read_text().rstrip("\n")
rows = src.split("\n")
print(f"{MAN}: {max(len(r) for r in rows)}x{len(rows)}")

walk = d3.WALK[:ncmd]
case = d3.cases_json(walk)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]
t0 = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=8_000_000_000)
print(
    f"  cmds={len(walk)} ticks={res.step:,} passed={res.passed} fatal={res.fatal}"
    f" ({time.time()-t0:.0f}s)"
)
