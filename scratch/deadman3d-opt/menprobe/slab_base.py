"""Baseline re-measure, either tier. usage: slab_base.py <store> [rounds]"""
import sys
import time

from common import setup, tour, run, SLUG

INSTR = 880_332


def main():
    store = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 21
    d3, hires, M, prog = setup()
    inp, frames = tour(hires, rounds)
    t = time.time()
    m = M.build_for(SLUG, program=prog, store=store)
    print(f"built {m.width}x{m.height} in {time.time()-t:.0f}s", flush=True)
    res = run(m, inp, frames, store)
    print(f"T/INSTR {res.frame_ticks[-1]/INSTR:.3f}")


if __name__ == "__main__":
    main()
