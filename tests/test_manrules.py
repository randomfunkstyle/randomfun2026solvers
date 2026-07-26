"""Tests for the rewrite-rule schema + registry (``manrules.py``).

The registry is the serialization point every parallel family stream writes into,
so the contract it enforces — valid families only, per-family isolation, faithful
round-trips — is pinned here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manrules import (  # noqa: E402
    CATALOG,
    FAMILIES,
    CostDelta,
    MatchSite,
    RewriteRule,
    register,
    rules_for,
)


def _make_rule(name: str, family: str) -> RewriteRule:
    def recognize(*_args: object) -> list[MatchSite]:
        return []

    def build(_site: MatchSite) -> list[object]:
        return []

    def cost_delta(_site: MatchSite) -> CostDelta:
        return CostDelta(d_cells=0, d_ticks_per_value=0.0)

    return RewriteRule(
        name=name, family=family, recognize=recognize, build=build, cost_delta=cost_delta
    )


def test_families_frozen_set() -> None:
    assert FAMILIES == {"loop", "arith", "const", "steer", "pipe", "io"}


def test_register_and_rules_for_round_trip() -> None:
    before = len(CATALOG["const"])
    rule = _make_rule("const.test_roundtrip", "const")
    assert register(rule) is rule
    got = rules_for("const")
    assert rule in got
    assert len(got) == before + 1


def test_rules_for_returns_a_fresh_list() -> None:
    got = rules_for("loop")
    got.append(_make_rule("loop.bogus", "loop"))
    # mutating the returned list must not touch the catalog
    assert not any(r.name == "loop.bogus" for r in rules_for("loop"))


def test_register_rejects_unknown_family() -> None:
    with pytest.raises(ValueError):
        register(_make_rule("nope.x", "nonsense"))


def test_rules_for_rejects_unknown_family() -> None:
    with pytest.raises(ValueError):
        rules_for("nonsense")


def test_default_preconditions_is_always_legal() -> None:
    rule = _make_rule("arith.defaults", "arith")
    assert rule.preconditions(object()) is True  # type: ignore[arg-type]
    assert rule.clobbers == frozenset()
    assert rule.resizes_room is False
    assert rule.mirrorable is False


def test_matchsite_env_defaults_empty() -> None:
    from randomfun2026solvers.manast import Port

    rule = _make_rule("io.site", "io")
    site = MatchSite(rule=rule, room_id=0, cells=frozenset(), entry=Port(0, 0, (1, 0)), exits=())
    assert site.env == {}
