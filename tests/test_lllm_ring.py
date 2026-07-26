"""The LLLM ring machine: token-level program, then the grid it compiles to."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from randomfun2026solvers import lllm_layout, lllm_ring, lllm_tables, optimize

PROBLEM = (
    Path(__file__).resolve().parents[1] / "tasks" / "problems" / ("little-little-little-man.json")
)


def _cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def _expected(case: dict) -> list[list[str]]:
    return [frame for rnd in case["rounds"] for frame in (rnd.get("frames") or [])]


# ── decode tables ─────────────────────────────────────────────────────────────
def test_hash_is_injective_over_every_non_digit_glyph() -> None:
    idx = [lllm_tables.hash_index(c) for c in lllm_tables.GLYPHS]
    assert len(set(idx)) == len(lllm_tables.GLYPHS)
    assert all(0 <= i < 16 for i in idx)


def test_magics_are_positive_64_bit_literals() -> None:
    for magic in (lllm_tables.CLASS_MAGIC, lllm_tables.COLOUR_MAGIC):
        assert 0 < magic < 1 << 63


def test_decode_matches_the_glyph_table() -> None:
    for code, want in lllm_tables.GLYPHS.items():
        assert lllm_tables.decode_ascii(code) == want
    for digit in range(10):
        assert lllm_tables.decode_ascii(48 + digit) == (digit, 8)


# ── the token program ─────────────────────────────────────────────────────────
def test_every_successor_names_a_real_block() -> None:
    for name, (_, succ) in lllm_ring.WORKER.items():
        targets = [succ] if isinstance(succ, str) else list(succ.values())
        for target in targets:
            assert target in lllm_ring.WORKER, f"{name} -> {target}"


def test_branch_blocks_end_in_a_branch_glyph() -> None:
    for name, (toks, succ) in lllm_ring.WORKER.items():
        if isinstance(succ, dict):
            assert toks[-1] in ("X", "x", "d"), (name, toks[-1])
            keys = set(succ)
            want = {"X": {"neg", "zero", "pos"}, "x": {"one", "zero"}, "d": {"pos", "zero"}}[
                toks[-1]
            ]
            assert keys == want, (name, keys)
        else:
            assert not toks or toks[-1] not in ("X", "x", "d"), name


def test_store_and_file_capacities_cover_the_stated_constraints() -> None:
    # 4 <= W, H <= 16.  The store is a *fixed* ring of sixteen rows of sixteen
    # cells packed eight to a word, whatever H is — a constant modulus is what
    # makes the per-tick rotation one sign test and one add.
    assert lllm_ring.STORE_WORDS == 16 * 16 // lllm_ring.CELLS_PER_WORD == 32
    assert lllm_ring.store_words(16) == lllm_ring.store_words(4) == 32
    # Eight live slots and a ninth held transiently while `TICK_LIVE` swaps CUR.
    assert lllm_ring.FILE_WORDS >= 9


def test_a_packed_word_holds_every_class_the_store_can_carry() -> None:
    # Classes are stored biased into 0..21, eight to a word at CELL_BITS each,
    # and the setup accumulator carries a sentinel bit above the payload — so
    # the whole thing has to stay inside a positive 64-bit literal.
    widest = lllm_ring.DIGIT_BIAS + 9
    assert widest < 1 << lllm_ring.CELL_BITS
    assert lllm_ring.WORD_BIT == lllm_ring.CELLS_PER_WORD * lllm_ring.CELL_BITS
    assert (1 << lllm_ring.WORD_BIT) << lllm_ring.CELL_BITS < 1 << 63


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_token_program_reproduces_every_public_frame(case: dict) -> None:
    frames, _ = lllm_ring.simulate_worker(case["rounds"])
    want = _expected(case)
    assert len(frames) == len(want), f"{len(frames)} frames, expected {len(want)}"
    for i, (got, exp) in enumerate(zip(frames, want, strict=True)):
        assert got == exp, f"frame {i}"


# ── the grid ──────────────────────────────────────────────────────────────────
GRID = (
    Path(__file__).resolve().parents[1]
    / "tasks"
    / "solutions"
    / ("little-little-little-man_ring.man")
)


@pytest.fixture(scope="module")
def built() -> tuple[list[str], object, dict]:
    return lllm_layout.build_grid()


def test_checked_in_grid_still_matches_the_generator(built) -> None:
    rows, _dbg, _info = built
    assert GRID.read_text() == "\n".join(rows) + "\n"


def test_the_machine_is_charged_from_its_height(built) -> None:
    """Not a footprint pin — the *shape* claim the layout is built around.

    `max(w, h)^2` bills only the larger side, so every decision here (the panel
    moved east of the room, the code area widened until the wraps stopped) trades
    width, which is free, for rows, which are not.  If the width ever overtook
    the height that reasoning would be inverted and the whole geometry would need
    re-deriving, so it is worth failing on.
    """
    rows, _dbg, _info = built
    w, h = max(len(r) for r in rows), len(rows)
    assert w < h, f"{w}x{h}: width has overtaken height; the geometry is upside down"


def test_rings_are_sized_to_the_stated_constraints_not_the_public_cases(built) -> None:
    _rows, _dbg, info = built
    # 4 <= W, H <= 16, so the worst case is sixteen rows of sixteen plus END.
    assert info["store_ring"] >= lllm_ring.STORE_WORDS + 2
    assert info["file_ring"] >= lllm_ring.FILE_WORDS + 2


def test_every_pipe_op_stands_in_a_band_that_binds_its_own_pipe() -> None:
    # `check_binding` raises while planning, so this pins the *bands* instead:
    # every column of a band must resolve to that band's pipe, both ways.
    for band, (lo, hi) in lllm_layout.ZONE_COLS.items():
        for token in (
            "rr" if band == "ST" else "rq" if band == "FI" else "ri",
            "sr" if band == "ST" else "sq" if band == "FI" else "sp",
        ):
            for x in (lo, (lo + hi) // 2, hi):
                lllm_layout.check_binding(x, token)


def test_no_column_holds_two_backticks(built) -> None:
    # Backticks pair down columns as well as along rows, and a vertical pair
    # with a turn glyph between it is a load error.
    rows, _dbg, _info = built
    width = max(len(r) for r in rows)
    counts = Counter(x for r in rows for x, ch in enumerate(r.ljust(width)) if ch == "`")
    assert not [x for x, n in counts.items() if n > 1]


def test_debug_sidecar_names_every_block(built) -> None:
    _rows, dbg, _info = built
    named = {r.name for r in dbg.regions if "block" in r.tags}
    assert named == {f"block:{n}" for n in lllm_ring.WORKER}


@pytest.mark.slow
def test_grid_passes_every_public_case() -> None:
    result = optimize.verify(GRID, PROBLEM, tick_cap=15_000_000)
    failed = [(c.name, c.detail) for c in result.cases if not c.passed]
    assert not failed, failed
    assert len(result.cases) == 10


def test_a_fall_through_that_lands_east_costs_no_lane_row() -> None:
    """The straight-lane row exists only to walk the man back west to ``NC``.

    When the successor is the next block *and* its first glyph stands east of where
    the predecessor stopped, he does not need the entry: he drops one row at his own
    column and keeps walking east. Those rows were 57 of the room's 101 overhead
    rows, and a row is the charged dimension here.

    Guarding the mechanism rather than the count: every dropped block must have no
    straight row allocated, and its drop column must really be west of the target's
    first glyph, or the man would walk backwards over glyphs he has already run.
    """
    from randomfun2026solvers.lllm_layout import (
        _droppable,
        _first_col,
        block_order,
        plan_blocks,
    )

    order = block_order(lllm_ring.WORKER)
    plans = plan_blocks(order, lllm_ring.WORKER)
    drop = _droppable(order, plans, lllm_ring.WORKER)
    assert drop, "no fall-through drops: the packing has regressed"

    room = lllm_layout.build_room()
    for src, dst in drop.items():
        assert src not in room.straight_y, f"{src} kept a lane row it cannot need"
        assert plans[src].rows[-1].end < _first_col(plans[dst])
        # and the target really is adjacent, which is what makes the drop reachable
        assert room.glyph_ys[dst][0] > room.glyph_ys[src][-1]
def test_wall_rows_hold_only_the_two_glyphs_the_biased_hash_covers() -> None:
    """The assumption `WALL_BIAS` rests on, checked against every public case.

    A wall row's bytes are biased before they are hashed, and the table has
    entries for exactly two of them — `+` and `-`. A rectangular room's top and
    bottom rows are `+---...---+`, so that is every byte that can arrive; a `|`
    or a space there would hash to a nibble nobody wrote and decode to garbage.
    The hash cannot be widened to cover `|` as well (no `(K, S)` is injective
    over fifteen values), so the guard is this test rather than the table.
    """
    for case in _cases():
        first = case["rounds"][0]["in"]
        w, h = int(first[0]), int(first[1])
        codes = [int(v) for v in first[2 : 2 + w * h]]
        for y in (0, h - 1):
            row = codes[y * w : (y + 1) * w]
            assert set(row) <= {43, 45}, (
                f"{case['name']}: wall row {y} holds "
                f"{sorted({chr(c) for c in row} - {'+', '-'})}"
            )
