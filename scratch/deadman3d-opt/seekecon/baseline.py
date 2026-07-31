"""Re-measure both tiers at 3a88d75, shipped registries, 21 rounds."""
import sys, time
sys.path.insert(0, "/tmp/seekecon")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
inp, frames = tour(hires, 21)
print(f"tour {len(frames)} rounds; program instrs={len(prog.instrs)} words={len(prog.words)}",
      flush=True)
for store in ("men-v3", "taped"):
    t = time.time()
    m = M.build_for(SLUG, program=prog, store=store)
    print(f"built {store} -> {m.width}x{m.height} ({time.time()-t:.0f}s)", flush=True)
    res = run(m, inp, frames, f"BASELINE {store}")
    print(f"  {store}: t/instr = {res.frame_ticks[-1]/res.step:.4f}", flush=True)
