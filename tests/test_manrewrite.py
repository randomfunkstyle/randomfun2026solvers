"""Tests for the matcher/applier engine (``manrewrite.py``).

The fast tier is pure: it hand-builds tiny :class:`Ast`s and stub
:class:`RewriteRule`s of each mutation kind and drives :func:`apply_rules` — the engine
core that :func:`rule_pass` wraps after parsing — so nothing here shells out to the wasm
oracle. It pins the two mutation primitives (gadget swap and cell edit/deletion, incl. a
room re-flow after a run shrinks), the ``placement=None`` content-rewrite contract, that
a rule raising in ``apply`` is skipped rather than fatal, and that an unfaithful base is
refused. One ``slow`` test reuses stream S1's fixture through the real engine, proving
the generalised applier still accepts the loop unroll (no S1 regression).
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

from randomfun2026solvers.manast import Ast, Atom, Port, RoomNode, Run, render  # noqa: E402
from randomfun2026solvers.manatom import counted_loop, unrolled  # noqa: E402
from randomfun2026solvers.manrewrite import apply_rules, swap_gadget  # noqa: E402
from randomfun2026solvers.manrules import CostDelta, MatchSite, RewriteRule  # noqa: E402

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)

_E = (1, 0)


def _sealed(*rooms: RoomNode) -> Ast:
    """An :class:`Ast` whose ``source`` is its own render, so it round-trips."""
    ast = Ast(rooms=list(rooms), refine=1)  # type: ignore[arg-type]
    ast.source = render(ast)
    return ast


def _cost(_site: MatchSite) -> CostDelta:
    return CostDelta(d_cells=0, d_ticks_per_value=0.0)


def _first_atom_site(rule: RewriteRule, room: RoomNode) -> list[MatchSite]:
    """A MatchSite over the room's first gadget :class:`Atom` (for swap stubs)."""
    for child in room.children:
        if isinstance(child, Atom):
            return [
                MatchSite(
                    rule=rule,
                    room_id=room.id,
                    cells=frozenset(child.paint()),
                    entry=child.entry or Port(0, 0, _E),
                    exits=(),
                    env={},
                )
            ]
    return []


# ── mutation kind 1: gadget swap (S1's path, via `build`) ──────────────────────
def test_gadget_swap_replaces_atom_and_grows_room() -> None:
    """A no-``apply`` rule swaps the matched loop atom for its `build` gadget."""
    loop = counted_loop("rs")  # 4 rows: >d / mr / _s / ^<
    atom = Atom(id=1, x=2, y=2, rows=list(loop.rows), entry=loop.entry, exits=loop.exits)
    room = RoomNode(id=0, x=1, y=1, kind="compute", w=4, h=4, children=[atom])
    base = _sealed(room)

    def recognize(_ast: object, r: RoomNode) -> list[MatchSite]:
        return _first_atom_site(SWAP, r)

    SWAP = RewriteRule(
        name="loop.swap_stub",
        family="loop",
        recognize=recognize,
        build=lambda _s: [unrolled(2)],  # 6 rows: two rows taller than the loop
        cost_delta=_cost,
        resizes_room=True,
    )

    cands = apply_rules(base, [SWAP])
    assert len(cands) == 1
    cand = cands[0]
    assert cand.placement is None  # a content rewrite never carries a placement
    assert cand.label == "loop.swap_stub"
    # the unrolled block's glyphs are present and the room grew two rows to hold it.
    body = "\n".join(cand.grid)
    assert "mr" in body and "ms" in body  # unrolled(2)'s two `m` decrement rows
    assert len(cand.grid) == len(base.source) + 2  # room bottom wall pushed down
    # the caller's AST is untouched: still the 4-row loop.
    assert render(base) == base.source


def test_swap_gadget_primitive_grows_only_when_taller() -> None:
    loop = counted_loop("rs")
    atom = Atom(id=1, x=2, y=2, rows=list(loop.rows))
    room = RoomNode(id=0, x=1, y=1, kind="compute", w=4, h=4, children=[atom])
    swap_gadget(room, atom, unrolled(2))
    assert atom.rows == list(unrolled(2).rows)
    assert room.h == 6  # 4 + 2 taller
    # a same-height swap leaves the room alone.
    room2 = RoomNode(id=0, x=1, y=1, kind="compute", w=4, h=4, children=[atom])
    swap_gadget(room2, atom, counted_loop("rs"))
    assert room2.h == 4


# ── mutation kind 2: cell edit / deletion (via `apply`) ────────────────────────
def _edit_room() -> tuple[Ast, RoomNode, Run]:
    run = Run(id=1, x=2, y=2, glyphs="45", heading="E")
    room = RoomNode(id=0, x=1, y=1, kind="compute", w=4, h=2, children=[run])
    return _sealed(room), room, run


def test_cell_edit_rewrites_a_run_in_place() -> None:
    """An ``apply`` rule folds a run's glyphs; grid shrinks, placement stays None."""
    base, _room, _run = _edit_room()

    def edit(ast: Ast, site: MatchSite) -> None:
        target = next(c for c in ast.rooms[0].children if isinstance(c, Run))
        target.glyphs = "9"  # constant-fold 45 -> 9 (stub)

    def recognize(_ast: object, r: RoomNode) -> list[MatchSite]:
        return [MatchSite(rule=FOLD, room_id=r.id, cells=frozenset(), entry=Port(0, 0, _E),
                          exits=(), env={})]

    FOLD = RewriteRule(
        name="const.fold_stub", family="const", recognize=recognize,
        build=lambda _s: [], cost_delta=_cost, apply=edit,
    )

    cands = apply_rules(base, [FOLD])
    assert len(cands) == 1
    cand = cands[0]
    assert cand.placement is None
    assert any("9" in row and "45" not in row for row in cand.grid)
    assert render(base) == base.source  # base untouched


def test_cell_edit_reflows_room_when_a_run_shrinks() -> None:
    """Shrinking a run and shifting the trailing node closes the gap cleanly."""
    run_a = Run(id=1, x=2, y=2, glyphs="45+", heading="E")  # 3 wide
    run_b = Run(id=2, x=5, y=2, glyphs="H", heading="E")  # immediately after
    room = RoomNode(id=0, x=1, y=1, kind="compute", w=5, h=2, children=[run_a, run_b])
    base = _sealed(room)
    assert "45+H" in "\n".join(base.source)

    def reflow(ast: Ast, site: MatchSite) -> None:
        a = next(c for c in ast.rooms[0].children if isinstance(c, Run) and c.id == 1)
        b = next(c for c in ast.rooms[0].children if isinstance(c, Run) and c.id == 2)
        a.glyphs = "9"  # 3 wide -> 1 wide
        b.translate(-2, 0)  # slide the trailing run left to close the gap

    def recognize(_ast: object, r: RoomNode) -> list[MatchSite]:
        return [MatchSite(rule=RE, room_id=r.id, cells=frozenset(), entry=Port(0, 0, _E),
                          exits=(), env={})]

    RE = RewriteRule(
        name="arith.reflow_stub", family="arith", recognize=recognize,
        build=lambda _s: [], cost_delta=_cost, apply=reflow,
    )

    cands = apply_rules(base, [RE])
    assert len(cands) == 1
    assert any("9H" in row for row in cands[0].grid)  # reflowed, no collision
    assert cands[0].placement is None


# ── robustness: a raising rule is skipped, not fatal ───────────────────────────
def test_rule_raising_in_apply_is_skipped_not_crashing() -> None:
    base, _room, _run = _edit_room()

    def boom(_ast: Ast, _site: MatchSite) -> None:
        raise RuntimeError("recogniser lied about the shape")

    def recognize(_ast: object, r: RoomNode) -> list[MatchSite]:
        return [MatchSite(rule=BAD, room_id=r.id, cells=frozenset(), entry=Port(0, 0, _E),
                          exits=(), env={})]

    BAD = RewriteRule(
        name="io.boom_stub", family="io", recognize=recognize,
        build=lambda _s: [], cost_delta=_cost, apply=boom,
    )

    # the pass survives; the raising rule simply yields no candidate.
    assert apply_rules(base, [BAD]) == []


def test_swap_that_collides_is_dropped() -> None:
    """A build path that returns the wrong count raises internally and is skipped."""
    loop = counted_loop("rs")
    atom = Atom(id=1, x=2, y=2, rows=list(loop.rows))
    room = RoomNode(id=0, x=1, y=1, kind="compute", w=4, h=4, children=[atom])
    base = _sealed(room)

    def recognize(_ast: object, r: RoomNode) -> list[MatchSite]:
        return _first_atom_site(TWO, r)

    TWO = RewriteRule(
        name="loop.two_gadgets_stub", family="loop", recognize=recognize,
        build=lambda _s: [unrolled(2), unrolled(2)],  # not one gadget -> refused
        cost_delta=_cost,
    )
    assert apply_rules(base, [TWO]) == []


def test_unfaithful_base_is_refused() -> None:
    run = Run(id=1, x=2, y=2, glyphs="45", heading="E")
    room = RoomNode(id=0, x=1, y=1, kind="compute", w=4, h=2, children=[run])
    ast = Ast(rooms=[room], refine=1)  # type: ignore[arg-type]
    ast.source = ["this does not match the render at all"]

    def recognize(_ast: object, r: RoomNode) -> list[MatchSite]:
        return [MatchSite(rule=ANY, room_id=r.id, cells=frozenset(), entry=Port(0, 0, _E),
                          exits=(), env={})]

    ANY = RewriteRule(
        name="const.any_stub", family="const", recognize=recognize,
        build=lambda _s: [], cost_delta=_cost, apply=lambda _a, _s: None,
    )
    assert apply_rules(ast, [ANY]) == []  # never rewrite an unfaithful parse


# ── slow: S1's loop unroll is still accepted end to end ────────────────────────
@pytest.mark.slow  # drives the fixture through the real engine (S1 regression guard)
@node_required
def test_s1_loop_unroll_still_accepted() -> None:
    from randomfun2026solvers import optimize
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
    res = optimize.optimize(grid, problem, passes=[rule_pass("loop")], max_sweeps=1)
    assert res.passed and res.improved  # generalised applier keeps S1's unroll win
    assert res.score is not None and res.base_score is not None and res.score < res.base_score
