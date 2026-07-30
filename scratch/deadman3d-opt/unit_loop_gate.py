"""Judge every candidate ``loop_row`` on the native engine, pixel for pixel.

Builds ``d3_unit.build_probe``'s standalone grid at each lift and runs the same
command mix ``test_deadman3d.test_doom_unit_probe_paints_like_the_model`` uses
— every arm, a negative-seed COL, the banding masks, both sprites — against the
``DoomUnit`` model's own frames.

usage: unit_loop_gate.py [lo] [hi]
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

lo = int(sys.argv[1]) if len(sys.argv) > 1 else d3_unit.MIN_LOOP_ROW
hi = int(sys.argv[2]) if len(sys.argv) > 2 else d3_unit.R_LOOP

for lr in range(lo, hi + 1):
    try:
        blk = d3_unit.build_doom(loop_row=lr)
    except d3_unit.DoomUnitError as exc:
        print(f"loop_row {lr:3d}: BUILD {exc}")
        continue
    rows, _dbg, _b = d3_unit.build_probe(CMDS, loop_row=lr)
    res = FastLittleman(rows).run([], frames=[EXPECTED], max_ticks=5_000_000)
    ok = res.fatal is None and res.passed is True
    print(
        f"loop_row {lr:3d}: block {blk.width}x{blk.height}  "
        f"{'PASS' if ok else 'FAIL'}  steps={res.step:,}"
        + ("" if ok else f"  fatal={res.fatal} @{res.fatal_pos} passed={res.passed}")
    )
