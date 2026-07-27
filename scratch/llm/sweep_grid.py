#!/usr/bin/env python3
"""Footprint sweep over an explicit (rom_rows, rom_buffer) product.

    uv run python scratch/llm/sweep_grid.py 84,85,86,87 600,800,1000,1200,1400
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor


def build(args):
    rr, rb, hot = args
    from randomfun2026solvers import llm_lm1

    try:
        built, prog, _ = llm_lm1.build_machine(rom_rows=rr, rom_buffer=rb, hot=hot)
        rows = built.rows
        w, h = max(len(r) for r in rows), len(rows)
        return (rr, rb, w, h, max(w, h) ** 2, prog.P)
    except Exception as exc:
        return (rr, rb, 0, 0, str(exc)[:70], 0)


def main() -> int:
    rrs = [int(x) for x in sys.argv[1].split(",")]
    rbs = [int(x) for x in sys.argv[2].split(",")]
    hot = tuple(int(x) for x in sys.argv[3].split(",")) if len(sys.argv) > 3 else None
    jobs = [(rr, rb, hot) for rr in rrs for rb in rbs]
    with ProcessPoolExecutor(max_workers=10) as ex:
        out = list(ex.map(build, jobs))
    for o in sorted(out, key=lambda t: (t[4] if isinstance(t[4], int) else 10**9)):
        print(f"rom_rows={o[0]:>3} buf={o[1]:>5} {o[2]}x{o[3]} area2={o[4]} P={o[5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
