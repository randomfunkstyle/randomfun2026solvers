"""How often each source label is entered — the counts a restructure needs.

Moving a rare arm out of the 16 unrolled DDA copies makes every x-step's forward
branch skip fewer words (8 ticks each), but the arm then costs a seek to reach
and a seek to return. Whether that trades depends entirely on how rare "rare"
is, so measure it.

    uv run python scratch/deadman3d-opt/label_execs.py [prefix ...]
"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "solvers" / "python"))

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402

prefixes = tuple(sys.argv[1:]) or ("ywr", "yneg", "dda", "xarm", "hity", "whx", "why")
prog = d3.taped_program()  # the shipped taped tier, whatever levers it carries
at = collections.defaultdict(list)
for label, word in prog.labels.items():
    at[word].append(label)

em = Emulator(prog)
real_step = em.step
hits: collections.Counter = collections.Counter()


def step():  # noqa: ANN202
    for label in at.get(em.phase, ()):
        hits[label] += 1
    return real_step()


em.step = step
walk = list(d3.WALK)
res = em.run([Round(input=tuple(d3.input_words(walk)))], max_instructions=40_000_000)
frames = len(walk) + 1
print(f"reason={res.reason} frames={frames}")
groups: collections.Counter = collections.Counter()
for label, n in hits.items():
    stem = label.rstrip("0123456789")
    if stem.startswith(prefixes) or label.startswith(prefixes):
        groups[stem] += n
for stem, n in groups.most_common():
    print(f"{stem:<10} {n:>9,}  {n / frames:>9,.1f}/frame")
