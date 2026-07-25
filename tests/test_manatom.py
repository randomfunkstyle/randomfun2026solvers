"""Tests for the described-atom factory (``manatom.py``).

A gadget's paperwork is only worth having if it cannot disagree with its glyphs,
so most of this checks the validation. The cost figures are pinned because the
whole case for unrolling a loop rests on them — and the derivation was wrong by
one tick per lap until it was measured.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manast import Port  # noqa: E402
from randomfun2026solvers.manatom import (  # noqa: E402
    counted_loop,
    counted_loop_horizontal,
    gadget,
    unrolled,
)

E, S = (1, 0), (0, 1)

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)


def test_a_port_resolves_to_an_absolute_cell_when_placed() -> None:
    """Offsets are relative so they survive placement; `at` does the arithmetic."""
    p = Port(2, 1, E, after=3, note="out")
    assert p.at(10, 20) == (12, 21)


def test_a_port_outside_the_block_is_refused() -> None:
    with pytest.raises(ValueError, match="outside the"):
        gadget("bad", ["ab"], entry=Port(0, 0, E), exits=(Port(5, 0, E),))


def test_an_exit_heading_must_agree_with_the_glyph_it_leaves_from() -> None:
    """Recorded paperwork that contradicts the cells is worse than none."""
    with pytest.raises(ValueError, match="forces"):
        gadget("bad", ["><"], entry=Port(0, 0, E), exits=(Port(1, 0, E),))
    # the same block with the honest heading is fine
    gadget("ok", ["><"], entry=Port(0, 0, E), exits=(Port(1, 0, (-1, 0)),))


def test_a_loop_must_agree_about_how_many_values_it_moves() -> None:
    with pytest.raises(ValueError, match="those must agree"):
        gadget(
            "bad", ["ab"], entry=Port(0, 0, E), exits=(Port(1, 0, E),),
            ticks=8, per_lap=2, count_multiple=4,
        )


# ── the cost model, which decides every loop trade-off ───────────────────────
def test_the_two_counted_loops_cost_the_same_but_are_shaped_differently() -> None:
    """The horizontal form is the one to reach for when the footprint binds."""
    tall = counted_loop("rs")
    flat = counted_loop_horizontal("rs")
    assert tall.ticks == flat.ticks == 8
    assert tall.ticks_per_value == flat.ticks_per_value == 8.0
    assert tall.size == (2, 4)
    assert flat.size == (4, 2)


def test_unrolling_approaches_four_ticks_per_value_from_above() -> None:
    """2k + 4 per lap with k = 2v, measured — not the 4v + 3 first derived."""
    assert unrolled(1).ticks == 8
    assert unrolled(2).ticks == 12
    assert unrolled(4).ticks == 20
    assert unrolled(8).ticks == 36
    assert unrolled(2).ticks_per_value == 6.0
    assert unrolled(4).ticks_per_value == 5.0
    assert unrolled(8).ticks_per_value == 4.5


def test_an_unrolled_loop_declares_the_count_it_requires() -> None:
    """Enter with a remainder and the tape over-rotates, uncaught by any later check."""
    g = unrolled(4)
    assert g.count_multiple == 4
    assert "REQUIRES BP % 4 == 0" in g.note


def test_a_gadget_becomes_an_atom_that_carries_its_ports() -> None:
    g = counted_loop("rs")
    atom = g.to_atom(7, 10, 20)
    assert atom.id == 7 and (atom.x, atom.y) == (10, 20)
    assert atom.entry is not None and atom.exits
    assert atom.port_cells()["in"] == (10, 20)
    assert atom.ticks == 8


@node_required
def test_the_measured_tick_cost_matches_the_declared_one() -> None:
    """Difference total run ticks across counts: the slope is ticks per lap."""
    from randomfun2026solvers.circuit import Circuit
    from randomfun2026solvers.littleman import Littleman

    def build(count: int, body: str) -> str:
        c = Circuit(30, 14)
        lit = f"`{count}`" if count > 9 else str(count)
        x, _ = c.run(1, 1, "@" + lit + "b")
        c.route((x, 1), E, [(12, 1), (12, 3)], (12, 3), E)
        ex, _ = c.counted_loop(13, 3, body)
        c.run(ex, 3, "H")
        rows = ["+" + "-" * 28 + "+"]
        rows += ["|" + r[1:29] + "|" for r in c.rows()[1:13]]
        rows.append("+" + "-" * 28 + "+")
        g = {
            (x2, y): ch
            for y, r in enumerate(rows)
            for x2, ch in enumerate(r)
            if ch != " "
        }
        base = len(rows)
        g[(4, base)] = "v"
        g[(4, base + 1)] = "v"
        for dy, r in enumerate(["+-+", "|O|", "+-+"]):
            for dx, ch in enumerate(r):
                g[(3 + dx, base + 2 + dy)] = ch
        mx = max(a for a, _ in g)
        my = max(b for _, b in g)
        return "\n".join(
            "".join(g.get((a, b), " ") for a in range(mx + 1)).rstrip()
            for b in range(my + 1)
        )

    lm = Littleman()
    for body, want in (("0s", 8.0), ("0s0s", 12.0)):
        t20 = lm.run(build(20, body), max_ticks=200_000).step
        t40 = lm.run(build(40, body), max_ticks=200_000).step
        assert (t40 - t20) / 20 == want, (body, t20, t40)
    assert counted_loop("rs").ticks == 8
    assert unrolled(2).ticks == 12
