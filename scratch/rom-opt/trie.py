"""Among slot sets with the SAME opcode-cell cost, which gives the cheapest trie?

The relabelling is row-neutral, but it does re-shape ``_uneven_trie`` — a pruned,
contracted trie's per-lane walk depends on which leaves are live. This scores each
candidate by the execution-weighted decode walk (cells traversed from the fetch
row to the lane's own row), so the cells win is not paid back at the decode.
"""
import collections
import itertools

from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1 import programs

SLUG, STORE = "deadman-3d", "taped"
program = M.seek_split(programs.load(SLUG), threshold=M.SEEK_THRESHOLD, ops=M.SEEK_OPS)
mo = list(M.LANE_ORDER[SLUG])
used_m = {op.mnemonic for op in program.ops_used}
at = min((mo.index(c) for c in ("JMPF", "BRZ", "BRN") if c in mo), default=len(mo))
for new in ("JMPS", "BRZS", "BRNS"):
    if new in used_m and new not in mo:
        mo.insert(at, new)
        at += 1
p = M.plan(program, middle_order=mo)
by_rank = sorted(p.number, key=lambda m: p.row[m])
N, K, LANES = len(by_rank), p.k, 1 << p.k
static = collections.Counter(i.mnemonic for i in sorted(program.instrs, key=lambda i: i.pos))
cnt = [static[m] for m in by_rank]

# Execution weights: run the emulator's abstract image for a couple of frames.
from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.lm1 import emulator as E

try:
    img = M.image_program(program, p)
except Exception:
    img = program
weights = None
try:
    cmds = d3.WALK[:2]
    case = d3.cases_json(cmds)["publicTestData"][0]
    ins = [w for r in case["rounds"] for w in r["in"]]
    prof = E.profile(img, [int(x) for x in ins]) if hasattr(E, "profile") else None
    weights = prof
except Exception as exc:  # noqa: BLE001
    print(f"(no execution profile: {exc})")
if weights is None:
    # LANE_ORDER's recorded frame-1 profile is proportional to the static count
    # for this program's hot lanes; fall back to static.
    weights = {m: static[m] for m in by_rank}
w = [weights.get(m, 0) for m in by_rank]


def cells(code):
    return 2 if code < 10 else 5


def walk_cost(slots):
    """Execution-weighted trie walk, in cells, for a slot assignment."""
    rows = {s: 2 * i for i, s in enumerate(sorted(slots))}
    lane_x0 = 4 + 2 * K
    dist = {}

    def paths(level, lo, hi):
        """`machine._uneven_trie`'s own recursion, accumulating each leaf's walk.

        Contract copied from it: a single-child level is *contracted* (the edge
        below carries its `]`s), a branching `x` sits at column `3 + 2*level` on
        the gap row above its down-half's first lane, and a leaf's edge runs east
        to `lane_x0`. So a leaf's walk is one cell per `x` it passes, plus the
        vertical run to the child's row, plus the final horizontal edge.
        """
        sl = [s for s in sorted(slots) if lo <= s < hi]
        mid = lo
        down: list[int] = []
        while len(sl) > 1:
            mid = (lo + hi) // 2
            up = [s for s in sl if s < mid]
            down = [s for s in sl if s >= mid]
            if up and down:
                break
            lo, hi = (lo, mid) if up else (mid, hi)
            level += 1
        if len(sl) == 1:
            dist[sl[0]] = lane_x0 - (3 + 2 * level)
            return rows[sl[0]]
        xrow = rows[min(down)] - 1
        for half in ((lo, mid), (mid, hi)):
            sub = [s for s in sorted(slots) if half[0] <= s < half[1]]
            crow = paths(level + 1, *half)
            for s in sub:
                dist[s] += 1 + abs(crow - xrow)
        return xrow

    paths(1, 0, LANES)
    return sum(wi * dist[s] for wi, s in zip(w, sorted(slots)))


INF = float("inf")
memo = {}


def solve(rank, slot):
    if rank == N:
        return 0
    if (rank, slot) in memo:
        return memo[(rank, slot)]
    if LANES - slot < N - rank:
        return INF
    r = min(
        cnt[rank] * cells(M._bitrev(slot, K)) + solve(rank + 1, slot + 1),
        solve(rank, slot + 1),
    )
    memo[(rank, slot)] = r
    return r


import sys

sys.setrecursionlimit(20000)
OPT = solve(0, 0)
print(f"optimal opcode cells = {OPT}")

# enumerate all slot sets achieving OPT (bounded)
sols = []


def walkall(rank, slot, chosen):
    if len(sols) > 4000:
        return
    if rank == N:
        sols.append(tuple(chosen))
        return
    if LANES - slot < N - rank:
        return
    if cnt[rank] * cells(M._bitrev(slot, K)) + solve(rank + 1, slot + 1) == solve(rank, slot):
        walkall(rank + 1, slot + 1, chosen + [slot])
    if solve(rank, slot + 1) == solve(rank, slot):
        walkall(rank, slot + 1, chosen)


walkall(0, 0, [])
print(f"{len(sols)} slot sets achieve it")
scored = sorted((walk_cost(s), s) for s in sols)
cur = tuple(sorted(p.row[m] // 2 for m in by_rank))
print(f"current  walk={walk_cost(cur):>10,}  {list(cur)}")
ship = tuple(sorted(M.OPCODE_SLOTS[(SLUG, STORE)].values()))
print(f"shipped  walk={walk_cost(ship):>10,}  {list(ship)}")
for c, s in scored[:5]:
    print(f"best     walk={c:>10,}  {list(s)}")
print("worst    walk=%10s  %s" % (f"{scored[-1][0]:,}", list(scored[-1][1])))
best = scored[0][1]
print("\nbest map: " + repr({m: s for m, s in zip(by_rank, best)}))
