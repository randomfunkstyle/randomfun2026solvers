#!/usr/bin/env python3
"""What each *newer* taped-tier lever is worth on ``deadman-3d_hires``, in ticks.

``hires_taped.py`` did this for the first four registries and reported boxes,
because the family had no tick number.  It has one now
(``FastLittleman(frame_tiles=(2, 2))``), so this reports ticks — which is the
only metric this family has (``AGENTS.md`` §deadman-3d is out of contest scope).

Each variant is built from scratch and then round-gated over the same tour, so
the numbers are comparable to each other and to the baseline.  Nothing is
assumed to transfer: two of the first four did not.

    python scratch/deadman3d-opt/hires_opt.py [rounds] [variant ...]
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

#: name -> (registry additions, program knobs).  The baseline is what HEAD
#: already gives hires: the compact gate and its own bank order.
VARIANTS: dict[str, dict] = {
    "base": {},
    "acc": {"reload": False},
    "roof": {"sets": ["STORE_REQUEST_REACH"]},
    "chain": {"sets": ["TAPED_CHAIN_REACH"]},
    "feed": {"sets": ["TAPED_FEED_TELEPORT"]},
    "teleport": {"sets": ["STORE_REQUEST_TELEPORT"]},
    # The roof only reaches once the block has been pulled west far enough for
    # the drop to have somewhere to start (`hires_roof.py`); `dx14` is the
    # control that prices the move on its own.
    "dx14": {"dx": -14},
    "roof14": {"dx": -14, "sets": ["STORE_REQUEST_REACH"]},
    # the form the roof supersedes, priced on the same offset that lets the
    # roof reach at all — and the two ends of the offset window, to show that
    # once the roof reaches, where the block sits stops mattering
    "tele14": {"dx": -14, "sets": ["STORE_REQUEST_TELEPORT"]},
    "roof9": {"dx": -9, "sets": ["STORE_REQUEST_REACH"]},
    "roof20": {"dx": -20, "sets": ["STORE_REQUEST_REACH"]},
    "roof14+feed": {"dx": -14, "sets": ["STORE_REQUEST_REACH",
                                        "TAPED_FEED_TELEPORT"]},
    "ship": {"reload": False, "dx": -14,
             "sets": ["STORE_REQUEST_REACH", "TAPED_FEED_TELEPORT"]},
    "ship+chain": {"reload": False, "dx": -14,
                   "sets": ["STORE_REQUEST_REACH", "TAPED_FEED_TELEPORT",
                            "TAPED_CHAIN_REACH"]},
}

SHIPPED = {"reload": False, "sets": ["STORE_REQUEST_REACH", "TAPED_FEED_TELEPORT"],
           "dx": -14}
VARIANTS["shipped"] = SHIPPED
for _k in ("lap_via_jump", "dda_diff", "dda_stepy_split"):
    VARIANTS[f"+{_k}"] = {**SHIPPED, "prog": {_k: True}}
VARIANTS["+all3"] = {**SHIPPED, "prog": {"lap_via_jump": True, "dda_diff": True,
                                         "dda_stepy_split": True}}
VARIANTS["+diff+jump"] = {**SHIPPED, "prog": {"dda_diff": True, "lap_via_jump": True}}
VARIANTS["+diff+split"] = {**SHIPPED, "prog": {"dda_diff": True,
                                               "dda_stepy_split": True}}

SETS = ("STORE_REQUEST_REACH", "TAPED_CHAIN_REACH", "TAPED_FEED_TELEPORT",
        "STORE_REQUEST_TELEPORT", "STORE_ANSWER_WEST")

# `answer_west` is `(CX + W + 4) - tx_pre` and `tx_pre` carries `store_dx`, so
# the collector's wall is `-18 - store_dx` and the guard wants >= 1: the answer
# collapse needs **dx <= -19**.  The roof needs the request column `101 + dx` to
# land in the adapter's floor `81..92`: **dx in -20..-9**.  The two windows
# intersect in exactly two offsets, and until `store_offset` existed for hires
# at all neither was reachable — which is why this is re-measured rather than
# inherited.
for _dx in (-19, -20):
    VARIANTS[f"answer{-_dx}"] = {"dx": _dx, "sets": ["STORE_ANSWER_WEST"]}
    VARIANTS[f"roof{-_dx}+answer"] = {
        "dx": _dx, "sets": ["STORE_REQUEST_REACH", "STORE_ANSWER_WEST"]}
    VARIANTS[f"ship{-_dx}"] = {
        "reload": False, "dx": _dx,
        "sets": ["STORE_REQUEST_REACH", "TAPED_FEED_TELEPORT"]}
    VARIANTS[f"ship{-_dx}+answer"] = {
        "reload": False, "dx": _dx,
        "sets": ["STORE_REQUEST_REACH", "TAPED_FEED_TELEPORT",
                 "STORE_ANSWER_WEST"]}


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
    cmds = list(hires.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    print(f"tour {len(rounds)} rounds, tape={M.TAPE_SIZE[SLUG]}", flush=True)

    saved = {name: set(getattr(M, name)) for name in SETS}
    results: dict[str, tuple[int, int, str]] = {}
    for name in names:
        spec = VARIANTS[name]
        for reg in SETS:
            getattr(M, reg).clear()
            getattr(M, reg).update(saved[reg])
            getattr(M, reg).discard(KEY)
        for reg in spec.get("sets", ()):
            getattr(M, reg).add(KEY)
        M.TIER_LAYOUT.pop(KEY, None)
        if "dx" in spec:
            M.TIER_LAYOUT[KEY] = {"store_offset": (spec["dx"], 0)}
        knobs = {"dda_acc_reload": spec.get("reload", True), **spec.get("prog", {})}
        src = d3.deadman3d_source(d3.GEOM128, **knobs)
        prog = assemble(src, name=SLUG)
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store="taped")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:>11}: BUILD FAILED — {type(exc).__name__}: {exc}", flush=True)
            continue
        box = f"{m.width}x{m.height}"
        res = FastLittleman("\n".join(m.rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        if res.fatal or res.passed is not True:
            print(f"  {name:>11}: {box} P={prog.P} RUN FAILED — fatal={res.fatal} "
                  f"passed={res.passed} at {res.step:,}", flush=True)
            continue
        walk = res.frame_ticks[-1] - res.frame_ticks[0]
        results[name] = (res.step, walk, box)
        vs = ""
        if "base" in results and name != "base" and results["base"][1]:
            b = results["base"][1]
            vs = f"  walk {walk - b:+,} = {100.0 * (walk - b) / b:+.3f}%"
        print(f"  {name:>11}: {box} P={prog.P} total={res.step:,} "
              f"walk={walk:,}{vs}  ({time.time() - t0:.0f}s)", flush=True)
    for reg in SETS:
        getattr(M, reg).clear()
        getattr(M, reg).update(saved[reg])
    M.TIER_LAYOUT.pop(KEY, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
