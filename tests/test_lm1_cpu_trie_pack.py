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

from test_lm1_opcode_slots import (  # noqa: E402
    LOADABLE,
    _seek_plan,
    _trie_of,
    _walk_trie,
)

#: The four glyphs the trie is allowed to draw, and which of them do anything.
#: ``d`` joins them only under :data:`machine.STRAIGHT_TRIE`, which the tests at
#: the foot of this file cover on their own; the cases above it are the ``x``-only
#: trie every other machine still gets.
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


# ── STRAIGHT_TRIE: the row floor the four glyphs above cannot get under ──────
#
# Everything before this point reasons about a trie drawn in ``x``, and reaches
# the same floor twice: ``x`` **always turns**, so a node whose up half is one
# lane cannot share that lane's row — the lane's entry ``>`` would land on the
# node's own cell. That is one blank row per such node, ten of them on
# ``deadman-3d_hires``, and it is what ``_uneven_gaps`` exists to count.
#
# ``SPEC.md`` gives ``d`` the property ``x`` refuses: *turn clockwise if BP > 0,
# else go straight*. These cases pin that the substitution is (a) inert where the
# builder does not make it, (b) worth a row every time it does, and (c) still
# routes every opcode into its own lane — which is the failure ``d`` could
# otherwise cause with every pipe bound and no error anywhere.

#: A checked-in program, so all of this stays build-free and in the fast tier.
#: ``deadman-3d_hires``' own trie is IWAD-derived and is covered in
#: ``tests/test_deadman3d_hires.py`` behind that file's skip.
STRAIGHT_KEY = ("deadman-3d", "taped")


def _straight_pair(pitch: int = 1):
    """The same plan's trie with and without the substitution."""
    slug, _tier = STRAIGHT_KEY
    slots = machine.OPCODE_SLOTS[STRAIGHT_KEY]
    return (
        _trie_of(slug, slots, pitch=pitch, straight=False),
        _trie_of(slug, slots, pitch=pitch, straight=True),
    )


def test_the_substitution_is_inert_at_a_pitch_of_two() -> None:
    """Why the flag cannot change any machine that does not stagger its band.

    A ``d`` has to sit on its up child's *own* row, and at a pitch of two there is
    always a spare row between the two — so the builder never makes the swap and
    the emitted cells are identical, glyph for glyph. That is what lets
    :data:`machine.STRAIGHT_TRIE` be a registry key rather than a fork.
    """
    (_p, rows_x, _x0, entry_x, cells_x), (_q, rows_d, _x1, entry_d, cells_d) = (
        _straight_pair(pitch=2)
    )
    assert cells_x == cells_d and entry_x == entry_d and rows_x == rows_d
    assert "d" not in set(cells_d.values())


def test_going_straight_buys_exactly_the_rows_x_was_costing() -> None:
    """The saving, counted rather than asserted: one row per row-forcing node.

    ``_uneven_gaps`` reports the ranks that need a blank row after them. Under the
    contiguous-with-a-pinned-tail packing every single-lane up half sits at its
    interval's base, which is precisely the case ``d`` decides correctly — so the
    whole gap set goes and the band contracts by its size.
    """
    slug, _tier = STRAIGHT_KEY
    _, p = _seek_plan(slug, machine.OPCODE_SLOTS[STRAIGHT_KEY])
    used = sorted((p.row[m] - 1) // 2 for m in p.number)
    gaps_x = machine._uneven_gaps(p.k, used, False)
    gaps_d = machine._uneven_gaps(p.k, used, True)
    assert gaps_x, "vacuous: this plan needs no gap rows even with `x`"
    assert gaps_d < gaps_x, "the substitution must not invent a gap"

    (_a, rows_x, _b, _c, _d0), (_e, rows_d, _f, _g, _h) = _straight_pair(pitch=1)
    span_x = max(rows_x.values()) - min(rows_x.values()) + 1
    span_d = max(rows_d.values()) - min(rows_d.values()) + 1
    assert span_x - span_d == len(gaps_x) - len(gaps_d)


def test_every_opcode_still_decodes_to_its_own_lane_through_a_d() -> None:
    """The one that matters. ``d`` reads ``BP > 0``; ``x`` reads BP's low bit.

    They are the same test only where the up half is a single lane at the
    interval's base, because after the ``L - 1`` shifts a level-``L`` node owes,
    ``BP`` **is** the slot's offset inside that node's interval. Get that wrong and
    the grid binds, renders, and sends an opcode to the wrong lane — so replay
    every one of them.
    """
    slug, _tier = STRAIGHT_KEY
    p, slot_rows, lane_x0, entry, cells = _trie_of(
        slug, machine.OPCODE_SLOTS[STRAIGHT_KEY], pitch=1, straight=True
    )
    assert "d" in set(cells.values()), "nothing was substituted; the case is vacuous"
    assert set(cells.values()) <= OPERATIONS | {PASS_THROUGH, "d"}
    for m, number in p.number.items():
        landed = _walk_trie(cells, number, entry, lane_x0)
        assert landed == slot_rows[(p.row[m] - 1) // 2], m


def test_a_d_only_ever_stands_on_a_lane_row() -> None:
    """The precondition, read off the grid rather than trusted from the code.

    A ``d`` on a blank row would send the man east along nothing when ``BP == 0``.
    Every ``d`` must therefore be on a row some lane occupies — and the replay
    above proves it is the *right* lane by arriving there.
    """
    slug, _tier = STRAIGHT_KEY
    _p, slot_rows, _x0, _entry, cells = _trie_of(
        slug, machine.OPCODE_SLOTS[STRAIGHT_KEY], pitch=1, straight=True
    )
    lane_rows = set(slot_rows.values())
    ds = [(x, y) for (x, y), g in cells.items() if g == "d"]
    assert ds, "vacuous"
    for x, y in ds:
        assert y in lane_rows, f"`d` at {(x, y)} is on a row no lane occupies"


def test_the_registry_names_only_the_slug_that_measured_it() -> None:
    """``STRAIGHT_TRIE`` is opt-in, so every other machine stays byte-identical.

    This used to name the men-v3 tier alone. The taped tier now takes it too
    (-4.643% on the 21-round tour, once its :data:`machine.SQUASH_BAND` /
    :data:`machine.ROM_TOUCH_DROP` pair is re-derived — at the shipped pair it does
    not build), so the tier is no longer the invariant.

    **The invariant that has not moved is the slug.** What this test protects is
    ``deadman-3d``, whose three grids are hash-pinned and which shares every CPU
    registry with hires; asserting the exact tier set only made an intended,
    measured change look like a regression. Assert the property the pin exists
    for — nothing outside ``deadman-3d_hires`` is named — and let the tiers move.
    """
    assert {slug for slug, _tier in machine.STRAIGHT_TRIE} == {"deadman-3d_hires"}


# ── HIGH_COLLECTOR / TIGHT_TRIE_COLS: the corridor row, and per-node columns ──
#
# Both re-shape the decode, and re-shaping the decode is the change that binds
# every pipe, renders, and routes an opcode into the wrong lane. So both are
# pinned the only way that catches it: replay every opcode number through the
# cells the generator actually emits and check where it lands.


def _corridor_rows(slug, slots):
    """Pitch-1 slot rows with one blank opened above the root's lane.

    That is what ``build_cpu`` does under :data:`machine.HIGH_COLLECTOR`: the root
    splits at ``1 << (k-1)`` and its ``x`` lands on the last up-half lane's row, so
    the corridor goes between the two ranks below it.
    """
    _, p = _seek_plan(slug, slots)
    used = sorted((p.row[m] - 1) // 2 for m in p.number)
    rank = {s: i for i, s in enumerate(used)}
    gaps = machine._uneven_gaps(p.k, used, True)
    at = [1]
    for i in range(len(used) - 1):
        at.append(at[-1] + (2 if i in gaps else 1))
    n_up = sum(1 for s in used if s < (1 << (p.k - 1)))
    at = [r + (1 if i >= n_up - 1 else 0) for i, r in enumerate(at)]
    return p, {s: at[rank[s]] for s in used}, at[n_up - 1] - 1


def _tight_lane_x0(k, slot_rows, tight):
    """``build_cpu``'s rule: the band starts one column east of the deepest node."""
    if not tight:
        return 4 + 2 * k
    _e, elevel, tree, root = machine._trie_shape(k, slot_rows, True, True)
    return max(machine._trie_columns(tree, root, elevel, True).values(), default=4) + 1


@pytest.mark.parametrize("tight", [False, True])
def test_the_corridor_row_carries_nothing_that_turns(tight) -> None:
    """What makes the second collector legal, checked on the emitted cells.

    The corridor sweeps west across the trie's own columns. A ``.`` there is fine —
    a westbound man keeps his heading — and so is a ``]``, which does not turn and
    whose BP the fetch's ``b`` overwrites before anything reads it. An ``x``, a
    ``d`` or a ``>`` would steer the returning man out of the corridor, silently.
    """
    slug, _tier = STRAIGHT_KEY
    p, slot_rows, corridor = _corridor_rows(slug, machine.OPCODE_SLOTS[STRAIGHT_KEY])
    lane_x0 = _tight_lane_x0(p.k, slot_rows, tight)
    _entry, cells = machine._uneven_trie(
        p.k, slot_rows, lane_x0, True, inline_far=True, tight_cols=tight
    )
    assert corridor not in set(slot_rows.values()), "vacuous: no row was opened"
    on_row = sorted((x, g) for (x, y), g in cells.items() if y == corridor)
    assert on_row, "vacuous: the trie does not cross the corridor at all"
    assert all(g in (PASS_THROUGH, "]") for _x, g in on_row), on_row


@pytest.mark.parametrize("tight", [False, True])
@pytest.mark.parametrize("corridor", [False, True])
def test_every_opcode_still_decodes_to_its_own_lane(corridor, tight) -> None:
    """The one failure this pair can cause that nothing else would report."""
    slug, _tier = STRAIGHT_KEY
    slots = machine.OPCODE_SLOTS[STRAIGHT_KEY]
    if corridor:
        p, slot_rows, _c = _corridor_rows(slug, slots)
    else:
        p, slot_rows, _x0, _e, _cells = _trie_of(slug, slots, pitch=1, straight=True)
    lane_x0 = _tight_lane_x0(p.k, slot_rows, tight)
    entry, cells = machine._uneven_trie(
        p.k, slot_rows, lane_x0, True, inline_far=corridor, tight_cols=tight
    )
    for m, number in p.number.items():
        landed = _walk_trie(cells, number, entry, lane_x0)
        assert landed == slot_rows[(p.row[m] - 1) // 2], m


def test_per_node_columns_are_narrower_and_can_never_be_wider() -> None:
    """``TIGHT_TRIE_COLS`` is only worth having if it moves ``lane_x0`` west.

    It also may never move it *east*: the per-node rule spends ``1 + max(0, shifts
    - leg slack)`` on an edge, which is at most the ``2 * d`` the level rule
    spends, so the inequality is structural rather than lucky. Assert the
    direction, not the number — the number is a property of this program's tree.
    """
    slug, _tier = STRAIGHT_KEY
    p, slot_rows, _c = _corridor_rows(slug, machine.OPCODE_SLOTS[STRAIGHT_KEY])
    assert _tight_lane_x0(p.k, slot_rows, True) < _tight_lane_x0(p.k, slot_rows, False)


def test_the_two_new_registries_name_only_the_slug_that_measured_them() -> None:
    """Both tiers of hires now, and nothing else — see the note above.

    ``HIGH_COLLECTOR`` reaching the taped tier needed one more thing than a key:
    taped's :data:`machine.OPCODE_SLOTS` map left slot 15 unused, which parked a
    turning ``x`` on the very row the corridor opens, and the build refused. The
    map was repaired (``MODI`` 13 -> 14, ``NEG`` 14 -> 15) rather than the lever
    weakened, which is why the corridor assertion in ``build_cpu`` is still a
    whitelist and still fires.
    """
    for reg in (machine.HIGH_COLLECTOR, machine.TIGHT_TRIE_COLS):
        assert {slug for slug, _tier in reg} == {"deadman-3d_hires"}
