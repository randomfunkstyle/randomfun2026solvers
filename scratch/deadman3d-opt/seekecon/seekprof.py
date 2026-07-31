"""Per-taken-seek cost and blocked fraction, on the real grid.

The emulator says the 21-round tour takes 8,252 taken seeks (JMPF sites with
static skip >= 256, weighted by dynamic taken count). Divide the `cpu:seek:*`
regions by that and the seek tail's true unit economics fall out — including
what fraction of it is the CPU *waiting*, which is what decides whether any
CPU-side protocol change can pay.

usage: seekprof.py <store> <rounds> <taken-seeks>
"""
import sys, time
sys.path.insert(0, "/tmp/seekecon")
from common import setup, tour, SLUG

INSTR = 880_332


def attribute(regions, heat, wait):
    """`_region_of`'s rule: the smallest box containing a cell owns it."""
    boxes = sorted(((w * h, n, x, y, w, h) for n, (x, y, w, h) in regions.items()),
                   key=lambda t: t[0])
    out = {}
    for (cx, cy), v in heat.items():
        for _a, n, x, y, w, h in boxes:
            if x <= cx < x + w and y <= cy < y + h:
                hh, ww = out.get(n, (0, 0))
                out[n] = (hh + v, ww + wait.get((cx, cy), 0))
                break
    return out


def main():
    from randomfun2026solvers.fast_littleman import FastLittleman
    d3, hires, M, prog = setup()
    store = sys.argv[1]
    rounds = int(sys.argv[2])
    seeks = int(sys.argv[3])
    inp, frames = tour(hires, rounds)
    m = M.build_for(SLUG, program=prog, store=store)
    print(f"built {store} {m.width}x{m.height}", flush=True)
    t = time.time()
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000,
        profile=True, profile_stride=17)
    print(f"ran in {time.time()-t:.0f}s  passed={res.passed} fatal={res.fatal} "
          f"ticks={res.frame_ticks[-1]:,}", flush=True)
    S, T = res.profile.samples, res.frame_ticks[-1]
    own = attribute(m.regions, res.profile.heat, res.profile.wait)
    rows = [(n, h, w) for n, (h, w) in own.items() if n.startswith("cpu") and h]
    tot = sum(h for _, h, _ in rows)
    print(f"cpu total {100*tot/S:.2f}% of run; {seeks:,} taken seeks over the tour")
    print(f"{'region':34s} {'%run':>7} {'%blk':>7} {'t/instr':>8} "
          f"{'t/seek':>9} {'blk/seek':>9}")
    seekpool = seekblk = 0.0
    for n, h, w in sorted(rows, key=lambda r: -r[1]):
        tk, bk = h / S * T, w / S * T
        if n.startswith("cpu:seek"):
            seekpool += tk
            seekblk += bk
        print(f"{n:34s} {100*h/S:7.2f} {100*w/S:7.2f} {h/S*T/INSTR:8.2f} "
              f"{tk/seeks:9.1f} {bk/seeks:9.1f}")
    print(f"\nSEEK POOL total {seekpool:,.0f} ticks = {seekpool/T*100:.2f}% of run, "
          f"{seekpool/INSTR:.2f} t/instr")
    print(f"  per taken seek: {seekpool/seeks:,.1f} ticks, of which "
          f"{seekblk/seeks:,.1f} blocked ({100*seekblk/seekpool:.1f}%)")


if __name__ == "__main__":
    main()
