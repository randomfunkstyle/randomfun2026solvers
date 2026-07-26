"""Tests for the ISA effect table (``mansem.py``).

The whole semantic layer trusts these needs/writes sets, so they are pinned
against SPEC.md's glyph tables: a wrong entry here would silently license an
illegal rewrite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.mansem import (  # noqa: E402
    BPFacts,
    GlyphEffect,
    glyph_effect,
    run_effect,
)


def test_digits_and_literal_write_a_only() -> None:
    for g in "0123456789`":
        eff = glyph_effect(g)
        assert eff.needs == frozenset()
        assert eff.writes == {"A"}
        assert eff.heading == "keep"


def test_arithmetic_needs_ab_writes_a() -> None:
    for g in "+-*%&|~{}":
        eff = glyph_effect(g)
        assert eff.needs == {"A", "B"}
        assert eff.writes == {"A"}


def test_division_writes_both_hands() -> None:
    eff = glyph_effect("/")
    assert eff.needs == {"A", "B"}
    assert eff.writes == {"A", "B"}  # remainder -> B


def test_hands() -> None:
    assert glyph_effect("M").needs == {"A"}
    assert glyph_effect("M").writes == {"B"}
    assert glyph_effect("W").needs == {"A", "B"}
    assert glyph_effect("W").writes == {"A", "B"}


def test_backpack_loads() -> None:
    assert glyph_effect("b").needs == {"A"}
    assert glyph_effect("b").writes == {"BP"}
    for g in "m]":
        assert glyph_effect(g).needs == {"BP"}
        assert glyph_effect(g).writes == {"BP"}
    assert glyph_effect("q").needs == frozenset()
    assert glyph_effect("q").writes == {"BP"}


def test_branches_and_steers() -> None:
    for g in "dax":
        assert glyph_effect(g).heading == "branch"
        assert glyph_effect(g).needs == {"BP"}
    assert glyph_effect("X").heading == "branch"
    assert glyph_effect("X").needs == {"A"}
    for g in "><^vV":
        eff = glyph_effect(g)
        assert eff.heading == "steer"
        assert eff.needs == frozenset()
        assert eff.writes == frozenset()


def test_halt_split_and_nops() -> None:
    assert glyph_effect("H").heading == "halt"
    assert glyph_effect("Y").heading == "split"
    for g in ". ":
        eff = glyph_effect(g)
        assert eff.heading == "keep"
        assert eff.needs == frozenset()
        assert eff.writes == frozenset()


def test_pipe_ops() -> None:
    for g in "sS":
        assert glyph_effect(g).needs == {"A"}
        assert glyph_effect(g).writes == frozenset()
    for g in "rR":
        assert glyph_effect(g).writes == {"A"}
        assert glyph_effect(g).needs == frozenset()
    u = glyph_effect("U")
    assert u.writes == {"A"}
    assert u.turns_on_read is True
    assert u.heading == "keep"


def test_unknown_glyph_raises() -> None:
    with pytest.raises(ValueError):
        glyph_effect("Z")
    with pytest.raises(ValueError):
        glyph_effect("ab")  # not a single character


def test_run_effect_needs_before_write() -> None:
    # M needs A (unwritten) then b needs A (still the entry A) -> needs {A};
    # writes accumulate {B, BP}.
    needs, writes = run_effect("Mb")
    assert needs == {"A"}
    assert writes == {"B", "BP"}


def test_run_effect_internal_write_satisfies_later_read() -> None:
    # r writes A, so the following s reads an A the run produced: no external need.
    needs, writes = run_effect("rs")
    assert needs == frozenset()
    assert writes == {"A"}


def test_glyph_effect_rejects_bad_heading() -> None:
    with pytest.raises(ValueError):
        GlyphEffect(needs=frozenset(), writes=frozenset(), heading="wobble")


def test_bpfacts_unknown_bottom() -> None:
    f = BPFacts.unknown()
    assert f.const is None
    assert f.divisible_by == frozenset()
    assert f.source == "unknown"
