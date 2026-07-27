"""How much corridor could the reroute pipeline take off each solution?

Prints the pacing man's corridor share (the reroute ceiling) and what
`manreroute` predicts it can remove.  Numbers are *per grid*: a bloated grid has
more slack and is still a worse starting point, so cross-check against the live
score before acting.  See littleman/HEADROOM.md.

    uv run python scratch/corridor_sweep.py [--timeout 90] [slug ...]
"""
from __future__ import annotations

import argparse
import pathlib
import signal
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402
from randomfun2026solvers.manflow import build_flow_graph  # noqa: E402
from randomfun2026solvers.manprofile import profile_program  # noqa: E402
from randomfun2026solvers.manreroute import reroute  # noqa: E402

CAPS = {
    "subset-sum": 15_000_000, "snake": 15_000_000, "pathfinder": 15_000_000,
    "little-little-man": 50_000_000, "little-little-little-man": 15_000_000,
}


class Timeout(Exception):
    pass


def slug_for(path: pathlib.Path) -> str | None:
    stem = path.stem
    while stem:
        if (REPO / "tasks" / "problems" / f"{stem}.json").is_file():
            return stem
        if "_" not in stem:
            return None
        stem = stem.rsplit("_", 1)[0]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*", help="limit to these problems")
    ap.add_argument("--timeout", type=int, default=90, help="seconds per grid")
    args = ap.parse_args()

    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Timeout()))
    print(f"{'problem':24s} {'grid':32s} {'corr%':>6s} {'moves':>6s} "
          f"{'cells':>6s} {'manticks':>9s}")

    for path in sorted((REPO / "tasks" / "solutions").glob("*.man")):
        slug = slug_for(path)
        if slug is None or (args.slugs and slug not in args.slugs):
            continue
        signal.alarm(args.timeout)
        try:
            prog = FastLittleman(path)
            graph = build_flow_graph(prog)
            profile = profile_program(
                prog, slug, graph=graph, tick_cap=CAPS.get(slug, 5_000_000)
            )
            if profile.mismatches or not profile.cases:
                signal.alarm(0)
                print(f"{slug:24s} {path.name:32s} {'unprofilable':>6s}", flush=True)
                continue
            if not all(ok for _, _, ok in profile.cases):
                signal.alarm(0)
                print(f"{slug:24s} {path.name:32s} {'fails':>6s}", flush=True)
                continue
            lead = profile.bottleneck_men()[0]
            share = (
                100 * profile.men[lead].corridor / profile.wall_ticks
                if profile.wall_ticks else 0.0
            )
            result = reroute(prog, profile, graph=graph)
            signal.alarm(0)
            print(f"{slug:24s} {path.name:32s} {share:6.1f} {len(result.moves):6d} "
                  f"{result.saved_cells:6d} {result.saved_ticks:9d}", flush=True)
        except Timeout:
            signal.alarm(0)
            print(f"{slug:24s} {path.name:32s} {'timeout':>6s}", flush=True)
        except Exception as exc:  # noqa: BLE001 - a survey should not stop
            signal.alarm(0)
            print(f"{slug:24s} {path.name:32s} err {type(exc).__name__}", flush=True)


if __name__ == "__main__":
    main()
