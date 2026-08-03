"""901-address readback of the hires taped store, batched P2 on and off.

The build gate cannot see a mis-bind: a worker that takes a request word off its
own tape answers, and answers wrong. This walks every address of every bank on
the *shipped* hires plan/chain/protocol/worker-pick, ascending, descending, and
interleaved read/write, and compares the two flag settings answer for answer.
"""
import sys
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/solvers/python")

from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine
from randomfun2026solvers import memory_tape as mt
from randomfun2026solvers.memory_taped import taped_store_block

SLUG, TIER = "deadman-3d_hires", "taped"
N = 902
PLAN = tuple(machine.TAPED_BANKS[SLUG])
ORDER = machine.TAPED_BANK_ORDER[(SLUG, TIER)]
# `_rot_kw()` from tests/test_memory_taped.py: the hi-res block as `lm1.machine`
# builds it, minus the machine-geometry knobs the standalone wrapper cannot give.
KW = dict(
    skip_batch=None,
    jump_threshold=machine.TAPED_JUMP_THRESHOLD[SLUG],
    compact_gate=True,
    gate_park_const=True,
    gate_south_reuse_b=True,
    tape_park_const=True,
    order=list(ORDER),
    chain_reach=True,
    feed_teleport=True,
    bank_lift=5,
    gate_return_slack=0,
    request_roof=20,
    feed_share_riser=True,
    bank_west_grow=machine.TAPED_BANK_WEST_GROW[(SLUG, TIER)],
    protocol="v5",
    rotate_banks=machine.TAPED_ROTATE_BANKS[(SLUG, TIER)],
)


def standalone(block):
    sx, sy = 6, 4
    grid = {(x + sx, y + sy): ch for (x, y), ch in block.cells.items()}
    ix, iy = block.in_cell[0] + sx, block.in_cell[1] + sy
    ox, oy = block.out_cell[0] + sx, block.out_cell[1] + sy
    for j, row in enumerate(("+-+", "|I|", "+-+")):
        for i, ch in enumerate(row):
            grid[(ix - 4 + i, iy - 1 + j)] = ch
    grid[(ix - 1, iy)] = ">"
    for j, row in enumerate(("+-+", "|O|", "+-+")):
        for i, ch in enumerate(row):
            grid[(ox - 1 + i, j)] = ch
    for y in range(3, oy):
        grid[(ox, y)] = "^"
    w = max(x for x, _ in grid) + 1
    h = max(y for _, y in grid) + 1
    return FastLittleman("\n".join(
        "".join(grid.get((x, y), " ") for x in range(w)) for y in range(h)))


def wire(op, addr):
    return [2 * addr - op]


VAL = lambda a: (a * 37 + 11) % 9973


def readback(batch, order_name):
    mt.JUMP_V4_P2_BATCH = batch
    engine = standalone(taped_store_block(N, PLAN, **KW))
    writes = [x for a in range(1, N) for x in (*wire(1, a), VAL(a))]
    bounds = [1]
    for m in PLAN:
        bounds.append(bounds[-1] + m)
    out = {}
    for lo, hi in zip(bounds, bounds[1:]):
        hi = min(hi, N)
        rng = list(range(lo, hi))
        if order_name == "desc":
            rng = rng[::-1]
        elif order_name == "interleave":
            rng = [a for pair in zip(rng[::2], rng[1::2] + [None]) for a in pair if a]
        reads = [x for a in rng for x in wire(0, a)]
        want = [VAL(a) for a in rng]
        res = engine.run(writes + reads, expected=want, max_ticks=1_600_000_000)
        assert res.fatal is None, (batch, order_name, lo, res.fatal)
        out.update(zip(rng, res.output))
    return out


want = {a: VAL(a) for a in range(1, N)}
for order_name in ("asc", "desc"):
    off = readback(0, order_name)
    on = readback(4, order_name)
    print(f"{order_name}: shipped == truth: {off == want}   batch4 == truth: {on == want}"
          f"   batch4 == shipped: {on == off}", flush=True)
    if on != want:
        bad = [a for a in want if on.get(a) != want[a]]
        print("   first bad addresses:", bad[:20], "of", len(bad))
# negative control: an off-by-one park must fail, proving the harness can see it
print("done")
