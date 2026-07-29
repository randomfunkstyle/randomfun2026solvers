"""Which *instruction* pays each big ring discard — and whether it can be a seek.

`machine.SEEK_OPS` is `("JMPF",)`: only a jump is rewritten to the seek drum, so
a long **`BRZ`/`BRN`** recirculates every word it skips at 8 ticks each, forever.
A backward branch's skip count is the whole ring, so one of those in a hot loop
is worth several percent. This lists the sites by discarded words with the
mnemonic and the seek verdict beside each.

    uv run python scratch/deadman3d-opt/skip_sites.py [walk_len] [top]
"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "solvers" / "python"))

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers.lm1 import machine as lm1_machine  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402

walk = list(d3.WALK[: int(sys.argv[1])] if len(sys.argv) > 1 else d3.WALK)
top = int(sys.argv[2]) if len(sys.argv) > 2 else 14
prog = d3.taped_program()  # the shipped taped tier, whatever levers it carries
split = lm1_machine.seek_split(prog)
seeky = {i.pos: i.mnemonic for i in split.instrs}
mnemonic = {i.pos: i.mnemonic for i in prog.instrs}
by_word = sorted((w, lbl) for lbl, w in prog.labels.items())


def label_of(phase):  # noqa: ANN001, ANN202
    best = "?"
    for w, lbl in by_word:
        if w <= phase:
            best = f"{lbl}+{phase - w}"
        else:
            break
    return best


skips: collections.Counter = collections.Counter()
execs: collections.Counter = collections.Counter()
em = Emulator(prog)
real_skip, real_step = em._skip, em.step
here = {"pc": 0}


def skip(n):  # noqa: ANN001, ANN202
    skips[here["pc"]] += n
    return real_skip(n)


def step():  # noqa: ANN202
    here["pc"] = em.phase
    execs[em.phase] += 1
    return real_step()


em._skip, em.step = skip, step
em.run([Round(input=tuple(d3.input_words(walk)))], max_instructions=40_000_000)
frames = len(walk) + 1
print(f"P={prog.P} frames={frames}  (8 ticks a discarded word; a seek is ~1,008)")
print(f"{'site':<14} {'op':<6} {'seek?':<6} {'execs':>7} {'words/exec':>11} "
      f"{'discard t/frame':>16}")
for pc, n in skips.most_common(top):
    op = mnemonic.get(pc, "?")
    became = seeky.get(pc, op)
    print(f"{label_of(pc):<14} {op:<6} {'SEEK' if became != op else 'no':<6} "
          f"{execs[pc]:>7,} {n / execs[pc]:>11,.0f} {8 * n / frames:>16,.0f}")
