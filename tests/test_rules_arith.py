"""Tests for the arithmetic/hands rewrite rules (``rules_arith.py``).

The fast tier is pure: it builds compute-room nodes in memory and checks each
recogniser's hits and — the load-bearing part — its **misses**, proving the
no-intervening-read guard (``M`` whose ``B`` is read is *not* elided) and the
unknown-operand refusals (a fold/strength with an unproven ``B`` does not fire),
plus the cost-delta signs and the preconditions. One ``slow`` test drives the real
engine: a redundant ``NN`` on the man's live path is removed, lowering ticks while
holding footprint, and ``optimize.optimize`` accepts it with ``passed=True``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
FIXTURE = REPO / "tests" / "fixtures" / "arith_identity_nn.man"
LM_MJS = REPO / "littleman" / "lm.mjs"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manast import RoomNode, Run  # noqa: E402
from randomfun2026solvers.manrules import MatchSite  # noqa: E402
from randomfun2026solvers.rules_arith import (  # noqa: E402
    CONST_FOLD,
    IDENTITY_PAIR,
    REDUNDANT_M,
    STRENGTH,
    m_is_dead,
    values_before,
)

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)

# The checked-in fixture, pinned so the fast tier fails if it drifts (AGENTS.md).
EXPECTED_FIXTURE = (
    "+-+  +--------+  +-+\n"
    "|I|>>|@rNNsH  |>>|O|\n"
    "+-+  |        |  +-+\n"
    "     +--------+\n"
)

# The echo problem the fixture solves: one value in, the same value out.
ECHO_PROBLEM = {
    "slug": "arith-echo",
    "scoring": "footprint-tick",
    "publicTestData": [
        {"name": "a", "rounds": [{"in": ["42"], "out": ["42"]}]},
        {"name": "b", "rounds": [{"in": ["7"], "out": ["7"]}]},
    ],
}


def _room(glyphs: str, *, kind: str = "compute") -> RoomNode:
    """A room whose single child is one east-heading run of `glyphs`."""
    run = Run(id=0, x=6, y=1, glyphs=glyphs, heading="E")
    return RoomNode(id=1, x=5, y=0, kind=kind, w=len(glyphs) + 1, h=1, children=[run])


# ── straight-line value propagation (strength + fold substrate) ───────────────
def test_values_before_tracks_digits_M_and_ops() -> None:
    # 0 -> A=0 ; M -> B=0 ; 5 -> A=5 ; +(=A+B) -> A=5, B still 0
    assert values_before("0M5+", 4) == (5, 0)
    assert values_before("0M5+", 2) == (0, 0)  # just after the M: A=0, B=0


def test_values_before_makes_unknowns_on_untracked_writes() -> None:
    # a backtick literal is not decoded here -> A unknown
    assert values_before("`12`", 4)[0] is None
    # a pipe receive writes A from outside -> A unknown, B untouched
    assert values_before("1MrN", 4) == (None, 1)
    # the spawn marker is a full barrier
    assert values_before("@5", 2)[0] == 5  # digit after @ still loads A


# ── rule 1: involution-pair elision (NN / WW) ─────────────────────────────────
def test_identity_recognises_nn_and_ww() -> None:
    assert len(IDENTITY_PAIR.recognize(None, _room("@rNNsH"))) == 1
    assert len(IDENTITY_PAIR.recognize(None, _room("@rWWsH"))) == 1


def test_identity_recognises_nonoverlapping_pairs() -> None:
    # "NNNN" is two independent no-op pairs; "NNN" (net one N) is a single pair.
    assert len(IDENTITY_PAIR.recognize(None, _room("@NNNNH"))) == 2
    assert len(IDENTITY_PAIR.recognize(None, _room("@NNNH"))) == 1


def test_identity_misses_without_terminal_H() -> None:
    # reflow guard: a run that does not end in H could disconnect a downstream node.
    assert IDENTITY_PAIR.recognize(None, _room("@rNNs")) == []


def test_identity_misses_outside_compute_rooms() -> None:
    assert IDENTITY_PAIR.recognize(None, _room("@rNNsH", kind="input")) == []
    assert IDENTITY_PAIR.recognize(None, _room("@rNNsH", kind="output")) == []


# ── rule 2: strength / identity reduction ─────────────────────────────────────
def test_strength_reduces_plus_zero() -> None:
    sites = STRENGTH.recognize(None, _room("@0M5+sH"))  # B=0, A+0 == A
    assert len(sites) == 1
    assert sites[0].env["glyph"] == "+" and sites[0].env["b_val"] == 0


def test_strength_reduces_times_one_and_and_negone() -> None:
    assert len(STRENGTH.recognize(None, _room("@1M5*sH"))) == 1  # A*1 == A
    # A & -1 == A ; get B=-1 via a literal 1 negated into B.
    assert len(STRENGTH.recognize(None, _room("@1NM5&sH"))) == 1


def test_strength_misses_on_unknown_B() -> None:
    # B comes from a pipe receive: provenance unknown -> refuse (never guess).
    assert STRENGTH.recognize(None, _room("@r5+sH")) == []


def test_strength_misses_when_constant_is_not_the_identity() -> None:
    # B==0 is the identity for +, not for & (which needs -1): A&0 == 0, not A.
    assert STRENGTH.recognize(None, _room("@0M5&sH")) == []
    # B==1 is the identity for *, not for + : A+1 != A.
    assert STRENGTH.recognize(None, _room("@1M5+sH")) == []


def test_strength_never_touches_division() -> None:
    # `/` also writes B (remainder), so it is never a pure no-op even at B==1.
    assert STRENGTH.recognize(None, _room("@1M5/sH")) == []


# ── rule 3: constant fold ─────────────────────────────────────────────────────
def test_const_fold_folds_to_single_digit() -> None:
    sites = CONST_FOLD.recognize(None, _room("@2M3+sH"))  # 3 + 2 == 5
    assert len(sites) == 1
    site = sites[0]
    assert site.env["result"] == 5 and site.env["repl"] == "5"
    assert site.env["length"] == 2  # replaces the two glyphs "3+"


def test_const_fold_refuses_multi_digit_result() -> None:
    # 9 + 8 == 17 is not a single digit; a backtick literal would *grow* the run.
    assert CONST_FOLD.recognize(None, _room("@8M9+sH")) == []


def test_const_fold_refuses_unknown_operand() -> None:
    # B unknown (from a receive) -> refuse.
    assert CONST_FOLD.recognize(None, _room("@r9+sH")) == []


# ── rule 4: redundant M — the no-intervening-read guard ───────────────────────
def test_redundant_m_dead_when_B_overwritten_before_read() -> None:
    # the second M overwrites B before anything reads it -> the first M is dead.
    assert m_is_dead("MMsH", 0) is True
    # M then the run ends (send + halt never read B) -> dead.
    assert m_is_dead("MsH", 0) is True


def test_redundant_m_live_when_B_is_read() -> None:
    # `+` reads B before any rewrite -> the M is LIVE and must not be elided.
    assert m_is_dead("M+sH", 0) is False
    # `W` swaps, reading B -> live.
    assert m_is_dead("MWsH", 0) is False
    # `/` reads B (divisor) -> live.
    assert m_is_dead("M/sH", 0) is False


def test_redundant_m_recogniser_hits_and_misses() -> None:
    assert len(REDUNDANT_M.recognize(None, _room("@rMsH"))) == 1  # dead
    assert REDUNDANT_M.recognize(None, _room("@rM+sH")) == []  # B read by + -> live


# ── cost + preconditions ──────────────────────────────────────────────────────
def test_cost_delta_signs_are_wins() -> None:
    for rule, room in (
        (IDENTITY_PAIR, _room("@rNNsH")),
        (STRENGTH, _room("@0M5+sH")),
        (CONST_FOLD, _room("@2M3+sH")),
        (REDUNDANT_M, _room("@rMsH")),
    ):
        site = rule.recognize(None, room)[0]
        cost = rule.cost_delta(site)
        assert cost.d_cells < 0  # fewer occupied cells
        assert cost.d_ticks_per_value < 0  # fewer walked cells per pass


def test_preconditions_reprove_each_guard() -> None:
    # strength: reproves the glyph is the identity op for its stored constant.
    s = STRENGTH.recognize(None, _room("@0M5+sH"))[0]
    assert STRENGTH.preconditions(s) is True
    bad_s = MatchSite(
        rule=STRENGTH, room_id=1, cells=frozenset(), entry=s.entry, exits=(),
        env={"glyph": "&", "b_val": 0},  # A&0 != A
    )
    assert STRENGTH.preconditions(bad_s) is False

    # const fold: recomputes a op b and checks the single-digit replacement.
    f = CONST_FOLD.recognize(None, _room("@2M3+sH"))[0]
    assert CONST_FOLD.preconditions(f) is True

    # redundant M: reproves B is dead over the stored suffix via run_effect.
    m = REDUNDANT_M.recognize(None, _room("@rMsH"))[0]
    assert REDUNDANT_M.preconditions(m) is True
    live_m = MatchSite(
        rule=REDUNDANT_M, room_id=1, cells=frozenset(), entry=m.entry, exits=(),
        env={"suffix": "+sH"},  # + reads B
    )
    assert REDUNDANT_M.preconditions(live_m) is False


def test_fixture_file_matches_expected() -> None:
    # Fast guard: if the checked-in fixture drifts, the slow proof below is stale.
    assert FIXTURE.read_text(encoding="utf-8") == EXPECTED_FIXTURE


# ── slow: the real engine proves the win ──────────────────────────────────────
@pytest.mark.slow  # runs the fixture through the engine before/after the rewrite
@node_required
def test_fixture_nn_elision_lowers_ticks_and_still_passes() -> None:
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manparse import parse_program
    from randomfun2026solvers.manrewrite import rule_pass

    grid = FIXTURE.read_text(encoding="utf-8")
    prog = parse_program(grid)
    cands = rule_pass("arith")(prog)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.label == "arith.identity_pair"

    base = optimize.verify(grid, ECHO_PROBLEM)
    opt = optimize.verify(cand.grid, ECHO_PROBLEM)
    assert base.passed and opt.passed
    assert opt.avg_ticks is not None and base.avg_ticks is not None
    assert opt.avg_ticks < base.avg_ticks  # the NN-elision win: fewer ticks

    base_score = optimize.score_grid(grid, ECHO_PROBLEM, result=base)
    opt_score = optimize.score_grid(cand.grid, ECHO_PROBLEM, result=opt)
    assert base_score is not None and opt_score is not None
    assert opt_score < base_score  # footprint held, ticks dropped -> lower objective

    # And the driver end-to-end accepts it (recognise -> apply -> gate -> verify).
    res = optimize.optimize(grid, ECHO_PROBLEM, passes=[rule_pass("arith")], max_sweeps=1)
    assert res.improved and res.passed
