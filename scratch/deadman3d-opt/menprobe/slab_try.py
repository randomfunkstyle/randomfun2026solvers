"""Run one slab-band variant, both tiers, at any round count.

    slab_try.py <store> <rounds> <spec>[ <spec> ...]

A spec is ``key=value`` joined by commas; unknown keys are a hard error so a
typo cannot silently measure the baseline again:

    drain=3            SEEK_CLASSIC_DRAIN bits (0 = off)
    ops=BRN|BRZ        SEEK_CLASSIC_DRAIN_OPS restriction ('*' = all)
    pad=4              MEM_PAD_FOR for this tier (-1 = let the build search)

Prints the built box, the pad the build actually kept, ticks to the last frame,
and the gate (``passed`` / ``fatal``).
"""
import sys
import time

from common import setup, tour, SLUG
from randomfun2026solvers.fast_littleman import FastLittleman

INSTR = 880_332


def apply(M, key, spec):
    M.SEEK_CLASSIC_DRAIN.pop(key, None)
    M.SEEK_CLASSIC_DRAIN_OPS.pop(key, None)
    base_pad = dict(M.MEM_PAD_FOR)
    for part in spec.split(","):
        if not part or part == "base":
            continue
        k, _, v = part.partition("=")
        if k == "drain":
            if int(v):
                M.SEEK_CLASSIC_DRAIN[key] = int(v)
        elif k == "ops":
            if v != "*":
                M.SEEK_CLASSIC_DRAIN_OPS[key] = tuple(v.split("|"))
        elif k == "pad":
            if int(v) < 0:
                M.MEM_PAD_FOR.pop(key, None)
            else:
                M.MEM_PAD_FOR[key] = int(v)
        else:
            raise SystemExit(f"unknown spec key {k!r}")
    return base_pad


def main():
    store = sys.argv[1]
    rounds = int(sys.argv[2])
    specs = sys.argv[3:]
    d3, hires, M, prog = setup()
    key = (SLUG, store)
    saved_drain = dict(M.SEEK_CLASSIC_DRAIN)
    saved_ops = dict(M.SEEK_CLASSIC_DRAIN_OPS)
    saved_pad = dict(M.MEM_PAD_FOR)
    inp, frames = tour(hires, rounds)
    for spec in specs:
        M.SEEK_CLASSIC_DRAIN.clear()
        M.SEEK_CLASSIC_DRAIN.update(saved_drain)
        M.SEEK_CLASSIC_DRAIN_OPS.clear()
        M.SEEK_CLASSIC_DRAIN_OPS.update(saved_ops)
        M.MEM_PAD_FOR.clear()
        M.MEM_PAD_FOR.update(saved_pad)
        apply(M, key, spec)
        t = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store=store)
        except Exception as exc:  # noqa: BLE001 — which failure is the datum
            print(f"  {spec:>34}: BUILD FAIL {type(exc).__name__}: {exc} "
                  f"({time.time()-t:.0f}s)", flush=True)
            continue
        bt = time.time() - t
        t = time.time()
        res = FastLittleman("\n".join(m.rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        print(f"  {spec:>34}: {m.width}x{m.height} pad={m.mem_pad} "
              f"last={res.frame_ticks[-1]:,} t/instr={res.frame_ticks[-1]/INSTR:.3f} "
              f"passed={res.passed} fatal={res.fatal} "
              f"({bt:.0f}s build, {time.time()-t:.0f}s run)", flush=True)


if __name__ == "__main__":
    main()
