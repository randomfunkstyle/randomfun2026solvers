"""Address-by-address readback of the REAL taped plan **through the request room**.

``readback.py`` drives the block's request stub directly, so it says nothing
about ``STORE_REQUEST_TELEPORT`` — the room and its two stubs live in
``lm1.machine``, outside the block. This script rebuilds that geometry around
the standalone block: input room -> two-cell stub -> teleport_v room -> four-cell
stub -> the block's own request stub, exactly the cell counts and the entry side
the machine draws. If the room reordered, dropped or duplicated a request word,
or if the gate's ``U`` stopped turning east, an address would come back wrong
here rather than only in a frame diff.

usage: readback_teleport.py <n> <plan csv> <order csv>
"""
import sys
import time

sys.path.insert(0, "solvers/python")

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402
from randomfun2026solvers.lm1.machine import _TELE_W  # noqa: E402
from randomfun2026solvers.memory_men import teleport_v  # noqa: E402
from randomfun2026solvers.memory_taped import taped_plan, taped_store_block  # noqa: E402

n = int(sys.argv[1])
plan = tuple(int(x) for x in sys.argv[2].split(","))
order = None if sys.argv[3] == "-" else tuple(int(x) for x in sys.argv[3].split(","))

#: The machine's own room: 27 interior rows between the adapter's floor and the
#: gate strip's roof. Only the height is arbitrary here — the stub lengths are
#: not, since they are what the request actually walks.
ROOM_H = 27


def wrapped(block) -> FastLittleman:
    """The block, its request reached through the same room the machine builds."""
    sx, sy = 6, 4
    grid = {(x + sx, y + sy): ch for (x, y), ch in block.cells.items()}
    ix, iy = block.in_cell[0] + sx, block.in_cell[1] + sy
    ox, oy = block.out_cell[0] + sx, block.out_cell[1] + sy

    # The room, hung above the block's request stub: west wall one clear of the
    # exit column, south wall four rows above the gate's entry row. The height
    # is clamped only so a small plan's short block still has sky above it — the
    # whole point of the room is that its height costs nothing.
    rx0, ry1 = ix - 1, iy - 4
    room_h = min(ROOM_H, ry1 - 8)
    assert room_h >= 2, f"no sky above the gate for the room: ry1={ry1}"
    rx1, ry0 = rx0 + _TELE_W + 1, ry1 - room_h - 1
    for x in range(rx0 + 1, rx1):
        grid[(x, ry0)] = grid[(x, ry1)] = "-"
    for y in range(ry0 + 1, ry1):
        grid[(rx0, y)] = grid[(rx1, y)] = "|"
    for c in ((rx0, ry0), (rx1, ry0), (rx0, ry1), (rx1, ry1)):
        grid[c] = "+"
    for j, row in enumerate(teleport_v(ROOM_H)[0]):
        for i, ch in enumerate(row):
            if ch != " ":
                grid[(rx0 + 1 + i, ry0 + 1 + j)] = ch

    # ... its exit: three cells down the west column, then east onto the block's
    # own two-cell stub (4 machine cells, 6 parsed — what the machine draws).
    for y in range(ry1 + 1, iy):
        grid[(ix, y)] = "v" if y == ry1 + 1 else "|"
    grid[(ix, iy)] = ">"

    # ... and its feed: an input room whose pipe drops two cells into the roof,
    # the way the adapter's floor does.
    drop = rx1 - 1
    for j, row in enumerate(("+-+", "|I|", "+-+")):
        for i, ch in enumerate(row):
            grid[(drop - 1 + i, ry0 - 5 + j)] = ch
    grid[(drop, ry0 - 2)] = "v"
    grid[(drop, ry0 - 1)] = "v"

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


def readback(order):
    engine = wrapped(
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
print(
    f"plan={plan} order={order}: {len(got)} addresses, {len(bad)} wrong  "
    f"({time.time()-t0:.0f}s)"
)
if bad:
    for a in sorted(bad)[:20]:
        print(f"  addr {a}: got {bad[a][0]} want {bad[a][1]}")
    sys.exit(1)
print("all addresses read back their own value, through the request room")
