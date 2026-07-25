"""Tests for the submission tool's pure parts — no network.

What is worth pinning here is the *archive* contract, because it is the thing
protecting work from being lost: a filename derived from the server-verified score,
zero-padded so a listing sorts best-first, and no path by which a worse result
overwrites a better one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import submit  # noqa: E402


def test_problem_id_is_the_uuid_not_the_slug() -> None:
    """Submitting takes the id; every other endpoint takes the slug."""
    pid = submit.problem_id("brackets")
    assert len(pid) == 36 and pid.count("-") == 4


def test_practice_problems_are_refused_before_any_call() -> None:
    """403 forbidden is the server's answer; there is no reason to ask."""
    with pytest.raises(submit.SubmitError, match="practice"):
        submit.problem_id("palette")


def test_unknown_slug_lists_what_exists() -> None:
    with pytest.raises(submit.SubmitError, match="no such problem"):
        submit.problem_id("not-a-problem")


def test_score_tags_sort_best_first_as_text() -> None:
    """`ls` has to put the best submission first, so the padding must be fixed-width."""
    scores = [7_760_316_749, 535_177_564, 1_023_149_581]
    by_tag = [s for s in sorted(scores, key=submit._score_tag)]
    assert by_tag == sorted(scores), "lexicographic order must match numeric order"
    assert len({len(submit._score_tag(s)) for s in scores}) == 1, "fixed width"
    assert submit._score_tag(None) == "unscored"


def test_archive_names_by_score_and_never_overwrites_a_better_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(submit, "ARCHIVE", tmp_path)
    good = submit.Verdict(
        id="a", status="done", score=500, area2=100, avg_ticks=5, cases_passed=9, cases_total=9
    )
    worse = submit.Verdict(
        id="b", status="done", score=900, area2=100, avg_ticks=9, cases_passed=9, cases_total=9
    )

    submit.archive("brackets", "+-+\n|@|\n+-+\n", good, note="first")
    submit.archive("brackets", "+--+\n|@ |\n+--+\n", worse, note="second")

    mans = sorted(p.name for p in (tmp_path / "brackets").glob("*.man"))
    assert len(mans) == 2, "a second submission must not clobber the first"
    assert mans[0].startswith(submit._score_tag(500))  # best sorts first
    assert submit.best_archived("brackets")[0] == 500


def test_descr_records_provenance_and_the_free_form_note(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(submit, "ARCHIVE", tmp_path)
    v = submit.Verdict(
        id="sub-1", status="done", score=42, area2=9, avg_ticks=None, cases_passed=3, cases_total=3
    )
    submit.archive(
        "brackets", "+-+\n|@|\n+-+\n", v, note="why this one", extra={"commit": "deadbee"}
    )
    text = (tmp_path / "brackets" / f"{submit._score_tag(42)}_brackets.descr").read_text()
    assert "sub-1" in text and "why this one" in text and "deadbee" in text
    assert "footprint-only" in text  # avgTicks is null on footprint problems


def test_a_verdict_without_a_score_is_not_accepted() -> None:
    """The API leaves score null until *every* case passes, so that is the only test."""
    partial = submit.Verdict(id="x", status="done", cases_passed=19, cases_total=20)
    assert not partial.accepted
    assert "no score" in partial.summary()
    assert submit.Verdict(id="y", status="failed").accepted is False


def test_load_error_is_reported_instead_of_case_counts() -> None:
    v = submit.Verdict(id="z", status="done", load_error="unmatched `")
    assert "load error" in v.summary()
