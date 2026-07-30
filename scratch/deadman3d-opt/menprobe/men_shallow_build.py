import sys, time
sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG

d3, hires, M, prog = setup()
n = M.TAPE_SIZE[SLUG]
KEY = (SLUG, "men-v3")
M.SEEK_TIER_LAYOUT[KEY] = {"rom_rows": 119}

SHAPES = [(20, 46), (23, 40), (26, 35), (30, 31), (34, 27), (38, 24), (42, 22),
          (46, 20), (52, 18), (57, 16), (65, 14), (75, 13), (90, 11), (113, 8)]
for cols, rows in SHAPES:
    assert cols * rows >= n, (cols, rows)
    M.STORE_SHAPE[SLUG] = (cols, rows)
    t = time.time()
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
        print(f"  {cols:4d}x{rows:3d} = {cols*rows:5d}  ->  {m.width}x{m.height}"
              f"  max={max(m.width,m.height)}  ({time.time()-t:.0f}s)", flush=True)
    except Exception as exc:
        print(f"  {cols:4d}x{rows:3d} = {cols*rows:5d}  ->  FAILED "
              f"{type(exc).__name__}: {str(exc)[:150]}  ({time.time()-t:.0f}s)", flush=True)
