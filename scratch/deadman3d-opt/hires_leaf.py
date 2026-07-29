#!/usr/bin/env python3
"""What ``DOOM_LEAF_COLS`` is worth on ``deadman-3d_hires``, in ticks.

The wall stacks four unmodified DOOM blocks, so the compacted trie's shorter
dispatch walk is paid four times over — but hires' own profile is nothing like
the 64x48 machine's (68% blocked on the store, and the CPU parks on
``cpu->stream`` for 0.0003% of the run), so it may convert to nothing at all.
Built from scratch either way and round-gated over the same tour
(``FastLittleman(frame_tiles=(2, 2))``), so the two numbers are comparable.

    python scratch/deadman3d-opt/hires_leaf.py [rounds]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path(os.environ.get("DEADMAN3D_IWAD", "")) if os.environ.get("DEADMAN3D_IWAD") \
    else Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
KEY = ("deadman-3d_hires", "taped")


def main(argv: list[str]) -> int:
    import tempfile

    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.fast_littleman import FastLittleman
    from randomfun2026solvers.lm1 import machine as M

    n = int(argv[0]) if argv else 9
    hires.install_wad(WAD)
    cmds = list(d3.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    print(f"tour {len(rounds)} rounds", flush=True)

    shipped = M.DOOM_LEAF_COLS.get(KEY)
    base: int | None = None
    for name, leaves in (("pitch", None), ("compact", shipped)):
        M.DOOM_LEAF_COLS.pop(KEY, None)
        if leaves is not None:
            M.DOOM_LEAF_COLS[KEY] = leaves
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.time()
            built = hires.build_local(WAD, Path(tmp), cmds, pngs=False)
            m = built["machine"]
            res = FastLittleman("\n".join(m.rows)).run(
                inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        if res.fatal or res.passed is not True:
            print(f"  {name:>8}: {m.width}x{m.height} FAILED — fatal={res.fatal} "
                  f"passed={res.passed} at {res.step:,}", flush=True)
            continue
        walk = res.frame_ticks[-1] - res.frame_ticks[0]
        base = res.step if base is None else base
        print(f"  {name:>8}: {m.width}x{m.height}  ticks={res.step:,}  "
              f"walk={walk:,}  ({100 * (res.step - base) / base:+.3f}%)  "
              f"[{time.time() - t0:.0f}s]", flush=True)
    if shipped is not None:
        M.DOOM_LEAF_COLS[KEY] = shipped
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
