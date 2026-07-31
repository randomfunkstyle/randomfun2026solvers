"""Dump the real hires CPU geometry: fetch column, trie extent, lane_x0, drop
columns, collector/high-corridor rows and the riser column — for both stores.

No run, build only. usage: geomdump.py [store]
"""
import sys

from common import setup, SLUG


def dump(m, store):
    print(f"\n===== {store}: {m.width}x{m.height} =====")
    cpu = {n: b for n, b in m.regions.items() if n.startswith("cpu")}
    for n, (x, y, w, h) in sorted(cpu.items(), key=lambda t: (t[1][1], t[1][0])):
        print(f"  {n:32s} x={x:4d}..{x+w-1:4d}  y={y:4d}..{y+h-1:4d}  ({w}x{h})")

    # the fetch row/column: the region box for cpu:fetch
    fx, fy, fw, fh = cpu["cpu:fetch"]
    print(f"\n  fetch box: x={fx}..{fx+fw-1} y={fy}..{fy+fh-1}")
    rows = m.rows
    for yy in range(fy, fy + fh):
        seg = rows[yy][max(0, fx - 6): fx + fw + 6]
        print(f"    row {yy:4d} |{seg}|")

    # trie box
    tx, ty, tw, th = cpu["cpu:trie"]
    print(f"\n  trie box: x={tx}..{tx+tw-1} y={ty}..{ty+th-1}  (width {tw})")

    # drops box
    if "cpu:drops" in cpu:
        dx, dy, dw, dh = cpu["cpu:drops"]
        print(f"  drops box: x={dx}..{dx+dw-1} y={dy}..{dy+dh-1}")
        # find actual drop columns: 'v' glyphs inside the drops box
        vcols = {}
        for yy in range(dy, dy + dh):
            r = rows[yy]
            for xx in range(dx, min(dx + dw, len(r))):
                if r[xx] == "v":
                    vcols.setdefault(xx, []).append(yy)
        print(f"  drop 'v' columns: {sorted(vcols)}")

    for key in ("cpu:return:collector", "cpu:return:high", "cpu:return:riser"):
        if key in cpu:
            x, y, w, h = cpu[key]
            print(f"\n  {key}: x={x}..{x+w-1} y={y}..{y+h-1}")
            for yy in range(y, min(y + h, y + 3)):
                seg = rows[yy][x: x + min(w, 70)]
                print(f"    row {yy:4d} x{x:4d} |{seg}|")


def main():
    d3, hires, M, prog = setup()
    stores = sys.argv[1:] or ["men-v3", "taped"]
    for store in stores:
        m = M.build_for(SLUG, program=prog, store=store)
        dump(m, store)


if __name__ == "__main__":
    main()
