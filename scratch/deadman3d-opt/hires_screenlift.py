#!/usr/bin/env python3
"""Where the packed wall's four panels sit, and what moving them costs.

    python scratch/deadman3d-opt/hires_screenlift.py geom [variant ...]
    python scratch/deadman3d-opt/hires_screenlift.py run  [rounds] [variant ...]

``geom`` builds the machine and prints the box plus the y-extent of every
``stream:`` region, which is the measurement the screens' row is read off.
``run`` gates each variant over the tour and reports ``res.frame_ticks[-1]``.
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

#: name -> the registry overrides to apply on top of HEAD.
VARIANTS: dict[str, dict] = {
    # HEAD before this branch: the cluster lifted 21, the north row where it
    # has always been.  Screens at y286.
    "base": {"lift": 21, "up": 0},
    # the north row rises with the lift, one row of screen per row of block
    "up1": {"lift": 22, "up": 1},
    "up2": {"lift": 23, "up": 2},
    # the previously reported hazard: builds, binds, paints the wrong picture
    "up3": {"lift": 24, "up": 3},
    "up4": {"lift": 25, "up": 4},
    # the lift alone, past its own ceiling, for the collision message
    "lift22": {"lift": 22, "up": 0},
}


def apply(name: str, M) -> None:
    v = VARIANTS[name]
    M.DOOM_CLUSTER_LIFT[KEY] = v["lift"]
    M.DOOM_PACK_NORTH_UP[KEY] = v["up"]


def regions(m) -> None:
    rs = getattr(m, "regions", None) or {}
    rows = []
    for name, (x, y, w, h) in sorted(rs.items(), key=lambda kv: kv[1][1]):
        if not name.startswith("stream:"):
            continue
        rows.append((y, y + h - 1, x, x + w - 1, name))
    prev = None
    for y0, y1, x0, x1, name in rows:
        gap = "" if prev is None else f"   (+{y0 - prev - 1} free)"
        print(f"    y{y0:>4}..{y1:<4} x{x0:>4}..{x1:<4} {y1 - y0 + 1:>4} rows  "
              f"{name}{gap}", flush=True)
        prev = max(prev or 0, y1)


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    mode = argv[0] if argv else "geom"
    argv = argv[1:]

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)

    if mode == "geom":
        for name in argv or list(VARIANTS):
            apply(name, M)
            try:
                m = M.build_for(SLUG, program=prog, store="taped")
            except Exception as exc:  # noqa: BLE001
                print(f"  {name}: BUILD FAILED — {type(exc).__name__}: {exc}",
                      flush=True)
                continue
            print(f"  {name}: {m.width}x{m.height}", flush=True)
            regions(m)
        return 0

    from randomfun2026solvers.fast_littleman import FastLittleman

    n = int(argv[0]) if argv and argv[0].isdigit() else 3
    if argv and argv[0].isdigit():
        argv = argv[1:]
    cmds = list(hires.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    print(f"tour {len(rounds)} rounds, tape={M.TAPE_SIZE[SLUG]}, P={prog.P}",
          flush=True)

    results: dict[str, tuple[int, str]] = {}
    for name in argv or list(VARIANTS):
        apply(name, M)
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store="taped")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:>10}: BUILD FAILED — {type(exc).__name__}: {exc}",
                  flush=True)
            continue
        box = f"{m.width}x{m.height}"
        res = FastLittleman("\n".join(m.rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        if res.fatal is not None or res.passed is not True:
            print(f"  {name:>10}: {box} RUN FAILED — fatal={res.fatal} "
                  f"passed={res.passed} at {res.step:,}", flush=True)
            continue
        ticks = res.frame_ticks[-1]
        results[name] = (ticks, box)
        vs = ""
        if "base" in results and name != "base":
            b = results["base"][0]
            vs = f"  {ticks - b:+,} = {100.0 * (ticks - b) / b:+.3f}%"
        print(f"  {name:>10}: {box} ticks={ticks:,}{vs}  ({time.time() - t0:.0f}s)",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
