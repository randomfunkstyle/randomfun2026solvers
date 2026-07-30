"""What each DOOM-unit arm actually occupies, against the columns it is given.

Rebuilds the unit interior with one arm at a time suppressed and diffs the cell
sets, so the answer is the arm's real column span and row span rather than the
region map's nominal box.

    python scratch/deadman3d-opt/unit_occupancy.py [pitch|compact]
"""
import sys

from randomfun2026solvers.lm1 import d3_unit as U

which = sys.argv[1] if len(sys.argv) > 1 else "pitch"
LEAVES = U.COMPACT_LEAF_COLS if which == "compact" else U.LEAF_COLS

unit = U.unit_interior(leaf_cols=LEAVES)
cells = unit.cells
cols = U.arm_columns(LEAVES)
codes = U.arm_codes(LEAVES)

print(f"{which}: interior {unit.width} x {U.UNIT_IH}   trie leaves at {list(LEAVES)}")
print(f"arm leaves: {cols}\ncodes: {codes}\n")

# Everything below the trie's last row belongs to exactly one arm: bucket every
# occupied cell by which leaf column is nearest at or west of it.
below = {(x, y): ch for (x, y), ch in cells.items()
         if y > U.R_TRIE + U.TRIE_BITS - 1 and y < U.R_COLLECT}
order = sorted(cols.items(), key=lambda kv: kv[1])
bounds = {}
for i, (arm, x0) in enumerate(order):
    x1 = order[i + 1][1] if i + 1 < len(order) else unit.width + 1
    own = [(x, y) for (x, y) in below if x0 - 1 <= x < x1 - 1]
    xs = sorted({x for x, _ in own})
    ys = sorted({y for _, y in own})
    bounds[arm] = (min(xs), max(xs), min(ys), max(ys), len(own))
    print(f"  {arm:7s} leaf x={x0:4d}  cols {min(xs):3d}..{max(xs):3d} "
          f"(span {max(xs) - min(xs) + 1:3d} of {x1 - x0:3d} granted)  "
          f"rows {min(ys):3d}..{max(ys):3d} (span {max(ys) - min(ys) + 1:2d} "
          f"of {U.R_COLLECT - U.R_ARG + 1})  cells={len(own):4d}")

used = sum(b[1] - b[0] + 1 for b in bounds.values())
print(f"\nreal column span, summed: {used} of the {unit.width}-wide interior "
      f"({100 * used / unit.width:.0f}%)")

# how many rows of the arm band are used at all
band = range(U.R_ARG, U.R_COLLECT)
dead_rows = [y for y in band if not any((x, y) in cells for x in range(unit.width + 1))]
print(f"rows in the arm band {U.R_ARG}..{U.R_COLLECT - 1} with no cell at all: {dead_rows}")

print("\nper-row occupancy across the arm band:")
for y in band:
    xs = [x for x in range(unit.width + 2) if (x, y) in cells]
    print(f"  row {y:3d}: {len(xs):3d} cells" + (f"  cols {min(xs)}..{max(xs)}" if xs else ""))
