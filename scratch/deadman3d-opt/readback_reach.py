"""Address-by-address readback of the REAL taped plan with the gates grown.

The failure this exists to catch is silent. A gate that binds the wrong
outgoing pipe, or a rebase literal off by one, does not error — it answers from
the wrong bank, and every frame after it is quietly wrong. So every address of
the live plan is written and read back **individually**, through the same chain
the machine builds (``TAPED_BANK_ORDER`` over ``TAPED_BANKS``), and both gate
forms are exercised because the chain order puts two high gates ahead of a low
one.

Four builds, so that a pass pins the change rather than the machine:

* shipped        — neither knob, the byte-identical baseline
* chain          — ``chain_reach``: gates 1..n-2 grown WEST to their caller
* roof           — ``request_roof``: gate 0 grown NORTH to its caller
* both           — what ``lm1.machine`` actually ships

usage: readback_reach.py <n> <plan csv> <order csv>
"""

import sys
import time

sys.path.insert(0, "solvers/python")

from randomfun2026solvers.memory_taped import taped_plan, taped_store_block  # noqa: E402

sys.path.insert(0, "tests")
from test_memory_taped import _standalone  # noqa: E402

n = int(sys.argv[1])
plan = tuple(int(x) for x in sys.argv[2].split(","))
order = None if sys.argv[3] == "-" else tuple(int(x) for x in sys.argv[3].split(","))

# the machine's roof: one row under the adapter's floor, in block coordinates
ROOF = 20


def readback(**kw):
    block = taped_store_block(n, plan, skip_batch=2, compact_gate=True, order=order, **kw)
    engine = _standalone(block)
    writes = [x for a in range(1, n) for x in (1, a, a * 13 + 7)]
    bounds = [1]
    for m in taped_plan(n, plan):
        bounds.append(bounds[-1] + m)
    out = {}
    for lo, hi in zip(bounds, bounds[1:]):
        hi = min(hi, n)
        if lo >= hi:
            continue
        reads = [x for a in range(lo, hi) for x in (0, a)]
        want = [a * 13 + 7 for a in range(lo, hi)]
        res = engine.run(writes + reads, expected=want, max_ticks=4_000_000_000)
        assert res.fatal is None, (kw, lo, res.fatal)
        out.update(zip(range(lo, hi), res.output))
    return block, out


want = {a: a * 13 + 7 for a in range(1, n)}
CASES = {
    "shipped": {},
    "chain": {"chain_reach": True},
    "roof": {"request_roof": ROOF},
    "both": {"chain_reach": True, "request_roof": ROOF},
}
bad_total = 0
for name, kw in CASES.items():
    t0 = time.time()
    block, got = readback(**kw)
    bad = {a: (got.get(a), want[a]) for a in want if got.get(a) != want[a]}
    bad_total += len(bad)
    print(
        f"{name:8s} {block.width}x{block.height} in={block.in_cell}: "
        f"{len(got)} addresses, {len(bad)} wrong  ({time.time() - t0:.0f}s)"
    )
    for a in sorted(bad)[:10]:
        print(f"    addr {a}: got {bad[a][0]} want {bad[a][1]}")

sys.exit(1 if bad_total else 0)
