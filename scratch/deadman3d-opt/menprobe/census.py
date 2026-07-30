"""Static glyph census: little men (`@`), splitters (`Y`), grid cells, per tier."""
import sys, collections
sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG

d3, hires, M, prog = setup()

def census(tag, m):
    c = collections.Counter()
    for row in m.rows:
        for ch in row:
            if ch != " ":
                c[ch] += 1
    cells = sum(c.values())
    print(f"{tag}: {m.width}x{m.height} bbox={m.width*m.height:,} cells={cells:,} "
          f"@={c['@']} Y={c['Y']}", flush=True)

census("taped", M.build_for(SLUG, program=prog, store="taped"))
for shape in ((14, 65), (15, 61), (18, 51)):
    M.STORE_SHAPE[SLUG] = shape
    M.SEEK_TIER_LAYOUT[(SLUG, "men-v3")] = {"rom_rows": 119}
    census(f"men-v3 {shape[0]}x{shape[1]}",
           M.build_for(SLUG, program=prog, store="men-v3"))
