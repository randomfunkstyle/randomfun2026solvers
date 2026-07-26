"""`snake` as one compiled room: the layout, its plumbing, and the whole machine.

The fast tier is all shape — everything the generator can prove without starting
an engine, which is most of what has actually gone wrong here:

* a pipe op standing in a band that binds somebody else's pipe;
* a corridor whose turn glyph lands inside another corridor and hijacks its man;
* a backtick sharing a column with another backtick, which pairs vertically;
* a pipe whose first cell does not point away from its room, so it parses as a
  loose pipe, the room's `s` binds nothing, and the grid still loads.

The slow tier runs the five public cases on the reference engine.
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from randomfun2026solvers import optimize, snake_layout
from randomfun2026solvers.snake_layout import TOKEN_ZONE, WORKER_L

ROOT = Path(__file__).resolve().parents[1]
GRID = ROOT / "tasks" / "solutions" / "snake_ring.man"
PROBLEM = ROOT / "tasks" / "problems" / "snake.json"
ROUTE_CHECK = ROOT / "littleman" / "tools" / "route-check.mjs"


@pytest.fixture(scope="module")
def built():
    return snake_layout.build_grid()


@pytest.fixture(scope="module")
def room():
    return snake_layout.build_room()


# ── the artifact matches its generator ────────────────────────────────────────
def test_the_checked_in_grid_is_what_the_generator_emits(built) -> None:
    rows, _dbg, _info = built
    assert GRID.read_text() == "\n".join(rows) + "\n"


def test_the_debug_sidecar_names_every_block(built) -> None:
    _rows, dbg, _info = built
    named = {r.name for r in dbg.regions if "block" in r.tags}
    assert named == {f"block:{n}" for n in WORKER_L}


# ── the bands ─────────────────────────────────────────────────────────────────
def test_every_column_of_a_band_binds_that_band_s_own_pipes(room) -> None:
    """`plan_blocks` raises on a stray op; this pins the bands it trusts."""
    geo = room.geo
    for band, (lo, hi) in geo.zone_cols.items():
        for token, zone in TOKEN_ZONE.items():
            if zone != band:
                continue
            for x in (lo, (lo + hi) // 2, hi):
                geo.check_binding(x, token)


def test_the_two_band_midpoints_sit_on_the_same_column(room) -> None:
    """One gap in the middle has to serve `r` and `s` alike, or a block would
    have to wrap in two different places depending on which way it was going."""
    geo = room.geo
    assert geo.pipe_in["RING"] + geo.pipe_in["IO"] == \
        geo.pipe_out["RING"] + geo.pipe_out["IO"]


def test_no_column_holds_two_backticks(built) -> None:
    # backticks pair down columns as well as along rows, and a vertical pair
    # with a turn glyph between them is a load error
    rows, _dbg, _info = built
    width = max(len(r) for r in rows)
    counts = Counter(x for r in rows for x, ch in enumerate(r.ljust(width)) if ch == "`")
    assert not [x for x, n in counts.items() if n > 1]


# ── the room ──────────────────────────────────────────────────────────────────
def test_every_lane_lands_on_a_corridor_that_goes_where_it_should(room) -> None:
    """Lanes may share a corridor, but only when they share a target — a merge
    that got that wrong would silently send one answer to the other's block."""
    routed = {(src, row): dst for src, dst, _k, row, _s in room.edges}
    for name in room.order:
        rows = room.lane_ys[name]
        for kind, target in snake_layout._lanes_of(WORKER_L, name, room.plans[name]):
            assert routed[(name, rows[kind])] == target, (name, kind)


def test_every_block_is_entered_at_the_head_of_its_first_glyph_row(room) -> None:
    c, nch = room.circuit, room.nch
    for i, name in enumerate(room.order):
        y = room.glyph_ys[name][0]
        assert c.get(nch, y) == ("@" if i == 0 else ">"), name
        # and the run east from the entry to the first glyph must be clear
        first = min(col for col, _g in room.plans[name].rows[0].cells)
        assert all(c.free(x, y) for x in range(nch + 1, first)), name


def test_the_ring_holds_more_words_than_the_worker_ever_leaves_in_it(built) -> None:
    """A pipe's capacity is its length; the ring is full at rest, so too short a
    ring deadlocks the first `sr` of a round rather than failing to load."""
    _rows, _dbg, info = built
    assert info["ring"] >= snake_layout.RING_MIN


def test_the_worker_program_reaches_the_room_intact(room) -> None:
    placed = sum(len(r.cells) for p in room.plans.values() for r in p.rows)
    want = 0
    for toks, _ in WORKER_L.values():
        for t in toks:
            want += 1 if not t.startswith("L") or int(t[1:]) <= 9 else len(t[1:]) + 2
    assert placed == want


# ── the engine ────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_every_pipe_parses_and_every_send_reaches_one(tmp_path: Path) -> None:
    """A pipe whose first cell points the wrong way still lets the grid load, so
    ask the oracle: seven pipes, and no `r`/`s` anywhere routing to nothing."""
    out = subprocess.run(
        ["node", str(ROUTE_CHECK), str(GRID)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "ERR" not in out
    listed = [ln for ln in out.splitlines() if re.match(r"  \d+: \d+ cells", ln)]
    assert len(listed) == 7, listed
    assert '"cells":[]' not in out


@pytest.mark.slow
def test_the_grid_passes_every_public_case() -> None:
    result = optimize.verify(GRID, PROBLEM, tick_cap=15_000_000)
    failed = [(c.name, c.detail) for c in result.cases if not c.passed]
    assert not failed, failed
    assert len(result.cases) == 5
