"""Tests for the cross-family macro rewrites (``rules_macros.py``).

Two macros are proven here:

* **``loop.const_unroll``** — a visible literal count feeding ``counted_loop("rs")`` is
  unrolled at the largest factor ``v`` in 2..8 that divides the constant. The fast tier
  checks the constant prover, the factor choice (incl. odd composites), the recogniser
  hits/misses, and the cost sign; one ``slow`` test drives the fixture through the real
  engine and measures the base→opt score win.
* **``arith.load_op_fold``** — ``L1 M L2 OP`` with literal operands folds to ``L1 M R``.
  The fast tier checks the 64-bit arithmetic, the literal encoder, the non-widening
  guard, the applier's in-place edit (no engine), and the cost sign; one ``slow`` test
  proves the folded grid still passes on the engine.

The fast tier is pure — it hand-builds room/AST nodes so nothing shells out to Node —
because ``manparse.parse_program`` uses the engine as an oracle and is itself slow.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
LM_MJS = REPO / "littleman" / "lm.mjs"
UNROLL_FIXTURE = REPO / "tests" / "fixtures" / "macro_const_unroll.man"
FOLD_FIXTURE = REPO / "tests" / "fixtures" / "macro_load_op_fold.man"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manast import (  # noqa: E402
    Ast,
    Atom,
    RoomNode,
    Run,
    render,
)
from randomfun2026solvers.manatom import counted_loop, unrolled  # noqa: E402
from randomfun2026solvers.manrewrite import apply_rules  # noqa: E402
from randomfun2026solvers.manrules import MatchSite  # noqa: E402
from randomfun2026solvers.mansem import BPFacts  # noqa: E402

# Importing the module registers both macros into the catalog.
from randomfun2026solvers.rules_macros import (  # noqa: E402
    CONST_UNROLL,
    LOAD_OP_FOLD,
    best_unroll_factor,
    const_feeding_bp,
    encode_literal,
    eval_binop,
)

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)

# The checked-in fixtures, pinned so the fast tier fails if they drift (AGENTS.md:
# keep a fast guard on the shape the slow engine tests prove).
EXPECTED_UNROLL = (
    "+-+  +----------+  +-+\n"
    "|I|>>|@8b >d H  |>>|O|\n"
    "+-+  |    mr    |  +-+\n"
    "     |     s    |\n"
    "     |    ^<    |\n"
    "     +----------+\n"
)
EXPECTED_FOLD = (
    "+--------+  +-+\n"
    "|@3M5+sH |>>|O|\n"
    "+--------+  +-+\n"
)


def _loop_room(prefix: str, *, body: str = "rs", kind: str = "compute") -> RoomNode:
    """A compute room: a ``prefix`` run, then a ``counted_loop(body)`` atom."""
    loop = Atom(id=1, x=10, y=1, rows=list(counted_loop(body).rows))
    run = Run(id=0, x=6, y=1, glyphs=prefix, heading="E")
    return RoomNode(id=1, x=5, y=0, kind=kind, w=10, h=4, children=[run, loop])


def _run_room(glyphs: str, *, kind: str = "compute") -> RoomNode:
    """A compute room holding a single straight run of `glyphs`."""
    run = Run(id=0, x=2, y=1, glyphs=glyphs, heading="E")
    return RoomNode(id=0, x=1, y=0, kind=kind, w=len(glyphs) + 1, h=1, children=[run])


# ══ flagship: const-count → provable unroll ══════════════════════════════════
def test_const_feeding_bp_reads_a_digit_literal() -> None:
    facts = const_feeding_bp(_loop_room("@8b"))
    assert facts.source == "literal" and facts.const == 8
    assert facts.divisible_by == frozenset({2, 4, 8})


def test_const_feeding_bp_reads_a_backtick_literal() -> None:
    facts = const_feeding_bp(_loop_room("@`12`b"))
    assert facts.source == "literal" and facts.const == 12
    assert facts.divisible_by == frozenset({2, 3, 4, 6})


def test_const_feeding_bp_unknown_without_a_literal() -> None:
    # `M` before `b` is not a literal load; a `q` count is unknowable too.
    assert const_feeding_bp(_loop_room("@Mb")).source == "unknown"
    assert const_feeding_bp(_loop_room("@qb")).source == "unknown"


def test_const_feeding_bp_ambiguous_two_loads_is_unknown() -> None:
    loop = Atom(id=1, x=10, y=1, rows=list(counted_loop("rs").rows))
    a = Run(id=0, x=6, y=1, glyphs="@8b", heading="E")
    b = Run(id=2, x=6, y=3, glyphs="4b", heading="E")
    room = RoomNode(id=1, x=5, y=0, kind="compute", w=10, h=6, children=[a, loop, b])
    assert const_feeding_bp(room).source == "unknown"


def test_best_factor_picks_the_largest_divisor() -> None:
    # even powers of two take the widest unroll…
    assert best_unroll_factor(const_feeding_bp(_loop_room("@8b"))) == 8
    assert best_unroll_factor(const_feeding_bp(_loop_room("@4b"))) == 4
    # …composite even counts take their largest ≤8 divisor…
    assert best_unroll_factor(const_feeding_bp(_loop_room("@6b"))) == 6
    # …and an ODD composite still matches (the even-only loop family would miss it).
    assert best_unroll_factor(const_feeding_bp(_loop_room("@9b"))) == 3


def test_best_factor_refuses_when_nothing_divides() -> None:
    # a prime above 8 has no 2..8 divisor; 1 and unknown counts are refused too.
    assert best_unroll_factor(const_feeding_bp(_loop_room("@`11`b"))) is None
    assert best_unroll_factor(const_feeding_bp(_loop_room("@1b"))) is None
    assert best_unroll_factor(BPFacts.unknown()) is None


def test_recognises_a_visible_constant_loop_and_picks_v8() -> None:
    sites = CONST_UNROLL.recognize(None, _loop_room("@8b"))
    assert len(sites) == 1
    site = sites[0]
    assert site.rule is CONST_UNROLL and site.env["body"] == "rs"
    assert site.env["pairs"] == [8]
    assert isinstance(site.env["k"], BPFacts) and site.env["k"].const == 8


def test_recognises_an_odd_composite_at_v3() -> None:
    sites = CONST_UNROLL.recognize(None, _loop_room("@9b"))
    assert len(sites) == 1 and sites[0].env["pairs"] == [3]


def test_no_match_when_count_is_not_a_visible_constant() -> None:
    # the whole point of the macro is a *proven* constant: refuse `M`/`q` sources.
    assert CONST_UNROLL.recognize(None, _loop_room("@Mb")) == []
    assert CONST_UNROLL.recognize(None, _loop_room("@qb")) == []


def test_no_match_on_a_prime_count() -> None:
    assert CONST_UNROLL.recognize(None, _loop_room("@`11`b")) == []


def test_no_match_without_a_counted_loop_atom() -> None:
    room = RoomNode(
        id=1, x=5, y=0, kind="compute", w=6, h=1,
        children=[Run(id=0, x=6, y=1, glyphs="@8brsH", heading="E")],
    )
    assert CONST_UNROLL.recognize(None, room) == []


def test_unroll_cost_delta_signs() -> None:
    site = CONST_UNROLL.recognize(None, _loop_room("@8b"))[0]
    cost = CONST_UNROLL.cost_delta(site)
    assert cost.d_ticks_per_value < 0  # 8 → 4.5 ticks per value for v=8
    assert cost.d_cells > 0  # taller block: it costs footprint
    expected = unrolled(8).ticks_per_value - counted_loop("rs").ticks_per_value
    assert cost.d_ticks_per_value == expected


def test_unroll_precondition_reproves_divisibility() -> None:
    good = MatchSite(
        rule=CONST_UNROLL, room_id=1, cells=frozenset(),
        entry=counted_loop("rs").entry, exits=(),
        env={"k": BPFacts(const=8, divisible_by=frozenset({2, 4, 8}), source="literal"),
             "pairs": [8]},
    )
    # a factor that does not divide the (literal) count is refused…
    bad_factor = MatchSite(
        rule=CONST_UNROLL, room_id=1, cells=frozenset(),
        entry=counted_loop("rs").entry, exits=(),
        env={"k": BPFacts(const=6, divisible_by=frozenset({2, 3, 6}), source="literal"),
             "pairs": [4]},
    )
    # …and an unknown source is refused whatever the factor claims.
    unknown = MatchSite(
        rule=CONST_UNROLL, room_id=1, cells=frozenset(),
        entry=counted_loop("rs").entry, exits=(),
        env={"k": BPFacts.unknown(), "pairs": [2]},
    )
    assert CONST_UNROLL.preconditions(good) is True
    assert CONST_UNROLL.preconditions(bad_factor) is False
    assert CONST_UNROLL.preconditions(unknown) is False


# ══ macro: const-fold across a hand move ═════════════════════════════════════
def test_eval_binop_matches_spec() -> None:
    # A = A(second literal) OP B(first literal).
    assert eval_binop("+", 5, 3) == 8
    assert eval_binop("-", 5, 3) == 2
    assert eval_binop("*", 5, 3) == 15
    assert eval_binop("%", 5, 3) == 2
    assert eval_binop("%", 5, 0) == 0  # SPEC: 0 when B == 0
    assert eval_binop("&", 6, 3) == 2
    assert eval_binop("|", 6, 1) == 7
    assert eval_binop("~", 6, 3) == 5  # `~` is XOR per SPEC
    assert eval_binop("{", 2, 3) == 16  # 2 << 3
    assert eval_binop("}", 16, 2) == 4  # 16 >> 2
    assert eval_binop("/", 5, 3) is None  # `/` also writes B → not folded


def test_encode_literal() -> None:
    assert encode_literal(0) == "0"
    assert encode_literal(9) == "9"
    assert encode_literal(15) == "`15`"
    assert encode_literal(-1) is None  # negatives would widen → declined


def test_fold_recognises_a_literal_op_and_shrinks() -> None:
    sites = LOAD_OP_FOLD.recognize(None, _run_room("@3M5+sH"))
    assert len(sites) == 1
    env = sites[0].env
    assert env["old"] == "3M5+" and env["new"] == "3M8" and env["result"] == 8


def test_fold_refuses_when_result_would_widen_the_run() -> None:
    # 5 * 3 = 15 needs `` `15` `` — wider than the `3M5*` span, so no reflow, no match.
    assert LOAD_OP_FOLD.recognize(None, _run_room("3M5*")) == []


def test_fold_refuses_a_negative_result() -> None:
    # 2 - 9 = -7: a negative literal needs an extra `N` glyph → declined.
    assert LOAD_OP_FOLD.recognize(None, _run_room("9M2-")) == []


def test_fold_refuses_division() -> None:
    # `/` writes B too, breaking the preserved-B invariant → never folded.
    assert LOAD_OP_FOLD.recognize(None, _run_room("6M8/")) == []


def test_fold_needs_the_hand_move() -> None:
    # `3 5 +` with no `M` is not this macro (B is not the first literal).
    assert LOAD_OP_FOLD.recognize(None, _run_room("35+")) == []


def test_fold_cost_delta_is_a_win() -> None:
    site = LOAD_OP_FOLD.recognize(None, _run_room("@3M5+sH"))[0]
    cost = LOAD_OP_FOLD.cost_delta(site)
    assert cost.d_ticks_per_value < 0  # at least the op glyph is gone
    assert cost.d_cells < 0


def test_fold_applier_edits_the_run_in_place_no_engine() -> None:
    """The applier folds ``3M5+`` to ``3M8`` on a private copy; the base is untouched."""
    room = _run_room("@3M5+sH")
    ast = Ast(rooms=[room], refine=1)  # type: ignore[arg-type]
    ast.source = render(ast)  # seal it so it round-trips

    cands = apply_rules(ast, [LOAD_OP_FOLD])
    assert len(cands) == 1
    cand = cands[0]
    assert cand.placement is None and cand.label == "arith.load_op_fold"
    body = "\n".join(cand.grid)
    assert "@3M8sH" in body and "3M5+" not in body
    assert render(ast) == ast.source  # caller's AST unchanged


def test_fixtures_match_expected() -> None:
    # Fast guard: if a checked-in fixture drifts, the slow proofs below are stale.
    assert UNROLL_FIXTURE.read_text(encoding="utf-8") == EXPECTED_UNROLL
    assert FOLD_FIXTURE.read_text(encoding="utf-8") == EXPECTED_FOLD


# ══ slow: the real engine proves the wins ════════════════════════════════════
@pytest.mark.slow  # the headline: const-count→unroll(8) lowers ticks and score
@node_required
def test_flagship_const_unroll_wins_on_the_engine() -> None:
    import randomfun2026solvers.rules_macros  # noqa: F401 — ensure registration
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manparse import parse_program
    from randomfun2026solvers.manrewrite import rule_pass

    ins = [str(v) for v in (10, 20, 30, 40, 50, 60, 70, 80)]
    problem = {
        "slug": "macro-const-unroll",
        "scoring": "footprint-tick",
        "publicTestData": [{"name": "eight", "rounds": [{"in": ins, "out": ins}]}],
    }
    grid = UNROLL_FIXTURE.read_text(encoding="utf-8")
    prog = parse_program(grid)

    # our macro emits exactly one candidate: the largest-factor unroll (v=8).
    cands = [c for c in rule_pass("loop")(prog) if c.label == "loop.const_unroll"]
    assert len(cands) == 1
    cand = cands[0]

    base = optimize.verify(grid, problem)
    opt = optimize.verify(cand.grid, problem)
    assert base.passed and opt.passed
    assert base.avg_ticks is not None and opt.avg_ticks is not None
    assert opt.avg_ticks < base.avg_ticks  # 8 → 4.5 ticks per value moved

    base_score = optimize.score_grid(grid, problem, result=base)
    opt_score = optimize.score_grid(cand.grid, problem, result=opt)
    assert opt_score is not None and base_score is not None
    assert opt_score < base_score  # footprint stays width-bound, ticks fall

    # and the driver end-to-end accepts it (recognise→build→apply→gate→verify→score).
    res = optimize.optimize(grid, problem, passes=[rule_pass("loop")], max_sweeps=1)
    assert res.improved and res.passed
    assert res.score is not None and res.base_score is not None
    assert res.score < res.base_score


@pytest.mark.slow  # the fold: `3M5+` → `3M8` still passes, fewer ticks
@node_required
def test_load_op_fold_still_passes_on_the_engine() -> None:
    import randomfun2026solvers.rules_macros  # noqa: F401 — ensure registration
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manrewrite import rule_pass

    problem = {
        "slug": "macro-load-op-fold",
        "scoring": "footprint-tick",
        "publicTestData": [{"name": "const", "rounds": [{"in": [], "out": ["8"]}]}],
    }
    grid = FOLD_FIXTURE.read_text(encoding="utf-8")
    res = optimize.optimize(grid, problem, passes=[rule_pass("arith")], max_sweeps=2)
    assert res.passed and res.improved
    assert res.score is not None and res.base_score is not None
    assert res.score < res.base_score
