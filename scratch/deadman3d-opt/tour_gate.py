"""Native round-gated run of the checked-in 115-frame tour."""
import sys
import time
from pathlib import Path

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman

tier = sys.argv[1]
EX = Path(__file__).resolve().parents[2] / "littleman" / "examples"
man = EX / ("deadman-3d.man" if tier == "men-v3" else "deadman-3d_taped.man")
src = man.read_text().rstrip("\n")
rows = src.split("\n")

# The chords `plan_tour.py` last wrote (it drops a sibling `.chords.txt`);
# pass a path to override. The assert below is what actually pins the tour.
chords_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/t.chords.txt")
chords = chords_path.read_text().strip()
cmds = [d3.keys("." if c == "," else c) for c in chords]
case = d3.cases_json(cmds)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
assert inp.replace(" / ", " ").split() == \
    EX.joinpath("deadman-3d_tour.input.txt").read_text().split(), "tour input drift"
frames = [r["frames"] for r in case["rounds"]]
print(f"{tier}: {max(len(r) for r in rows)}x{len(rows)}  rounds={len(frames)}")
t = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=6_000_000_000)
print(f"  ticks={res.step:,} passed={res.passed} fatal={res.fatal} ({time.time()-t:.0f}s)")
