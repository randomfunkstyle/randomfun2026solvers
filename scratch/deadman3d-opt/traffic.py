"""Per-ADDRESS store traffic for deadman-3d, on the emulator's abstract wire.

Differences an N-command run against the boot round alone, so the counts are
per *gameplay* frame.  Writes a CSV-ish dump to stdout plus bank rollups.

usage: traffic.py [n_commands] [--dump]
"""
import sys

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.lm1.store import DictStore


class Counting(DictStore):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.reads: dict[int, int] = {}
        self.writes: dict[int, int] = {}

    def _read(self, addr):
        self.reads[addr] = self.reads.get(addr, 0) + 1
        return super()._read(addr)

    def _write(self, addr, value):
        self.writes[addr] = self.writes.get(addr, 0) + 1
        return super()._write(addr, value)


def run(cmds):
    from randomfun2026solvers.lm1.emulator import Emulator, Round

    st = Counting()
    em = Emulator(d3._current_program(), store=st)
    words = list(d3.preamble_words()) + d3.title_words() + list(cmds)
    em.run([Round(input=tuple(words))], max_instructions=2_000_000_000)
    return st


ncmd = int(sys.argv[1]) if len(sys.argv) > 1 else 4
dump = "--dump" in sys.argv

boot = run([])
full = run(d3.WALK[:ncmd])
R = {a: full.reads.get(a, 0) - boot.reads.get(a, 0) for a in set(full.reads) | set(boot.reads)}
W = {a: full.writes.get(a, 0) - boot.writes.get(a, 0) for a in set(full.writes) | set(boot.writes)}
R = {a: v / ncmd for a, v in R.items() if v}
W = {a: v / ncmd for a, v in W.items() if v}
tr, tw = sum(R.values()), sum(W.values())
print(f"cmds={ncmd}  reads/frame={tr:,.0f}  writes/frame={tw:,.0f}")

if dump:
    for a in sorted(set(R) | set(W)):
        print(f"  addr {a:4d}  r={R.get(a,0):10.1f}  w={W.get(a,0):9.1f}")

# cumulative distribution over addresses, descending by read traffic
print("\ntop 40 addresses by reads:")
for a in sorted(R, key=lambda a: -R[a])[:40]:
    print(f"  addr {a:4d}  r={R[a]:9.1f} ({100*R[a]/tr:5.2f}%)  w={W.get(a,0):8.1f}")

print("\nrollup by 32-address block:")
for lo in range(0, 608, 32):
    r = sum(v for a, v in R.items() if lo <= a < lo + 32)
    w = sum(v for a, v in W.items() if lo <= a < lo + 32)
    if r or w:
        print(f"  {lo:4d}..{lo+31:4d}  r={r:9.1f} ({100*r/tr:5.2f}%)  w={w:8.1f} ({100*w/tw:5.2f}%)")

import json, pathlib
pathlib.Path(__file__).with_name("traffic.json").write_text(
    json.dumps({"reads": {str(k): v for k, v in R.items()},
                "writes": {str(k): v for k, v in W.items()}})
)
print("\nwrote traffic.json")
