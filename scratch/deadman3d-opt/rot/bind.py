"""Enumerate every `r`/`s` binding inside a tape worker room, strictly.

The four wall anchors are the worker's contract with :func:`lm1.machine._tape_shell`:

* request pipe  (incoming) -- west wall, row ``V2_IN_ROW``; the segment touching
  the room is the stub cell at ``x = -2 - west_grow``.
* ring return   (incoming) -- south wall, column ``RET_COL``; the segment is one
  row *below* the wall, i.e. ``y = IH + 1``.
* ring forward  (outgoing) -- east wall, row ``FWD_ROW``; segment at ``x = IW + 1``.
* answer riser  (outgoing) -- north wall, column ``OUT_COL``; segment at ``y = -2``.

Checked against a fact the source already records: the narrow v4 body's P1 ``r``
at (10, 6) is 15 cells from the ring return and 16 from the request stub.

usage: bind.py            # both workers, west_grow 0 and 4
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/"
                   ".claude/worktrees/compactor/solvers/python")

from randomfun2026solvers import memory_tape as T  # noqa: E402


def anchors(iw: int, ih: int, in_row: int, out_col: int, fwd_row: int,
            ret_col: int, wg: int):
    return {
        "request": ("in", (-2 - wg, in_row)),
        "ring-return": ("in", (ret_col, ih + 1)),
        "ring-forward": ("out", (iw + 1, fwd_row)),
        "answer": ("out", (out_col, -2)),
    }


def report(name, c, iw, ih, in_row, out_col, fwd_row, ret_col, want=None):
    bad = 0
    for wg in (0, 4):
        ports = anchors(iw, ih, in_row, out_col, fwd_row, ret_col, wg)
        for (x, y), ch in sorted(c.cell.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            if ch not in "rsSRU":
                continue
            side = "in" if ch in "rRU" else "out"
            if ch in "SR U":
                pass
            d = sorted(
                (abs(x - px) + abs(y - py), nm)
                for nm, (sd, (px, py)) in ports.items()
                if sd == side
            )
            if ch in "SR":  # S writes every outgoing pipe; R reads any incoming
                continue
            near, second = d[0], d[1]
            margin = second[0] - near[0]
            flag = "TIE-FAIL" if margin == 0 else ("tight" if margin <= 2 else "")
            if want is not None and (x, y) in want and want[(x, y)] != near[1]:
                flag = f"WRONG-PIPE (wanted {want[(x, y)]})"
            if flag:
                bad += 1
            print(f"  {name} wg={wg} ({x:2d},{y:2d}) {ch!r} -> {near[1]:<12s} "
                  f"{near[0]:3d} vs {second[1]:<12s} {second[0]:3d}  margin {margin:2d} {flag}")
    return bad


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 53
    bad = 0
    print("== narrow v4 (worker_v2, batch 1) ==")
    bad += report("narrow", T.worker_v2(n, park_const=True, protocol="v4"),
                  T.V2_V4_IW, T.V2_IH, T.V2_V4_IN_ROW, T.V2_V4_OUT_COL,
                  T.V2_FWD_ROW, T.V2_RET_COL)
    print("== wide v4 (worker_v2_jump, batch 2) ==")
    bad += report("wide", T.worker_v2_jump(n, park_const=True, protocol="v4"),
                  T.V2_JUMP_IW, T.V2_JUMP_V4_IH, T.V2_IN_ROW, T.V2_OUT_COL,
                  T.V2_JUMP_FWD_ROW, T.V2_JUMP_RET_COL)
    print("== rotating (worker_v2_rot) ==")
    bad += report("rot", T.worker_v2_rot(n),
                  T.V2_ROT_IW, T.V2_ROT_IH, T.V2_IN_ROW, T.V2_OUT_COL,
                  T.V2_JUMP_FWD_ROW, T.V2_JUMP_RET_COL,
                  want={(1, 5): "request", (13, 2): "request",
                        (27, 9): "ring-return", (17, 8): "ring-return",
                        (16, 8): "ring-forward", (31, 4): "ring-forward"})
    print(f"\n{bad} flagged")
