"""`gradebook` laid out as a banked ring machine: the grid, not the model.

:mod:`gradebook_cfg` is verified round by round against all seven public cases
and against sixteen program-agnostic gates, and none of that says anything about
the *grid*.  Everything gradebook has actually been bitten by is invisible to the
model: a pipe whose first cell points the wrong way, an `r` that binds the ring
next door, a corridor that eats a glyph off a block's row.  And the warning sign
is on the record -- gradebook's own first submission scored 19/20 on a private
case after passing 7/7 public.

So the gates are re-run here **against the built grid, on the engine**, and the
shape tier below them states the invariants that a wrong grid still loads
through.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import blockplace as B  # noqa: E402
from randomfun2026solvers import gradebook_place as gp  # noqa: E402
from randomfun2026solvers import optimize, scoring  # noqa: E402
from randomfun2026solvers.gradebook_cfg import (  # noqa: E402
    WORKER,
    worker_glyph_cells,
)

GRID = REPO / "tasks" / "solutions" / "gradebook_ring.man"
PROBLEM = REPO / "tasks" / "problems" / "gradebook.json"
ROUTE_CHECK = REPO / "littleman" / "tools" / "route-check.mjs"

#: The CPU build this replaces, for the numbers the report is written against.
CPU_AREA2, CPU_SCORE = 8_649, 6.39e9


# ── the geometry ──────────────────────────────────────────────────────────────
def test_every_band_covers_its_own_bank_and_nothing_else() -> None:
    """The bands are *derived* from the pipe columns, so check they came out sane.

    A bank whose window strayed over a split would plan happily and bind the
    wrong ring at run time, which is why `layout` refuses rather than warns.
    """
    geo, banks = gp.layout(gp.BANKS)
    for z in gp.ZONES:
        lo, hi = geo.zone_cols[z]
        bank = banks[f"{z}:{z}"]
        assert lo <= bank.code0 and bank.code_hi <= hi, z
    lows = [geo.zone_cols[z][0] for z in gp.ZONES]
    assert lows == sorted(lows), "bands must run west to east in ZONES order"
    for a, b in zip(gp.ZONES, gp.ZONES[1:], strict=False):
        assert geo.zone_cols[a][1] < geo.zone_cols[b][0]


def test_every_column_of_every_band_binds_the_band_it_is_in() -> None:
    """`Geometry.binds` is the authority; ask it about every column, not a sample."""
    geo, _ = gp.layout(gp.BANKS)
    for z, (lo, hi) in geo.zone_cols.items():
        for x in range(lo, hi + 1):
            assert geo.binds(x, "rr") == z, (z, x, "incoming")
            assert geo.binds(x, "sr") == z, (z, x, "outgoing")


def test_the_eight_pipe_columns_are_distinct_and_never_touch() -> None:
    """Pipe glyphs join by 4-adjacency: two touching risers parse as **one** pipe.

    That failure lets the grid load and leaves the machine reading a ring it
    never wrote, so the spacing is a property of the layout, not of the drawing.
    """
    geo, _ = gp.layout(gp.BANKS)
    cols = sorted(list(geo.pipe_in.values()) + list(geo.pipe_out.values()))
    assert len(set(cols)) == 8
    assert all(b - a >= 2 for a, b in zip(cols, cols[1:], strict=False)), cols


def test_no_column_of_any_band_is_equidistant_from_two_pipes() -> None:
    """A tie is not a binding.  `Geometry.binds` breaks one westward; nothing
    says the engine does, and the op that stands on one reads a plausible number
    off the wrong ring.  `audit_bindings` caught one on a wide `IO` bank."""
    geo, _ = gp.layout(gp.BANKS)
    for z, (lo, hi) in geo.zone_cols.items():
        for x in range(lo, hi + 1):
            for cols in (geo.pipe_in, geo.pipe_out):
                near = sorted(abs(x - c) for c in cols.values())
                assert near[0] != near[1], (z, x, near)


def test_no_edge_of_the_cfg_could_have_been_a_free_fall_through() -> None:
    """`blockplace` routes every edge as a corridor, which is only a loss when a
    block's successor could have been laid immediately after it -- the trap that
    cost `matmul` 21% of its ticks.  It cannot arise here: every fall-through
    target is re-entered from somewhere else, so none of them can be merged."""
    preds: dict[str, int] = {}
    for _n, (_toks, succ) in WORKER.items():
        for target in ([succ] if isinstance(succ, str) else succ.values()):
            preds[target] = preds.get(target, 0) + 1
    fall_through = [(n, s) for n, (_t, s) in WORKER.items() if isinstance(s, str)]
    assert len(fall_through) == 23
    assert [(n, s) for n, s in fall_through if preds[s] == 1] == []


def test_a_bank_exists_for_every_run_of_bands_a_block_could_need() -> None:
    """Fourteen of the thirty-seven blocks use two bands or more."""
    _geo, banks = gp.layout(gp.BANKS)
    idx = {z: i for i, z in enumerate(gp.ZONES)}
    wide = 0
    for name in WORKER:
        z = B.block_zones(WORKER, name, gp.TOKEN_ZONE)
        if len(z) < 2:
            continue
        wide += 1
        i, j = min(idx[k] for k in z), max(idx[k] for k in z)
        bank = banks[f"{gp.ZONES[i]}:{gp.ZONES[j]}"]
        assert set(bank.zones) >= z, name
    assert wide == 14


# ── the north band ────────────────────────────────────────────────────────────
def test_a_riser_never_leaves_the_cone_its_anchor_owns() -> None:
    """The premise of the whole column discipline, stated on the bare geometry.

    A cell one row further from the room may stand one column off the anchor and
    still lose to it.  A staircase sits exactly on that boundary; anything flatter
    -- the first version of this module -- does not, and then an op near a split
    binds its neighbour's ring.
    """
    top, bot = gp.RELAY_H + 2, gp.BAND_H - 1
    for east in (True, False):
        anchor = 40
        cells = gp._riser(anchor, top, bot, east)
        room_end = cells[0] if east else cells[-1]
        assert room_end == (anchor, bot)
        for px, py in cells:
            assert abs(px - anchor) <= bot - py, (east, px, py)


def test_both_risers_of_a_ring_together_hold_a_full_roster() -> None:
    """``N + 1`` words at ``N = 16``, and a turnaround room adds exactly one.

    ``dataflow_relay.relay_words`` counts words carried *per lap*; the room has a
    single spawn, so one man walks it and holds one word between his `r` and his
    `s`.  Measured: the N=16 case deadlocked at 14 pipe cells a ring and passed
    at 16.
    """
    top, bot = gp.RELAY_H + 2, gp.BAND_H - 1
    cells = sum(len(gp._riser(40, top, bot, e)) for e in (True, False))
    assert cells + gp.RELAY_HOLDS >= gp.RING_WORDS
    assert gp.RELAY_HOLDS == 1


def test_the_relay_is_wide_enough_for_both_ports_and_narrow_enough_to_fit() -> None:
    geo, banks = gp.layout(gp.BANKS)
    pitch = min(banks[f"{b}:{b}"].code_hi - banks[f"{a}:{a}"].code_hi
                for a, b in zip(gp.ZONES, gp.ZONES[1:], strict=False))
    assert gp.RELAY_W + 2 <= pitch, "adjacent relays would overlap"
    assert gp.RELAY_H >= 3, "a perimeter walk needs a 3x3 interior"


# ── the built grid ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def built() -> tuple[list[str], object, dict]:
    return gp.build_grid()


def test_the_checked_in_grid_is_what_the_generator_emits(built) -> None:
    rows, _dbg, _info = built
    assert GRID.read_text(encoding="utf-8") == "\n".join(rows) + "\n", (
        f"{GRID.name} is stale; regenerate with "
        f"`python -m randomfun2026solvers.gradebook_place --man {GRID}`"
    )


def test_the_generator_is_deterministic(built) -> None:
    rows, _dbg, _info = built
    again, _d2, _i2 = gp.build_grid()
    assert again == rows


def test_every_block_is_placed_and_every_ring_holds_a_roster(built) -> None:
    _rows, _dbg, info = built
    assert info["blocks"] == len(WORKER) == 37
    assert set(info["rings"]) == {"RING", "IDS", "FILE"}
    for z, held in info["rings"].items():
        assert held >= gp.RING_WORDS, z


def test_the_grid_holds_every_glyph_the_program_compiles_to(built) -> None:
    """322 cells of program.  A block whose row lost one still runs, differently.

    `build_grid` asserts this cell by cell against each block's own plan; this
    states the total so a silently *shorter* plan cannot pass it either.
    """
    rows, _dbg, _info = built
    ops = sum(1 for r in rows for ch in r if ch not in " +-|<>^v")
    assert worker_glyph_cells() == 322
    assert ops >= 322


def test_the_grid_renders_to_a_png_that_a_viewer_would_accept(tmp_path) -> None:
    """The layout is easier to judge by eye than by coordinate, so it renders.

    Only the header and the size are asserted -- the point of the picture is to
    be looked at, and a test that pinned its pixels would just make it hard to
    change the palette.
    """
    from randomfun2026solvers import man_png

    out = tmp_path / "grid.png"
    w, h = man_png.render(GRID, out, scale=2, pipe_rows=gp.BAND_H)
    rows = GRID.read_text(encoding="utf-8").splitlines()
    assert (w, h) == (2 * max(len(r) for r in rows), 2 * len(rows))
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_footprint_beats_the_cpu_build_it_replaces(built) -> None:
    rows, _dbg, info = built
    w, h = info["grid"]
    assert (w, h) == (max(len(r) for r in rows), len(rows))
    assert info["area2"] == max(w, h) ** 2
    assert info["area2"] < CPU_AREA2


# ── the engine ────────────────────────────────────────────────────────────────
def _route_check(grid: Path) -> str:
    return subprocess.run(["node", str(ROUTE_CHECK), str(grid)],
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.slow
def test_all_eight_pipes_parse_and_no_send_reaches_nothing() -> None:
    """A pipe whose first cell points the wrong way fails to parse in *silence*.

    Three rings, an input and an output: eight pipes, and route-check is the only
    thing that knows how long a drawn pipe actually turned out to be.
    """
    out = _route_check(GRID)
    assert "ERR" not in out
    listed = [ln for ln in out.splitlines() if re.match(r"  \d+: \d+ cells", ln)]
    assert len(listed) == 8, listed
    assert '"cells":[]' not in out, "an r/s that reaches no pipe"
    lens = sorted(int(re.search(r": (\d+) cells", ln).group(1)) for ln in listed)
    # The six ring risers are the long ones; each pair must hold a full roster.
    assert lens[0] + lens[1] + gp.RELAY_HOLDS >= gp.RING_WORDS, lens


@pytest.mark.slow
def test_every_public_case_passes_on_the_fast_engine() -> None:
    res = optimize.verify(GRID, PROBLEM)
    failed = [(c.name, c.detail) for c in res.cases if not c.passed]
    assert not failed, failed
    assert len(res.cases) == 7


@pytest.mark.slow
def test_the_reference_engine_agrees_with_the_fast_one_to_the_tick(monkeypatch) -> None:
    """The fast engine is a re-implementation; the reference one is the judge's.

    Comparing tick counts rather than just re-asserting "passed" does two jobs:
    it is a much sharper statement than agreement on the output, and a run that
    silently fell back to the fast engine would return the identical object for
    a reason that has nothing to do with the machine being right.
    """
    fast = optimize.verify(GRID, PROBLEM)
    monkeypatch.setenv("LM_VALIDATOR", "reference")
    ref = optimize.verify(GRID, PROBLEM)
    failed = [(c.name, c.detail) for c in ref.cases if not c.passed]
    assert not failed, failed
    assert len(ref.cases) == 7
    assert [(c.name, c.ticks) for c in ref.cases] == \
           [(c.name, c.ticks) for c in fast.cases]


@pytest.mark.slow
def test_the_score_is_reported_against_the_build_it_replaces() -> None:
    s = scoring.score_program(GRID, PROBLEM)
    assert s.area2 < CPU_AREA2
    assert s.score < CPU_SCORE / 20, f"{s.width}x{s.height} scored {s.score:.3g}"


# ── the semantic gates, against the grid ─────────────────────────────────────
#
# `test_gradebook_cfg.py` runs these against the op model.  Passing there says
# the *program* is right; it says nothing about whether this grid is that
# program, and gradebook's own first submission scored 19/20 on a private case
# after passing 7/7 public.  So they are re-run here as a synthetic problem, and
# the real engine answers them off the real grid.
#
# Thirteen behaviours; some need more than one roster to pin down, so there are
# more cases than gates.  Every case names the gate it belongs to, and
# :func:`test_every_semantic_gate_of_the_cfg_suite_is_here` checks the set
# against the list rather than against a number somebody typed.
GATE_NAMES = (
    "TOP breaks a three-way tie with the smallest id",
    "TOP on an all-zero subject still names a student",
    "TOP follows a SET that demotes the leader",
    "AVG rounds down",
    "AVG divides by every legal roster size",
    "every subject of a full-width roster is addressable",
    "SET emits nothing and GET sees it",
    "a SET to 0 and to 100 both survive the packing",
    "AVG is exact when every column is at its maximum",
    "the id column cannot carry into subject four",
    "a search that wraps the sentinel still finds its student",
    "a batch of eight operations is answered in full",
    "ten rounds of eight operations all land",
)



def _roster(students, subjects) -> dict:
    values = [len(students), subjects]
    for rec in students:
        values.extend(rec)
    return {"in": [str(v) for v in values], "out": []}


def _batch(ops, expected) -> dict:
    values = [len(ops)]
    for op in ops:
        values.extend(op)
    return {"in": [str(v) for v in values], "out": [str(v) for v in expected]}


def _gates() -> list[dict]:
    cases: list[dict] = []

    def case(gate, *rounds, tag=""):
        # A case is one *run* of the machine, and a run reads exactly one roster
        # -- a second one would be read as a batch.  So a gate that needs several
        # rosters needs several cases, and they carry the gate's name with them.
        cases.append({"gate": gate, "name": gate + tag, "rounds": list(rounds)})

    case("TOP breaks a three-way tie with the smallest id",
         _roster([(5000, 70), (1200, 70), (9999, 70), (3000, 10)], 1),
         _batch([(4, 1)], (1200,)))
    case("TOP on an all-zero subject still names a student",
         _roster([(4000, 0), (2000, 0), (7000, 0), (9000, 0)], 1),
         _batch([(4, 1)], (2000,)))
    case("TOP follows a SET that demotes the leader",
         _roster([(4000, 90), (2000, 80), (7000, 70), (9000, 60)], 1),
         _batch([(4, 1)], (4000,)),
         _batch([(2, 4000, 1, 10), (4, 1)], (2000,)),
         _batch([(2, 2000, 1, 5), (4, 1)], (7000,)))
    for grades, want in (((1, 2, 3, 3), 2), ((0, 0, 0, 1), 0),
                         ((100, 100, 100, 100), 100), ((99, 100, 100, 100), 99)):
        case("AVG rounds down",
             _roster([(4001 + i, g) for i, g in enumerate(grades)], 1),
             _batch([(3, 1)], (want,)), tag=f" {grades} -> {want}")
    for n in range(4, 17):
        # `N` is the ring's own sentinel *and* AVG's divisor, so every roster
        # size the rules allow is a different division.
        case("AVG divides by every legal roster size",
             _roster([(1000 + i, i + 1) for i in range(n)], 1),
             _batch([(3, 1)], ((n + 1) // 2,)), tag=f", N={n}")
    for k in range(1, 5):
        students = [(2000 + i, *[10 * (s + 1) + i for s in range(k)]) for i in range(4)]
        ops = [(1, 2000 + i, s + 1) for i in range(4) for s in range(k)]
        want = [10 * (s + 1) + i for i in range(4) for s in range(k)]
        case("every subject of a full-width roster is addressable",
             _roster(students, k), _batch(ops, want), tag=f", K={k}")
    case("SET emits nothing and GET sees it",
         _roster([(1111, 1, 2), (2222, 3, 4), (3333, 5, 6), (4444, 7, 8)], 2),
         _batch([(2, 3333, 2, 42)], ()),
         _batch([(1, 3333, 2), (1, 3333, 1)], (42, 5)))
    case("a SET to 0 and to 100 both survive the packing",
         _roster([(1111 * i, 50, 50, 50, 50) for i in (1, 2, 3, 4)], 4),
         _batch([(2, 2222, 1, 0), (2, 2222, 4, 100), (1, 2222, 1), (1, 2222, 4),
                 (1, 2222, 2), (1, 2222, 3)], (0, 100, 50, 50)))
    case("AVG is exact when every column is at its maximum",
         _roster([(1000 + i, 100, 100, 100, 100) for i in range(16)], 4),
         _batch([(3, s + 1) for s in range(4)], (100,) * 4))
    case("the id column cannot carry into subject four",
         _roster([(1000 + i, 7, 7, 7, 3) for i in range(16)], 4),
         _batch([(3, 4), (3, 1)], (3, 7)))
    students = [(1000 + 7 * i, i, 100 - i) for i in range(16)]
    ops, want = [], []
    for i in (15, 15, 0, 15, 8, 15):
        ops.append((1, 1000 + 7 * i, 1))
        want.append(i)
    ops.append((3, 1))
    want.append(sum(range(16)) // 16)
    ops.append((4, 2))
    want.append(1000)
    case("a search that wraps the sentinel still finds its student",
         _roster(students, 2), _batch(ops, want))
    small = [(1000 + i, 10 + i) for i in range(4)]
    case("a batch of eight operations is answered in full",
         _roster(small, 1),
         _batch([(1, 1000 + (i % 4), 1) for i in range(8)],
                [10 + (i % 4) for i in range(8)]))
    case("ten rounds of eight operations all land",
         _roster(small, 1),
         *[_batch([(1, 1000 + (i % 4), 1) for i in range(8)],
                  [10 + (i % 4) for i in range(8)]) for _ in range(10)])
    return cases


GATES = _gates()


def test_every_semantic_gate_of_the_cfg_suite_is_here() -> None:
    """The gates are named, not counted: a dropped one has to show up as a name."""
    assert list(dict.fromkeys(g["gate"] for g in GATES)) == list(GATE_NAMES)
    assert len(GATES) == 31, "thirteen gates, thirty-one runs of the machine"


@pytest.mark.slow
@pytest.mark.parametrize("case", GATES, ids=lambda c: c["name"])
def test_a_semantic_gate_holds_on_the_built_grid(case) -> None:
    problem = json.loads(PROBLEM.read_text(encoding="utf-8"))
    problem["publicTestData"] = [case]
    res = optimize.verify(GRID, problem)
    failed = [(c.name, c.detail) for c in res.cases if not c.passed]
    assert not failed, failed
