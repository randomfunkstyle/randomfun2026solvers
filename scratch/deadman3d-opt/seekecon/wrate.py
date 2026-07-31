"""The classic counted discard's ticks per ring word, from the profile alone.

Independent of the threshold sweep's regression: take each family's discard
region straight off the grid and divide by the ring words the emulator says that
family actually discarded over the tour. If the two routes agree, W is real.

usage: wrate.py <store> <rounds>
"""
import sys, time
sys.path.insert(0, "/tmp/seekecon")
from common import setup, tour, SLUG
from seekprof import attribute

# ring words discarded per family over the 21-round tour, from econ.py's trace,
# with JMPF split at the shipped threshold 256.
WORDS = {"JMPF": 1_440_489, "BRZ": 353_889, "BRN": 745_423}


def main():
    from randomfun2026solvers.fast_littleman import FastLittleman
    d3, hires, M, prog = setup()
    store, rounds = sys.argv[1], int(sys.argv[2])
    inp, frames = tour(hires, rounds)
    m = M.build_for(SLUG, program=prog, store=store)
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000,
        profile=True, profile_stride=17)
    S, T = res.profile.samples, res.frame_ticks[-1]
    own = attribute(m.regions, res.profile.heat, res.profile.wait)
    print(f"{store} {m.width}x{m.height} ticks={T:,} passed={res.passed}")
    print(f"{'family':8} {'regions':46} {'ticks':>12} {'words':>11} {'t/word':>8}")
    for fam, words in WORDS.items():
        names = [n for n in own if n.endswith(":" + fam)
                 and any(k in n for k in ("discard", "slab", "riser"))]
        tk = sum(own[n][0] for n in names) / S * T
        print(f"{fam:8} {','.join(sorted(n.split(':')[1] for n in names)):46} "
              f"{tk:>12,.0f} {words:>11,} {tk/words:>8.2f}")


if __name__ == "__main__":
    main()
