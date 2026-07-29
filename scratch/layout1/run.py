"""One command: run the ladder, print each rung's known answer beside the solver's.

    uv run python -m scratch.layout1.run           # the ladder
    uv run python -m scratch.layout1.run --store   # ...and the store's request legs
"""

from __future__ import annotations

import sys
import time

from .ladder import ladder
from .solve import solve


def main(argv: list[str]) -> int:
    bad = 0
    print("=" * 78)
    print("LADDER — each rung's answer is known by construction")
    print("=" * 78)
    for rung in ladder():
        t0 = time.time()
        rep = solve(rung.problem)
        dt = time.time() - t0
        if rep.best is None:
            print(f"\nFAIL {rung.name}")
            print(f"     known : {rung.known}")
            print(f"     got   : no feasible layout — {rep.summary()}")
            bad += 1
            continue
        ok, saw = rung.check(rep.best, rep)
        print(f"\n{'PASS' if ok else 'FAIL'} {rung.name}   ({dt:.2f}s)")
        print(f"     known : {rung.known}")
        print(f"     got   : {saw}")
        print(f"     search: {rep.summary()}")
        print(f"     cost  : {rep.best.weighted_cells:.1f} weighted cells "
              f"= {rep.best.ticks:,.0f} ticks")
        if rep.segment_warnings:
            print("     NOTE  : the segment reading of §7.1 disagrees:")
            for w in rep.segment_warnings:
                print(f"             {w}")
        bad += not ok

    if "--store" in argv:
        from .store import run_store

        bad += run_store()

    print("\n" + "=" * 78)
    print(f"{'ALL RUNGS PASS' if not bad else f'{bad} FAILURE(S)'}")
    print("=" * 78)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
