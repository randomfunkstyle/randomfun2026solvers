#!/usr/bin/env python3
"""What the *grader* spends, which is not what the score counts.

A program's score is ``max(w, h)^2 × avgTicks``.  The judge's cost is wall clock,
and a simulator's wall clock goes as **runners × ticks**: every live little man is
stepped on every tick, whether or not he does anything.  Those two numbers can move
in opposite directions, and when they do the judge wins.

Measured the hard way on `little-little-man` (`littleman/LLM-DESIGN.md`).  Moving
52 of its store slots into a man-memory made it **2.36x faster in ticks** at an
identical footprint, passed all 14 public cases on both local validators, and was
rejected ``4/28`` with the runner reporting ``10 time-cap`` / ``14 time-cap``:

    machine                     live men   ticks a case   runner-ticks
    one 427-slot pipe tape             5     20,275,186         0.10bn
    + a 52-slot man-memory tier      114      8,605,207         0.98bn

Ticks fell 2.36x; simulator work rose 9.7x.  A stored word in a man-memory *is* a
little man; a word in a pipe tape is a value in a pipe and costs no runner at all.

**The observed limit.** Of the fourteen public cases the four cheapest passed and
the rest timed out.  Sorted by cost that puts the judge's ceiling between the 4th
and 5th case — **0.73bn and 0.87bn runner-ticks** — so treat anything above
~0.7bn a case as unshippable, and note the shipped machine sits at 0.10bn with
roughly 8x of headroom.

This module is the gate that was missing.  It is deliberately cheap: settle the
machine for a few thousand ticks, count the men, multiply.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

__all__ = ["JUDGE_TIMEOUT_FLOOR", "SETTLE_TICKS", "live_runners", "runner_ticks", "verdict"]

#: Ticks to run before counting men.  A man-memory is *born* — an igniter splits
#: one man per cell — so counting at tick 1 undercounts a 52-cell tier by 109.
SETTLE_TICKS = 20_000

#: Runner-ticks a case above which the judge has been observed to time out.  The
#: bound is empirical and one-sided: 0.10bn passes, 0.87bn timed out.
JUDGE_TIMEOUT_FLOOR = 700_000_000


def live_runners(man: str | Path, *, settle: int = SETTLE_TICKS) -> int:
    """Little men alive after `settle` ticks — the multiplier on every later tick."""
    root = Path(__file__).resolve().parents[3]
    out = subprocess.run(
        ["node", str(root / "littleman" / "lm.mjs"), "tick", str(man), str(settle), "--json"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    snap = json.loads(out[out.index("{") :])
    return len(snap["entities"]["runners"])


def runner_ticks(man: str | Path, ticks: float, *, settle: int = SETTLE_TICKS) -> int:
    """`runners × ticks` — the grader's cost, not the score's."""
    return int(live_runners(man, settle=settle) * ticks)


def verdict(man: str | Path, ticks: float, *, settle: int = SETTLE_TICKS) -> str:
    """A one-line report, safe to print next to a score."""
    men = live_runners(man, settle=settle)
    cost = men * ticks
    flag = "OVER the observed time-cap floor" if cost >= JUDGE_TIMEOUT_FLOOR else "ok"
    return f"{men} live men × {ticks:,.0f} ticks = {cost / 1e9:.2f}bn runner-ticks — {flag}"


def _cli() -> int:  # pragma: no cover - a convenience entry point
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("man", type=Path)
    ap.add_argument("ticks", type=float, help="average (or worst) ticks a case")
    ap.add_argument("--settle", type=int, default=SETTLE_TICKS)
    args = ap.parse_args()
    print(verdict(args.man, args.ticks, settle=args.settle))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
