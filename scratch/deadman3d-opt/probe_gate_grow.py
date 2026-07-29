"""Can the GATE's own room be grown to meet the request, instead of a forwarder?

The request teleport costs one forwarder — a six-cell loop that re-serialises
the request's two words at one per six ticks (~0.66% of the tour, M12). Growing
the gate strip's room north instead would cost nothing at all: the pipe attaches
two cells below the adapter's floor and the gate's man walks the same glyphs.

The reason it is not obviously legal is ``U``. The gate's entry is ``U``, not
``R``: *"like R, but on success the man turns away from the side of the room he
read from"* (SPEC.md). With the room grown, the pipe still lands on the **west**
wall but 33 rows above the man. If "side" means the wall the pipe attaches to,
the man still turns east and the gate works. If it means the direction from the
man to the pipe, he turns south and the gate silently mis-routes.

The same question decides the ``reqK->bankK`` follow-up (45/45/44/97 cells,
6.65% of the run), which wants exactly this move on three more gates — so it is
worth one probe rather than one refactor.

usage: probe_gate_grow.py <n> <plan csv> <order csv> [lift]
"""
import sys

sys.path.insert(0, "solvers/python")

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402
from randomfun2026solvers.memory_taped import (  # noqa: E402
    gate_rows,
    taped_plan,
    taped_store_block,
)

n = int(sys.argv[1])
plan = tuple(int(x) for x in sys.argv[2].split(","))
order = None if sys.argv[3] == "-" else tuple(int(x) for x in sys.argv[3].split(","))
LIFT = int(sys.argv[4]) if len(sys.argv) > 4 else 30


def grown(block, lift: int) -> FastLittleman:
    """The block with gate 0's room pulled ``lift`` rows north, fed at the top.

    Only the room grows. Every glyph, every other pipe and both outgoing arms
    keep their cells, so the two outgoing ``s`` distances are untouched.
    """
    sx, sy = 6, 4
    cells = dict(block.cells)
    ix, iy = block.in_cell  # local: the request stub, gate 0's west wall + 2

    # gate 0's room: west wall at ix+2, north wall at the row of its own corner.
    gx0 = ix + 2
    gy0 = min(y for (x, y) in cells if x == gx0 and cells[(x, y)] == "+")
    gx1 = gx0 + 1
    while cells.get((gx1, gy0)) == "-":
        gx1 += 1
    assert cells.get((gx1, gy0)) == "+", cells.get((gx1, gy0))

    # erase the old north wall, redraw it `lift` rows up, and extend both sides
    for x in range(gx0, gx1 + 1):
        del cells[(x, gy0)]
    ny = gy0 - lift
    for x in range(gx0 + 1, gx1):
        cells[(x, ny)] = "-"
    for y in range(ny + 1, gy0 + 1):
        cells.setdefault((gx0, y), "|")
        cells.setdefault((gx1, y), "|")
    cells[(gx0, ny)] = cells[(gx1, ny)] = "+"

    # the request now enters the WEST wall two rows below the new roof, which is
    # 33 rows above the `U` that reads it.
    del cells[(ix, iy)]
    del cells[(ix + 1, iy)]
    feed = ny + 2

    grid = {(x + sx, y + sy): ch for (x, y), ch in cells.items()}
    fx, fy = ix + sx, feed + sy
    for j, row in enumerate(("+-+", "|I|", "+-+")):
        for i, ch in enumerate(row):
            grid[(fx - 4 + i, fy - 1 + j)] = ch
    for i in range(-1, 2):  # ix-1 .. ix+1, into the west wall at ix+2
        grid[(fx + i, fy)] = ">"

    ox, oy = block.out_cell[0] + sx, block.out_cell[1] + sy
    for j, row in enumerate(("+-+", "|O|", "+-+")):
        for i, ch in enumerate(row):
            grid[(ox - 1 + i, j)] = ch
    for y in range(3, oy):
        grid[(ox, y)] = "^"
    w = max(x for x, _ in grid) + 1
    h = max(y for _, y in grid) + 1
    return FastLittleman(
        "\n".join("".join(grid.get((x, y), " ") for x in range(w)) for y in range(h))
    )


engine = grown(
    taped_store_block(n, plan, skip_batch=2, compact_gate=True, order=order), LIFT
)
writes = [x for a in range(1, n) for x in (1, a, a * 13 + 7)]
bounds = [1]
for m in taped_plan(n, plan):
    bounds.append(bounds[-1] + m)
got, fatal = {}, None
for lo, hi in zip(bounds, bounds[1:]):
    hi = min(hi, n)
    if lo >= hi:
        continue
    reads = [x for a in range(lo, hi) for x in (0, a)]
    want = [a * 13 + 7 for a in range(lo, hi)]
    res = engine.run(writes + reads, expected=want, max_ticks=4_000_000_000)
    if res.fatal is not None:
        fatal = (lo, res.fatal)
        break
    got.update(zip(range(lo, hi), res.output))

want = {a: a * 13 + 7 for a in range(1, n)}
bad = {a: (got.get(a), want[a]) for a in want if got.get(a) != want[a]}
print(f"lift={LIFT} plan={plan} order={order}")
if fatal:
    print(f"  FATAL at bank starting {fatal[0]}: {fatal[1]}")
    sys.exit(1)
print(f"  {len(got)} addresses, {len(bad)} wrong")
if bad:
    for a in sorted(bad)[:10]:
        print(f"  addr {a}: got {bad[a][0]} want {bad[a][1]}")
    sys.exit(1)
print("  U turns off the WALL the pipe attaches to, not the direction to it")
