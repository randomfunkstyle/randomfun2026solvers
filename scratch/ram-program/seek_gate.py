"""Native round-gated A/B gate: a built deadman-3d machine on the walk or the tour.

usage: seek_gate.py <tier> <walk|tour> <base|seek> [rom_rows] [mem_pad] [store_dx]
"""
import sys
import time
from pathlib import Path

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M

tier, gate, mode = sys.argv[1], sys.argv[2], sys.argv[3]
seek = mode == "seek"
arg = lambda i: sys.argv[i] if len(sys.argv) > i and sys.argv[i] != "-" else None  # noqa: E731
if seek:
    layout: dict[str, object] = {}
    if arg(4):
        layout["rom_rows"] = int(sys.argv[4])
    if arg(6):
        layout["store_offset"] = (int(sys.argv[6]), 0)
    if layout:
        base = dict(M.TIER_LAYOUT.get(("deadman-3d", tier), {}))
        base.update(layout)
        M.SEEK_TIER_LAYOUT[("deadman-3d", tier)] = base
    if arg(5):
        M.SEEK_MEM_PAD["deadman-3d"] = int(sys.argv[5])

t0 = time.time()
m = M.build_for("deadman-3d", store=tier, seek=seek)
src = "\n".join(m.rows)
w, h = max(len(r) for r in m.rows), len(m.rows)

EX = Path(__file__).resolve().parents[2] / "littleman" / "examples"
if gate == "tour":
    chords = Path(arg(7) or "/tmp/t.chords.txt").read_text().strip()
    cmds = [d3.keys("." if c == "," else c) for c in chords]
else:
    cmds = d3.WALK
case = d3.cases_json(cmds)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
if gate == "tour":
    assert inp.replace(" / ", " ").split() == \
        EX.joinpath("deadman-3d_tour.input.txt").read_text().split(), "tour input drift"
frames = [r["frames"] for r in case["rounds"]]
print(f"{tier}/{gate}/{mode}: {w}x{h} max={max(w,h)} skew={abs(w-h)/max(w,h)*100:.1f}% "
      f"area2={w*h:,} rounds={len(frames)} (build {time.time()-t0:.0f}s)")
t0 = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=8_000_000_000)
print(f"  ticks={res.step:,} passed={res.passed} fatal={res.fatal} ({time.time()-t0:.0f}s)")
