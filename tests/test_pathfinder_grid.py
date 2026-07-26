"""The `pathfinder` band grid: the invariants that make the layout checkable.

Everything the generator can get wrong is invisible in the output — a pipe op
one column too far east binds to the wrong FIFO and the board shears silently,
a backtick sharing a column with another one is a *load* error. So the fast tier
here re-derives each of those from the emitted grid rather than trusting the
generator's own assertions, and the engine runs are marked slow.
"""

from __future__ import annotations

import functools
import json
from collections import Counter
from pathlib import Path

import pytest
from randomfun2026solvers import pathfinder_grid as pg
from randomfun2026solvers import pathfinder_prog as prog
from randomfun2026solvers.circuit import Circuit, Collision

ROOT = Path(__file__).resolve().parents[1]
MAN = ROOT / "tasks" / "solutions" / "pathfinder_grid.man"
PROBLEM = ROOT / "tasks" / "problems" / "pathfinder.json"

PIPE_TOK = {
    "sr": ("s", "R"),
    "sg": ("s", "G"),
    "sf": ("s", "F"),
    "sp": ("s", "P"),
    "rr": ("r", "R"),
    "rg": ("r", "G"),
    "rf": ("r", "F"),
    "ri": ("r", "I"),
}
#: From SPEC.md's `validOps`, plus the spawn and the room/pipe structure glyphs.
VALID = set("0123456789`.MWN+-*/%&|~{}<>^vVXxYdabmq]sSrRUH") | set(" @+-|=:IO")


@functools.cache
def grid() -> list[str]:
    rows, _ = pg.build()
    return rows


@functools.cache
def cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


# ── the band partition ────────────────────────────────────────────────────────
def test_no_column_can_tie_between_two_anchors():
    """A tie falls back on reading order, so a one-cell edit could rebind."""
    for anchors in (pg.SEND_ANCHOR, pg.RECV_ANCHOR):
        cols = sorted(anchors.values())
        for a, b in zip(cols, cols[1:], strict=False):
            assert (a + b) % 2 == 1, f"anchors {a} and {b} tie at column {(a + b) // 2}"
    for x in range(pg.CW0, pg.CW1 + 1):
        pg.send_band(x)  # raises Collision on a tie
        pg.recv_band(x)


def test_bands_are_four_contiguous_equal_spans_holding_their_own_anchor():
    for kind, anchors in (("s", pg.SEND_ANCHOR), ("r", pg.RECV_ANCHOR)):
        seen = []
        for band in anchors:
            lo, hi = pg.band_span(band, kind)
            assert hi - lo + 1 == pg.BAND_W, (kind, band, lo, hi)
            assert lo <= anchors[band] <= hi, "an anchor outside its own band"
            seen.append((lo, hi))
        assert sorted(seen) == [
            (pg.CW0 + i * pg.BAND_W, pg.CW0 + (i + 1) * pg.BAND_W - 1) for i in range(4)
        ]


# ── the block order ───────────────────────────────────────────────────────────
def test_order_is_exactly_the_program_and_every_chain_falls_through():
    supers, succ = pg._superblocks()  # raises on a mismatch
    assert sum(len(chain) for chain in pg.ORDER) == len(prog.build())
    assert set(supers) == {chain[0] for chain in pg.ORDER}


def test_every_branch_lane_can_reach_where_its_target_sits():
    """North reaches only upward and south only downward — that is the order."""
    _, succ = pg._superblocks()
    pg._check_order(succ)  # raises Collision if violated


def test_a_north_lane_pointing_downwards_is_rejected():
    _, succ = pg._superblocks()
    broken = dict(succ)
    broken["PACKT"] = {"pos": "ROTT", "zero": "PACKEND"}  # ROTT is far below
    with pytest.raises(Collision, match="north lane"):
        pg._check_order(broken)


# ── the placer ────────────────────────────────────────────────────────────────
def _placer() -> tuple[Circuit, pg.Placer]:
    c = Circuit(pg.IW, 64, strict_corridors=True)
    p = pg.Placer(c, {})
    p.start(0)
    return c, p


def test_placer_puts_every_pipe_op_in_its_own_band():
    c, p = _placer()
    for tok in ("sr", "sg", "sf", "sp", "ri", "rf", "rg", "rr"):
        p.token(tok)
        # the glyph just written is one step behind the cursor
        x, y = p.x - p.dx, p.y
        kind, band = PIPE_TOK[tok]
        assert c.get(x, y) == kind
        got = pg.send_band(x) if kind == "s" else pg.recv_band(x)
        assert got == band, f"{tok} landed in {kind}{got} at column {x}"


def test_placer_drops_a_row_when_the_band_is_behind_it():
    _, p = _placer()
    p.token("sp")  # far east, still heading east
    row, dx = p.y, p.dx
    assert dx > 0
    p.token("sr")  # band R is behind: must reverse
    assert p.y == row + 1
    assert p.dx < 0


def test_placer_never_leaves_the_code_area():
    c, p = _placer()
    for _ in range(200):
        p.token("M")
    for (x, _y), ch in c.cell.items():
        if ch != " ":
            assert pg.CW0 <= x <= pg.CW1 or x == pg.ENTRY_COL


def test_placer_refuses_unsafe_backtick_column_reuse():
    c = Circuit(pg.IW, 64, strict_corridors=True)
    taken = {x: 0 for x in range(pg.CW0, pg.CW1 + 1)}
    for x in taken:
        c.set(x, 1, "M")
    p = pg.Placer(c, taken)
    p.start(2)
    with pytest.raises(Collision, match="backtick"):
        p.literal("256")


def test_placer_reuses_backtick_columns_over_only_blanks():
    c = Circuit(pg.IW, 64, strict_corridors=True)
    taken = {pg.CW0: 0, pg.CW0 + 3: 0}
    p = pg.Placer(c, taken)
    p.start(2)
    p.literal("16")
    assert c.get(pg.CW0, 2) == "`"
    assert c.get(pg.CW0 + 3, 2) == "`"


def test_a_block_always_ends_heading_west_out_of_the_code_area():
    _, p = _placer()
    p.token("M")
    last = p.exit_west(2, 0)
    assert p.dx < 0
    assert last >= 1, "a block that enters eastbound cannot leave on its own row"


# ── the emitted grid ──────────────────────────────────────────────────────────
def test_grid_uses_only_valid_glyphs():
    bad = {ch for row in grid() for ch in row} - VALID
    assert not bad, f"invalid glyphs {bad}"


def test_every_pipe_glyph_in_the_worker_stands_in_its_band():
    """Re-derived from the grid, and counted against the program's own tally."""
    rows = grid()
    got: Counter[tuple[str, str]] = Counter()
    for y in range(pg.WY, len(rows)):
        row = rows[y]
        for x in range(pg.WX, min(pg.WX + pg.IW, len(row))):
            if row[x] not in ("s", "r"):
                continue
            col = x - pg.WX
            assert pg.CW0 <= col <= pg.CW1, f"pipe op in the channel at ({x},{y})"
            band = pg.send_band(col) if row[x] == "s" else pg.recv_band(col)
            got[(row[x], band)] += 1
    want = Counter(PIPE_TOK[t] for toks, _ in prog.build().values() for t in toks if t in PIPE_TOK)
    assert got == want


def test_no_column_holds_a_backtick_pair_with_a_live_glyph_between_it():
    """Backticks pair on columns as well as rows; a non-digit between is a
    *load* error, not a wrong answer."""
    rows = grid()
    width = max(len(r) for r in rows)
    for x in range(width):
        ticks = [y for y, r in enumerate(rows) if x < len(r) and r[x] == "`"]
        for a, b in zip(ticks, ticks[1:], strict=False):
            between = [rows[y][x] for y in range(a + 1, b) if x < len(rows[y])]
            assert all(ch.isdigit() or ch == " " for ch in between), (
                f"column {x} pairs backticks at rows {a},{b} over {between}"
            )


def test_pipe_loops_hold_enough_cells_to_not_deadlock():
    """Capacity here is correctness: one word short and the ring stalls with no
    error at all."""
    _, dbg = pg.build()
    notes = {r.name: r.note for r in dbg.regions}
    ring = int(notes["ring relay"].split()[0])
    assert ring >= pg.RING_CELLS >= prog.RING_WORDS + 1
    assert int(notes["G relay"].split()[0]) >= pg.SCRATCH_CELLS >= prog.SCRATCH_WORDS + 1
    assert int(notes["F relay"].split()[0]) >= pg.FIFO_CELLS >= prog.FIFO_WORDS + 1


def test_relay_walk_is_an_alternating_read_write():
    art = pg.flat_relay(pg.RING_RELAY_W)
    assert len(art) == 4
    assert art[1][2] == "@", "the man's first act must be a read, never a send"
    assert art[2][2] == ".", "an odd body would drop a word on the last lap"
    with pytest.raises(ValueError):
        pg.flat_relay(7)


def test_checked_in_man_matches_the_generator():
    assert MAN.read_text() == "\n".join(grid()) + "\n"


def test_debug_sidecar_names_every_block():
    _, dbg = pg.build()
    named = {r.name for r in dbg.regions}
    assert {chain[0] for chain in pg.ORDER} <= named
    assert {"worker", "west channel", "panel block", "input", "ring relay"} <= named


# ── the engine ────────────────────────────────────────────────────────────────
@pytest.mark.slow
@pytest.mark.parametrize("backend", ["fast", "reference"])
def test_all_seven_public_cases_pass(backend):
    from randomfun2026solvers import pathfinder_check as check

    res = check.check_all(MAN, backend=backend)
    failed = [c.name for c in res.cases if not c.passed]
    assert not failed, f"{backend}: {failed}"
    assert len(res.cases) == 7
