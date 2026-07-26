"""Tests for the IO / display / split rewrite rules (``rules_io.py``).

The fast tier is pure — it builds display :class:`RoomNode`s and :class:`PipeNode`s
in memory (no engine) and checks the attach analyser, every display hazard, the two
recognisers (the registered dead-panel rule and the exported height tightener), the
cost signs, and that an apply re-renders without collision. The one ``slow`` test
drives the real engine: it proves the ``io`` family cleanly no-matches the ``plotter``
archive (a live, driven panel) and leaves that passing grid untouched — the honest
outcome, since a valid display is already at its stated resolution and the useful
shrink of a live panel is deferred (see ``rules_io`` module docstring).
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
LM_MJS = REPO / "littleman" / "lm.mjs"
PLOTTER = REPO / "tasks" / "solutions" / "plotter_cpu.man"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manast import Ast, PipeNode, RoomNode, render, round_trip_ok  # noqa: E402
from randomfun2026solvers.manrewrite import apply_rules  # noqa: E402
from randomfun2026solvers.manrules import rules_for  # noqa: E402
from randomfun2026solvers.rules_io import (  # noqa: E402
    IO_DISPLAY_DEAD_PANEL,
    display_facts,
    display_tighten_rule,
    is_oversized,
    shrink_display,
)

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists() or not PLOTTER.exists(),
    reason="needs Node + littleman/lm.mjs + the plotter archive",
)


# ── fixture builders (pure, no engine) ────────────────────────────────────────
def _display(w: int, h: int, *, x: int = 4, y: int = 4) -> RoomNode:
    """A display panel with a ``w × h`` interior at ``(x, y)``."""
    return RoomNode(id=0, x=x, y=y, kind="display", w=w, h=h)


def _pipe(pid: int, end: tuple[int, int], approach: tuple[int, int]) -> PipeNode:
    """A 3-cell pipe whose last cell is `end`, arriving along `approach` (dx, dy)."""
    ax, ay = approach
    ex, ey = end
    path = [(ex - 2 * ax, ey - 2 * ay), (ex - ax, ey - ay), (ex, ey)]
    return PipeNode(id=pid, x=path[0][0], y=path[0][1], path=path, glyphs=list("...."[: len(path)]))


def _driven(w: int, h: int) -> tuple[Ast, RoomNode]:
    """A well-formed live panel: one pipe on each of top / left / bottom."""
    room = _display(w, h)
    x0, y0 = room.x, room.y
    y1 = y0 + h + 1  # bottom border row
    top = _pipe(0, (x0 + 1, y0 - 1), (0, 1))
    left = _pipe(1, (x0 - 1, y0 + 1), (1, 0))
    bottom = _pipe(2, (x0 + 1, y1 + 1), (0, -1))
    return Ast(rooms=[room], pipes=[top, left, bottom]), room


# ── the attach analyser + the display hazards ─────────────────────────────────
def test_display_facts_identifies_the_three_ports() -> None:
    ast, room = _driven(4, 3)
    facts = display_facts(ast, room)
    assert facts is not None
    assert set(facts.attaches) == {"top", "left", "bottom"}
    assert facts.well_formed and facts.driven and facts.n_attaches == 3
    assert facts.interior == (4, 3)


def test_facts_none_on_a_non_display_room() -> None:
    room = RoomNode(id=0, x=0, y=0, kind="compute", w=4, h=3)
    assert display_facts(Ast(rooms=[room]), room) is None


def test_facts_flags_a_right_side_attach() -> None:
    room = _display(3, 3)
    x1 = room.x + 3 + 1
    ast = Ast(rooms=[room], pipes=[_pipe(0, (x1 + 1, room.y + 1), (-1, 0))])
    facts = display_facts(ast, room)
    assert facts is not None and not facts.well_formed
    assert any(h.startswith("right-attach") for h in facts.hazards)


def test_facts_flags_a_corner_attach() -> None:
    room = _display(3, 3)
    # top row at the corner column x0 -> a load error, not a valid top attach.
    ast = Ast(rooms=[room], pipes=[_pipe(0, (room.x, room.y - 1), (0, 1))])
    facts = display_facts(ast, room)
    assert facts is not None and not facts.well_formed
    assert any(h.startswith("corner-attach") for h in facts.hazards)


def test_facts_flags_two_pipes_on_one_side() -> None:
    room = _display(4, 3)
    a = _pipe(0, (room.x + 1, room.y - 1), (0, 1))
    b = _pipe(1, (room.x + 2, room.y - 1), (0, 1))
    facts = display_facts(Ast(rooms=[room], pipes=[a, b]), room)
    assert facts is not None and "double-attach:top" in facts.hazards


# ── the registered dead-panel rule ────────────────────────────────────────────
def test_registered_io_catalog_holds_the_dead_panel_rule() -> None:
    names = [r.name for r in rules_for("io")]
    assert "io.display_dead_panel" in names


def test_dead_panel_recognises_an_undriven_oversized_display() -> None:
    room = _display(5, 4)
    sites = IO_DISPLAY_DEAD_PANEL.recognize(Ast(rooms=[room], pipes=[]), room)
    assert len(sites) == 1
    assert sites[0].env["target"] == (1, 1)


def test_dead_panel_ignores_a_driven_display() -> None:
    ast, room = _driven(5, 4)
    # A live panel is exactly what this rule must never touch (frame-judged).
    assert IO_DISPLAY_DEAD_PANEL.recognize(ast, room) == []


def test_dead_panel_ignores_an_already_minimal_panel() -> None:
    room = _display(1, 1)
    assert IO_DISPLAY_DEAD_PANEL.recognize(Ast(rooms=[room], pipes=[]), room) == []


def test_dead_panel_cost_removes_cells() -> None:
    room = _display(5, 4)
    site = IO_DISPLAY_DEAD_PANEL.recognize(Ast(rooms=[room], pipes=[]), room)[0]
    assert IO_DISPLAY_DEAD_PANEL.cost_delta(site).d_cells < 0


def test_dead_panel_apply_shrinks_and_round_trips() -> None:
    room = _display(6, 5, x=0, y=0)
    ast = Ast(rooms=[room], pipes=[])
    ast.source = render(ast)
    assert round_trip_ok(ast)
    before = copy.deepcopy(ast)  # apply must not touch the caller's AST

    cands = apply_rules(ast, [IO_DISPLAY_DEAD_PANEL])
    assert len(cands) == 1
    # 3x3 border for a 1x1 interior; the panel really got smaller.
    assert cands[0].grid == ["+=+", ": :", "+=+"]
    assert render(ast) == render(before)  # the source AST is unchanged
    assert cands[0].placement is None  # a content rewrite: verify is its gate


# ── the exported (unregistered) height tightener ──────────────────────────────
def _tall_no_bottom(w: int, h: int) -> tuple[Ast, RoomNode]:
    """An oversized panel with only top + left attaches (bottom free to move)."""
    room = _display(w, h)
    top = _pipe(0, (room.x + 1, room.y - 1), (0, 1))
    left = _pipe(1, (room.x - 1, room.y + 1), (1, 0))
    return Ast(rooms=[room], pipes=[top, left]), room


def test_is_oversized_requires_matching_width() -> None:
    ast, room = _tall_no_bottom(4, 8)
    facts = display_facts(ast, room)
    assert facts is not None
    assert is_oversized(facts, 4, 3)  # same width, taller: a candidate
    assert not is_oversized(facts, 5, 3)  # width change would re-address pixels
    assert not is_oversized(facts, 4, 8)  # not actually oversized


def test_tighten_recognises_a_surgery_free_height_trim() -> None:
    ast, room = _tall_no_bottom(4, 8)
    rule = display_tighten_rule(4, 3)
    sites = rule.recognize(ast, room)
    assert len(sites) == 1
    assert sites[0].env["target"] == (4, 3)
    assert rule.cost_delta(sites[0]).d_cells < 0


def test_tighten_refuses_when_a_bottom_pipe_would_need_surgery() -> None:
    ast, room = _tall_no_bottom(4, 8)
    x1_bottom = room.y + 8 + 1
    ast.pipes.append(_pipe(2, (room.x + 1, x1_bottom + 1), (0, -1)))
    assert display_tighten_rule(4, 3).recognize(ast, room) == []


def test_tighten_refuses_a_width_change_that_re_addresses() -> None:
    ast, room = _tall_no_bottom(4, 8)
    assert display_tighten_rule(5, 3).recognize(ast, room) == []


def test_tighten_no_match_when_already_at_resolution() -> None:
    ast, room = _tall_no_bottom(4, 3)
    assert display_tighten_rule(4, 3).recognize(ast, room) == []


def test_tighten_refuses_to_delete_a_row_a_side_pipe_uses() -> None:
    # a left (DATA) attach low in the panel would fall outside the shrunk box.
    room = _display(4, 8)
    top = _pipe(0, (room.x + 1, room.y - 1), (0, 1))
    left_low = _pipe(1, (room.x - 1, room.y + 7), (1, 0))  # row 7, deleted by a trim to 3
    ast = Ast(rooms=[room], pipes=[top, left_low])
    assert display_tighten_rule(4, 3).recognize(ast, room) == []


def test_tighten_with_no_resolution_is_inert() -> None:
    ast, room = _tall_no_bottom(4, 8)
    assert display_tighten_rule(None, None).recognize(ast, room) == []


def test_tighten_apply_round_trips_on_a_synthetic_oversized_panel() -> None:
    # The synthetic-fixture proof the plan asks for: recognise + apply + re-render an
    # oversized display, with no engine, and show the interior really shrank.
    room = _display(4, 8, x=0, y=2)  # top attach needs a row above the panel
    top = _pipe(0, (1, 1), (0, 1))
    left = _pipe(1, (-1, 3), (1, 0))
    ast = Ast(rooms=[room], pipes=[top, left])
    ast.source = render(ast)
    assert round_trip_ok(ast)

    rule = display_tighten_rule(4, 3)
    cands = apply_rules(ast, [rule])
    assert len(cands) == 1
    assert room.h == 8  # apply_rules works on a deep copy: the caller's panel is untouched
    # the rendered candidate is strictly shorter than the input panel.
    assert len(cands[0].grid) < len(ast.source)


def test_shrink_display_refuses_a_grow_or_sub_minimum() -> None:
    room = _display(4, 4)
    with pytest.raises(ValueError, match="not a shrink"):
        shrink_display(room, 5, 4)
    with pytest.raises(ValueError, match="minimum"):
        shrink_display(room, 0, 4)


# ── slow: the engine confirms the io family leaves a live panel alone ─────────
@pytest.mark.slow  # parses + verifies the plotter archive on the engine
@node_required
def test_io_family_cleanly_no_matches_the_plotter_archive() -> None:
    """A live, driven panel: the io family must recognise nothing and change nothing.

    This is the plan's "cleanly no-matches, grid unchanged + passing" outcome. The
    dead-panel rule sees all three ports driven and declines; the (unregistered)
    tightener is not in the catalog. So ``rule_pass("io")`` yields no candidate, and
    the archive — verified here on two cheap Bresenham cases — stays valid.
    """
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manparse import parse_program
    from randomfun2026solvers.manrewrite import rule_pass

    prog = parse_program(PLOTTER)
    assert rule_pass("io")(prog) == []  # no io rewrite touches the live panel

    # And the panel really is the live 3-pipe one we declined to shrink.
    from randomfun2026solvers.manast import Refine, parse_ast

    ast = parse_ast(prog, refine=Refine.BLOCKS)
    displays = [r for r in ast.rooms if r.kind == "display"]
    assert len(displays) == 1
    facts = display_facts(ast, displays[0])
    assert facts is not None and facts.driven

    # The archive still passes its own public cases (two cheap segments).
    cheap = {"one pixel", "main diagonal"}
    prob = json.loads((REPO / "tasks" / "problems" / "plotter.json").read_text(encoding="utf-8"))
    prob = {**prob, "publicTestData": [c for c in prob["publicTestData"] if c["name"] in cheap]}
    res = optimize.verify(PLOTTER, prob, tick_cap=3_000_000)
    assert res.passed, [(c.name, c.detail) for c in res.cases if not c.passed]
