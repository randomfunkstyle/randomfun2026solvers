"""`DDA_SPLIT_UNROLL` against the 116-round tour.

The split halves the copy, so the unroll that was the knee for one loop is not
the knee for two: a shorter unroll costs more backward laps, and `lap_via_jump`
has already made a lap a ~1,008-tick seek instead of a ~17,700-tick discard.
Sweep it on the real gate rather than re-using the old number.

    uv run python scratch/deadman3d-opt/split_unroll_sweep.py 12 13 14 15
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402
from randomfun2026solvers.lm1 import machine  # noqa: E402

EX = Path(__file__).resolve().parents[2] / "littleman" / "examples"
chords = Path(sys.argv[-1] if sys.argv[-1].endswith(".txt") else "/tmp/t.chords.txt")
args = [a for a in sys.argv[1:] if not a.endswith(".txt")]
cmds = [d3.keys("." if c == "," else c) for c in chords.read_text().strip()]
case = d3.cases_json(cmds)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
assert inp.replace(" / ", " ").split() == \
    EX.joinpath("deadman-3d_tour.input.txt").read_text().split(), "tour input drift"
frames = [r["frames"] for r in case["rounds"]]

base = d3.DDA_SPLIT_UNROLL
for unroll in (int(a) for a in (args or ("12", "13", "14", "15"))):
    d3.DDA_SPLIT_UNROLL = unroll
    prog = d3.taped_program()
    try:
        m = machine.build_for("deadman-3d", store="taped")
    except Exception as exc:  # noqa: BLE001
        print(f"unroll {unroll:>3}  P={prog.P:>5}  build failed: {exc}")
        continue
    t = time.time()
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, max_ticks=6_000_000_000
    )
    print(f"unroll {unroll:>3}  P={prog.P:>5}  {m.width}x{m.height}  "
          f"ticks={res.step:,}  passed={res.passed}  ({time.time() - t:.0f}s)")
d3.DDA_SPLIT_UNROLL = base
