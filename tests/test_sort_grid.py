"""`sort-numbers`: the 14x14 comparator-bank ring, and the round gate it needs.

**Read this before touching the grid.**  ``scoring.score_program`` hands the
engine every round of a case concatenated into one input stream.  The contest
judge does not: the problem statement and ``littleman/GRADING.md`` both say the
input for round N+1 is withheld until all of round N's output has been
received, and ``lm.mjs judge --expected`` is the reference interpreter's
implementation of exactly that.

This machine's relay pulls from the input pipe into the ring, so it *depends* on
that gate -- fed an ungated stream it happily sorts all the rounds interleaved
and emits the right multiset in the wrong order.  Everything here therefore
measures under ``judge``, and :func:`test_ungated_feed_is_not_a_regression`
pins the disagreement so nobody mistakes it for a broken grid.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from randomfun2026solvers import sort_grid
from randomfun2026solvers.brackets_men import check_no_phantom_pipes

ROOT = Path(__file__).resolve().parents[1]
GRID = ROOT / "tasks" / "solutions" / "sort-numbers_network.man"
PROBLEM = ROOT / "tasks" / "problems" / "sort-numbers.json"

FOOTPRINT = (14, 14)
PINNED_SCORE = 291_844.0  # area2 196 x avg 1489.0 ticks, judged-gated


def _cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def _judge(case: dict) -> tuple[int, list[str]]:
    """Run one case with engine-side round gating; return (ticks, output)."""
    inp = " / ".join(" ".join(map(str, r["in"])) for r in case["rounds"])
    exp = " / ".join(" ".join(map(str, r["out"])) for r in case["rounds"])
    proc = subprocess.run(
        ["node", str(ROOT / "littleman" / "lm.mjs"), "judge", str(GRID),
         "--input", inp, "--expected", exp, "--max-ticks", "300000"],
        capture_output=True, text=True, check=True,
    )
    snap = json.loads(proc.stdout)
    return snap["step"], [str(v) for v in snap["output"]]


def test_generated_grid_matches_the_archive():
    assert GRID.read_text() == "\n".join(sort_grid.build()) + "\n"


def test_footprint():
    rows = sort_grid.build()
    assert (max(map(len, rows)), len(rows)) == FOOTPRINT


def test_no_phantom_pipes():
    """Four pipes, and no arrowhead accidentally sitting against a wall.

    Column 11 is the trap: it runs the length of the worker's east wall, so any
    arrowhead there would mint a fifth, *outgoing* pipe that loads clean, binds
    an `s`, and computes nonsense.  Only the terminal `<` is allowed to stand
    in it.
    """
    check_no_phantom_pipes(sort_grid.build(), sort_grid.boxes(), expect=4)


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_public_case_passes_under_the_round_gate(case):
    _, got = _judge(case)
    want = [str(v) for r in case["rounds"] for v in r["out"]]
    assert got == want


def test_score_is_not_worse_than_pinned():
    total = sum(_judge(c)[0] for c in _cases())
    avg = total / len(_cases())
    area2 = max(FOOTPRINT) ** 2
    assert area2 * avg <= PINNED_SCORE


def test_ungated_feed_is_not_a_regression():
    """The ungated harness disagrees, and that is the design, not a bug.

    Kept as an assertion so that if someone later makes the machine
    gate-independent this test fails loudly and the caveat can be deleted along
    with it -- rather than the caveat quietly outliving the reason for it.
    """
    from randomfun2026solvers import scoring

    with pytest.raises(scoring.ScoringError, match="does not pass this case"):
        scoring.score_program(GRID, "sort-numbers")
