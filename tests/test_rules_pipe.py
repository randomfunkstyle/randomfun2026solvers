"""Tests for the pipe-family rewrite rule (``rules_pipe.py``).

The fast tier is engine-free: it hand-builds :class:`~manast.Ast` nodes in memory and
exercises the recogniser, its guards, the applier, and the cost sign. It proves the
one rule *hits* an over-provisioned conduit and *misses* every hazard — a storage
ring, a display/output feed, a display-panel endpoint, and (the headline safety
claim) any reshape that would move a pipe's wall-adjacency signature and so risk a
nearest-pipe re-bind.

Two ``slow`` tests drive the real engine. The positive one runs a hand fixture — an
over-long conduit routed as a wall-free serpentine that puffs the bounding box out —
through ``optimize`` and asserts the rewrite is accepted: footprint² and score both
fall and every public case still passes. The negative one runs a real archived
solution whose conduits are already minimal and whose rings must never be touched,
and asserts the pass changes nothing.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
LM_MJS = REPO / "littleman" / "lm.mjs"
TRIANGLE = REPO / "tasks" / "solutions" / "triangle_cpu.man"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manast import Ast, PipeNode, RoomNode  # noqa: E402
from randomfun2026solvers.manfree import PipeRole, pipe_roles  # noqa: E402
from randomfun2026solvers.manroute import reglyph  # noqa: E402
from randomfun2026solvers.manrules import rules_for  # noqa: E402
from randomfun2026solvers.rules_pipe import (  # noqa: E402
    SHORTEN_CONDUIT,
    proposed_shortening,
)

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)

# The hello-world bytes the fixture emitter sends — fixed by the problem.
_HELLO = [104, 101, 108, 108, 111, 32, 119, 111, 114, 108, 100]


# ── engine-free AST builders ──────────────────────────────────────────────────
def _room(rid: int, x: int, y: int, w: int = 1, h: int = 1, kind: str = "compute") -> RoomNode:
    return RoomNode(id=rid, x=x, y=y, kind=kind, w=w, h=h)


def _pipe(
    pid: int,
    src: int,
    dst: int,
    path: list[tuple[int, int]],
    *,
    ed: tuple[int, int] = (1, 0),
    xd: tuple[int, int] = (1, 0),
    minc: int | None = None,
) -> PipeNode:
    return PipeNode(
        id=pid,
        x=min(x for x, _ in path),
        y=min(y for _, y in path),
        path=list(path),
        glyphs=["?"] * len(path),
        src=src,
        dst=dst,
        entry_dir=ed,
        exit_dir=xd,
        min_capacity=minc,
    )


#: A conduit between two 1×1 rooms (A at 0-2, B at 8-10) that detours down into the
#: open field below the gap — endpoints (3,1)/(7,1), body clear of every wall. Its
#: shortest endpoint-pinned route is the 5-cell straight line, so it is shortenable.
_DETOUR = [(3, 1), (4, 1), (4, 2), (4, 3), (4, 4), (5, 4), (6, 4), (6, 3), (6, 2), (6, 1), (7, 1)]


def _conduit_ast() -> Ast:
    return Ast(rooms=[_room(0, 0, 0), _room(1, 8, 0)], pipes=[_pipe(0, 0, 1, _DETOUR)])


def _sites(ast: Ast) -> list:
    return [s for room in ast.rooms for s in SHORTEN_CONDUIT.recognize(ast, room)]


# ── hit: an over-provisioned conduit is offered ───────────────────────────────
def test_hits_an_overlong_conduit() -> None:
    ast = _conduit_ast()
    assert pipe_roles(ast)[0] is PipeRole.CONDUIT
    sites = _sites(ast)
    assert len(sites) == 1
    site = sites[0]
    assert site.rule is SHORTEN_CONDUIT
    assert site.env["side"] == "conduit" and site.env["pipe_id"] == 0
    assert site.env["old_cap"] == 11 and site.env["new_cap"] == 5  # 11-cell detour → 5 straight


def test_conduit_offered_exactly_once() -> None:
    # Keyed on pipe.src, so the two-room sweep must not double-count the pipe.
    assert len(_sites(_conduit_ast())) == 1


def test_apply_shortens_the_pipe_in_place() -> None:
    import copy

    ast = _conduit_ast()
    site = _sites(ast)[0]
    trial = copy.deepcopy(ast)
    SHORTEN_CONDUIT.apply(trial, site)
    pipe = next(p for p in trial.pipes if p.id == 0)
    assert pipe.capacity == 5  # rerouted to the straight minimum
    assert pipe.path[0] == (3, 1) and pipe.path[-1] == (7, 1)  # endpoints held fixed


def test_proposed_shortening_is_endpoint_pinned_and_shorter() -> None:
    ast = _conduit_ast()
    new = proposed_shortening(ast, 0)
    assert new is not None
    assert new.capacity == 5 and new.capacity < 11
    assert new.path[0] == (3, 1) and new.path[-1] == (7, 1)


# ── the hazard misses ─────────────────────────────────────────────────────────
def test_misses_a_storage_ring() -> None:
    # Two pipes forming a directed cycle 0→1→0 → both are RING; length is capacity,
    # so shortening could deadlock the ring. The rule must never fire.
    ring_back = [(7, 3), (6, 3), (5, 3), (4, 3), (3, 3)]
    ast = Ast(
        rooms=[_room(0, 0, 0), _room(1, 8, 0)],
        pipes=[_pipe(0, 0, 1, _DETOUR), _pipe(1, 1, 0, ring_back, ed=(-1, 0), xd=(-1, 0))],
    )
    roles = pipe_roles(ast)
    assert roles[0] is PipeRole.RING and roles[1] is PipeRole.RING
    assert _sites(ast) == []


def test_misses_a_feed_to_an_output() -> None:
    # dst < 0: the pipe ends off-grid at an output/display port whose terminal cell
    # selects the port — reshaping it destroys meaning no room glyph shows.
    ast = Ast(rooms=[_room(0, 0, 0)], pipes=[_pipe(0, 0, -1, _DETOUR)])
    assert pipe_roles(ast)[0] is PipeRole.FEED
    assert _sites(ast) == []


def test_misses_a_display_panel_endpoint() -> None:
    # A conduit into a display room: belt-and-suspenders over the FEED guard — the
    # display's terminus is semantic and must never be reshaped.
    ast = Ast(
        rooms=[_room(0, 0, 0), _room(1, 8, 0, kind="display")],
        pipes=[_pipe(0, 0, 1, _DETOUR)],
    )
    assert _sites(ast) == []


def test_misses_a_reshape_that_would_move_a_wall_segment() -> None:
    # The no-rebind guard. A conduit whose detour hugs a third room's top wall: its
    # shortest route runs clear of that wall, so the wall-adjacency signature would
    # change — exactly the move that can silently re-bind an s/r. Refuse it, even
    # though a shorter route exists.
    hugging = [(3, 1), (4, 1), (4, 2), (5, 2), (6, 2), (6, 1), (7, 1)]
    ast = Ast(
        rooms=[_room(0, 0, 0), _room(1, 8, 0), _room(2, 3, 3, w=3, h=1)],  # C spans cols 3-7 row 3
        pipes=[_pipe(0, 0, 1, hugging)],
    )
    # A strictly-shorter route does exist (proving the miss is the *guard*, not a
    # failure to find one) …
    new = proposed_shortening(ast, 0)
    assert new is not None and new.capacity < 7
    # … but its wall signature differs, so the rule refuses.
    assert _sites(ast) == []


# ── cost + precondition ───────────────────────────────────────────────────────
def test_cost_delta_shrinks_cells() -> None:
    site = _sites(_conduit_ast())[0]
    cost = SHORTEN_CONDUIT.cost_delta(site)
    assert cost.d_cells < 0  # 11 → 5: six fewer cells
    assert cost.d_cells == 5 - 11
    assert cost.d_ticks_per_value < 0  # shed latency: a conduit cell is one tick


def test_precondition_requires_a_real_shortening_above_the_floor() -> None:
    from randomfun2026solvers.manrules import MatchSite

    ok = MatchSite(
        rule=SHORTEN_CONDUIT, room_id=0, cells=frozenset(), entry=SHORTEN_CONDUIT.recognize,  # type: ignore[arg-type]
        exits=(), env={"capacity": 2, "old_cap": 11, "new_cap": 5},
    )
    not_shorter = MatchSite(
        rule=SHORTEN_CONDUIT, room_id=0, cells=frozenset(), entry=SHORTEN_CONDUIT.recognize,  # type: ignore[arg-type]
        exits=(), env={"capacity": 2, "old_cap": 5, "new_cap": 5},
    )
    below_floor = MatchSite(
        rule=SHORTEN_CONDUIT, room_id=0, cells=frozenset(), entry=SHORTEN_CONDUIT.recognize,  # type: ignore[arg-type]
        exits=(), env={"capacity": 2, "old_cap": 3, "new_cap": 1},
    )
    assert SHORTEN_CONDUIT.preconditions(ok) is True
    assert SHORTEN_CONDUIT.preconditions(not_shorter) is False
    assert SHORTEN_CONDUIT.preconditions(below_floor) is False


def test_rule_is_registered() -> None:
    assert SHORTEN_CONDUIT in rules_for("pipe")
    assert "pipe.shorten_conduit" in {r.name for r in rules_for("pipe")}


# ── the engine fixture (built engine-free, verified on the engine) ────────────
def build_overlong_conduit_fixture() -> list[str]:
    """A hello-world relay whose first conduit is a wall-free serpentine detour.

    Emitter room A sends the eleven ``hello world`` bytes over pipe0 to relay room B,
    which forwards them to the output O. pipe0 is laid out as a deep down-and-up
    serpentine in the open gap between A and B: its shortest endpoint-pinned route is
    six cells, but the detour dips to row ``W`` so the fixture is one row *taller than
    it is wide*. Shortening pipe0 collapses that dip, so ``max(w,h)²`` drops from
    ``(W+1)²`` to ``W²`` while every value still arrives — a footprint win the rule
    must find. Built with pure string + :func:`manroute.reglyph` ops (no engine), so
    the fast tier can pin its shape.
    """
    a_inner = "@" + "".join(f"`{v}`s" for v in _HELLO) + "H"
    b_inner = "@" + "rs" * len(_HELLO) + "H"
    la = len(a_inner) + 2  # A box width; pipe0 leaves A's right wall at col la
    gap = 4
    bx = la + 1 + gap + 1  # B box left column; pipe0 enters B's wall just before it

    # Base one-line layout (three rows) as a char grid, then grow it for the dip.
    def box(inner: str) -> tuple[str, str, str]:
        bar = "+" + "-" * len(inner) + "+"
        return bar, "|" + inner + "|", bar

    at, am, ab = box(a_inner)
    bt, bm, bb = box(b_inner)
    ox = bx + len(bt)  # output column, two cells right of B for the "     >>|O|"
    width = ox + 5
    first, last = (la, 1), (bx - 1, 1)
    down_col, up_col = la + 1, la + 2
    depth = width  # dip to row == width, so height = width + 1 > width

    grid = [[" "] * width for _ in range(depth + 1)]

    def put(x: int, y: int, s: str) -> None:
        for i, ch in enumerate(s):
            grid[y][x + i] = ch

    for y, (arow, brow, orow) in enumerate(
        zip((at, am, ab), (bt, bm, bb), ("  +-+", ">>|O|", "  +-+"), strict=True)
    ):
        put(0, y, arow)
        put(bx, y, brow)
        put(ox, y, orow)

    # pipe0 serpentine: first → down col → across the bottom → up col → across to last.
    path = [first]
    path += [(down_col, y) for y in range(1, depth + 1)]
    path += [(up_col, y) for y in range(depth, 0, -1)]
    path += [(x, 1) for x in range(up_col + 1, last[0])]
    path.append(last)
    glyphs = reglyph(path, (1, 0), (1, 0))
    for (x, y), g in zip(path, glyphs, strict=True):
        grid[y][x] = g

    return ["".join(r).rstrip() for r in grid]


def test_fixture_shape_is_pinned() -> None:
    # Fast guard: if the generator drifts, the slow proof below is stale.
    grid = build_overlong_conduit_fixture()
    width = max(len(r) for r in grid)
    height = len(grid)
    assert height == width + 1  # taller than wide: the dip is what the rule reclaims
    # eleven `NNN`s emits + '@' + 'H' → the emitter width fixes the footprint floor.
    assert grid[1].startswith("|@`104`s`101`s")


# ── slow: the real engine proves the win, and proves the misses are safe ──────
@pytest.mark.slow  # runs the fixture through the engine before and after the reshape
@node_required
def test_fixture_shorten_shrinks_footprint_and_still_passes() -> None:
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manrewrite import rule_pass
    from randomfun2026solvers.scoring import footprint

    grid = "\n".join(build_overlong_conduit_fixture()) + "\n"

    base = optimize.verify(grid, _hello_world())
    assert base.passed  # the fixture is a genuine, passing solution

    res = optimize.optimize(grid, _hello_world(), passes=[rule_pass("pipe")], max_sweeps=2)
    assert res.improved and res.passed  # the reshape is accepted and still correct

    bw, bh, base_area2 = footprint("\n".join(res.base_grid))
    ow, oh, opt_area2 = footprint("\n".join(res.grid))
    assert opt_area2 < base_area2  # max(w,h)² falls — the footprint win the task asks for
    assert max(ow, oh) < max(bw, bh)

    # And the output is identical: both grids pass every public case (same expected
    # outputs), and the accepted grid re-verifies clean on its own.
    assert optimize.verify("\n".join(res.grid), _hello_world()).passed


@pytest.mark.slow  # a real archive: minimal conduits + storage rings must be left alone
@node_required
def test_real_solution_with_rings_is_left_unchanged() -> None:
    from randomfun2026solvers import optimize
    from randomfun2026solvers.manast import Refine, parse_ast
    from randomfun2026solvers.manparse import parse_program
    from randomfun2026solvers.manrewrite import rule_pass

    grid = TRIANGLE.read_text(encoding="utf-8")

    # The archive has two ring legs and three already-minimal conduits, so the rule
    # finds nothing — proving the ring/feed/floor guards on a real program.
    ast = parse_ast(parse_program(grid), refine=Refine.BLOCKS)
    roles = pipe_roles(ast)
    assert PipeRole.RING in roles.values()  # the tape ring the rule must not touch
    assert _sites(ast) == []

    res = optimize.optimize(grid, "triangle", passes=[rule_pass("pipe")], max_sweeps=1)
    assert res.passed
    assert res.grid == res.base_grid  # nothing accepted → the archive output is preserved


def _hello_world() -> dict:
    return {
        "slug": "hello-world",
        "scoring": "footprint-tick",
        "publicTestData": [
            {
                "name": "hello world",
                "rounds": [
                    {
                        "in": [],
                        "out": ["104", "101", "108", "108", "111", "32",
                                "119", "111", "114", "108", "100"],
                    }
                ],
            }
        ],
    }
