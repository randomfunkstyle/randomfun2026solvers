"""Is the pocket beside the CPU fetch still sealed on CURRENT geometry?

Dumps every region whose box intersects a window around the fetch, plus the raw
grid crop, so the west-wall/above-fetch band can be judged from cells rather
than from an inherited comment.

usage: pocket.py [store] [x0 x1 y0 y1]
"""
import sys

from common import setup, SLUG


def crop(m, x0, x1, y0, y1, label=""):
    rows = m.rows
    print(f"\n--- grid crop {label} x={x0}..{x1} y={y0}..{y1} ---")
    hdr = "       " + "".join(str((x // 10) % 10) for x in range(x0, x1 + 1))
    hdr2 = "       " + "".join(str(x % 10) for x in range(x0, x1 + 1))
    print(hdr)
    print(hdr2)
    for y in range(y0, min(y1 + 1, len(rows))):
        r = rows[y]
        seg = "".join(r[x] if x < len(r) else " " for x in range(x0, x1 + 1))
        print(f"{y:5d} |{seg}|")


def regions_in(m, x0, x1, y0, y1):
    out = []
    for n, (x, y, w, h) in m.regions.items():
        if x <= x1 and x + w - 1 >= x0 and y <= y1 and y + h - 1 >= y0:
            out.append((n, x, y, w, h))
    out.sort(key=lambda t: (t[2], t[1]))
    return out


def main():
    d3, hires, M, prog = setup()
    store = sys.argv[1] if len(sys.argv) > 1 else "men-v3"
    m = M.build_for(SLUG, program=prog, store=store)
    print(f"===== {store}: {m.width}x{m.height} =====")
    fx, fy, fw, fh = m.regions["cpu:fetch"]
    print(f"fetch box x={fx}..{fx+fw-1} y={fy}..{fy+fh-1}")

    if len(sys.argv) > 5:
        x0, x1, y0, y1 = (int(v) for v in sys.argv[2:6])
    else:
        x0, x1, y0, y1 = 0, fx + 40, fy - 30, fy + 12

    x0 = max(0, x0)
    y0 = max(0, y0)
    print(f"\n--- regions intersecting x={x0}..{x1} y={y0}..{y1} ---")
    for n, x, y, w, h in regions_in(m, x0, x1, y0, y1):
        print(f"  {n:40s} x={x:4d}..{x+w-1:4d}  y={y:4d}..{y+h-1:4d} ({w}x{h})")

    crop(m, x0, x1, y0, y1, store)


if __name__ == "__main__":
    main()
