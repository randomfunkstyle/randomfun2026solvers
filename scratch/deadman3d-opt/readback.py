"""Address-by-address readback of the REAL taped plan, both chains.

usage: readback.py <n> <plan csv> <order csv>
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


def readback(order):
    engine = _standalone(
        taped_store_block(n, plan, skip_batch=2, compact_gate=True, order=order)
    )
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
        assert res.fatal is None, (order, lo, res.fatal)
        out.update(zip(range(lo, hi), res.output))
    return out


t0 = time.time()
got = readback(order)
want = {a: a * 13 + 7 for a in range(1, n)}
bad = {a: (got.get(a), want[a]) for a in want if got.get(a) != want[a]}
print(f"plan={plan} order={order}: {len(got)} addresses, {len(bad)} wrong  ({time.time()-t0:.0f}s)")
if bad:
    for a in sorted(bad)[:20]:
        print(f"  addr {a}: got {bad[a][0]} want {bad[a][1]}")
    sys.exit(1)
print("all addresses read back their own value")
