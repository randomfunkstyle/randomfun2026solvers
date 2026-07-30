#!/usr/bin/env python3
"""Gate :data:`~lm1.machine.TUCKED_DROPS` against a baseline built in the same run.

    uv run python scratch/deadman3d-opt/tuck_gate.py [rounds]

Measures **ticks to the last frame** (``res.frame_ticks[-1]``, i.e. ``res.step``),
which is the one metric for this family, and ships nothing unless
``fatal is None and passed is True``.  Both variants are built here, in this
process, so the delta is never a difference of two builds at different settings.

Writes to ``measurements.jsonl`` through ``config.py`` exactly as ``revalidate.py``
does, so each row carries the feature digest it was taken under.  Only scalars —
nothing IWAD-derived.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
KEY = (SLUG, "taped")
KNOBS = dict(dda_acc_reload=False, dda_diff=True, dda_stepy_split=True, lap_via_jump=True)


def main(argv: list[str]) -> int:
    import config as cfg
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.fast_littleman import FastLittleman
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    n = int(argv[0]) if argv and argv[0].isdigit() else 3
    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    cmds = list(hires.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    log = Path(__file__).resolve().parent / "measurements.jsonl"
    src = d3.deadman3d_source(d3.GEOM128, **KNOBS)
    program = assemble(src, name=SLUG)

    def run(label: str, on: bool):
        was = KEY in M.TUCKED_DROPS
        M.TUCKED_DROPS.add(KEY) if on else M.TUCKED_DROPS.discard(KEY)
        try:
            feats = cfg.feature_set(SLUG, "taped", **KNOBS)
            t0 = time.time()
            try:
                m = M.build_for(SLUG, program=program, store="taped")
            except Exception as exc:  # noqa: BLE001
                print(f"  {label:>16}: BUILD FAILED — {type(exc).__name__}: {exc}")
                cfg.record(log, label, feats, rounds=n, outcome="build-failed",
                           error=type(exc).__name__)
                return None
            res = FastLittleman("\n".join(m.rows)).run(
                inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
            dt = time.time() - t0
            if res.fatal or res.passed is not True:
                print(f"  {label:>16}: RUN FAILED — fatal={res.fatal} passed={res.passed}")
                cfg.record(log, label, feats, rounds=n, outcome="run-failed")
                return None
            ft = list(res.frame_ticks)
            total, walk = ft[-1], ft[-1] - ft[0]
            cfg.record(log, label, feats, rounds=n, outcome="ok",
                       ticks_to_last_frame=total, walk=walk, boot=ft[0],
                       width=m.width, height=m.height)
            print(f"  {label:>16}: {m.width}x{m.height} ticks={total:,} "
                  f"boot={ft[0]:,} passed={res.passed}  ({dt:.0f}s) "
                  f"[{cfg.digest(feats)}]", flush=True)
            return total
        finally:
            M.TUCKED_DROPS.add(KEY) if was else M.TUCKED_DROPS.discard(KEY)

    print(f"{n} rounds", flush=True)
    base = run("baseline", False)
    cand = run("tucked", True)
    if base and cand:
        d = cand - base
        print(f"\n  tucked - baseline = {d:+,} = {100.0 * d / base:+.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
