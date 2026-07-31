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

    The whitelist is about the **trie's** cells, which is why it is still exactly
    two glyphs wide after :data:`machine.FETCH_FOLD` put an ``r`` and a ``b`` on
    this row. Those two are drawn by ``return:high``, not by the trie, and they
    are placed on cells the trie left empty (the ``b`` at column 2, which no node
    can reach because the root is the westmost at column 3 and ``_trie_columns``
    only moves east). Widening *this* list to admit them would be the wrong fix
    twice over: it would stop the assertion catching a trie node that wandered
    into the corridor, and it would say nothing about the glyphs it was widened
    for. What the fold actually needs proving is that the two approaches still run
    ``r``, ``b``, ``r`` in order and once each — see
    ``test_both_approaches_to_the_fetch_run_the_prologue_exactly_once``.
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


# ── FETCH_FOLD: the prologue on the approach, not on the fetch row ────────────
#
# The fold is two claims and they fail in different ways, so they are two cases.
#
# 1. The trie **translates** by the two columns the prologue hands back and is
#    otherwise the same tree. That is a statement about :func:`_trie_columns`,
#    and it is the whole of the payoff: ``lane_x0`` anchors the fetch, the trie,
#    every lane, every drop, the corridor and the riser at once.
# 2. Both approaches to the fetch still perform ``r``, ``b``, ``r`` — in that
#    order, once each. That is a statement about the emitted grid, and it is the
#    whole of the risk: a man who crosses both copies fetches twice and a man who
#    crosses neither decodes a stale opcode, and both diverge silently, deep in
#    the tour, with no binding error and no collision.

#: The lever stack the fold needs. It is registered on ``deadman-3d_hires``,
#: whose program is IWAD-derived and so unreachable from a test; ``deadman-3d``'s
#: checked-in seek program takes the same stack and draws the same shapes, and
#: passing the levers here changes no shipped grid.
FOLD_LEVERS = dict(
    trim_dead=True,
    lane_pitch=1,
    straight_trie=True,
    high_collector=True,
    tight_trie_cols=True,
)

_DIRS = {"E": (1, 0), "W": (-1, 0), "N": (0, -1), "S": (0, 1)}


def _lane_x0_at(k, slot_rows, fetch_w):
    """``build_cpu``'s rule, at a given prologue width."""
    _e, elevel, tree, root = machine._trie_shape(k, slot_rows, True, True)
    cols = machine._trie_columns(tree, root, elevel, True, fetch_w)
    return max(cols.values(), default=fetch_w) + 1


def _fold_cpu(fold: bool):
    """``deadman-3d``'s seek CPU under the fold's lever stack. Pure, milliseconds."""
    from randomfun2026solvers.lm1 import programs

    program = machine.seek_split(
        programs.load("deadman-3d"), threshold=machine.SEEK_THRESHOLD, ops=machine.SEEK_OPS
    )
    order = list(machine.LANE_ORDER["deadman-3d"])
    used = {op.mnemonic for op in program.ops_used}
    at = min((order.index(c) for c in ("JMPF", "BRZ", "BRN") if c in order), default=len(order))
    for new in ("JMPS", "BRZS", "BRNS"):
        if new in used and new not in order:
            order.insert(at, new)
            at += 1
    p = machine.plan(program, middle_order=order)
    return machine.build_cpu(
        program,
        p,
        mem_pad=22,
        seek=True,
        drain_unit_bits=machine.DRAIN_UNIT_BITS.get("deadman-3d", 0),
        fetch_fold=fold,
        **FOLD_LEVERS,
    )


def _approach(cells, x, y, d, limit=2000):
    """Walk to the decode's first branch; return the operations the man performed.

    Only the glyphs that *do* something are recorded — ``r``, ``b`` and ``]`` —
    with a final ``"x"`` for the branch that ends the approach. Turns steer and
    ``.`` does nothing, so neither belongs in the answer; anything else is a man
    walking somewhere the fold did not intend and is an outright failure.
    """
    ops: list[str] = []
    for _ in range(limit):
        g = cells.get((x, y), " ")
        if g in ("x", "d"):
            return [*ops, "x"]
        if g in "rb]":
            ops.append(g)
        elif g == ">":
            d = "E"
        elif g == "<":
            d = "W"
        elif g == "^":
            d = "N"
        elif g in "vV":
            d = "S"
        elif g not in ". ":
            raise AssertionError(f"the approach ran onto {g!r} at {(x, y)}")
        dx, dy = _DIRS[d]
        x, y = x + dx, y + dy
    raise AssertionError("the approach never reached the decode")


def test_the_fold_translates_the_trie_west_and_reshapes_nothing() -> None:
    """Two columns, and the *same* tree — which is why the saving is real.

    The prologue's width is an additive term in the root's column and
    ``_trie_columns`` places every other node relative to the root, so dropping
    ``>rbr`` to ``>r`` moves the whole decode two columns west without changing
    one edge, one shift or one row. Assert exactly that: the cell maps agree
    under a two-column shift, and ``lane_x0`` — the number the fold is actually
    buying — comes down by the same two.
    """
    slug, _tier = STRAIGHT_KEY
    p, slot_rows, _c = _corridor_rows(slug, machine.OPCODE_SLOTS[STRAIGHT_KEY])
    wide, tight = _lane_x0_at(p.k, slot_rows, 4), _lane_x0_at(p.k, slot_rows, 2)
    assert wide - tight == 2, (wide, tight)
    kw = dict(inline_far=True, tight_cols=True)
    entry4, cells4 = machine._uneven_trie(p.k, slot_rows, wide, True, fetch_w=4, **kw)
    entry2, cells2 = machine._uneven_trie(p.k, slot_rows, tight, True, fetch_w=2, **kw)
    assert entry4 == entry2, "the fetch row is set by the band, not by the prologue"
    assert {(x - 2, y): g for (x, y), g in cells4.items()} == cells2


def test_the_fold_still_decodes_every_opcode_to_its_own_lane() -> None:
    """The mis-decode is silent, so the decoder is walked rather than trusted."""
    slug, _tier = STRAIGHT_KEY
    p, slot_rows, _c = _corridor_rows(slug, machine.OPCODE_SLOTS[STRAIGHT_KEY])
    lane_x0 = _lane_x0_at(p.k, slot_rows, 2)
    entry, cells = machine._uneven_trie(
        p.k, slot_rows, lane_x0, True, inline_far=True, tight_cols=True, fetch_w=2
    )
    for m, number in p.number.items():
        landed = _walk_trie(cells, number, entry, lane_x0, fetch_w=2)
        assert landed == slot_rows[(p.row[m] - 1) // 2], m


def test_both_approaches_to_the_fetch_run_the_prologue_exactly_once() -> None:
    """The risk the fold is built on, checked on the cells the generator emits.

    ``HIGH_COLLECTOR`` splits the return in two — west along the corridor, or up
    the riser — and they share **no** cell before the fetch's own ``>``. So the
    prologue is drawn twice, and the thing that has to be true is that each man
    meets exactly one copy: one opcode ``r``, one ``b``, then the operand ``r``,
    then the branch. Two copies on one path would fetch twice; none would decode
    the previous instruction's opcode.

    The ``]``s are filtered out of the comparison and checked separately, because
    they are the one glyph that is allowed to differ between the two approaches:
    the corridor crosses the trie's own legs and the riser does not. They are
    harmless exactly while they fall **before** the ``b``, which overwrites the BP
    they shifted — the same argument ``build_cpu``'s corridor whitelist makes, and
    the reason it does not have to know they are there.
    """
    cpu = _fold_cpu(True)
    cells, centre = cpu.cells, cpu.centre
    hi_row = centre - 1
    assert [cells.get((x, centre)) for x in (1, 2)] == [">", "r"]
    assert cells.get((3, centre)) in ("x", "d"), "the root did not take the freed column"

    collector = max(y for (x, y), g in cells.items() if x == 1 and g == "^")
    east = max(x for (x, y), g in cells.items() if y == hi_row and g == "<")
    riser = _approach(cells, 1, collector, "N")
    corridor = _approach(cells, east, hi_row, "W")

    for name, ops in (("riser", riser), ("corridor", corridor)):
        assert [o for o in ops if o != "]"] == ["r", "b", "r", "x"], (name, ops)
        after = ops[ops.index("b") :]
        assert "]" not in after, (name, ops)
    assert "]" in corridor, "vacuous: the corridor crosses no trie leg at all"


def test_the_fold_is_inert_when_it_is_off() -> None:
    """The lever has to leave the ``>rbr`` machine alone glyph for glyph."""
    plain = _fold_cpu(False)
    assert [plain.cells.get((x, plain.centre)) for x in range(1, 6)] == [
        ">", "r", "b", "r", "x",
    ]
    assert (2, plain.centre, "r", "rom") in plain.pipe_glyphs
    assert (4, plain.centre, "r", "rom") in plain.pipe_glyphs


def test_the_fold_refuses_the_geometries_it_cannot_translate() -> None:
    """It buys ``lane_x0``, and only the per-node trie prices ``lane_x0`` off the
    prologue. Under the uniform ``3 + 2 * level`` rule the tree is anchored to the
    level instead, so the fold would move the fetch and pay for nothing."""
    levers = dict(FOLD_LEVERS, tight_trie_cols=False)
    with pytest.raises(machine.MachineError, match="tight_trie_cols"):
        _fold_cpu_with(levers)


def _fold_cpu_with(levers, *, fold=True, tuck=False):
    from randomfun2026solvers.lm1 import programs

    program = machine.seek_split(
        programs.load("deadman-3d"), threshold=machine.SEEK_THRESHOLD, ops=machine.SEEK_OPS
    )
    return machine.build_cpu(
        program, machine.plan(program), fetch_fold=fold, fetch_tuck=tuck, **levers
    )


def test_the_fold_names_only_the_slug_that_measured_it() -> None:
    """Same rule as the two registries above: the key is the whole guarantee that
    every other machine's grid is byte-identical."""
    assert {slug for slug, _tier in machine.FETCH_FOLD} <= {"deadman-3d_hires"}


# ── FETCH_TUCK: the whole prologue east of the up-leg, and a shorter U-turn ───
#
# The tuck keeps the fold's two claims and adds a third that is easier to break
# than either: the corridor's ``b`` now runs **east** of the root's up-leg, so a
# ``]`` on the corridor row is no longer dead. It would shift a BP that has
# already been loaded, and the man would decode a right-shifted opcode — one
# more silent divergence with no binding error and no collision.


def _tuck_cpu():
    return _fold_cpu_with(dict(FOLD_LEVERS), tuck=True)


def test_the_tuck_empties_the_fetch_row_and_leaves_the_root_where_it_was() -> None:
    """``fetch_w`` is unchanged, so the tuck is free of ``lane_x0`` entirely.

    That is the reason it needs no pad re-sweep and no wall to move: the fold
    bought two columns and this buys none, it only shortens the *walk* over cells
    that were already drawn.
    """
    fold, tuck = _fold_cpu(True), _tuck_cpu()
    assert [tuck.cells.get((x, tuck.centre)) for x in (1, 2)] == [">", ">"]
    assert tuck.cells.get((3, tuck.centre)) in ("x", "d")
    assert tuck.centre == fold.centre, "the fetch row is set by the band"
    # no `r` left on the fetch row, and none claimed for the ROM pipe there
    assert not [g for g in tuck.pipe_glyphs if g[:2] == (2, tuck.centre)]


def test_the_tuck_keeps_both_approaches_at_exactly_one_prologue() -> None:
    """The fold's invariant, restated on the tuck's two paths.

    Same failure modes, same walk: one opcode ``r``, one ``b``, the operand
    ``r``, then the branch. Both paths, once each.
    """
    cpu = _tuck_cpu()
    cells, centre = cpu.cells, cpu.centre
    hi_row = centre - 1
    collector = max(y for (x, y), g in cells.items() if x == 1 and g == "^")
    east = max(x for (x, y), g in cells.items() if y == hi_row and g == "<")
    riser = _approach(cells, 1, collector, "N")
    corridor = _approach(cells, east, hi_row, "W")
    for name, ops in (("riser", riser), ("corridor", corridor)):
        assert [o for o in ops if o != "]"] == ["r", "b", "r", "x"], (name, ops)


def test_the_tuck_leaves_no_shift_at_all_on_the_corridor_row() -> None:
    """The claim the fold could not make, and the one the tuck lives on.

    Under the fold the corridor's ``b`` sat at column 2, west of every trie leg
    it crossed, so a ``]`` on the row shifted a BP that ``b`` was about to
    overwrite. The tuck moves ``b`` **east** of the up-leg, and that argument
    dies with it — so the shifts are moved off the row instead, which
    ``_uneven_trie``'s ``avoid_hi`` does by putting a leg's ``]`` on a later cell
    of the same leg. Only the *count* of shifts between a node and its child is
    semantics; where on the leg they stand is not.

    Assert the strong form: not one ``]`` anywhere on the corridor row.
    """
    cpu = _tuck_cpu()
    hi_row = cpu.centre - 1
    on_row = sorted((x, g) for (x, y), g in cpu.cells.items() if y == hi_row)
    assert on_row, "vacuous: nothing on the corridor row"
    assert not [x for x, g in on_row if g == "]"], on_row
    # and it is not vacuous the other way either: the fold *does* put one there
    fold = _fold_cpu(True)
    assert [x for (x, y), g in fold.cells.items() if y == fold.centre - 1 and g == "]"]


def test_the_tuck_moves_a_leg_shift_without_changing_how_many() -> None:
    """``avoid_hi`` is a re-ordering, not a re-count — checked per column."""
    slug, _tier = STRAIGHT_KEY
    p, slot_rows, _c = _corridor_rows(slug, machine.OPCODE_SLOTS[STRAIGHT_KEY])
    lane_x0 = _lane_x0_at(p.k, slot_rows, 2)
    kw = dict(inline_far=True, tight_cols=True, fetch_w=2)
    entry, plain = machine._uneven_trie(p.k, slot_rows, lane_x0, True, **kw)
    entry2, moved = machine._uneven_trie(p.k, slot_rows, lane_x0, True, avoid_hi=True, **kw)
    assert entry == entry2 and plain.keys() == moved.keys()
    assert plain != moved, "vacuous: nothing moved"
    per_col = lambda cs: sorted(  # noqa: E731
        (x, sum(1 for (xx, _y), g in cs.items() if xx == x and g == "]"))
        for x in {x for x, _ in cs}
    )
    assert per_col(plain) == per_col(moved)


def test_the_tuck_still_decodes_every_opcode_to_its_own_lane() -> None:
    """The re-ordered legs are walked, not trusted — the mis-decode is silent."""
    slug, _tier = STRAIGHT_KEY
    p, slot_rows, _c = _corridor_rows(slug, machine.OPCODE_SLOTS[STRAIGHT_KEY])
    lane_x0 = _lane_x0_at(p.k, slot_rows, 2)
    entry, cells = machine._uneven_trie(
        p.k, slot_rows, lane_x0, True,
        inline_far=True, tight_cols=True, fetch_w=2, avoid_hi=True,
    )
    for m, number in p.number.items():
        landed = _walk_trie(cells, number, entry, lane_x0, fetch_w=2)
        assert landed == slot_rows[(p.row[m] - 1) // 2], m


def test_the_tuck_refuses_a_layout_with_no_corridor_to_shorten() -> None:
    """It shortens the westbound man's U-turn, and without ``HIGH_COLLECTOR``
    there is no westbound man. It is a delta on the fold for the same reason."""
    with pytest.raises(machine.MachineError, match="fetch_tuck"):
        _fold_cpu_with(dict(FOLD_LEVERS, high_collector=False), tuck=True)
    with pytest.raises(machine.MachineError, match="fetch_tuck"):
        _fold_cpu_with(dict(FOLD_LEVERS), fold=False, tuck=True)


def test_the_tuck_names_only_the_slug_that_measured_it() -> None:
    assert {slug for slug, _tier in machine.FETCH_TUCK} <= {"deadman-3d_hires"}
