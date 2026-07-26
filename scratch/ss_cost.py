"""Throwaway: what does phase 2 actually cost on the engine?"""
from randomfun2026solvers import scoring
from randomfun2026solvers.subset_sum_mitm import brute_force, public_cases

cases = []
for name, vals, t, _ in public_cases():
    cases.append({
        "name": name,
        "rounds": [{"in": [str(len(vals)), *map(str, vals), str(t)],
                    "out": ["1" if brute_force(vals, t) else "0"]}],
    })
# The real worst case: twenty values, every left mask tried and every scan run
# to the sentinel.  All values even and the target odd makes it unsatisfiable
# while staying inside the stated constraints.
adv = [2 * (12345 + 3719 * i % 40000) for i in range(20)]
cases.append({
    "name": "adversarial n=20, no solution",
    "rounds": [{"in": [str(len(adv)), *map(str, adv), str(sum(adv) // 3 | 1)],
                "out": ["0"]}],
})
prob = {"slug": "p2", "scoring": "footprint-tick", "tickCap": 15_000_000,
        "publicTestData": cases}
res = scoring.score_program("/tmp/ss_p2.man", prob)
for c in res.cases:
    print(f"{c.name:32} {c.ticks:>10,}")
print(f"{'avg':32} {res.avg_ticks:>10,.0f}   area2={res.area2:,}")
