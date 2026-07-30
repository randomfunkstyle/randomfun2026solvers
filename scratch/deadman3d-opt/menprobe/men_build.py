import sys, time, traceback
sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG

d3, hires, M, prog = setup()
n = M.TAPE_SIZE[SLUG]
KEY = (SLUG, "men-v3")
print("TAPE_SIZE", n, "default ROM_ROWS", M.ROM_ROWS.get(SLUG), flush=True)

SHAPES = [(15, 61), (14, 65), (16, 57), (18, 51), (12, 76), (10, 91), (20, 46),
          (24, 38), (30, 31)]
ROWS = [119]
for rom in ROWS:
    M.SEEK_TIER_LAYOUT[KEY] = {"rom_rows": rom}
    for cols, rows in SHAPES:
        assert cols * rows >= n, (cols, rows)
        M.STORE_SHAPE[SLUG] = (cols, rows)
        t = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store="men-v3")
            print(f"  rom={rom} {cols:3d}x{rows:3d} = {cols*rows:5d}  ->  "
                  f"{m.width}x{m.height}  max={max(m.width,m.height)}  "
                  f"({time.time()-t:.0f}s)", flush=True)
        except Exception as exc:
            print(f"  rom={rom} {cols:3d}x{rows:3d} = {cols*rows:5d}  ->  FAILED "
                  f"{type(exc).__name__}: {str(exc)[:200]}  ({time.time()-t:.0f}s)",
                  flush=True)
