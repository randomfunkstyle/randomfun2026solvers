"""Throwaway: score the checked-in grid the way the contest does."""
import random

from randomfun2026solvers import scoring
from randomfun2026solvers.subset_sum_mitm import expected_output, public_cases

GRID = "tasks/solutions/subset-sum_mitm.man"
res = scoring.score_program(GRID, "subset-sum")
for c in res.cases:
    print(f"{c.name:32} {c.ticks:>10,}")
print(f"\n{res.width} x {res.height}   area2={res.area2:,}   "
      f"avg_ticks={res.avg_ticks:,.0f}   score={res.score:,.0f}")

# ── beyond the public set: the constraints, sampled ──────────────────────────
rng = random.Random(20260726)
cases = []
for i in range(24):
    n = rng.randint(10, 20)
    vals = [rng.randint(1, 99999) for _ in range(n)]
    tot = sum(vals)
    t = rng.randint(max(101, tot // 10), min(999_999, 3 * tot // 5))
    cases.append({
        "name": f"random n={n} #{i}",
        "rounds": [{"in": [str(n), *map(str, vals), str(t)],
                    "out": [str(v) for v in expected_output(vals, t)]}],
    })
# All values even and the target odd: unsatisfiable, so every mask is tried.
adv = [2 * (12345 + 3719 * i % 40000) for i in range(20)]
cases.append({
    "name": "adversarial n=20, no solution",
    "rounds": [{"in": [str(len(adv)), *map(str, adv), str(sum(adv) // 3 | 1)],
                "out": ["0"]}],
})
prob = {"slug": "subset-sum", "scoring": "footprint-tick", "tickCap": 15_000_000,
        "publicTestData": cases}
r2 = scoring.score_program(GRID, prob)
worst = max(r2.cases, key=lambda c: c.ticks)
print(f"\n{len(cases)} sampled cases all pass; worst = {worst.name} "
      f"at {worst.ticks:,} ticks ({worst.ticks / 15_000_000:.0%} of cap)")
