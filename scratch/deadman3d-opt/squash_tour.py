#!/usr/bin/env python3
"""What the **partial** squash is worth, with ``SEEK_TELEPORT`` kept.

``squash_grid.py`` shows the squash is not all-or-nothing: taking k of the ten rows
the stagger frees builds for k<=8 with ``SEEK_TELEPORT`` on, because room H needs
only four rows between the store's bottom and the STREAM unit's top and a full
squash leaves it two. The recorded -0.243% was measured with ``SEEK_TELEPORT``
*removed* from both sides, so it prices the squash on a machine nobody ships.

This measures it on the shipped machine. Metric: ``res.frame_ticks[-1]`` — ticks to
the last frame, the whole run including boot, equal to ``res.step``. The walk
(``frame_ticks[-1] - frame_ticks[0]``) is printed alongside because the two differ
by boot's ~8% and the older tables in this directory are the walk.

    python scratch/deadman3d-opt/squash_tour.py [rounds] [variant ...]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(REPO))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
KEY = (SLUG, "taped")

#: name -> (squash_band, rom_touch_drop, seek_teleport)
VARIANTS: dict[str, tuple[bool | int, int, bool]] = {
    "shipped":      (False, 22, True),
    "k7-d22":       (7,     22, True),
    "k8-d18":       (8,     18, True),
    "k8-d14":       (8,     14, True),
    "k4-d22":       (4,     22, True),
    # corridor-compensated: drop = 22 + k holds ``fetch_y`` where it shipped, so
    # the squash takes rows off the box and changes no pipe length. k=3 is the
    # deepest §7.1 allows (k=4 / drop 26 ties the fetch against ``in``).
    "k1-d23":       (1,     23, True),
    "k2-d24":       (2,     24, True),
    "k3-d25":       (3,     25, True),
    # the recorded pair, to re-derive -0.243% under the stated metric. The second
    # one does not build: ``layout2.noseek`` solves the full-squash drop interval
    # as [5, 17], so the recorded row cannot have used the registry's drop 22 and
    # the two rows it was differenced from do not share a corridor length.
    "noseek":       (False, 22, False),
    "noseek-full":  (True,  22, False),
    # A matched pair on the no-teleport machine: both at effective drop 7
    # (``drop - k``), so the only difference is the squash itself.
    "noseek-d7":      (False, 7,  False),
    "noseek-full-d17": (True, 17, False),
    # On the no-teleport machine the corridor's tick derivative has the *opposite*
    # sign (d7 beats d22), so the recorded 191,600,156 may sit at a low drop —
    # which would mean the recorded pair differed by ~17 rows of corridor as well
    # as by the squash.
    "noseek-full-d5":  (True, 5,  False),
    "noseek-full-d10": (True, 10, False),
    # drop 18 alone, to separate the squash from the drop it needs
    "k0-d18":       (False, 18, True),
}


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.fast_littleman import FastLittleman
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    n = int(argv[0]) if argv else 21
    names = argv[1:] or ["shipped", "k7-d22", "k8-d18"]

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    cmds = list(hires.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    print(f"tour {len(rounds)} rounds, tape={M.TAPE_SIZE[SLUG]}, P={prog.P}", flush=True)

    base: int | None = None
    for name in names:
        sq, drop, tele = VARIANTS[name]
        had = KEY in M.SEEK_TELEPORT
        (M.SEEK_TELEPORT.add if tele else M.SEEK_TELEPORT.discard)(KEY)
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store="taped",
                            squash_band=sq, rom_touch_drop=drop)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:>13}: BUILD FAILED — {exc}", flush=True)
            (M.SEEK_TELEPORT.add if had else M.SEEK_TELEPORT.discard)(KEY)
            continue
        box = f"{m.width}x{m.height}"
        res = FastLittleman("\n".join(m.rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        (M.SEEK_TELEPORT.add if had else M.SEEK_TELEPORT.discard)(KEY)
        if res.fatal is not None or res.passed is not True:
            print(f"  {name:>13}: {box} RUN FAILED — fatal={res.fatal} "
                  f"passed={res.passed} at {res.step:,}", flush=True)
            continue
        last = res.frame_ticks[-1]
        walk = res.frame_ticks[-1] - res.frame_ticks[0]
        if name == names[0]:
            base = last
        vs = ""
        if base is not None and name != names[0]:
            vs = f"  {last - base:+,} = {100.0 * (last - base) / base:+.3f}%"
        print(f"  {name:>13}: {box} last={last:,} walk={walk:,}{vs}"
              f"  ({time.time() - t0:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
