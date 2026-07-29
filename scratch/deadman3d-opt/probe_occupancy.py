from randomfun2026solvers.lm1.machine import build_for


def analyse(tag, m):
    rows = list(m.rows)
    h = len(rows)
    w = max(len(r) for r in rows)
    pad = [r.ljust(w) for r in rows]
    print(f"=== {tag}: rows {w} x {h} (machine {m.width}x{m.height})")
    empty_rows = [y for y in range(h) if pad[y].strip() == ""]
    empty_cols = [x for x in range(w) if all(pad[y][x] == " " for y in range(h))]
    print("  fully empty rows:", empty_rows)
    print("  fully empty cols:", empty_cols)
    # right extent histogram: which rows reach the max column
    ext = [len(pad[y].rstrip()) - 1 for y in range(h)]
    mx = max(ext)
    print(f"  max right extent {mx}; rows reaching it: {[y for y in range(h) if ext[y]==mx][:30]}")
    # for each column from the right, how many rows occupy it
    for x in range(w - 1, max(-1, w - 25), -1):
        n = sum(1 for y in range(h) if pad[y][x] != " ")
        if n:
            print(f"    col {x}: {n} occupied rows, first={[y for y in range(h) if pad[y][x]!=' '][:6]}")
    for y in range(h - 1, max(-1, h - 8), -1):
        n = sum(1 for x in range(w) if pad[y][x] != " ")
        print(f"    row {y}: {n} occupied cols, first={[x for x in range(w) if pad[y][x]!=' '][:6]}")
    # CPU band scan (rows 64..130)
    band = range(64, min(131, h))
    bempty = [x for x in range(w) if all(pad[y][x] == " " for y in band)]
    print("  cols empty within rows 64..130:", bempty[:5], "...", bempty[-8:], f"(n={len(bempty)})")
    return pad


for store in ("men-v3", "taped"):
    m = build_for("deadman-3d", store=store)
    analyse(store, m)
