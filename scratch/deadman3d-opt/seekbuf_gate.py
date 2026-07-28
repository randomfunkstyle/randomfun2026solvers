"""Head-to-head gate for ROM_BUFFER x SEEK_OPS on deadman-3d.

usage: seekbuf_gate.py <tier> <n_commands|full> <rom_buffer|-> <seek_ops csv|-> [mem_pad|-]

Every knob is a per-slug registry override, so "-" means "the shipped default".
Same fixed input on every arm: a prefix of `deadman3d.WALK`.
"""
import sys
import time

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M

tier = sys.argv[1]
ncmd = sys.argv[2]
buf = sys.argv[3]
ops = sys.argv[4] if len(sys.argv) > 4 else "-"
pad = sys.argv[5] if len(sys.argv) > 5 else "-"

if buf != "-":
    M.ROM_BUFFER["deadman-3d"] = int(buf)
if ops != "-":
    M.SEEK_OPS_FOR["deadman-3d"] = tuple(ops.split(","))
if pad != "-":
    M.SEEK_MEM_PAD["deadman-3d"] = None if pad == "search" else int(pad)

t0 = time.time()
m = M.build_for("deadman-3d", store=tier)
src = "\n".join(m.rows)
w, h = max(len(r) for r in m.rows), len(m.rows)
print(f"tier={tier} buf={buf} ops={ops} pad={M.SEEK_MEM_PAD['deadman-3d']}")
print(f"  dims {w}x{h}  max={max(w, h)}  area={w * h:,}  build {time.time() - t0:.1f}s", flush=True)

walk = d3.WALK if ncmd == "full" else d3.WALK[: int(ncmd)]
case = d3.cases_json(walk)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]
t0 = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=8_000_000_000)
print(
    f"  cmds={len(walk)} rounds={len(frames)} ticks={res.step:,} "
    f"passed={res.passed} fatal={res.fatal} ({time.time() - t0:.1f}s)",
    flush=True,
)
