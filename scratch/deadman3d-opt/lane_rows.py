#!/usr/bin/env python3
"""Which opcode owns which lane row, and what each row's drop actually costs.

Pairs the CPU's emitted cells with the plan, so a row in ``lane_slack.py``'s dump
can be named.  Geometry only.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lane_slack import cpu_of  # noqa: E402


def main() -> int:
    from randomfun2026solvers.lm1 import machine as M  # noqa: F401

    m, cpu = cpu_of()
    p = m.plan
    cells = cpu.cells
    w, h = cpu.width, cpu.height
    collector = max(
        y for y in range(h) if sum(1 for x in range(w) if cells.get((x, y)) == "<") > w // 3
    )
    # rebuild row_of the way build_cpu does is fragile; instead match by the row's
    # glyph run against each opcode's micro-program.
    rows = {}
    for y in range(h):
        line = "".join(cells.get((x, y), " ") for x in range(w))
        rows[y] = line

    print("opcode -> number:", dict(p.number))
    print()
    print("plan rows (p.row, untrimmed slot space):")
    for m_ in p.number:
        print(f"  {m_:<10} slot={(p.row[m_] - 1) // 2:<3} sem={p.sem[m_]}")
    print()
    order = sorted(p.number, key=lambda k: p.row[k])
    lane_ys = [
        y
        for y in range(h)
        if y < collector and any(cells.get((x, y)) == "v" for x in range(w))
    ]
    print("lane rows with a drop, in order:", lane_ys)
    print()
    for y, m_ in zip(sorted(lane_ys), order, strict=False):
        turn = next(x for x in range(w) if cells.get((x, y)) == "v")
        print(f"  row {y:>3}  {m_:<10} sem={str(p.sem[m_]):<16} turn={turn:>3}  {rows[y].rstrip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
