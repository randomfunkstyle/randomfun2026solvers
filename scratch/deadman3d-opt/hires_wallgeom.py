#!/usr/bin/env python3
"""The packed wall's own column/row map, printed rather than re-derived by hand.

    python scratch/deadman3d-opt/hires_wallgeom.py [lift]
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))


def main(argv: list[str]) -> int:
    from randomfun2026solvers.lm1 import d3_router as R
    from randomfun2026solvers.lm1 import d3_unit

    lift = int(argv[0]) if argv else 21
    logic = d3_unit.build_logic(R.TILE_FLOOR_ROW[0], 10,
                                (3, 7, 27, 33, 37, 41, 73, 79))
    lw, lh = logic.width, logic.height
    wx = R.RX + R.BLOCK_X0
    cx = wx + lw + R.PACK_CH_W
    cl = R.cluster_at(cx, R.PACK_CLUSTER_Y - lift)
    ex = cx + cl.width + R.PACK_CH_E
    print(f"RX={R.RX} RY={R.RY} IW={R.IW} IH={R.IH} BLOCK_X0={R.BLOCK_X0}")
    print(f"logic {lw}x{lh}  cmd_cell={logic.cmd_cell}  ports={logic.ports}")
    print(f"  regions: {logic.regions}")
    print(f"wx={wx}..{wx + lw - 1}  channel {wx + lw}..{cx - 1}  "
          f"cluster {cx}..{cx + cl.width - 1}  chan {cx + cl.width}..{ex - 1}  "
          f"ex={ex}..{ex + lw - 1}  margin {ex + lw + 1}..{ex + lw + 3}")
    print(f"cluster: corners={cl.corners} {cl.width}x{cl.height} tunnel={cl.tunnel} "
          f"corridor={cl.corridor} band={cl.band} lane={cl.lane} "
          f"west_data={cl.west_data} north={cl.north} south={cl.south}")
    print(f"rows: router 0..{R.RY + R.IH + 1}  N {R.PACK_ROW_N}..{R.PACK_ROW_N + lh - 1}"
          f"  cluster {cl.corners[0][1]}..{cl.corners[0][1] + cl.height - 1}"
          f"  S {R.PACK_ROW_S - lift}..{R.PACK_ROW_S - lift + lh - 1}"
          f"  fan {R.PACK_ROW_S - lift + lh}..{R.PACK_ROW_S - lift + lh + 2}")
    print(f"outlets={R.outlet_cols()}  (abs {[R.RX + c for c in R.outlet_cols()]})")
    w = R.build_packed_wall(loop_row=10, leaf_cols=(3, 7, 27, 33, 37, 41, 73, 79),
                            lift=lift)
    print(f"wall {w.width}x{w.height} legs={w.legs} panels={w.panels}")
    xs = {}
    for (x, y) in w.cells:
        xs.setdefault(y, []).append(x)
    print(f"max x used = {max(x for (x, _y) in w.cells)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
