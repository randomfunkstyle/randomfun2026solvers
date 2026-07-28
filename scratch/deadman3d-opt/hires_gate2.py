"""Native round-gated tick gate for the built deadman-3d_hires machine.

Reads the local build (``littleman/examples/local/deadman-3d_hires.*``) rather
than rebuilding it, because the build is minutes and the gate is seconds.

usage: hires_gate2.py [n_rounds]
"""
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402

LOCAL = REPO / "littleman" / "examples" / "local"
src = (LOCAL / "deadman-3d_hires.man").read_text()
case = json.loads((LOCAL / "deadman-3d_hires.cases.json").read_text())
case = case["publicTestData"][0]
rounds = case["rounds"]
if len(sys.argv) > 1:
    rounds = rounds[: int(sys.argv[1])]

w, h = max(len(r) for r in src.splitlines()), len(src.splitlines())
print(f"machine {w}x{h}, {len(rounds)} rounds")
# No frame gating: the engine's display judge wants exactly one display and this
# machine has four, so the walk is fed as one flat stream and only the TICKS are
# read here.  Correctness against the model is the emulator's job
# (tests/test_deadman3d_hires.py compares every pixel of every frame); this is
# the cost measurement, and an ungated run is the ideal-gate cost — the input is
# always available, so the machine never stalls waiting for a round.
inp = " ".join(w for r in rounds for w in r["in"])
t0 = time.time()
res = FastLittleman(src).run(inp, max_ticks=40_000_000_000)
print(f"ticks={res.step:,} fatal={res.fatal} ({time.time() - t0:.1f}s)")
if len(rounds) > 1:
    print(f"~{res.step // (len(rounds) - 1):,} ticks/frame over the walk "
          "(round 0 is the boot burst and the title)")
