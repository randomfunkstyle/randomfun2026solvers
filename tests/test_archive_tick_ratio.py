"""Our local tick average is always optimistic, on every problem we have shipped.

`scoring.score_program` averages the public cases; the judge averages all of
them and the private ones are longer. Each archived `.descr` records both
numbers for the same grid, so the claim is checkable rather than folklore — and
checking it here means the table in `littleman/GRADING.md` cannot quietly rot as
new submissions land.

This asserts the *invariant*, not the measured values: a recorded ratio is a
deliverable metric and does not belong in a test.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "solutions"

_JUDGE = re.compile(r"avgTicks ([\d,]+)")
_LOCAL = re.compile(r"avg ([\d,]+) ticks")


def _pairs() -> list[tuple[str, float, float]]:
    out = []
    for descr in sorted(ARCHIVE.glob("*/*.descr")):
        text = descr.read_text(encoding="utf-8")
        judge, local = _JUDGE.search(text), _LOCAL.search(text)
        if not (judge and local):
            continue  # footprint-scored problems carry no avgTicks
        out.append(
            (
                descr.parent.name,
                float(judge.group(1).replace(",", "")),
                float(local.group(1).replace(",", "")),
            )
        )
    return out


def test_the_archive_actually_records_both_numbers() -> None:
    pairs = _pairs()
    assert len(pairs) > 20, len(pairs)
    assert len({slug for slug, _, _ in pairs}) > 5


def test_the_judge_never_reports_fewer_ticks_than_our_public_average() -> None:
    """The public cases are a subset and the easier one, so a local average can
    only ever flatter a grid. Anything below 1.0 would mean the local harness
    and the judge disagree about what the program does."""
    for slug, judge, local in _pairs():
        assert judge >= local, (slug, judge, local)


def test_at_least_one_problem_is_punished_hard_enough_to_flip_a_decision() -> None:
    """Somewhere in the archive the gap is big enough that a locally-measured
    2x speedup would still be a leaderboard regression. That is the whole reason
    the multiplication is not optional."""
    assert max(judge / local for _, judge, local in _pairs()) > 2.0
