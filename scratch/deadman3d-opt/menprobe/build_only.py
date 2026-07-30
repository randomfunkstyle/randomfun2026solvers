import sys, time
sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG

d3, hires, M, prog = setup()
print("TAPE_SIZE", M.TAPE_SIZE[SLUG], "P", prog.P, flush=True)
t = time.time()
m = M.build_for(SLUG, program=prog, store="taped")
print(f"taped {m.width}x{m.height} in {time.time()-t:.1f}s", flush=True)
