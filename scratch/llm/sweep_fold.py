#!/usr/bin/env python3
"""Footprint-only sweep of the ROM fold against the corridor depth.

Build is ~7s and verification ~30s, so the shape is swept first and only the
survivors are timed.
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor


def build(args):
    rr, rb = args
    from randomfun2026solvers import llm_lm1

    try:
        built, prog, _ = llm_lm1.build_machine(rom_rows=rr, rom_buffer=rb)
        rows = built.rows
        w, h = max(len(r) for r in rows), len(rows)
        return (rr, rb, w, h, max(w, h) ** 2, prog.P)
    except Exception as exc:  # a fold that will not place is a datum too
        return (rr, rb, 0, 0, str(exc)[:70], 0)


def main() -> int:
    lo, hi = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (80, 93)
    buffers = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [1800]
    jobs = [(rr, rb) for rr in range(lo, hi) for rb in buffers]
    with ProcessPoolExecutor(max_workers=10) as ex:
        out = list(ex.map(build, jobs))
    for o in sorted(out, key=lambda t: (t[4] if isinstance(t[4], int) else 10**9)):
        print(f"rom_rows={o[0]:>3} buf={o[1]:>5} {o[2]}x{o[3]} area2={o[4]} P={o[5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
