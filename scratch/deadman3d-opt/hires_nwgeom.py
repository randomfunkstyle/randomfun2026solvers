#!/usr/bin/env python3
"""The north-west packed wall's column/row map and its twelve pipe lengths.

    python scratch/deadman3d-opt/hires_nwgeom.py

Builds :func:`d3_router.build_packed_wall` with ``north_west=True`` at the
compacted unit the hires tier ships, and prints everything the crossing
arguments are stated against — so a claim about a row can be read off the
build rather than off the docstring.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

LOOP_ROW = 10
LEAF_COLS = (3, 7, 27, 33, 37, 41, 73, 79)


def main(argv: list[str]) -> int:
    from randomfun2026solvers.lm1 import d3_router as R
    from randomfun2026solvers.lm1 import d3_unit

    lg = d3_unit.build_logic(R.TILE_FLOOR_ROW[0], LOOP_ROW, LEAF_COLS)
    lw, lh = lg.width, lg.height
    fx = R.RX + R.BLOCK_X0
    wx = fx + lw + R.PACK_NW_CH_A
    cx = wx + lw + R.PACK_NW_CH_W
    cy = R.PACK_NW_CLUSTER_Y
    cl = R.cluster_at(cx, cy)
    ex = cx + cl.width + R.PACK_CH_E
    row_s = cy + (R.PACK_ROW_S - R.PACK_CLUSTER_Y)

    print(f"logic {lw}x{lh} cmd_cell={lg.cmd_cell} ports={lg.ports}")
    print(f"cols: T1 {fx}..{fx + lw - 1} | ch {fx + lw}..{wx - 1} | "
          f"T0/T2 {wx}..{wx + lw - 1} | chan {wx + lw}..{cx - 1} | "
          f"cluster {cx}..{cx + cl.width - 1} | {cx + cl.width}..{ex - 1} | "
          f"T3 {ex}..{ex + lw - 1} | margin {ex + lw + 1}..{ex + lw + 3}")
    print(f"rows: router {R.RY - 1}..{R.RY + R.IH + 1} | fan {R.RY + R.IH + 2}..{cy - 1} | "
          f"north blocks+cluster {cy}..{cy + cl.height - 1} | "
          f"S {row_s}..{row_s + lh - 1} | under {row_s + lh}..{row_s + lh + 2}")
    print(f"cluster: north={cl.north} band={cl.band} lane={cl.lane} south={cl.south} "
          f"tunnel={cl.tunnel} corridor={cl.corridor} west_data={cl.west_data}")
    lane_a, lane_d, lane_s = cy - 4, cy - 3, cy - 2
    print(f"outlets on row {R.RY + R.IH + 2}; lanes: T1.addr {lane_a}, T1.data {lane_d}, "
          f"T1.swap {lane_s}, T1 leg {max(lane_s, R.RY + R.IH + 3)}, "
          f"T0 leg + cmd row {cl.north}; drops {[cy + d for d in R.PACK_NW_DROP]}; "
          f"T3 leg on lane {cl.lane}")

    w = R.build_packed_wall(loop_row=LOOP_ROW, leaf_cols=LEAF_COLS, north_west=True)
    print(f"\nwall {w.width}x{w.height} pipes={w.pipes} legs={w.legs}")
    print(f"panels={w.panels}  cluster row = {cy}")
    used = max(x for (x, _y) in w.cells)
    print(f"max column used = {used}")
    b = R.build_packed_wall(loop_row=LOOP_ROW, leaf_cols=LEAF_COLS, lift=24, north_up=3)
    print(f"\nsandwich for comparison: {b.width}x{b.height} panels={b.panels}")
    print(f"screens rise {b.panels[0][1] - w.panels[0][1]} rows within the wall; "
          f"wall loses {b.height - w.height} rows of height")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
