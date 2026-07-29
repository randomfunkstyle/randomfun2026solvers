"""Per-static-site census: reads issued, and ring words discarded, per source line.

Two questions the grid profiler cannot answer, both exact on the emulator (no
pipes, no sampling, seconds to run):

1. **Which reads, from where.** `scratch/DOOM-OPCODES.md` §5's address census
   said *which word*; this says *which line of the program*, which is what a
   program lever needs. A site that is 100% "already in ACC" is an M13 — a
   deletable instruction.
2. **Which jump discards the most words.** A taken forward branch recirculates
   the words it skips at 8 ticks each, and that is 16% of the run ("slab work").
   The discard is charged to the branch that caused it, so this says exactly
   which `BRN`/`BRZ`/`JMP` is paying for the 16x unroll's shape.

    uv run python scratch/deadman3d-opt/read_census.py [walk_len] [top]
"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "solvers" / "python"))

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers.lm1.asm import assemble  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402

walk = list(d3.WALK[: int(sys.argv[1])] if len(sys.argv) > 1 else d3.WALK)
top = int(sys.argv[2]) if len(sys.argv) > 2 else 20
prog = assemble(
    d3.deadman3d_source(dda_acc_reload=False, dda_diff=True), name="deadman-3d"
)
names = {addr: name for name, addr in prog.equs.items()}
# phase -> the nearest label at or before it, so a count can be read as a line.
site_label: dict[int, str] = {}
by_word = sorted((w, lbl) for lbl, w in prog.labels.items())


def label_of(phase: int) -> str:
    best = "?"
    for w, lbl in by_word:
        if w <= phase:
            best = f"{lbl}+{phase - w}"
        else:
            break
    return best


reads: collections.Counter = collections.Counter()
reads_addr: collections.defaultdict = collections.defaultdict(collections.Counter)
already: collections.Counter = collections.Counter()
skips: collections.Counter = collections.Counter()
execs: collections.Counter = collections.Counter()
addr_total: collections.Counter = collections.Counter()

em = Emulator(prog)
real_read, real_skip, real_step = em._mem_read, em._skip, em.step
here = {"pc": 0, "acc": 0}


def mem_read(addr):  # noqa: ANN001, ANN202
    value = real_read(addr)
    reads[here["pc"]] += 1
    reads_addr[here["pc"]][addr] += 1
    addr_total[addr] += 1
    if value == here["acc"]:
        already[here["pc"]] += 1
    return value


def skip(n):  # noqa: ANN001, ANN202
    skips[here["pc"]] += n
    return real_skip(n)


def step():  # noqa: ANN202
    here["pc"], here["acc"] = em.phase, em.b
    execs[em.phase] += 1
    return real_step()


em._mem_read, em._skip, em.step = mem_read, skip, step
res = em.run([Round(input=tuple(d3.input_words(walk)))], max_instructions=40_000_000)
frames = len(walk) + 1
n_reads = sum(reads.values())
print(f"reason={res.reason} instructions={res.instructions:,} frames={frames}")
print(f"reads {n_reads:,} ({n_reads / frames:,.0f}/frame)   "
      f"words discarded {sum(skips.values()):,} "
      f"({8 * sum(skips.values()) / frames:,.0f} ticks/frame at 8/word)\n")

print(f"{'addr':>5} {'name':<8} {'reads':>9} {'/frame':>8}")
for addr, n in addr_total.most_common(top):
    print(f"{addr:>5} {names.get(addr, ''):<8} {n:>9,} {n / frames:>8,.1f}")

print(f"\nwhere the discarded words come from — 8 ticks each")
print(f"{'site':<16} {'execs':>9} {'skipped':>11} {'/exec':>7} {'ticks/frame':>12}")
for pc, n in skips.most_common(top):
    print(f"{label_of(pc):<16} {execs[pc]:>9,} {n:>11,} {n / execs[pc]:>7.1f} "
          f"{8 * n / frames:>12,.0f}")

print(f"\nread sites whose word was already in ACC")
print(f"{'site':<16} {'reads':>9} {'already':>9} {'%':>6}")
for pc, n in reads.most_common(top):
    a = already.get(pc, 0)
    if a * 4 >= n:
        print(f"{label_of(pc):<16} {n:>9,} {a:>9,} {100 * a / n:>5.1f}%")
