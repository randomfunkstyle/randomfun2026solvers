"""The real pipe-binding table at the CPU, and where a *return* pipe could touch.

Captures ``check_bindings``' own arguments, prints every ``r``/``s`` with its
margin, then asks the question the dispatch teleport needs answered: for each
candidate touch cell on the CPU wall, would a home ``r`` beside the fetch bind a
return pipe there **without** breaking any existing binding?

usage: bindprobe.py [store]
"""
import sys

from common import setup, SLUG


def main():
    d3, hires, M, prog = setup()
    store = sys.argv[1] if len(sys.argv) > 1 else "men-v3"

    cap = {}
    real = M.check_bindings

    def spy(glyphs, touches):
        # build_for retries (MEM_PAD etc.), so failing trials also come through
        # here. Only the call that *survives* describes the shipped grid.
        out = real(glyphs, touches)
        if len(glyphs) > cap.get("n", -1):
            cap["n"] = len(glyphs)
            cap["glyphs"] = list(glyphs)
            cap["touches"] = dict(touches)
        return out

    M.check_bindings = spy
    try:
        m = M.build_for(SLUG, program=prog, store=store)
    finally:
        M.check_bindings = real

    glyphs, touches = cap["glyphs"], cap["touches"]
    print(f"===== {store}: {m.width}x{m.height} =====")
    fx, fy, fw, fh = m.regions["cpu:fetch"]
    print(f"fetch box x={fx}..{fx+fw-1} y={fy}..{fy+fh-1}")
    print(f"\ntouches ({len(touches)}):")
    for n, (x, y) in sorted(touches.items(), key=lambda kv: kv[1]):
        print(f"  {str(n):16s} ({x:4d},{y:4d})")

    INC = {"rom", "in", "mem_resp", "stream_resp"}

    def want_of(glyph, band):
        if str(band) == "mem":
            return "mem_req" if glyph == "s" else "mem_resp"
        return band

    print(f"\npipe glyphs ({len(glyphs)}):  margin = 2nd-nearest - bound")
    rows = []
    for x, y, glyph, band in glyphs:
        want = want_of(glyph, band)
        rivals = sorted(
            ((abs(px - x) + abs(py - y), n) for n, (px, py) in touches.items()
             if (str(n) in INC) == (glyph == "r")),
        )
        d_want = next(d for d, n in rivals if str(n) == str(want))
        second = next((d for d, n in rivals if str(n) != str(want)), 10**9)
        rows.append((x, y, glyph, str(want), d_want, second - d_want, rivals[:3]))
    for x, y, glyph, want, d, marg, riv in sorted(rows, key=lambda r: (r[1], r[0])):
        rr = " ".join(f"{n}:{dd}" for dd, n in riv)
        print(f"  {glyph!r} ({x:4d},{y:4d}) -> {want:12s} d={d:3d} margin={marg:+4d}   [{rr}]")

    # ---- the question: a return pipe touching the CPU wall, read by a home `r` ----
    print("\n--- candidate return-pipe touch cells on the CPU west wall ---")
    print("a home `r` would sit just east of the fetch's second `r`; test it at the")
    print("fetch row and one row either side.  A candidate passes only if the home")
    print("`r` binds `ret` strictly AND every existing `r` keeps its own pipe.\n")

    incoming_glyphs = [(x, y, g, want_of(g, b)) for x, y, g, b in glyphs if g == "r"]
    homes = [(fx + fw, fy), (fx + fw, fy - 1), (fx + fw, fy + 1), (fx + 1, fy)]
    wall_x = min(px for px, _ in touches.values())
    ys = sorted({y for _, y in touches.values()} | set(range(fy - 20, fy + 12)))

    ok_any = False
    for hx, hy in homes:
        good = []
        for ty in ys:
            t2 = dict(touches)
            t2["ret"] = (wall_x, ty)
            # home r must bind ret strictly
            d = {n: abs(px - hx) + abs(py - hy) for n, (px, py) in t2.items()
                 if str(n) in INC or n == "ret"}
            best = min(d.values())
            if d["ret"] != best or sum(1 for v in d.values() if v == best) > 1:
                continue
            # every existing r must keep its pipe
            broken = None
            for gx, gy, _g, want in incoming_glyphs:
                dd = {n: abs(px - gx) + abs(py - gy) for n, (px, py) in t2.items()
                      if str(n) in INC or n == "ret"}
                b = min(dd.values())
                if dd[want] != b or sum(1 for v in dd.values() if v == b) > 1:
                    broken = (gx, gy, want, sorted(dd.items(), key=lambda kv: kv[1])[:3])
                    break
            if broken is None:
                good.append(ty)
        ok_any = ok_any or bool(good)
        print(f"  home `r` at ({hx},{hy}): {len(good)} legal touch rows"
              f"{'  ' + str(good) if good else ''}")
    if not ok_any:
        print("\n  NONE — no touch row on the west wall lets a home `r` coexist.")


if __name__ == "__main__":
    main()
