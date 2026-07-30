"""Re-measure the 52dbadf men-v3 baseline at 21 rounds, exactly as shipped."""
import sys, time
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WT / "solvers" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import setup, tour, SLUG  # noqa: E402

d3, hires, M, prog = setup()
inp, frames = tour(hires, 21)
t = time.time()
m = M.build_for(SLUG, program=prog, store="men-v3")
print(f"build {time.time()-t:.0f}s -> {m.width}x{m.height}", flush=True)

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402

t = time.time()
res = FastLittleman("\n".join(m.rows)).run(
    inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
print(f"run {time.time()-t:.0f}s  fatal={res.fatal} passed={res.passed}", flush=True)
print(f"BASELINE step={res.step:,} last_frame={res.frame_ticks[-1]:,} "
      f"box={m.width}x{m.height}", flush=True)
