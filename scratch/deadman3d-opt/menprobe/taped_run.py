import sys
sys.path.insert(0, "/tmp/menprobe")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 21
inp, frames = tour(hires, rounds)
print(f"tour {len(frames)} rounds", flush=True)
m = M.build_for(SLUG, program=prog, store="taped")
run(m, inp, frames, "taped")
