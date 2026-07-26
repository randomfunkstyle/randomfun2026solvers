#!/usr/bin/env python3
"""Tests for the heading/branch rewrite rules (``rules_steer.py``).

The fast tier is pure: it hand-builds room nodes in memory (no engine, no Node) and
checks each recogniser's hits and misses, the exec-order/straightness guard
(:func:`~rules_steer._straight_east_lane` via ``route_man``), the shareable/shear guard,
the ``X`` sign-proof and its engine-confirmed heading mapping, the cost signs, and that
every ``apply`` round-trips. One ``slow`` test drives the real engine: it proves the
corridor-trim fixture lowers ticks, still passes, and is accepted by the driver.
"""

from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
FIXTURE = REPO / "tests" / "fixtures" / "steer_trim_corridor.man"
LM_MJS = REPO / "littleman" / "lm.mjs"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import rules_steer as rs  # noqa: E402
from randomfun2026solvers.manast import Ast, Atom, Corridor, RoomNode, Run, render  # noqa: E402

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)

# Checked-in fixture, pinned so the fast tier fails if it drifts from the slow proof.
EXPECTED_FIXTURE = (
    "+-+  +----------+  +-+\n"
    "|I|>>|@r...s   H|>>|O|\n"
    "+-+  +----------+  +-+\n"
)

PROBLEM = {
    "slug": "steer-fixture",
    "scoring": "footprint-tick",
    "publicTestData": [{"name": "echo", "rounds": [{"in": ["42"], "out": ["42"]}]}],
}


# ── pure room builders (no engine) ────────────────────────────────────────────
def _row_room(
    interior: str, *, x0: int = 6, y: int = 1, kind: str = "compute", h: int = 1,
    extra: list | None = None,
) -> RoomNode:
    """A one-glyph-tall compute room from an interior string: ``.`` → corridor, runs → Run."""
    children: list = []
    dots = [(x0 + i, y) for i, ch in enumerate(interior) if ch == "."]
    if dots:
        children.append(Corridor(id=-1, x=min(x for x, _ in dots), y=y, dots=dots))
    i = 0
    rid = 0
    while i < len(interior):
        if interior[i] in ". ":
            i += 1
            continue
        j = i
        while j < len(interior) and interior[j] not in ". ":
            j += 1
        children.append(Run(id=rid, x=x0 + i, y=y, glyphs=interior[i:j], heading="E"))
        rid += 1
        i = j
    if extra:
        children.extend(extra)
    return RoomNode(id=1, x=x0 - 1, y=y - 1, kind=kind, w=len(interior), h=h, children=children)


def _one_run(glyphs: str, heading: str = "E") -> RoomNode:
    """A compute room whose sole child is one run, for the branch/re-steer rules."""
    return RoomNode(
        id=1, x=0, y=0, kind="compute", w=max(len(glyphs), 1), h=1,
        children=[Run(id=0, x=1, y=1, glyphs=glyphs, heading=heading)],
    )


def _apply_first(room: RoomNode, rule) -> str:
    """Recognise `rule`'s first site on `room`, apply it to a copy, return the rendered row."""
    site = rule.recognize(None, room)[0]
    ast = Ast(rooms=[copy.deepcopy(room)])
    rule.apply(ast, site)
    return "\n".join(render(ast))


# ── rule 1: path straightening / corridor trim ────────────────────────────────
def test_trim_recognises_a_coasting_corridor() -> None:
    sites = rs.TRIM_CORRIDOR.recognize(None, _row_room("@r...s   H"))
    assert len(sites) == 1
    assert sites[0].env["segment"] == (8, 10, 1) and sites[0].env["length"] == 3


def test_trim_cost_delta_signs() -> None:
    site = rs.TRIM_CORRIDOR.recognize(None, _row_room("@r...s   H"))[0]
    cost = rs.TRIM_CORRIDOR.cost_delta(site)
    assert cost.d_ticks_per_value < 0  # the man coasts three fewer cells
    assert cost.d_cells < 0  # three nop cells vanish


def test_trim_apply_pulls_the_block_west() -> None:
    out = _apply_first(_row_room("@r...s   H"), rs.TRIM_CORRIDOR)
    assert "|@rs   H   |" in out  # dots gone, `s`/`H` slid west, walls unmoved
    assert "..." not in out


def test_trim_misses_without_a_west_anchor() -> None:
    # floor (not an executed glyph) west of the corridor: not the man's bounded lane.
    assert rs.TRIM_CORRIDOR.recognize(None, _row_room("@ ...s   H")) == []


def test_trim_misses_when_a_gadget_straddles_the_row() -> None:
    # a two-row gadget with a cell on the corridor's row east of it would shear.
    gad = Atom(id=9, x=12, y=1, rows=[">d", "^<"])
    assert rs.TRIM_CORRIDOR.recognize(None, _row_room("@r...s    ", h=2, extra=[gad])) == []


def test_trim_misses_on_a_non_compute_room() -> None:
    assert rs.TRIM_CORRIDOR.recognize(None, _row_room("@r...s   H", kind="input")) == []


def test_exec_order_guard_rejects_an_op_on_the_lane() -> None:
    # route_man must detour off-row when an executing cell sits between the anchors, so
    # `_straight_east_lane` returns None -> the man's executed-glyph order is not preserved.
    code = {(9, 1)}  # an op squarely on the straight lane
    assert rs._straight_east_lane(code, (7, 1), (11, 1), 40, 40) is None
    # with a clear lane it is a straight, turn-free walk.
    lane = rs._straight_east_lane(set(), (7, 1), (11, 1), 40, 40)
    assert lane is not None and not lane.turns and {cy for _cx, cy in lane.cells} == {1}


# ── rule 2: redundant re-steer elision ────────────────────────────────────────
def test_resteer_recognises_a_duplicated_inline_steer() -> None:
    assert rs._redundant_resteer_index("@>>r", "E") == 2
    assert rs._redundant_resteer_index("@vvr", "S") == 2


def test_resteer_misses_when_not_duplicated_or_off_axis() -> None:
    assert rs._redundant_resteer_index("@><r", "E") is None  # opposite, does real work
    assert rs._redundant_resteer_index("@vr", "S") is None  # single steer
    assert rs._redundant_resteer_index("@vvr", "E") is None  # `v` leaves an eastbound run


def test_resteer_apply_blanks_the_redundant_one() -> None:
    assert "|@>.r|" in _apply_first(_one_run("@>>r"), rs.COALESCE_RESTEER)


def test_resteer_cost_is_one_fewer_glyph_no_ticks() -> None:
    site = rs.COALESCE_RESTEER.recognize(None, _one_run("@>>r"))[0]
    cost = rs.COALESCE_RESTEER.cost_delta(site)
    assert cost.d_cells == -1 and cost.d_ticks_per_value == 0.0


# ── rule 3: provable X sign-branch simplification ─────────────────────────────
def test_const_a_is_provable_from_literals_and_negation() -> None:
    assert rs.const_a_terminal_x("@5X") == 5
    assert rs.const_a_terminal_x("@5NX") == -5  # N negates a proven literal
    assert rs.const_a_terminal_x("@0X") == 0
    assert rs.const_a_terminal_x("@`12`X") == 12  # backtick literal
    assert rs.const_a_terminal_x("@5MbX") == 5  # M/b leave A alone


def test_const_a_is_unprovable_for_unknown_writers_or_no_x() -> None:
    assert rs.const_a_terminal_x("@rX") is None  # receive: A unknown -> refuse
    assert rs.const_a_terminal_x("@5WX") is None  # W swaps in an unknown B
    assert rs.const_a_terminal_x("@5+X") is None  # arithmetic: unknown
    assert rs.const_a_terminal_x("@5H") is None  # not a terminal X


def test_steer_after_matches_the_engine_confirmed_turns() -> None:
    # verified glyph-by-glyph on lm.mjs: east + A>0 -> south (v), east + A<0 -> north (^).
    assert rs.steer_after((1, 0), 1) == "v"
    assert rs.steer_after((1, 0), -1) == "^"
    assert rs.steer_after((1, 0), 0) == "."  # A==0 is a heading-free nop
    assert rs.steer_after((0, 1), 1) == "<"  # south + A>0 -> west


def test_branch_recognises_and_folds_a_constant_x() -> None:
    for glyphs, want in (("@1X", "v"), ("@1NX", "^"), ("@0X", ".")):
        sites = rs.BRANCH_CONST.recognize(None, _one_run(glyphs))
        assert len(sites) == 1
        site = sites[0]
        assert site.env["replacement"] == want and rs.BRANCH_CONST.preconditions(site)
        folded = glyphs[:-1] + want
        assert f"|{folded}|" in _apply_first(_one_run(glyphs), rs.BRANCH_CONST)


def test_branch_misses_when_sign_is_not_provable() -> None:
    assert rs.BRANCH_CONST.recognize(None, _one_run("@rX")) == []  # A from a pipe
    assert rs.BRANCH_CONST.recognize(None, _one_run("@5H")) == []  # no terminal X


def test_fixture_file_matches_expected() -> None:
    # Fast guard: if the checked-in fixture drifts, the slow proof below is stale.
    assert FIXTURE.read_text(encoding="utf-8") == EXPECTED_FIXTURE


# ── slow: the real engine proves the corridor-trim win ────────────────────────
@pytest.mark.slow  # runs the fixture through the engine before/after the rewrite
@node_required
def test_fixture_trim_lowers_ticks_and_still_passes() -> None:
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manparse import parse_program
    from randomfun2026solvers.manrewrite import rule_pass

    grid = FIXTURE.read_text(encoding="utf-8")
    cands = rule_pass("steer")(parse_program(grid))
    assert [c.label for c in cands] == ["steer.trim_corridor"]

    base = optimize.verify(grid, PROBLEM)
    opt = optimize.verify(cands[0].grid, PROBLEM)
    assert base.passed and opt.passed
    assert opt.avg_ticks is not None and base.avg_ticks is not None
    assert opt.avg_ticks < base.avg_ticks  # fewer ticks: the man coasts less

    base_score = optimize.score_grid(grid, PROBLEM, result=base)
    opt_score = optimize.score_grid(cands[0].grid, PROBLEM, result=opt)
    assert opt_score is not None and base_score is not None and opt_score < base_score

    res = optimize.optimize(grid, PROBLEM, passes=[rule_pass("steer")], max_sweeps=1)
    assert res.improved and res.passed
