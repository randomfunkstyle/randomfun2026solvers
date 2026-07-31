"""The pocket question, answered generally: is there ANY wall cell where a return
pipe can touch such that a home ``r`` near the fetch binds it and nothing else
breaks?  All four walls, every home cell in the return band -- not just the west
wall the stale analysis looked at.

Also counts the CPU room's men (`@`) and splits (`Y`), which is the premise the
whole "the man must walk home" argument rests on.

usage: pocket2.py [store]
"""
import sys

from common import setup, SLUG

INC = {"rom", "in", "mem_resp", "stream_resp"}


def main():
    d3, hires, M, prog = setup()
    store = sys.argv[1] if len(sys.argv) > 1 else "men-v3"

    cap = {}
    real = M.check_bindings

    def spy(glyphs, touches):
        out = real(glyphs, touches)
        if len(glyphs) > cap.get("n", -1):
            cap.update(n=len(glyphs), glyphs=list(glyphs), touches=dict(touches))
        return out

    M.check_bindings = spy
    try:
        m = M.build_for(SLUG, program=prog, store=store)
    finally:
        M.check_bindings = real

    glyphs, touches = cap["glyphs"], cap["touches"]
    rows = m.rows
    fx, fy, fw, fh = m.regions["cpu:fetch"]
    print(f"===== {store}: {m.width}x{m.height} =====")

    # ---- CPU room bounds: walk out from the fetch until a wall glyph ----
    wx = fx - 1
    while rows[fy][wx] not in "|+":
        wx -= 1
    ex = fx
    while rows[fy][ex] not in "|+":
        ex += 1
    ny = fy
    while rows[ny][fx] not in "-+":
        ny -= 1
    sy = fy
    while rows[sy][fx] not in "-+":
        sy += 1
    print(f"CPU room walls: x={wx}..{ex}  y={ny}..{sy}  (interior {ex-wx-1}x{sy-ny-1})")

    men = [(x, y) for y in range(ny, sy + 1) for x in range(wx, ex + 1)
           if rows[y][x] == "@"]
    splits = [(x, y) for y in range(ny, sy + 1) for x in range(wx, ex + 1)
              if rows[y][x] == "Y"]
    halts = [(x, y) for y in range(ny, sy + 1) for x in range(wx, ex + 1)
             if rows[y][x] == "H"]
    bigR = [(x, y) for y in range(ny, sy + 1) for x in range(wx, ex + 1)
            if rows[y][x] in "RUq"]
    print(f"CPU room: {len(men)} '@' at {men}, {len(splits)} 'Y', {len(halts)} 'H', "
          f"{len(bigR)} 'R'/'U'/'q' at {bigR}")

    # ---- the general pocket search ----
    incoming_glyphs = [(x, y, g, ("mem_resp" if str(b) == "mem" else str(b)))
                       for x, y, g, b in glyphs if g == "r"]
    print(f"\n{len(incoming_glyphs)} incoming ('r') glyphs must keep their pipe.")
    by_want = {}
    for _x, _y, _g, w in incoming_glyphs:
        by_want[w] = by_want.get(w, 0) + 1
    print(f"  by pipe: {by_want}")

    # candidate wall cells: every cell on the four walls (a pipe may touch anywhere)
    wall = ([(wx, y) for y in range(ny + 1, sy)] + [(ex, y) for y in range(ny + 1, sy)]
            + [(x, ny) for x in range(wx + 1, ex)] + [(x, sy) for x in range(wx + 1, ex)])
    # touch cells sit one outside the wall
    def outside(c):
        x, y = c
        if x == wx:
            return (x - 1, y)
        if x == ex:
            return (x + 1, y)
        if y == ny:
            return (x, y - 1)
        return (x, y + 1)

    # candidate home cells: anywhere in the fetch/return band (rows fy-2..fy+2,
    # the high corridor row and the collector row), west half of the room.
    hi = m.regions.get("cpu:return:high")
    col = m.regions["cpu:return:collector"]
    cand_rows = sorted({fy - 2, fy - 1, fy, fy + 1, fy + 2,
                        hi[1] if hi else fy, col[1]})
    homes = [(x, y) for y in cand_rows for x in range(wx + 1, wx + 14)]

    def ok(home, touch):
        t2 = dict(touches)
        t2["ret"] = touch
        hx, hy = home
        d = {n: abs(px - hx) + abs(py - hy) for n, (px, py) in t2.items()
             if str(n) in INC or n == "ret"}
        b = min(d.values())
        if d["ret"] != b or sum(1 for v in d.values() if v == b) > 1:
            return False
        for gx, gy, _g, want in incoming_glyphs:
            dd = {n: abs(px - gx) + abs(py - gy) for n, (px, py) in t2.items()
                  if str(n) in INC or n == "ret"}
            bb = min(dd.values())
            if dd[want] != bb or sum(1 for v in dd.values() if v == bb) > 1:
                return False
        return True

    hits = []
    for home in homes:
        for c in wall:
            t = outside(c)
            if ok(home, t):
                hits.append((home, c, t))
    print(f"\nsearched {len(homes)} home cells x {len(wall)} wall cells = "
          f"{len(homes)*len(wall)} pairs")
    if not hits:
        print("  NO PAIR WORKS — the pocket is sealed on every wall.")
    else:
        print(f"  {len(hits)} legal pairs; first 25:")
        for home, c, t in hits[:25]:
            print(f"    home r at {home}, wall cell {c}, touch {t}")
        hr = sorted({h for h, _, _ in hits})
        print(f"  distinct home cells: {len(hr)} -> {hr[:20]}")

    # ---- why: the fetch `r` and any home `r` are 2 cells apart on one row ----
    print("\n--- why the west wall in particular cannot work ---")
    print("rom touches the west wall, so for glyphs on the SAME row the x-term is")
    print("common and cancels: a home `r` binds ret iff |y-ret_y| < |y-rom_y|, and")
    print("the fetch `r` on that same row needs the opposite. Contradiction.")
    print(f"  rom touch = {touches['rom']}, fetch `r`s at "
          f"{[(x,y) for x,y,g,_ in glyphs if g=='r' and y==fy and x<fx+fw]}")


if __name__ == "__main__":
    main()
