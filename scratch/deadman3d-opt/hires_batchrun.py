#!/usr/bin/env python3
"""What ``TAPED_SKIP_BATCH`` is worth to ``deadman-3d_hires`` *after* the cut.

``deadman-3d`` runs ``skip_batch=2`` (the two-word counted worker, ~5 ticks a
skipped word against batch 1's 8) and booked -13% on its frame gate.  hires has
no entry, so ``machine.py``'s ``TAPED_SKIP_BATCH.get(program.name, 1)`` leaves it
on the 8-tick worker.  The naive read is that the same -13% is sitting there.

It is not obviously sitting there, because the lever pays per *slot walked* and
``c51a748`` cut hires' rings from one 223-slot ring holding 93% of accesses to
eleven short ones.  Batch size and bank count are the same trade seen twice, so
this measures them **jointly**: each variant carries its own ``TAPED_BANKS`` and
``TAPED_BANK_ORDER`` (which move together — see ``hires_bankrun.py``) *and* its
own ``TAPED_SKIP_BATCH``, and every variant is built from HEAD otherwise.

``hires_batchdp.py`` establishes the analytic half: the split DP's cut for a
fixed bank count is RING-invariant (the ring term scales linearly), so only the
*count* can move with the batch, and it does not until RING ~ 2.0 — below batch
2's ~5.  ``dp9`` is carried anyway as the nearest rival, because the model's
HOP=21 is itself an estimate.

    python scratch/deadman3d-opt/hires_batchrun.py [rounds] [variant ...]
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

DP11 = ((102, 21, 229, 7, 306, 135, 6, 9, 7, 58, 21),
        (10, 9, 8, 7, 6, 0, 1, 2, 3, 5, 4))
DP9 = ((102, 21, 229, 10, 438, 9, 13, 58, 21),
       (8, 7, 6, 5, 0, 1, 4, 3, 2))
DP10 = ((102, 21, 229, 10, 438, 6, 9, 7, 58, 21),
        (9, 8, 7, 6, 5, 0, 1, 4, 3, 2))
#: the pre-``c51a748`` shape: ``taped_plan(902, 4)``'s uniform quarters, with the
#: order that was shipped with them.  Carried as the *control* — the whole
#: question is whether batching pays on the long rings the cut removed.
UQ = ((226, 226, 226, 223), (3, 0, 1, 2))
DP4 = ((127, 673, 22, 79), (3, 2, 0, 1))

#: name -> (sizes, order, skip_batch).  ``b1-dp11`` is HEAD and the baseline
#: every table is reported against.
VARIANTS: dict[str, tuple[tuple[int, ...], tuple[int, ...], int]] = {
    "b1-dp11": (*DP11, 1),
    "b2-dp11": (*DP11, 2),
    "b4-dp11": (*DP11, 4),
    "b1-dp9": (*DP9, 1),
    "b2-dp9": (*DP9, 2),
    "b4-dp9": (*DP9, 4),
    "b2-dp10": (*DP10, 2),
    "b4-dp10": (*DP10, 4),
    "b1-uq": (*UQ, 1),
    "b2-uq": (*UQ, 2),
    "b4-uq": (*UQ, 4),
    "b1-dp4": (*DP4, 1),
    "b2-dp4": (*DP4, 2),
    "b1-dp7": ((123, 236, 441, 9, 13, 58, 21), (6, 5, 4, 3, 0, 1, 2), 1),
    "b2-dp7": ((123, 236, 441, 9, 13, 58, 21), (6, 5, 4, 3, 0, 1, 2), 2),
    "b1-dp8": ((119, 233, 10, 438, 9, 13, 58, 21), (7, 6, 5, 4, 0, 3, 2, 1), 1),
    "b2-dp8": ((119, 233, 10, 438, 9, 13, 58, 21), (7, 6, 5, 4, 0, 3, 2, 1), 2),
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
        sizes, order, batch = VARIANTS[name]
        M.TAPED_BANKS[SLUG] = sizes
        M.TAPED_BANK_ORDER[KEY] = order
        M.TAPED_SKIP_BATCH.pop(SLUG, None)
        if batch != 1:
            M.TAPED_SKIP_BATCH[SLUG] = batch
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store="taped")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:>9}: BUILD FAILED — {type(exc).__name__}: {exc}", flush=True)
            continue
        box = f"{m.width}x{m.height}"
        print(f"  {name:>9}: built {box} ({time.time() - t0:.0f}s), running...", flush=True)
        res = FastLittleman("\n".join(m.rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        if res.fatal or res.passed is not True:
            print(f"  {name:>9}: {box} RUN FAILED — fatal={res.fatal} "
                  f"passed={res.passed} at {res.step:,}", flush=True)
            continue
        walk = res.frame_ticks[-1] - res.frame_ticks[0]
        results[name] = (walk, box)
        vs = ""
        if "b1-dp11" in results and name != "b1-dp11":
            b = results["b1-dp11"][0]
            vs = f"  {walk - b:+,} = {100.0 * (walk - b) / b:+.3f}%"
        print(f"  {name:>9}: {box} walk={walk:,}{vs}  ({time.time() - t0:.0f}s)",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
