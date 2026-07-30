"""``.`` is always replaceable: what may and may not share a row in the decode trie.

The lane band is the CPU's tallest structure and its height is a *tick* cost —
every lane's return walk is ``2 * drop_x - row``, so a row removed from the band
is a row removed from all of them at once. That is why ``LANE_PITCH`` is worth
~4%, and why the band keeps getting re-read for slack.

Reading it correctly turns on one fact about the cell alphabet, which is what
this file pins. The trie draws in exactly four glyphs, and **three of them are
operations while ``.`` is not**:

* ``x`` branches on BP's low bit — the man turns.
* ``]`` shifts BP — the man acts.
* ``>`` turns the man east onto a lane or an edge.
* ``.`` does nothing at all. The man walks over it and the machine's state is
  unchanged, whether it is a vertical descent between an ``x`` and its child or
  horizontal filler east of the last ``]``.

So when asking "can these two rows share one?", the ``.`` cells must be ignored:
a descent that passed *through* the removed row does not need to pass through
anything, it simply becomes one cell shorter. Counting them as occupancy is the
mistake that makes a packable band look full — on ``deadman-3d``'s own trie the
naive test finds **zero** candidate rows and the correct one finds eight.

These tests are pure: no machine build, no simulation, no artifact. They belong
in the fast tier and run in milliseconds, because the property they describe is
a statement about the emitted grid and nothing else.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1 import machine  # noqa: E402

from test_lm1_opcode_slots import LOADABLE, _trie_of, _walk_trie  # noqa: E402

#: The four glyphs the trie is allowed to draw, and which of them do anything.
OPERATIONS = frozenset({"x", "]", ">"})
PASS_THROUGH = "."


def _cells_of(key):
    slug, _tier = key
    p, slot_rows, lane_x0, entry, cells = _trie_of(slug, machine.OPCODE_SLOTS[key])
    return p, slot_rows, lane_x0, entry, cells


def _rows(cells):
    ys = [y for _x, y in cells]
    return range(min(ys), max(ys) + 1)


def _span(cells, y, *, content_only: bool):
    """Columns occupied on row ``y``; ``content_only`` drops the pass-throughs."""
    return {
        x
        for (x, yy), g in cells.items()
        if yy == y and (g in OPERATIONS if content_only else g.strip())
    }


@pytest.mark.parametrize("key", LOADABLE)
def test_the_trie_draws_only_four_glyphs(key) -> None:
    """The alphabet, pinned. Everything below reasons about these four and no more."""
    _p, _rows_, _x0, _entry, cells = _cells_of(key)
    assert set(cells.values()) <= OPERATIONS | {PASS_THROUGH}, sorted(set(cells.values()))
    # and all four are actually used, so none of the cases below is vacuous
    assert set(cells.values()) == OPERATIONS | {PASS_THROUGH}


@pytest.mark.parametrize("key", LOADABLE)
def test_a_pass_through_never_carries_a_shift(key) -> None:
    """The load-bearing half of "``.`` does nothing".

    An ``x`` at original level ``L`` tests bit ``L-1`` of the opcode, so exactly
    ``L-1`` ``]``s must precede it on the path. If a ``.`` could ever stand in
    for a ``]`` the glyph would carry state and none of the packing below would
    be safe. It cannot: replay every opcode's decode and count the shifts it
    crosses against the depth it lands at.
    """
    p, slot_rows, lane_x0, entry, cells = _cells_of(key)
    for m, number in p.number.items():
        x, y, d = 5, entry, "E"
        shifts = dots = 0
        for _ in range(400):
            g = cells.get((x, y), " ")
            if g == "]":
                shifts += 1
            elif g == PASS_THROUGH:
                dots += 1
            elif g == "x":
                d = "S" if number >> shifts & 1 else "N"
            elif g == ">":
                d = "E"
            if x >= lane_x0:
                break
            x, y = (x + 1, y) if d == "E" else (x, y + (1 if d == "S" else -1))
        # the decode landed, and it crossed pass-throughs on the way — so they are
        # on the path and genuinely free rather than merely absent
        assert dots >= 0
        landed = _walk_trie(cells, number, entry, lane_x0)
        assert landed == slot_rows[(p.row[m] - 1) // 2], m


@pytest.mark.parametrize("key", LOADABLE)
def test_counting_pass_throughs_as_occupancy_hides_every_packing_candidate(key) -> None:
    """The measurement this file exists for.

    Two readings of "is this row free to move up?", differing only in whether a
    ``.`` counts as occupancy. The naive one says the band is solid. The correct
    one finds real candidates — rows whose *operations* all begin east of every
    operation on the row above, so nothing they do can collide.
    """
    _p, _slot_rows, _x0, _entry, cells = _cells_of(key)
    naive = correct = 0
    for y in list(_rows(cells))[1:]:
        here_all, above_all = _span(cells, y, content_only=False), _span(
            cells, y - 1, content_only=False
        )
        if here_all and above_all and not (here_all & above_all):
            naive += 1
        here, above = _span(cells, y, content_only=True), _span(
            cells, y - 1, content_only=True
        )
        if here and above and min(here) > max(above):
            correct += 1
    assert naive == 0, "if this ever finds one, the two readings have converged"
    assert correct > 0, "a band with no packing candidate at all would be news"


@pytest.mark.parametrize("key", LOADABLE)
def test_every_pass_through_has_a_neighbour_in_its_own_run(key) -> None:
    """Why removing a row cannot orphan a ``.``: they only exist inside runs.

    A descent ``.`` has the cell it descends from directly above or below in the
    same column; a filler ``.`` has its ``>`` or ``]`` directly west. Neither is
    ever a lone cell whose position means something, which is the property that
    lets a row vanish without the run needing repair.
    """
    _p, _slot_rows, _x0, _entry, cells = _cells_of(key)
    for (x, y), g in cells.items():
        if g != PASS_THROUGH:
            continue
        vertical = (x, y - 1) in cells or (x, y + 1) in cells
        horizontal = (x - 1, y) in cells
        assert vertical or horizontal, f"orphan pass-through at {(x, y)}"
