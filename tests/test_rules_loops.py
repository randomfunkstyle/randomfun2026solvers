"""Tests for the loop-family rewrite rules (``rules_loops.py``).

The fast tier is pure — it builds room nodes in memory and checks the recogniser,
the backpack-even proof (the divisibility guard, hazard #2), the cost sign, and the
precondition. Two ``slow`` tests drive the real engine: one proves the fixture
unroll lowers ticks and still passes, one is the archive smoke run.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
FIXTURE = REPO / "tests" / "fixtures" / "loop_unroll_counted.man"
LM_MJS = REPO / "littleman" / "lm.mjs"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manast import Atom, RoomNode, Run  # noqa: E402
from randomfun2026solvers.manatom import (  # noqa: E402
    counted_loop,
    counted_loop_horizontal,
    unrolled,
)
from randomfun2026solvers.manrules import MatchSite, rules_for  # noqa: E402
from randomfun2026solvers.mansem import BPFacts  # noqa: E402
from randomfun2026solvers.rules_loops import (  # noqa: E402
    MIRROR,
    UNROLL2,
    UNROLL4,
    UNROLL8,
    prove_backpack_even,
)

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)

# The checked-in fixture, pinned so the fast tier fails if it drifts (AGENTS.md:
# keep a fast guard on the shape the slow test proves on the engine).
EXPECTED_FIXTURE = (
    "+-+  +----------+  +-+\n"
    "|I|>>|@4b >d H  |>>|O|\n"
    "+-+  |    mr    |  +-+\n"
    "     |     s    |\n"
    "     |    ^<    |\n"
    "     +----------+\n"
)


def _room(prefix: str, *, body: str = "rs", kind: str = "compute") -> RoomNode:
    """A compute room: a ``prefix`` run, then a ``counted_loop(body)`` atom."""
    loop = Atom(id=1, x=10, y=1, rows=list(counted_loop(body).rows))
    run = Run(id=0, x=6, y=1, glyphs=prefix, heading="E")
    return RoomNode(id=1, x=5, y=0, kind=kind, w=10, h=4, children=[run, loop])


# ── the divisibility guard (hazard #2) ───────────────────────────────────────
def test_prove_even_literal() -> None:
    facts = prove_backpack_even(_room("@4b"))
    assert facts.source == "literal" and facts.const == 4
    assert 2 in facts.divisible_by and 4 in facts.divisible_by


def test_prove_backtick_literal() -> None:
    facts = prove_backpack_even(_room("@`12`b"))
    assert facts.source == "literal" and facts.const == 12 and 2 in facts.divisible_by


def test_odd_literal_is_a_proof_of_unsafety() -> None:
    facts = prove_backpack_even(_room("@3b"))
    assert facts.source == "literal" and facts.const == 3
    assert 2 not in facts.divisible_by  # nothing to divide by → refuse


def test_non_literal_count_is_unknown() -> None:
    # `M` before `b` is not a literal load, so the count cannot be proven.
    assert prove_backpack_even(_room("@Mb")).source == "unknown"


def test_ambiguous_two_loads_is_unknown() -> None:
    # two `…<digit>b` runs → the prover refuses rather than guess which feeds it.
    loop = Atom(id=1, x=10, y=1, rows=list(counted_loop("rs").rows))
    a = Run(id=0, x=6, y=1, glyphs="@4b", heading="E")
    b = Run(id=2, x=6, y=3, glyphs="2b", heading="E")
    room = RoomNode(id=1, x=5, y=0, kind="compute", w=10, h=6, children=[a, loop, b])
    assert prove_backpack_even(room).source == "unknown"


# ── recognise: hit and the three misses ──────────────────────────────────────
def test_recognises_a_provable_even_loop() -> None:
    sites = UNROLL2.recognize(None, _room("@4b"))
    assert len(sites) == 1
    site = sites[0]
    assert site.rule is UNROLL2 and site.env["body"] == "rs" and site.env["pairs"] == [2]
    assert isinstance(site.env["k"], BPFacts) and site.env["k"].const == 4


def test_no_match_on_a_non_loop_room() -> None:
    # a room whose only child is a straight run, no counted-loop atom.
    room = RoomNode(
        id=1, x=5, y=0, kind="compute", w=6, h=1,
        children=[Run(id=0, x=6, y=1, glyphs="@4brsH", heading="E")],
    )
    assert UNROLL2.recognize(None, room) == []


def test_no_match_on_an_odd_bp_loop() -> None:
    # the loop shape matches, but BP is an odd literal → refused (hazard #2).
    assert UNROLL2.recognize(None, _room("@3b")) == []


def test_no_match_on_an_unknown_bp_loop() -> None:
    # BP not a literal → refused; unrolling an unprovable count over-rotates.
    assert UNROLL2.recognize(None, _room("@Mb")) == []


# ── cost + precondition ──────────────────────────────────────────────────────
def test_cost_delta_signs() -> None:
    site = UNROLL2.recognize(None, _room("@4b"))[0]
    cost = UNROLL2.cost_delta(site)
    assert cost.d_ticks_per_value < 0  # 8 → 6 ticks per value: the win
    assert cost.d_cells > 0  # two rows taller: it costs footprint
    # pinned to the measured figures.
    expected = unrolled(2).ticks_per_value - counted_loop("rs").ticks_per_value
    assert cost.d_ticks_per_value == expected


def test_precondition_reproves_evenness() -> None:
    good = MatchSite(
        rule=UNROLL2, room_id=1, cells=frozenset(), entry=counted_loop("rs").entry, exits=(),
        env={"k": BPFacts(const=4, divisible_by=frozenset({2}), source="literal")},
    )
    odd = MatchSite(
        rule=UNROLL2, room_id=1, cells=frozenset(), entry=counted_loop("rs").entry, exits=(),
        env={"k": BPFacts(const=3, divisible_by=frozenset(), source="literal")},
    )
    unknown = MatchSite(
        rule=UNROLL2, room_id=1, cells=frozenset(), entry=counted_loop("rs").entry, exits=(),
        env={"k": BPFacts.unknown()},
    )
    assert UNROLL2.preconditions(good) is True
    assert UNROLL2.preconditions(odd) is False
    assert UNROLL2.preconditions(unknown) is False


# ── the wider unrolls (4 / 8): divisibility gates the factor ──────────────────
def test_unroll4_recognises_multiple_of_four() -> None:
    assert len(UNROLL4.recognize(None, _room("@4b"))) == 1
    assert len(UNROLL4.recognize(None, _room("@8b"))) == 1
    site = UNROLL4.recognize(None, _room("@8b"))[0]
    assert site.rule is UNROLL4 and site.env["pairs"] == [4]


def test_unroll4_refuses_a_non_multiple_of_four() -> None:
    # even but not divisible by four → unroll2 fires, unroll4 must not (hazard #2).
    assert UNROLL2.recognize(None, _room("@2b")) != []
    assert UNROLL4.recognize(None, _room("@2b")) == []
    assert UNROLL4.recognize(None, _room("@6b")) == []  # 6 % 4 != 0


def test_unroll8_recognises_only_multiples_of_eight() -> None:
    assert len(UNROLL8.recognize(None, _room("@8b"))) == 1
    assert UNROLL8.recognize(None, _room("@4b")) == []  # 4 % 8 != 0
    assert UNROLL8.recognize(None, _room("@Mb")) == []  # unknown BP


def test_wider_unroll_cost_signs() -> None:
    for rule, pairs in ((UNROLL4, 4), (UNROLL8, 8)):
        arg = "@8b"  # divisible by 8, hence by 4 too
        cost = rule.cost_delta(rule.recognize(None, _room(arg))[0])
        assert cost.d_ticks_per_value < 0  # 8 → faster: the win
        assert cost.d_cells > 0  # taller: it costs footprint
        expected = unrolled(pairs).ticks_per_value - counted_loop("rs").ticks_per_value
        assert cost.d_ticks_per_value == expected


def test_unroll_precondition_reproves_its_own_factor() -> None:
    # a site proven for /4 is legal for unroll4; a /2-only count is not.
    div4 = MatchSite(
        rule=UNROLL4, room_id=1, cells=frozenset(), entry=counted_loop("rs").entry, exits=(),
        env={"k": BPFacts(const=8, divisible_by=frozenset({2, 4, 8}), source="literal")},
    )
    only2 = MatchSite(
        rule=UNROLL4, room_id=1, cells=frozenset(), entry=counted_loop("rs").entry, exits=(),
        env={"k": BPFacts(const=2, divisible_by=frozenset({2}), source="literal")},
    )
    assert UNROLL4.preconditions(div4) is True
    assert UNROLL4.preconditions(only2) is False


# ── the horizontal mirror (footprint lever) ───────────────────────────────────
def test_mirror_recognises_a_tall_loop() -> None:
    # No `ast` → the height-bind gate defaults open, so the recogniser fires.
    sites = MIRROR.recognize(None, _room("@4b"))
    assert len(sites) == 1
    site = sites[0]
    assert site.rule is MIRROR and site.env["body"] == "rs"


def test_mirror_misses_a_non_loop_room() -> None:
    room = RoomNode(
        id=1, x=5, y=0, kind="compute", w=6, h=1,
        children=[Run(id=0, x=6, y=1, glyphs="@4brsH", heading="E")],
    )
    assert MIRROR.recognize(None, room) == []


def test_mirror_gated_out_when_width_binds() -> None:
    # A stub AST whose bbox is wider than tall: the tall side is not the binder, so
    # the footprint lever is not proposed (the score gate would reject it anyway).
    class _WideAst:
        bbox = (40, 6)

    assert MIRROR.recognize(_WideAst(), _room("@4b")) == []

    class _TallAst:
        bbox = (6, 40)

    assert len(MIRROR.recognize(_TallAst(), _room("@4b"))) == 1


def test_mirror_cost_is_footprint_only() -> None:
    site = MIRROR.recognize(None, _room("@4b"))[0]
    cost = MIRROR.cost_delta(site)
    assert cost.d_ticks_per_value == 0.0  # same 8 ticks/value, a pure footprint lever
    assert cost.d_cells != 0  # the reshape changes the binding dimension
    assert cost.d_cells < 0  # k+2 rows → 2 rows: shorter, the whole point
    # pinned: the reshape trades the loop's height for width, tick cost untouched.
    assert counted_loop("rs").ticks_per_value == counted_loop_horizontal("rs").ticks_per_value


def test_all_loop_rules_registered() -> None:
    names = {r.name for r in rules_for("loop")}
    assert {
        "loop.unroll2",
        "loop.unroll4",
        "loop.unroll8",
        "loop.mirror_horizontal",
    } <= names


def test_fixture_file_matches_expected() -> None:
    # Fast guard: if the checked-in fixture drifts, the slow proof below is stale.
    assert FIXTURE.read_text(encoding="utf-8") == EXPECTED_FIXTURE


# ── slow: the real engine proves the win ─────────────────────────────────────
@pytest.mark.slow  # runs the fixture through the engine before/after the rewrite
@node_required
def test_fixture_unroll_lowers_ticks_and_still_passes() -> None:
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manparse import parse_program
    from randomfun2026solvers.manrewrite import rule_pass

    problem = {
        "slug": "loop-fixture",
        "scoring": "footprint-tick",
        "publicTestData": [
            {"name": "even4", "rounds": [{"in": ["10", "20", "30", "40"],
                                          "out": ["10", "20", "30", "40"]}]},
        ],
    }
    grid = FIXTURE.read_text(encoding="utf-8")
    prog = parse_program(grid)
    cands = rule_pass("loop")(prog)
    # The catalog now holds several loop rules; select the unroll2 candidate. (The
    # count 4 is divisible by 2 and 4, so unroll2/unroll4 both fire, plus the mirror
    # emits a verify-gated candidate — hence more than one candidate here.)
    u2 = [c for c in cands if c.label == "loop.unroll2"]
    assert len(u2) == 1
    cand = u2[0]

    base = optimize.verify(grid, problem)
    opt = optimize.verify(cand.grid, problem)
    assert base.passed and opt.passed
    assert opt.avg_ticks is not None and base.avg_ticks is not None
    assert opt.avg_ticks < base.avg_ticks  # the unroll win: fewer ticks

    base_score = optimize.score_grid(grid, problem, result=base)
    opt_score = optimize.score_grid(cand.grid, problem, result=opt)
    assert opt_score is not None and base_score is not None and opt_score < base_score

    # And the driver end-to-end accepts it (recognise→build→apply→gate→verify→score).
    res = optimize.optimize(grid, problem, passes=[rule_pass("loop")], max_sweeps=1)
    assert res.improved and res.passed


@pytest.mark.slow  # the wider unroll: BP=4 is a multiple of 4, so unroll4 is legal
@node_required
def test_fixture_unroll4_lowers_ticks_and_still_passes() -> None:
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manparse import parse_program
    from randomfun2026solvers.manrewrite import rule_pass

    problem = {
        "slug": "loop-fixture",
        "scoring": "footprint-tick",
        "publicTestData": [
            {"name": "even4", "rounds": [{"in": ["10", "20", "30", "40"],
                                          "out": ["10", "20", "30", "40"]}]},
        ],
    }
    grid = FIXTURE.read_text(encoding="utf-8")
    prog = parse_program(grid)
    u4 = [c for c in rule_pass("loop")(prog) if c.label == "loop.unroll4"]
    assert len(u4) == 1
    cand = u4[0]

    base = optimize.verify(grid, problem)
    opt = optimize.verify(cand.grid, problem)
    assert base.passed and opt.passed
    assert opt.avg_ticks is not None and base.avg_ticks is not None
    # unrolled(4) moves 4 values per lap (5 ticks/value) vs the tall loop's 8.
    assert opt.avg_ticks < base.avg_ticks


@pytest.mark.slow  # the mirror is verify-safe: its raw swap never passes as wrong
@node_required
def test_mirror_swap_round_trips_but_verify_rejects_it() -> None:
    # The tall fixture is width-bound, so MIRROR's height-bind gate correctly does
    # not propose it in a normal pass. Here we bypass that gate (an ungated clone of
    # the rule) purely to exercise the *applier*: it must (a) build a grid that
    # renders without collision — proving apply round-trips — and (b) FAIL verify,
    # because the tall→horizontal port geometry differs, so the naive swap can never
    # be silently accepted as a wrong grid. This pins MIRROR's safety claim.
    from dataclasses import replace

    from randomfun2026solvers import optimize
    from randomfun2026solvers.manast import Atom as _Atom
    from randomfun2026solvers.manast import Refine, parse_ast
    from randomfun2026solvers.manparse import parse_program
    from randomfun2026solvers.manrecog import match_counted_loop
    from randomfun2026solvers.manrewrite import apply_rules
    from randomfun2026solvers.manrules import MatchSite

    def _ungated(_ast: object, room: object) -> list[MatchSite]:
        out: list[MatchSite] = []
        for child in room.children:  # type: ignore[attr-defined]
            if not isinstance(child, _Atom):
                continue
            m = match_counted_loop(child)
            if m is None:
                continue
            out.append(
                MatchSite(
                    rule=MIRROR, room_id=room.id, cells=frozenset(child.paint()),  # type: ignore[attr-defined]
                    entry=m.entry, exits=(m.exit_,), env={"body": m.body},
                )
            )
        return out

    problem = {
        "slug": "loop-fixture",
        "scoring": "footprint-tick",
        "publicTestData": [
            {"name": "even4", "rounds": [{"in": ["10", "20", "30", "40"],
                                          "out": ["10", "20", "30", "40"]}]},
        ],
    }
    grid = FIXTURE.read_text(encoding="utf-8")
    prog = parse_program(grid)
    ast = parse_ast(prog, refine=Refine.BLOCKS)
    cands = apply_rules(ast, [replace(MIRROR, recognize=_ungated)])
    assert len(cands) == 1  # applier produced a renderable candidate: apply round-trips
    assert not optimize.verify(cands[0].grid, problem).passed  # …but it is not equivalent


@pytest.mark.slow  # archive smoke run: full optimize on real solutions
@node_required
def test_bench_smoke_on_archives() -> None:
    from randomfun2026solvers.manbench import bench
    from randomfun2026solvers.manrewrite import rule_pass

    rows = bench([rule_pass("loop")], ["memory", "brackets"])
    assert {r.slug for r in rows} == {"memory", "brackets"}
    # A clean no-match is acceptable: the archives need not contain a bare loop.
    for r in rows:
        assert r.passed  # optimize never returns a worse/failing grid
