#!/usr/bin/env python3
"""Profile the shipped taped hires machine and cache per-cell heat.

The floor theorem tells you what a block *could* cost.  Ranking the blocks needs
the other factor -- how often each one runs -- and that is measured here, not
assumed: ``FastProfile.heat`` is a per-cell sample count, so

    ticks on a cell  =  heat * stride            (measured)
    laps of a loop   =  ticks on the loop / lap cells

and a cell's ``wait`` share says how much of its time was spent blocked on a
pipe rather than walking, which is the difference between "this loop is too long"
and "this loop is waiting for something else".

Output is cached to ``/tmp/compactor/heat-<stride>.pkl``.  Nothing goes in the
repo: the counts are IWAD-derived.

    python3 prof.py [stride] [rounds]
"""

from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

HOME = Path("/Users/ptaykalo/.claude-personal/jobs/06214102/tmp/h")
OUT = Path("/tmp/compactor")


def main() -> int:
    stride = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 21
    sys.path.insert(0, str(HOME))
    import common

    d3, hires, M, prog = common.setup()
    import randomfun2026solvers.lm1.machine as mach

    assert "worktrees/compactor" in mach.__file__, mach.__file__
    from randomfun2026solvers.fast_littleman import FastLittleman

    inp, frames = common.tour(hires, rounds)
    t = time.time()
    m = M.build_for("deadman-3d_hires", program=prog, store=hires.STORE_TIER)
    print(f"built {m.width}x{m.height} ({time.time() - t:.0f}s)", flush=True)
    t = time.time()
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000,
        profile=True, profile_stride=stride)
    print(f"ran in {time.time() - t:.0f}s: step={res.step:,} "
          f"passed={res.passed} fatal={res.fatal} "
          f"last_frame={res.frame_ticks[-1]:,}", flush=True)
    p = res.profile
    path = OUT / f"heat-{stride}-{rounds}.pkl"
    with path.open("wb") as fh:
        pickle.dump({"regions": {k: tuple(v) for k, v in m.regions.items()},
                     "heat": dict(p.heat), "wait": dict(p.wait),
                     "samples": p.samples, "stride": p.stride,
                     "step": res.step, "rows": m.rows,
                     "passed": res.passed, "fatal": res.fatal}, fh)
    print(f"wrote {path}: samples={p.samples:,} stride={p.stride}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
