"""Per-ARM command-word traffic for the DOOM unit (`stream:unit`).

Wraps ``store.DoomUnit.send`` and buckets every word by frame (COMMIT is the
frame delimiter), so the boot round separates itself and each gameplay frame is
reported on its own.  Also rolls up the pixels each arm paints — the arm's
latency — beside the word count, which is what its *area* buys.

usage: unit_traffic.py [n_frames]
"""
import sys
from collections import Counter

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.lm1.store import DoomUnit

BY_CODE = {v: k for k, v in DoomUnit.CODES.items()}


def run(cmds):
    from randomfun2026solvers.lm1.emulator import Emulator, Round

    frames: list[Counter] = [Counter()]
    pixels: list[Counter] = [Counter()]
    orig = DoomUnit.send

    def send(self, word):
        arm = BY_CODE[word & 7]
        frames[-1][arm] += 1
        before = self.pixels
        orig(self, word)
        pixels[-1][arm] += self.pixels - before
        if arm == "COMMIT":
            frames.append(Counter())
            pixels.append(Counter())

    DoomUnit.send = send
    try:
        em = Emulator(d3._current_program())
        stream = list(d3.preamble_words()) + d3.title_words() + list(cmds)
        em.run([Round(input=tuple(stream))], max_instructions=2_000_000_000)
    finally:
        DoomUnit.send = orig
    return frames, pixels


nf = int(sys.argv[1]) if len(sys.argv) > 1 else 8
frames, pixels = run(d3.WALK[:nf])
arms = list(DoomUnit.CODES)

print(f"chords: {''.join(d3.WALK_CHORDS[:nf])!r}")
print("\nper frame (frame 0 = boot: preamble + title screen)")
print("  frame  " + "".join(f"{a:>8s}" for a in arms) + "     total")
for i, f in enumerate(frames):
    if not f:
        continue
    print(f"  {i:5d}  " + "".join(f"{f[a]:8,d}" for a in arms) + f"{sum(f.values()):10,d}")

play = frames[1:]
play = [f for f in play if f]
n = len(play)
tot = Counter()
for f in play:
    tot += f
pix = Counter()
for p in pixels[1 : 1 + n]:
    pix += p
tw = sum(tot.values())
print(f"\ngameplay mean over {n} frames — words per FRAME:")
for a in sorted(arms, key=lambda a: -tot[a]):
    share = 100 * tot[a] / tw if tw else 0
    hit = sum(1 for f in play if f[a])
    print(
        f"  {a:7s} {tot[a] / n:9,.2f} ({share:5.2f}%)  "
        f"pixels={pix[a] / n:9,.1f}  frames-used={hit}/{n}"
    )
print(f"  {'TOTAL':7s} {tw / n:9,.2f}")
