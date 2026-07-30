import sys
sys.path.insert(0, "/tmp/menprobe")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
inp, frames = tour(hires, 21)
print(f"tour {len(frames)} rounds, tape={M.TAPE_SIZE[SLUG]}", flush=True)
m = M.build_for(SLUG, program=prog, store="taped")
run(m, inp, frames, "taped-baseline")
