"""Tests for the constant-family rewrite rules (``rules_const.py``).

The fast tier is pure: it builds a room node in memory and checks the three
recognisers (dead-literal removal, single-digit backtick shrink, redundant-reload
hoist), their misses (a read value, an unknown-downstream value, a multi-digit
backtick the reversal hazard forbids re-encoding), the in-place applier's splice,
and the cost signs. One ``slow`` test drives the real engine: it proves the checked-in
dead-literal fixture verifies before and after and that the driver accepts the rewrite
for a strictly lower objective.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
FIXTURE = REPO / "tests" / "fixtures" / "const_dead_literal.man"
LM_MJS = REPO / "littleman" / "lm.mjs"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manast import Ast, RoomNode, Run  # noqa: E402
from randomfun2026solvers.rules_const import (  # noqa: E402
    DEAD_LITERAL,
    HOIST_RELOAD,
    SHRINK_BACKTICK,
    literals,
)

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)

# The checked-in fixture, pinned so the fast tier fails if it drifts (AGENTS.md).
EXPECTED_FIXTURE = (
    "+-+  +-------+  +-+\n"
    "|I|>>|@5rsH  |>>|O|\n"
    "+-+  +-------+  +-+\n"
)


def _room(glyphs: str, *, kind: str = "compute") -> RoomNode:
    """A room whose single child is a straight run of `glyphs`."""
    run = Run(id=0, x=6, y=1, glyphs=glyphs, heading="E")
    return RoomNode(id=1, x=5, y=0, kind=kind, w=len(glyphs) + 2, h=1, children=[run])


def _apply_only(rule, glyphs: str) -> str:
    """Recognise the one site in `glyphs`, apply it in place, return the new run."""
    room = _room(glyphs)
    sites = rule.recognize(None, room)
    assert len(sites) == 1
    rule.apply(Ast(rooms=[room]), sites[0])
    return room.children[0].glyphs


# ── tokeniser ─────────────────────────────────────────────────────────────────
def test_literals_splits_digits_and_backticks() -> None:
    # each bare digit is its own load; a backtick span is one token.
    toks = literals("@4`12`5")
    assert [(t[2], t[3]) for t in toks] == [(4, False), (12, True), (5, False)]


def test_literals_stops_at_unbalanced_backtick() -> None:
    # a lone trailing backtick is not trusted: the scan stops before it.
    assert [t[2] for t in literals("4`5")] == [4]


# ── const.dead_literal: hit + the misses ──────────────────────────────────────
def test_dead_literal_recognised() -> None:
    sites = DEAD_LITERAL.recognize(None, _room("@5rsH"))
    assert len(sites) == 1
    site = sites[0]
    assert site.rule is DEAD_LITERAL and site.env["value"] == 5
    assert site.env["span"] == (1, 2) and site.env["replacement"] == ""


def test_dead_literal_removed_by_applier() -> None:
    # the `5` is overwritten by `r` before any read → deletion shifts `rsH` left.
    assert _apply_only(DEAD_LITERAL, "@5rsH") == "@rsH"


def test_no_match_when_value_is_read() -> None:
    # `M` reads A before anything overwrites it → the load is live, not dead.
    assert DEAD_LITERAL.recognize(None, _room("@5MrsH")) == []


def test_no_match_when_value_may_be_read_downstream() -> None:
    # the literal is last in the run: A could be read in a later run/at a branch,
    # so deadness is unknown and the conservative recogniser refuses.
    assert DEAD_LITERAL.recognize(None, _room("@r5")) == []


def test_no_match_outside_a_compute_room() -> None:
    assert DEAD_LITERAL.recognize(None, _room("@5rsH", kind="input")) == []


# ── const.shrink_backtick: hit + the reversal-guard misses ────────────────────
def test_shrink_single_digit_backtick() -> None:
    sites = SHRINK_BACKTICK.recognize(None, _room("@`5`sH"))
    assert len(sites) == 1
    assert sites[0].env["value"] == 5 and sites[0].env["replacement"] == "5"


def test_shrink_rewrites_backtick_to_bare_digit() -> None:
    assert _apply_only(SHRINK_BACKTICK, "@`5`sH") == "@5sH"


def test_no_shrink_of_multi_digit_backtick() -> None:
    # `12` loads twelve; the bare digits `12` would load 1 then 2 → refuse (hazard #3).
    assert SHRINK_BACKTICK.recognize(None, _room("@`12`sH")) == []


def test_no_shrink_of_palindromic_multi_digit_backtick() -> None:
    # `55` is a palindrome (mirror-safe) yet loads 55, while bare `55` loads 5 —
    # the single-digit guard, not just `_mirror_safe`, is what keeps this exact.
    assert SHRINK_BACKTICK.recognize(None, _room("@`55`sH")) == []


# ── const.hoist_reload: hit + the miss ────────────────────────────────────────
def test_hoist_redundant_reload() -> None:
    # `4 M 4 +`: A still holds 4 at the second load (M writes B), so drop the second.
    sites = HOIST_RELOAD.recognize(None, _room("@4M4+H"))
    assert len(sites) == 1
    assert sites[0].env["value"] == 4 and sites[0].env["span"] == (3, 4)


def test_hoist_drops_the_second_load() -> None:
    assert _apply_only(HOIST_RELOAD, "@4M4+H") == "@4M+H"


def test_no_hoist_when_a_is_reassigned_between() -> None:
    # `4 + 4 +`: the first `+` rewrites A, so the second load is not redundant.
    assert HOIST_RELOAD.recognize(None, _room("@4+4+H")) == []


# ── cost signs ────────────────────────────────────────────────────────────────
def test_cost_delta_signs() -> None:
    for rule, glyphs in (
        (DEAD_LITERAL, "@5rsH"),
        (SHRINK_BACKTICK, "@`5`sH"),
        (HOIST_RELOAD, "@4M4+H"),
    ):
        site = rule.recognize(None, _room(glyphs))[0]
        cost = rule.cost_delta(site)
        assert cost.d_cells < 0  # fewer occupied cells
        assert cost.d_ticks_per_value < 0  # the man reaches the send sooner
        assert rule.preconditions(site) is True


def test_precondition_rejects_stripped_env() -> None:
    # a site whose recogniser flag is gone (a malformed env) must not apply.
    from randomfun2026solvers.manrules import MatchSite
    from randomfun2026solvers.rules_const import _ENTRY

    bad = MatchSite(
        rule=DEAD_LITERAL, room_id=1, cells=frozenset(), entry=_ENTRY, exits=(),
        env={"value": 5, "run_id": 0, "span": (1, 2), "replacement": ""},  # no "dead"
    )
    assert DEAD_LITERAL.preconditions(bad) is False


def test_fixture_file_matches_expected() -> None:
    # Fast guard: if the checked-in fixture drifts, the slow proof below is stale.
    assert FIXTURE.read_text(encoding="utf-8") == EXPECTED_FIXTURE


# ── slow: the real engine proves the win ─────────────────────────────────────
@pytest.mark.slow  # runs the fixture through the engine before/after the rewrite
@node_required
def test_fixture_dead_literal_lowers_score_and_still_passes() -> None:
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manparse import parse_program
    from randomfun2026solvers.manrewrite import rule_pass

    problem = {
        "slug": "const-fixture",
        "scoring": "footprint-tick",
        "publicTestData": [
            {"name": "echo7", "rounds": [{"in": ["7"], "out": ["7"]}]},
            {"name": "echo42", "rounds": [{"in": ["42"], "out": ["42"]}]},
        ],
    }
    grid = FIXTURE.read_text(encoding="utf-8")
    prog = parse_program(grid)
    cands = rule_pass("const")(prog)
    assert len(cands) == 1  # exactly the dead `5`
    cand = cands[0]

    base = optimize.verify(grid, problem)
    opt = optimize.verify(cand.grid, problem)
    assert base.passed and opt.passed
    assert opt.avg_ticks is not None and base.avg_ticks is not None
    assert opt.avg_ticks < base.avg_ticks  # the send lands a tick sooner

    base_score = optimize.score_grid(grid, problem, result=base)
    opt_score = optimize.score_grid(cand.grid, problem, result=opt)
    assert opt_score is not None and base_score is not None and opt_score < base_score

    # And the driver end-to-end accepts it (recognise→apply→gate→verify→score).
    res = optimize.optimize(grid, problem, passes=[rule_pass("const")], max_sweeps=1)
    assert res.improved and res.passed
    assert res.score is not None and res.score < res.base_score
