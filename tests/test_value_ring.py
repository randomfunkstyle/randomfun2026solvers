"""The value-ring machine for `reverse-a-list`, run on the reference interpreter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.value_ring import build_reverse

ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "tasks" / "solutions" / "reverse-a-list_ring.man"
PROBLEM = ROOT / "tasks" / "problems" / "reverse-a-list.json"

CASES = json.loads(PROBLEM.read_text())["publicTestData"]


def test_generator_reproduces_the_committed_grid() -> None:
    assert build_reverse() == SOLUTION.read_text().rstrip("\n").split("\n")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_public_cases(case: dict) -> None:
    """One test case is several rounds; the engine gates them on our output.

    Rounds are joined with `/` for both input and expectation, which is how the
    engine is told where each round ends -- the next round's input is withheld
    until the current round's output is complete.
    """
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    want = [int(t) for r in case["rounds"] for t in r["out"]]
    snap = Littleman().judge(
        SOLUTION.read_text(),
        input=inp,
        expected=" / ".join(" ".join(r["out"]) for r in case["rounds"]),
        max_ticks=200_000,
    )
    assert snap.fatal is None, f"fatal: {snap.fatal}"
    assert list(snap.output) == want


def test_reverses_a_single_full_length_list() -> None:
    """n = 16 is the constraint limit, and the worst case for the O(n^2) rotation."""
    vals = list(range(100, 116))
    snap = Littleman().judge(
        SOLUTION.read_text(),
        input="16 " + " ".join(map(str, vals)),
        expected=" ".join(map(str, reversed(vals))),
        max_ticks=200_000,
    )
    assert list(snap.output) == list(reversed(vals))
