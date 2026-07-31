"""Build-only: which slab-band variants bind, and at which pad.

Ticks are 5x-25x the cost of a build, so answer "does it place" first.

    slab_gate.py <store> <pad-lo>-<pad-hi> <spec> [<spec> ...]

``pad`` ``-1`` in the range means "let the build search"; it is reported as the
pad the build kept. Specs are :mod:`slab_try`'s.
"""
import sys
import time

from common import setup, SLUG
from slab_try import apply


def main():
    store = sys.argv[1]
    if sys.argv[2] == "search":
        pads = [-1]
    else:
        lo, _, hi = sys.argv[2].partition("-")
        pads = list(range(int(lo), int(hi) + 1)) if hi else [int(lo)]
    specs = sys.argv[3:]
    d3, hires, M, prog = setup()
    key = (SLUG, store)
    saved = (dict(M.SEEK_CLASSIC_DRAIN), dict(M.SEEK_CLASSIC_DRAIN_OPS),
             dict(M.MEM_PAD_FOR))
    for spec in specs:
        for pad in pads:
            for reg, s in zip((M.SEEK_CLASSIC_DRAIN, M.SEEK_CLASSIC_DRAIN_OPS,
                               M.MEM_PAD_FOR), saved):
                reg.clear()
                reg.update(s)
            apply(M, key, f"{spec},pad={pad}")
            t = time.time()
            try:
                m = M.build_for(SLUG, program=prog, store=store)
            except Exception as exc:  # noqa: BLE001
                print(f"  {spec:>26} pad {pad:>3}: FAIL {type(exc).__name__}: "
                      f"{exc} ({time.time()-t:.0f}s)", flush=True)
                continue
            print(f"  {spec:>26} pad {pad:>3}: {m.width}x{m.height} "
                  f"kept={m.mem_pad} area={m.width*m.height:,} "
                  f"({time.time()-t:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
