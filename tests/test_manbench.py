"""Tests for the value-oracle harness (``manbench.py``).

The fast tier keeps the engine out entirely: ``optimize.optimize`` is
monkeypatched to a counter returning a canned result, so what is under test is the
pure harness — the :class:`BenchRow` delta/pass math, the win/regression
classification in :func:`format_rows`, and the ``(source, slug, passes)`` memoise
that must not re-run an optimise for a repeated grid. One ``slow`` test drives a
real archive through a real ``optimize`` to prove the slug→archive→problem wiring.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import manbench  # noqa: E402


@dataclass
class _FakeResult:
    """Stand-in for ``optimize.OptimizeResult`` (only the fields ``bench`` reads)."""

    grid: list[str]
    base_grid: list[str]
    score: float | None
    base_score: float | None
    passed: bool
    log: list[str] = field(default_factory=list)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Isolate the module-level memoise so call counts are per-test."""
    manbench._CACHE.clear()
    yield
    manbench._CACHE.clear()


def _patch_optimize(monkeypatch, result, counter):
    """Replace ``optimize.optimize`` with a counter returning `result`."""
    from randomfun2026solvers import optimize as optimize_mod

    def fake_optimize(program, problem, *, passes, **kw):
        counter["n"] += 1
        return result

    monkeypatch.setattr(optimize_mod, "optimize", fake_optimize)


def _patch_problem(monkeypatch):
    """Make ``scoring.load_problem`` cheap and offline for any slug."""
    from randomfun2026solvers import scoring

    monkeypatch.setattr(scoring, "load_problem", lambda slug: {"scoring": "footprint-tick"})


# ── delta / pass math ─────────────────────────────────────────────────────────
def test_full_mode_reports_score_delta(monkeypatch):
    """fast=False → base/opt are the judged scores; delta = opt-base (win)."""
    res = _FakeResult(grid=["ab"], base_grid=["ab"], score=80.0, base_score=100.0, passed=True)
    counter = {"n": 0}
    _patch_optimize(monkeypatch, res, counter)
    _patch_problem(monkeypatch)

    (row,) = manbench.bench([], ["plotter"], fast=False)
    assert row.base == 100.0
    assert row.opt == 80.0
    assert row.delta == -20.0  # negative = improvement
    assert row.passed is True


def test_fast_mode_uses_footprint_proxy(monkeypatch):
    """fast=True ignores the judged score and measures max(w,h)² of the grids."""
    # base grid 3x2 → max=3 → area2=9; opt grid 2x2 → max=2 → area2=4.
    res = _FakeResult(
        grid=["ab", "cd"],
        base_grid=["abc", "def"],
        score=999.0,
        base_score=1.0,
        passed=True,
    )
    counter = {"n": 0}
    _patch_optimize(monkeypatch, res, counter)
    _patch_problem(monkeypatch)

    (row,) = manbench.bench([], ["plotter"], fast=True)
    assert row.base == 9.0
    assert row.opt == 4.0
    assert row.delta == -5.0


def test_failed_grid_flagged_not_raised(monkeypatch):
    """A base that does not pass is reported passed=False, delta nan — no raise."""
    res = _FakeResult(grid=["ab"], base_grid=["ab"], score=None, base_score=None, passed=False)
    counter = {"n": 0}
    _patch_optimize(monkeypatch, res, counter)
    _patch_problem(monkeypatch)

    (row,) = manbench.bench([], ["plotter"], fast=False)
    assert row.passed is False
    assert math.isnan(row.delta)


def test_missing_slug_is_a_failure_row(monkeypatch):
    """An unresolvable slug yields a failure row, not an exception."""
    monkeypatch.setattr(manbench, "_resolve_man", lambda slug: None)
    (row,) = manbench.bench([], ["does-not-exist"])
    assert row.slug == "does-not-exist"
    assert row.path == ""
    assert row.passed is False
    assert math.isnan(row.delta)


# ── caching ──────────────────────────────────────────────────────────────────
def test_optimize_called_once_for_repeated_grid(monkeypatch):
    """A repeated slug (same source) must optimise exactly once, not per row."""
    res = _FakeResult(grid=["ab"], base_grid=["ab"], score=80.0, base_score=100.0, passed=True)
    counter = {"n": 0}
    _patch_optimize(monkeypatch, res, counter)
    _patch_problem(monkeypatch)

    rows = manbench.bench([], ["plotter", "plotter"], fast=False)
    assert len(rows) == 2
    assert rows[0].delta == rows[1].delta == -20.0
    assert counter["n"] == 1  # second row served from _CACHE


# ── classification / formatting ──────────────────────────────────────────────
def _row(slug, base, opt, passed=True):
    delta = opt - base if not (math.isnan(base) or math.isnan(opt)) else math.nan
    return manbench.BenchRow(slug, f"/x/{slug}.man", base, opt, delta, passed)


def test_format_rows_classifies_and_summarises():
    rows = [
        _row("win", 100.0, 80.0),
        _row("same", 50.0, 50.0),
        _row("reg", 40.0, 60.0),
        _row("fail", 10.0, 10.0, passed=False),
    ]
    out = manbench.format_rows(rows)
    assert "WIN" in out
    assert "REGRESSION" in out
    assert "FAIL" in out
    assert "1 win, 1 regression, 1 no-change, 1 fail" in out
    # total = -20 + 0 + 20 + 0 = 0.0
    assert "total objective delta: 0.0" in out
    # regression + failure must be flagged loudly
    assert "!!" in out


def test_format_rows_clean_portfolio_has_no_alarm():
    out = manbench.format_rows([_row("a", 100.0, 90.0), _row("b", 50.0, 50.0)])
    assert "!!" not in out
    assert "0 regression" in out
    assert "0 fail" in out


# ── real archive (slow) ──────────────────────────────────────────────────────
@pytest.mark.slow
def test_empty_passes_resolves_real_archive():
    """No passes → opt == base, delta 0, passed True, on a real archived grid.

    Proves slug→best-archive→problem resolution and the ``optimize`` wiring. Uses
    ``memory`` (a small archive that round-trips cleanly); ``plotter``/``snake``
    best archives currently trip an upstream ``manparse`` ``parse_program`` →
    ``to_grid`` round-trip bug inside ``optimize`` ("pipe ends without reaching
    another room"), which ``bench`` reports as a failure row rather than raising.
    """
    (row,) = manbench.bench([], ["memory"])
    assert row.path.endswith("_memory.man")
    assert row.passed is True
    assert row.base == row.opt
    assert row.delta == 0.0
    print("\n" + manbench.format_rows([row]))
