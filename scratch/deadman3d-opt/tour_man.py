"""Run the checked-in .man through the 115-frame tour (no rebuild).

usage: tour_man.py <path to .man>
"""
import sys
import time
from pathlib import Path

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman

EX = Path(__file__).resolve().parents[2] / "littleman" / "examples"
man = Path(sys.argv[1])
src = man.read_text().rstrip("\n")
rows = src.split("\n")
words = [int(w) for w in EX.joinpath("deadman-3d_tour.input.txt").read_text().split()]
boot = d3.preamble_words() + d3.title_words()
cmds = words[len(boot) :]
case = d3.cases_json(cmds)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]
print(f"{man.name}: {max(len(r) for r in rows)}x{len(rows)} rounds={len(frames)}")
t = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=6_000_000_000)
print(f"  ticks={res.step:,} passed={res.passed} fatal={res.fatal} ({time.time()-t:.0f}s)")
