"""The 2D block placer: what a cell does to a man, and what a corridor may do to it.

Everything here is the shape tier — no engine.  The failures this repo has
actually been bitten by on CFG machines are all silent: a corridor that steers
somebody else's man, a cell that is walked but holds no op, a backtick that
pairs down a column.  None of them stop the grid loading.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from randomfun2026solvers import blockplace as B
from randomfun2026solvers import optimize
from randomfun2026solvers import snake_place as SP
from randomfun2026solvers.blockplace import Collision, E, N, S, W
from randomfun2026solvers.snake_layout import TOKEN_ZONE, WORKER_L


# ── the field ─────────────────────────────────────────────────────────────────
def test_two_corridors_may_cross_a_blank_in_different_directions() -> None:
    """A man is only ever on a cell for one tick; a blank is a blank to both."""
    f = B.Field(5, 5)
    f.step((2, 2), E, E)
    assert f.can_step((2, 2), S, S)
    f.step((2, 2), S, S)
    assert f.get(2, 2) == " "


def test_a_turn_may_not_land_on_a_blank_somebody_crosses_the_other_way() -> None:
    f = B.Field(5, 5)
    f.step((2, 2), E, E)                      # a corridor crosses heading east
    assert f.can_step((2, 2), S, S)           # straight through is still fine
    assert not f.can_step((2, 2), N, W)       # but a `<` here would turn him west
    assert f.can_step((2, 2), N, E)           # ... unless the turn agrees with it


def test_a_corridor_may_merge_into_a_turn_glyph_that_already_points_its_way() -> None:
    f = B.Field(5, 5)
    f.step((2, 2), E, S)                      # places `v`
    assert f.get(2, 2) == "v"
    assert f.can_step((2, 2), W, S)           # arrives west, leaves south: merge
    assert not f.can_step((2, 2), W, W)       # `v` would steer him south anyway


def test_an_op_is_never_crossable() -> None:
    f = B.Field(5, 5)
    f.op(2, 2, "r")
    for din in (E, W, N, S):
        for dout in (E, W, N, S):
            assert not f.can_step((2, 2), din, dout)


def test_a_cell_a_block_walks_over_may_be_crossed_but_never_turned_on() -> None:
    """The one that hijacked INIT: `seek` jumps the pen, the man does not."""
    f = B.Field(9, 3)
    f.walk(2, 1, 5, E)                        # five blanks the block's man crosses
    assert (3, 1) in f.walked
    r = B.shortest(f, (3, 0), S, (8, 1))
    assert r is None or all(cell not in f.walked or din == dout
                            for cell, din, dout in r.steps)


# ── the router ────────────────────────────────────────────────────────────────
def test_the_router_finds_a_way_round_a_wall_of_ops() -> None:
    f = B.Field(9, 5)
    for y in range(4):
        f.op(4, y, "M")
    f.op(8, 2, ">")
    r = B.shortest(f, (0, 2), E, (8, 2))
    assert r is not None
    assert (4, 4) in r.cells                  # under the wall, not through it


def test_a_corridor_never_arrives_head_on_at_an_entry() -> None:
    """The entry glyph is a `>`; a man who reached it heading west would be sent
    straight back the way he came, so the router has to come round."""
    f = B.Field(6, 3)
    f.op(2, 1, ">")
    r = B.shortest(f, (5, 1), W, (2, 1))
    assert r is not None
    assert r.steps[-2][2] != W          # the heading he holds one cell short
    f2 = B.Field(6, 1)                  # ... and with no room to come round
    f2.op(2, 0, ">")
    assert B.shortest(f2, (5, 0), W, (2, 0)) is None


# ── one whole room ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def room():
    return SP.build_room()


def test_every_block_walks_its_own_tokens(room) -> None:
    """A block whose row lost a glyph still loads; it just computes something else."""
    SP.walked_cells_all_hold_a_glyph(room)


def test_a_run_the_pen_dropped_is_caught_against_the_cfg_not_the_plan(room) -> None:
    """The two checks are not the same check, and only one of them can disagree.

    `walked_cells_all_hold_a_glyph` compares the *grid* against the *plan*, so a
    plan that never held the glyph passes it.  That is precisely the failure that
    cost a `matmul` machine six sessions: its checker expanded the pen's own
    output on both sides, so four missing constant runs were invisible while
    bindings, placement and pipes were all genuinely fine.  `walks_are_the_program`
    compares the plan against `WORKER_L`, which is the source.
    """
    victim = next(n for n, p in room.placed.items()
                  if sum(len(r.cells) for r in p.plan.rows) > 3)
    rows = room.placed[victim].plan.rows
    row = next(r for r in rows if r.cells)
    dropped, row.cells = row.cells, row.cells[:-1]
    try:
        SP.walked_cells_all_hold_a_glyph(room)      # grid vs plan: still agrees
        with pytest.raises(Collision):
            B.walks_are_the_program(room, WORKER_L)
    finally:
        row.cells = dropped
    B.walks_are_the_program(room, WORKER_L)


def test_a_token_compiles_to_the_cells_it_is_written_with() -> None:
    """A literal is written between backticks unless it is a single digit."""
    assert B.token_glyphs("L7") == "7"
    assert B.token_glyphs("L2048") == "`2048`"
    assert B.token_glyphs("rr") == B.token_glyphs("ri") == "r"
    assert B.token_glyphs("sq") == B.token_glyphs("so") == "s"
    assert B.token_glyphs("X") == "X"


def test_every_lane_of_the_finished_field_arrives_where_the_cfg_says(room) -> None:
    """Re-walked on the *finished* grid, not on the routes: a later corridor that
    drops a turn onto an earlier one re-steers it and nothing complains."""
    B.walk_edges(room.field, WORKER_L, room.placed)


def test_no_two_blocks_overlap(room) -> None:
    seen: dict[tuple[int, int], str] = {}
    for name, p in room.placed.items():
        for i, r in enumerate(p.plan.rows):
            for c, _g in r.cells:
                assert (c, p.ys[i]) not in seen, (name, seen.get((c, p.ys[i])))
                seen[(c, p.ys[i])] = name


def test_every_pipe_op_stands_in_a_column_that_binds_its_own_band(room) -> None:
    geo, _banks = SP.layout()
    for name, p in room.placed.items():
        toks = [t for t in WORKER_L[name][0] if t in TOKEN_ZONE]
        cols = [c for r in p.plan.rows for c, g in r.cells if g in ("r", "s")]
        for col, tok in zip(cols, toks, strict=True):
            geo.check_binding(col, tok)


def test_no_column_holds_two_backticks(room) -> None:
    """Backticks pair down columns as well as along rows, and a vertical pair
    with a turn glyph between them is a load error."""
    seen: dict[int, int] = {}
    for y, line in enumerate(room.rows()):
        for x, ch in enumerate(line):
            if ch == "`":
                assert x not in seen, (x, seen[x], y)
                seen[x] = y


def test_the_banks_do_not_overlap_and_the_split_lies_between_the_zones() -> None:
    geo, banks = SP.layout()
    ring_hi, io_lo = geo.zone_cols["RING"][1], geo.zone_cols["IO"][0]
    assert ring_hi < io_lo
    for x in (geo.zone_cols["RING"][0], ring_hi):
        geo.check_binding(x, "rr")
        geo.check_binding(x, "sr")
    for x in (io_lo, geo.zone_cols["IO"][1]):
        geo.check_binding(x, "ri")
        geo.check_binding(x, "sp")
    for a, b in ((banks["P"], banks["Q"]), (banks["Q"], banks["R"])):
        assert a.code_hi < b.ch0


# ── the engine ────────────────────────────────────────────────────────────────
GRID = Path(__file__).resolve().parents[1] / "tasks" / "solutions" / "snake_banked.man"
PROBLEM = Path(__file__).resolve().parents[1] / "tasks" / "problems" / "snake.json"
ROUTE_CHECK = (Path(__file__).resolve().parents[1] / "littleman" / "tools"
               / "route-check.mjs")


def test_the_checked_in_banked_grid_is_what_the_generator_emits() -> None:
    rows, _dbg, _info = SP.build_grid()
    assert GRID.read_text() == "\n".join(rows) + "\n"


@pytest.mark.slow
def test_every_pipe_parses_and_every_send_reaches_one() -> None:
    """A pipe whose first cell points the wrong way still lets the grid load, so
    ask the oracle: seven pipes, and no `r`/`s` anywhere routing to nothing."""
    out = subprocess.run(["node", str(ROUTE_CHECK), str(GRID)],
                         capture_output=True, text=True, check=True).stdout
    assert "ERR" not in out
    listed = [ln for ln in out.splitlines() if re.match(r"  \d+: \d+ cells", ln)]
    assert len(listed) == 7, listed
    assert '"cells":[]' not in out


@pytest.mark.slow
def test_the_banked_grid_passes_every_public_case() -> None:
    result = optimize.verify(GRID, PROBLEM, tick_cap=15_000_000)
    failed = [(c.name, c.detail) for c in result.cases if not c.passed]
    assert not failed, failed
    assert len(result.cases) == 5


def test_the_shipped_grid_builds_exactly_the_pipes_it_draws() -> None:
    """Counted the way the runtime counts, which is not the way `analyze` does.

    A corridor cell that turns against the underside of a wall mints a pipe
    nobody meant; the grid loads, `analyze` reports the drawn pipes, and the room
    splits its `s` glyphs across a mouth the author never saw.  Live twice today
    on other machines.  snake draws seven: the ring pair, the I/O pair, and the
    painter's three port pipes to the panel.
    """
    from randomfun2026solvers.man_png import pipe_mouths, rooms_of

    rows = GRID.read_text().rstrip("\n").split("\n")
    # worker, relay, input room, painter, panel
    assert len(rooms_of(rows)) == 5
    assert len(pipe_mouths(rows)) == 7
