"""men-v3 knob sweep for hires: rom_rows, store_offset, STORE_OPS.

usage: men_knobs.py <rounds> <shape colsxrows> <variant> [variant ...]
variant syntax: rom=N | off=DX,DY | ops=N | pad=N  (comma-joined for combos)
"""
import sys, time
sys.path.insert(0, "/tmp/menprobe")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
KEY = (SLUG, "men-v3")
rounds = int(sys.argv[1])
cols, rws = (int(v) for v in sys.argv[2].split("x"))
variants = sys.argv[3:]
inp, frames = tour(hires, rounds)
print(f"tour {len(frames)} rounds, shape {cols}x{rws}", flush=True)

M.STORE_SHAPE[SLUG] = (cols, rws)
for spec in variants:
    rom, off, ops, pad = 119, None, None, None
    for part in spec.split(","):
        k, _, v = part.partition("=")
        if k == "rom":
            rom = int(v)
        elif k == "off":
            off = tuple(int(t) for t in v.split(":"))
        elif k == "ops":
            ops = int(v)
        elif k == "pad":
            pad = int(v)
    M.SEEK_TIER_LAYOUT[KEY] = {"rom_rows": rom}
    M.TIER_LAYOUT.pop(KEY, None)
    if off is not None:
        M.TIER_LAYOUT[KEY] = {"store_offset": off}
    M.STORE_OPS[SLUG] = 1 if ops is None else ops
    M.MEM_PAD_FOR.pop(KEY, None)
    if pad is not None:
        M.MEM_PAD_FOR[KEY] = pad
    t = time.time()
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
    except Exception as exc:
        print(f"  {spec:>24}: BUILD FAILED {type(exc).__name__}: {str(exc)[:140]}",
              flush=True)
        continue
    print(f"  {spec:>24}: built {m.width}x{m.height} ({time.time()-t:.0f}s)", flush=True)
    run(m, inp, frames, spec)
