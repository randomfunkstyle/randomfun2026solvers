#!/usr/bin/env python3
"""What ``hires_bankcut.py``'s cut is actually worth, in tour ticks.

The DP prices a *model* (``~8 * local`` a ring access, ``~21`` a gate hop).  This
runs the real machine: every variant is built from HEAD's registries with only
:data:`machine.TAPED_BANKS` and :data:`machine.TAPED_BANK_ORDER` moved, then
round-gated over the same 21-round tour, reporting ticks over frames 1..20 —
the one metric this family has (``AGENTS.md`` §deadman-3d is out of scope).

The order moves *with* the cut and is not a second variable: a chain order is
read off the bank traffic, and re-cutting the banks re-writes that traffic.
``(3, 0, 1, 2)`` was correct for uniform quarters and is wrong for every cut
below it.

    python scratch/deadman3d-opt/hires_bankrun.py [rounds] [variant ...]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
KEY = (SLUG, "taped")

#: name -> (sizes, order), straight off ``hires_bankcut.py``'s DP.  ``base`` is
#: HEAD: no ``TAPED_BANKS`` entry, so ``taped_plan``'s uniform quarters.
VARIANTS: dict[str, tuple[tuple[int, ...] | None, tuple[int, ...] | None]] = {
    # HEAD: no ``TAPED_BANKS`` entry (uniform quarters) but its *own* order,
    # which is not optional — at address order the block does not even build
    # ("taped block collision at (30, 18)").
    "base": (None, (3, 0, 1, 2)),
    "dp4": ((127, 673, 22, 79), (3, 2, 0, 1)),
    "dp5": ((123, 236, 441, 22, 79), (4, 3, 0, 1, 2)),
    "dp6": ((123, 236, 441, 17, 63, 21), (5, 4, 3, 0, 1, 2)),
    "dp8": ((119, 233, 10, 438, 9, 13, 58, 21), (7, 6, 5, 4, 0, 3, 2, 1)),
    "dp12": ((102, 21, 229, 7, 306, 135, 6, 9, 7, 58, 8, 13),
             (11, 10, 9, 8, 7, 6, 0, 1, 2, 3, 5, 4)),
    "dp16": ((102, 21, 229, 7, 306, 135, 4, 2, 3, 6, 2, 5, 11, 47, 8, 13),
             (15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 0, 1, 2, 3, 5, 4)),
    "dp20": ((102, 9, 16, 225, 7, 246, 105, 90, 4, 2, 3, 6, 2, 5, 11, 47, 4, 5, 10, 2),
             (19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 0, 1, 2, 3, 4, 7, 6, 5)),
    "dp10": ((102, 21, 229, 10, 438, 6, 9, 7, 58, 21),
             (9, 8, 7, 6, 5, 0, 1, 4, 3, 2)),
    "dp11": ((102, 21, 229, 7, 306, 135, 6, 9, 7, 58, 21),
             (10, 9, 8, 7, 6, 0, 1, 2, 3, 5, 4)),
    "dp13": ((102, 21, 229, 7, 306, 135, 5, 6, 5, 6, 58, 8, 13),
             (12, 11, 10, 9, 8, 7, 6, 0, 1, 2, 3, 5, 4)),
    "dp14": ((102, 21, 229, 7, 306, 135, 4, 4, 6, 3, 5, 58, 8, 13),
             (13, 12, 11, 10, 9, 8, 7, 6, 0, 1, 2, 3, 5, 4)),
    # the cut without the order, to show the order is not separable
    "dp4-oldorder": ((127, 673, 22, 79), (3, 0, 1, 2)),
}


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.fast_littleman import FastLittleman
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    n = int(argv[0]) if argv else 21
    names = argv[1:] or list(VARIANTS)

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    cmds = list(hires.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    print(f"tour {len(rounds)} rounds, tape={M.TAPE_SIZE[SLUG]}, P={prog.P}", flush=True)

    results: dict[str, tuple[int, str]] = {}
    for name in names:
        sizes, order = VARIANTS[name]
        M.TAPED_BANKS.pop(SLUG, None)
        M.TAPED_BANK_ORDER.pop(KEY, None)
        if sizes is not None:
            M.TAPED_BANKS[SLUG] = sizes
        if order is not None:
            M.TAPED_BANK_ORDER[KEY] = order
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store="taped")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:>13}: BUILD FAILED — {type(exc).__name__}: {exc}", flush=True)
            continue
        box = f"{m.width}x{m.height}"
        res = FastLittleman("\n".join(m.rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        if res.fatal or res.passed is not True:
            print(f"  {name:>13}: {box} RUN FAILED — fatal={res.fatal} "
                  f"passed={res.passed} at {res.step:,}", flush=True)
            continue
        walk = res.frame_ticks[-1] - res.frame_ticks[0]
        results[name] = (walk, box)
        vs = ""
        if "base" in results and name != "base":
            b = results["base"][0]
            vs = f"  {walk - b:+,} = {100.0 * (walk - b) / b:+.3f}%"
        print(f"  {name:>13}: {box} walk={walk:,}{vs}  ({time.time() - t0:.0f}s)",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
