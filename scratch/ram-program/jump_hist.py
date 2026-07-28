"""Per-taken-jump skip histogram for deadman-3d, boot separated from gameplay.

The drum's counted discard is paid per *ring word*, so that is the bill this
buckets; ``seek_split``'s threshold is expressed in fixed-image units
(``2 * instruction distance``), so both are reported per jump.

usage: jump_hist.py [n_frames]
"""
import sys
from collections import Counter, defaultdict

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.lm1 import programs
from randomfun2026solvers.lm1.emulator import Emulator, Round

NFRAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 3

prog = programs.load("deadman-3d")
instrs = sorted(prog.instrs, key=lambda i: i.pos)
NI = len(instrs)
index_of_word = {ins.pos: k for k, ins in enumerate(instrs)}
P = len(prog.words)
print(f"P={P} words, {NI} instructions")

boot_inputs = len(d3.preamble_words()) + len(d3.title_words())
cmds = d3.WALK[:NFRAMES]
inp = d3.input_words(cmds)

em = Emulator(prog)
records: list[tuple[int, str, int, int]] = []  # frame, mnemonic, words, fiskip
state = {"pos": 0, "mn": "?", "words": 1}
raw_skip = Emulator._skip


def _skip(self, n):
    frame = max(0, self._in_cursor - boot_inputs)
    k = index_of_word.get(state["pos"])
    fi = -1
    if k is not None:
        after = (state["pos"] + state["words"] + n) % P
        t = index_of_word.get(after)
        if t is not None:
            fi = 2 * ((t - k - 1) % NI)
    records.append((frame, state["mn"], n, fi))
    raw_skip(self, n)


Emulator._skip = _skip
raw_step = Emulator.step


def step(self):
    # decode BEFORE executing: `_skip` fires inside the handler, so the
    # mnemonic and word count must already describe *this* instruction.
    state["pos"] = self.phase
    op = self.program.isa.by_code(self.words[self.phase])
    state["mn"] = op.mnemonic
    state["words"] = 1 + (1 if op.operands else 0)
    return raw_step(self)


Emulator.step = step
res = em.run([Round(input=tuple(inp))], max_instructions=40_000_000)
print("reason:", res.reason, " instructions:", res.instructions)

BUCKETS = [(0, 64), (64, 256), (256, 1024), (1024, 1 << 30)]


def report(rows, label):
    tot_w = sum(r[2] for r in rows)
    print(f"\n=== {label}: {len(rows):,} taken jumps, {tot_w:,} words discarded ===")
    print("| skip distance (ring words) | jumps | words | share |")
    print("|---|---|---|---|")
    for lo, hi in BUCKETS:
        sel = [r for r in rows if lo <= r[2] < hi]
        w = sum(r[2] for r in sel)
        name = f"{lo}-{hi}" if hi < (1 << 30) else f"{lo}+"
        print(f"| {name} | {len(sel):,} | {w:,} | {100*w/max(tot_w,1):.1f}% |")
    # by mnemonic, long jumps only (fixed-image skip >= 256)
    by = defaultdict(lambda: [0, 0])
    for f, mn, w, fi in rows:
        if fi >= 256:
            by[mn][0] += 1
            by[mn][1] += w
    lw = sum(v[1] for v in by.values())
    print(f"  long (fixed-image skip >= 256): {sum(v[0] for v in by.values()):,} jumps, "
          f"{lw:,} words = {100*lw/max(tot_w,1):.1f}% of the bill")
    for mn, (c, w) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print(f"    {mn:6s} {c:6,} jumps {w:9,} words  {100*w/max(lw,1):.1f}% of long")
    # Threshold sweep in fixed-image units, per candidate SEEK_OPS set. The
    # threshold is a plateau for JMPF alone; the question BRZ raises is whether
    # its handful of jumps have a different enough length distribution to move
    # the corner, so both sets are swept side by side.
    for opset in (("JMPF",), ("JMPF", "BRZ")):
        print(f"  threshold sweep (fixed-image units), {'+'.join(opset)}:")
        for thr in (64, 128, 192, 256, 384, 512, 768, 1024, 2048):
            sel = [r for r in rows if r[3] >= thr and r[1] in opset]
            w = sum(r[2] for r in sel)
            print(
                f"    thr {thr:5d}: {len(sel):6,} jumps  {w:9,} words  "
                f"{100*w/max(tot_w,1):5.1f}%"
            )


boot = [r for r in records if r[0] == 0]
report(boot, "boot")
for f in range(1, NFRAMES + 1):
    rows = [r for r in records if r[0] == f]
    if rows:
        report(rows, f"gameplay frame {f}")
