"""Judge a candidate ``leaf_cols`` on the native engine, pixel for pixel.

Same command mix and the same standalone probe as ``unit_loop_gate.py`` — every
arm, a negative-seed COL, the banding masks, both sprites — against the
``DoomUnit`` model's own frames, but sweeping the *columns* lever instead of the
rows one. ``steps`` is what the lever is for: the probe is the unit alone, so
its tick count is the unit's own service time for the whole mix, dispatch walk
included.

usage: unit_leaf_gate.py [loop_row ...]
"""
import sys

from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import d3_unit
from randomfun2026solvers.lm1.store import DoomUnit
from tests.test_deadman3d import frames_from_writes

codes = DoomUnit.CODES
w = d3_unit.word


def col(top: int, bot: int, x: int, colour: int) -> int:
    seed = (top * 64 + x) * 16 + colour - 1024
    return w(codes["COL"], seed * 64 + (bot - top + 1))


CMDS = [
    w(codes["RUN"], 70 * 16 + 9), w(codes["RUN"], 1 * 16 + 12),
    w(codes["RUN"], 200 * 16 + 3), w(codes["COMMIT"], 0),
    col(3, 10, 5, 12), col(0, 39, 0, 9), col(20, 20, 63, 7), col(5, 39, 30, 14),
    w(codes["GUN"], 0),
    w(codes["CURS"], 2560), w(codes["RUN"], 64 * 16 + 7),
    w(codes["RUN"], 55 * 16 + 8),
    w(codes["CURS"], 41 * 64 + 4), w(codes["RUN"], 25 * 16 + 9),
    w(codes["COMMIT"], 0),
    col(10, 30, 22, 11), w(codes["GUNF"], 0), w(codes["COMMIT"], 0),
]

writes: list[tuple[int, int]] = []
unit = DoomUnit(lambda p, v: writes.append((p, v)))
for cmd in CMDS:
    unit.send(cmd)
EXPECTED = frames_from_writes(writes, width=64, height=48)
assert len(EXPECTED) == 3

LAYOUTS = {"shipped": d3_unit.LEAF_COLS, "compact": d3_unit.COMPACT_LEAF_COLS}
rows_to_try = [int(a) for a in sys.argv[1:]] or [d3_unit.R_LOOP, d3_unit.MIN_LOOP_ROW]

for loop_row in rows_to_try:
    base = None
    for name, leaves in LAYOUTS.items():
        blk = d3_unit.build_doom(loop_row=loop_row, leaf_cols=leaves)
        grid, _dbg, _b = d3_unit.build_probe(CMDS, loop_row=loop_row, leaf_cols=leaves)
        res = FastLittleman(grid).run([], frames=[EXPECTED], max_ticks=5_000_000)
        ok = res.fatal is None and res.passed is True
        base = base if base is not None else res.step
        print(
            f"loop_row {loop_row:3d}  {name:8s}  block {blk.width:4d}x{blk.height:<4d} "
            f"iw={d3_unit.interior_width(leaves):3d}  {'PASS' if ok else 'FAIL'}  "
            f"steps={res.step:,}  ({100 * (res.step - base) / base:+.2f}%)"
            + ("" if ok else f"  fatal={res.fatal} @{res.fatal_pos} passed={res.passed}")
        )
