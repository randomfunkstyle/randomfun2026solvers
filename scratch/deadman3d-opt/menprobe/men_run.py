import sys, time
sys.path.insert(0, "/tmp/menprobe")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
KEY = (SLUG, "men-v3")
rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 21
shapes = []
for a in sys.argv[2:]:
    c, r = a.split("x")
    shapes.append((int(c), int(r)))
if not shapes:
    shapes = [(15, 61)]
inp, frames = tour(hires, rounds)
print(f"tour {len(frames)} rounds", flush=True)

M.SEEK_TIER_LAYOUT[KEY] = {"rom_rows": 119}
for cols, r in shapes:
    M.STORE_SHAPE[SLUG] = (cols, r)
    t = time.time()
    m = M.build_for(SLUG, program=prog, store="men-v3")
    print(f"built {cols}x{r} -> {m.width}x{m.height} ({time.time()-t:.0f}s)", flush=True)
    run(m, inp, frames, f"men-v3 {cols}x{r}")
