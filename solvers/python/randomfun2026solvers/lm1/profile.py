#!/usr/bin/env python3
"""Attribute a ``tools/heatmap.mjs`` profile to the generator's named regions.

The heat map answers "which cells are hot" and that is not quite the question. A
generated machine has ~10k cells and no comments, so a list of coordinates says
nothing; and pooling every runner together is actively misleading, because a
*servant* blocked on its input — the adapter waiting for a request, the tape
waiting for the adapter — looks exactly like a bottleneck while being the opposite.

So this does two things the raw profile cannot:

* **splits by runner**, and identifies which one is the critical path (the CPU);
* **names the cells**, using the region map :meth:`Machine.debug_map` records at
  generation time.

Read the result as: for the critical-path man, what fraction of ticks is spent in
fetch, in the decode trie, in each opcode's lane, in a jump's discard loop, and
walking the return path. Those five numbers are what any optimisation has to move.

Usage::

    node littleman/tools/heatmap.mjs prog.man --input "..." --json p.json
    python -m randomfun2026solvers.lm1.profile p.json --slug plotter
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

__all__ = ["RegionProfile", "attribute", "critical_runner", "report"]


@dataclass
class RegionProfile:
    name: str
    samples: int
    share: float
    ticks: int
    hottest: tuple[int, int, str, int] | None = None  # x, y, glyph, samples


def _region_of(
    x: int, y: int, regions: dict[str, tuple[int, int, int, int]]
) -> str:
    """The most *specific* region containing the cell (smallest area wins).

    Regions nest — a lane sits inside the CPU, the CPU inside nothing — so the
    smallest box containing a cell is the informative label.
    """
    best, best_area = None, None
    for name, (rx, ry, w, h) in regions.items():
        if rx <= x < rx + w and ry <= y < ry + h:
            area = w * h
            if best_area is None or area < best_area:
                best, best_area = name, area
    return best or "unattributed"


def critical_runner(prof: dict) -> dict:
    """The runner whose time actually costs: the least-stalled one.

    Servants spend most of their life blocked on an input pipe, so the man doing
    the real work is the one with the lowest stall fraction. On every machine this
    generator emits that is the CPU.
    """
    runners = prof["runners"]
    return min(runners, key=lambda r: r["stalled"] / max(1, r["samples"]))


def attribute(
    prof: dict, regions: dict[str, tuple[int, int, int, int]], *, runner: dict | None = None
) -> list[RegionProfile]:
    """Group a profile's cells by region, hottest first."""
    runner = runner if runner is not None else critical_runner(prof)
    cells = runner["cells"]
    total = sum(c["n"] for c in cells) or 1
    ticks = prof["ticks"]

    grouped: dict[str, list[dict]] = defaultdict(list)
    for c in cells:
        grouped[_region_of(c["x"], c["y"], regions)].append(c)

    out = []
    for name, group in grouped.items():
        n = sum(c["n"] for c in group)
        hot = max(group, key=lambda c: c["n"])
        out.append(
            RegionProfile(
                name=name,
                samples=n,
                share=n / total,
                ticks=round(n / total * ticks),
                hottest=(hot["x"], hot["y"], hot["ch"], hot["n"]),
            )
        )
    return sorted(out, key=lambda r: -r.samples)


def report(prof: dict, regions: dict[str, tuple[int, int, int, int]], *, top: int = 20) -> str:
    lines: list[str] = []
    crit = critical_runner(prof)
    stall = crit["stalled"] / max(1, crit["samples"])
    lines.append(
        f"{prof['file']}  {prof['w']}x{prof['h']}  {prof['ticks']:,} ticks "
        f"({prof['samples']:,} samples x{prof['stride']})"
    )
    lines.append(
        f"critical path = runner {crit['id']}, {stall * 100:.0f}% stalled "
        f"(the rest are servants; a servant blocked on input is idle, not a bottleneck)"
    )
    lines.append("")
    lines.append(f"  {'region':26s} {'share':>7s} {'ticks':>10s}  hottest cell")
    rows = attribute(prof, regions, runner=crit)
    for r in rows[:top]:
        hx, hy, hch, hn = r.hottest or (0, 0, "", 0)
        lines.append(
            f"  {r.name:26s} {r.share * 100:6.2f}% {r.ticks:10,d}  "
            f"({hx},{hy}) {hch!r} {hn / max(1, r.samples) * 100:.0f}% of region"
        )
    if len(rows) > top:
        rest = sum(r.share for r in rows[top:])
        lines.append(f"  {'... ' + str(len(rows) - top) + ' more':26s} {rest * 100:6.2f}%")

    # Roll the CPU's own regions up into the five things worth comparing.
    buckets = {"fetch": 0.0, "trie": 0.0, "lanes": 0.0, "slabs": 0.0, "return": 0.0}
    for r in rows:
        if r.name.startswith("cpu:lane:"):
            buckets["lanes"] += r.share
        elif r.name.startswith("cpu:slab:"):
            buckets["slabs"] += r.share
        elif r.name.startswith("cpu:return"):
            buckets["return"] += r.share
        elif r.name == "cpu:trie":
            buckets["trie"] += r.share
        elif r.name == "cpu:fetch":
            buckets["fetch"] += r.share
    lines.append("")
    lines.append("  CPU rollup:")
    for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {k:10s} {v * 100:6.2f}%  {round(v * prof['ticks']):>10,d} ticks")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import machine

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile", help="JSON written by tools/heatmap.mjs --json")
    ap.add_argument("--slug", required=True, help="task whose machine produced it")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args(argv)

    prof = json.loads(Path(args.profile).read_text())
    print(report(prof, machine.build_for(args.slug).regions, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
