#!/usr/bin/env python3
"""Can ``STORE_REQUEST_REACH`` be made to bind on ``deadman-3d_hires``?

Out of the box it cannot: the store's request column is 101 and the adapter's
floor spans 81..92, so the two-cell drop has nowhere to start.  ``deadman-3d``
buys that overlap with a ``TIER_LAYOUT`` ``store_offset`` of ``(-20, 0)``; hires
has no ``TIER_LAYOUT`` entry at all.  Column ``101 + dx`` lands in ``81..92``
for ``dx`` in ``-20..-9``, and ``STORE_ANSWER_WEST``'s note records that ``-18``
places (it failed that registry's *guard*, not placement) while ``-19``/``-20``
do not — so the untried window is ``-18..-9``.

Builds each offset with the roof on and reports the box, then gates the ones
that bind.  Offsets are also gated *without* the roof, because moving the store
is not free: it lengthens or shortens every leg that crosses the gap.

    python scratch/deadman3d-opt/hires_roof.py [rounds] [dx ...]
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


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.fast_littleman import FastLittleman
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    n = int(argv[0]) if argv else 5
    offsets = [int(a) for a in argv[1:]] or list(range(-9, -19, -1))

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    cmds = list(hires.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    prog = assemble(d3.deadman3d_source(d3.GEOM128), name=SLUG)
    print(f"tour {len(rounds)} rounds, P={prog.P}", flush=True)

    def run(dx: int | None, roof: bool) -> None:
        M.STORE_REQUEST_REACH.discard(KEY)
        M.TIER_LAYOUT.pop(KEY, None)
        if roof:
            M.STORE_REQUEST_REACH.add(KEY)
        if dx is not None:
            M.TIER_LAYOUT[KEY] = {"store_offset": (dx, 0)}
        tag = f"dx={dx if dx is not None else 0:>4} roof={int(roof)}"
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store="taped")
        except Exception as exc:  # noqa: BLE001
            print(f"  {tag}: BUILD FAILED — {exc}", flush=True)
            return
        res = FastLittleman("\n".join(m.rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        if res.fatal or res.passed is not True:
            print(f"  {tag}: {m.width}x{m.height} RUN FAILED — fatal={res.fatal} "
                  f"passed={res.passed} at {res.step:,}", flush=True)
            return
        walk = res.frame_ticks[-1] - res.frame_ticks[0]
        print(f"  {tag}: {m.width}x{m.height} total={res.step:,} walk={walk:,} "
              f"({time.time() - t0:.0f}s)", flush=True)

    try:
        run(None, False)
        for dx in offsets:
            run(dx, True)
            run(dx, False)
    finally:
        M.STORE_REQUEST_REACH.discard(KEY)
        M.TIER_LAYOUT.pop(KEY, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
