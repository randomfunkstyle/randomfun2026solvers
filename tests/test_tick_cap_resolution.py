"""Every verifier grades under the *problem's* cap, not a hardcoded default.

`little-little-man` declares ``tickCap: 50_000_000``. When `optimize.verify` and
`littleman-validate` defaulted to `scoring.DEFAULT_TICK_CAP` (5M) instead of
asking the problem, six of its fourteen public cases were reported as failures
and `optimize` refused the grid with "input program does not pass its own public
cases" -- on a grid that was live at rank 1.
"""

from __future__ import annotations

import inspect

import pytest

from randomfun2026solvers import optimize, scoring


def test_problem_cap_wins_over_the_default():
    assert scoring.resolve_tick_cap("little-little-man") == 50_000_000
    assert scoring.DEFAULT_TICK_CAP == 5_000_000


def test_explicit_cap_beats_the_problem():
    assert scoring.resolve_tick_cap("little-little-man", 1234) == 1234


def test_problem_without_a_cap_falls_back():
    assert scoring.resolve_tick_cap("matmul") == scoring.DEFAULT_TICK_CAP
    assert scoring.resolve_tick_cap({}) == scoring.DEFAULT_TICK_CAP
    assert scoring.resolve_tick_cap(None) == scoring.DEFAULT_TICK_CAP


def test_unknown_slug_does_not_raise():
    assert scoring.resolve_tick_cap("no-such-problem") == scoring.DEFAULT_TICK_CAP


@pytest.mark.parametrize("fn", [optimize.verify, optimize.optimize])
def test_verifiers_default_to_asking_the_problem(fn):
    """A non-None default here is the bug: it silently mis-grades raised caps."""
    assert inspect.signature(fn).parameters["tick_cap"].default is None


def test_every_problem_cap_is_honoured():
    """Whatever each problem declares is what `resolve_tick_cap` reports."""
    for path in sorted(scoring._PROBLEMS_DIR.glob("*.json")):
        if path.name == "_index.json":
            continue
        prob = scoring.load_problem(path)
        expected = prob.get("tickCap") or scoring.DEFAULT_TICK_CAP
        assert scoring.resolve_tick_cap(prob) == expected, path.name
