"""The band's geometry as the *build* reports it, not as a table remembers it.

``dropcols.py``'s lesson, applied to the slab band: bases, entry rows, drop
columns, reserved columns and the registry flags that produced them, read off a
real build.

usage: slab_geom.py [store ...]
"""
import sys

from common import setup, SLUG


def main():
    d3, hires, M, prog = setup()
    stores = sys.argv[1:] or ["men-v3", "taped"]
    flags = [
        "SEEK_DRUM", "SEEK_TAKEN_DROP_EAST", "SEEK_TIGHT_STRUCT_DROPS",
        "PACKED_SLAB_BAND", "TIGHT_STRUCT_DROPS", "HIGH_COLLECTOR",
        "SPARSE_COLLECTOR", "TUCKED_DROPS", "FOLDED_LANES", "HIGH_DROPS_FREE",
    ]
    for store in stores:
        key = (SLUG, store)
        print(f"\n== {store} ==")
        for f in flags:
            reg = getattr(M, f, None)
            if reg is None:
                print(f"   {f:26s} MISSING")
                continue
            if isinstance(reg, dict):
                hit = reg.get(key, reg.get(SLUG, "-"))
            else:
                hit = (key in reg) or (SLUG in reg)
            print(f"   {f:26s} {hit}")
        for name in ("SQUASH_BAND", "ROM_TOUCH_DROP", "LEAN_TRIE", "MEM_PAD_FOR",
                     "SEEK_CLASSIC_DRAIN", "SEEK_CLASSIC_DRAIN_OPS",
                     "SEEK_SLAB_PITCH", "LANE_ORDER", "OPCODE_SLOTS"):
            reg = getattr(M, name, None)
            if isinstance(reg, dict):
                print(f"   {name:26s} {reg.get(key, reg.get(SLUG, '-'))}")
        m = M.build_for(SLUG, program=prog, store=store)
        print(f"   built {m.width}x{m.height} pad={m.mem_pad}")
        band = {n: b for n, b in m.regions.items()
                if n.split(":")[1:2] and n.split(":")[1] in
                ("slab", "discard", "riser", "entry")}
        for n, b in sorted(band.items(), key=lambda kv: kv[1][0]):
            print(f"     {n:26s} x={b[0]:3d} y={b[1]:3d} w={b[2]:2d} h={b[3]:2d}")
        for n in ("cpu:fetch", "cpu:return:collector", "cpu:return:high",
                  "cpu:return:riser", "cpu:trie", "cpu:drops", "cpu:seek:taken"):
            if n in m.regions:
                print(f"     {n:26s} {m.regions[n]}")


if __name__ == "__main__":
    main()
