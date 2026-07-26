"""Tests for the counted-loop recogniser (``manrecog.py``).

The fast tier is pure: it matches hand-built rows against the generator and pins
the hit/miss boundary (width, missing turns, body extraction). One ``slow`` test
drives the reference parser so the recogniser is exercised on a real block.
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

from randomfun2026solvers.manast import Atom  # noqa: E402
from randomfun2026solvers.manatom import counted_loop  # noqa: E402
from randomfun2026solvers.manrecog import match_counted_loop  # noqa: E402

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)


def test_matches_the_canonical_rs_loop() -> None:
    m = match_counted_loop(counted_loop("rs").rows)
    assert m is not None
    assert m.body == "rs" and m.k == 2
    assert m.rows == counted_loop("rs").rows
    # ports come straight off the gadget: enter top-left east, exit the `d` east.
    assert (m.entry.dx, m.entry.dy) == (0, 0)
    assert (m.exit_.dx, m.exit_.dy) == (1, 0)
    assert m.origin is None  # bare rows carry no placement


def test_body_is_read_back_for_other_bodies() -> None:
    for body in ("0s", "rsr", "0s0s"):
        m = match_counted_loop(counted_loop(body).rows)
        assert m is not None and m.body == body and m.k == len(body)


def test_a_placed_atom_records_its_origin() -> None:
    atom = Atom(id=3, x=10, y=1, rows=list(counted_loop("rs").rows))
    m = match_counted_loop(atom)
    assert m is not None and m.origin == (10, 1)


def test_a_straight_run_is_not_a_loop() -> None:
    assert match_counted_loop(["@rsH"]) is None
    assert match_counted_loop(["ab", "cd"]) is None  # right width, wrong glyphs


def test_a_three_column_block_is_not_a_loop() -> None:
    # counted loops are always two columns; a wider block cannot match.
    assert match_counted_loop([">d ", "mr ", " s ", "^< "]) is None


def test_a_missing_turn_does_not_match() -> None:
    # drop the closing `^<` row: the rebuild-and-compare check rejects it.
    rows = list(counted_loop("rs").rows)
    rows[-1] = "m<"
    assert match_counted_loop(rows) is None


def test_wrong_decrement_placement_does_not_match() -> None:
    # `m` belongs on the first body row; move it and the exact rebuild fails.
    assert match_counted_loop([">d", " r", "ms", "^<"]) is None


@pytest.mark.slow  # parses the fixture through the reference engine
@node_required
def test_finds_the_loop_block_in_the_parsed_fixture() -> None:
    from randomfun2026solvers.manast import Refine, parse_ast

    ast = parse_ast(FIXTURE, refine=Refine.BLOCKS)
    hits = [
        m
        for room in ast.rooms
        if room.kind == "compute"
        for child in room.children
        if isinstance(child, Atom) and (m := match_counted_loop(child)) is not None
    ]
    assert len(hits) == 1
    assert hits[0].body == "rs" and hits[0].origin is not None
