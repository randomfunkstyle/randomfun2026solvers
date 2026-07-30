"""``OPCODE_SLOTS``: relabel the trie's leaves, move no lane, shrink the drum —
and, because the slot *values* choose the tree's branches, reshape the decode.

The knob's whole claim is that it is *row-neutral* — a lane's row is its leaf
slot's rank under ``trim_dead``, so relabelling the slots leaves every row, drop
column and lane tick exactly where it was and changes only
``number = _bitrev(slot, k)``. If that claim ever stopped holding, the symptom
would be a silently retuned ``LANE_ORDER`` rather than a failure, so it is
checked here from both ends: the rows really are identical, and a map that would
reorder the lanes is refused.

Row-neutral is not shape-neutral, though, and the second half of this file is
about the difference. ``_uneven_trie`` splits the **slot space** at each dyadic
midpoint rather than the used slots, so the values decide every branch — which
is what the registry's dispatch tuning is for, and what makes a bad map decode
an opcode into the wrong lane with every pipe still bound.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1 import machine, programs  # noqa: E402
from randomfun2026solvers.lm1 import rom as rommod  # noqa: E402


def _seek_plan(slug: str, slots: dict[str, int] | None):
    """``build``'s own plan for a seek build, which is what the registry keys."""
    program = machine.seek_split(
        programs.load(slug), threshold=machine.SEEK_THRESHOLD, ops=machine.SEEK_OPS
    )
    order = list(machine.LANE_ORDER[slug])
    used = {op.mnemonic for op in program.ops_used}
    at = min(
        (order.index(c) for c in ("JMPF", "BRZ", "BRN") if c in order), default=len(order)
    )
    for new in ("JMPS", "BRZS", "BRNS"):
        if new in used and new not in order:
            order.insert(at, new)
            at += 1
    return program, machine.plan(program, middle_order=order, slots=slots)


#: Keys whose program is a checked-in one, which is what ``_seek_plan`` can
#: reach.  ``deadman-3d_hires``' program is assembled from a locally owned IWAD
#: at build time and exists nowhere in the tree, so the same two properties are
#: pinned for it in ``tests/test_deadman3d_hires.py``, behind that file's IWAD
#: skip.  Listed by exclusion rather than inclusion so a new entry is checked
#: here by default and only leaves on purpose.
_WAD_ONLY = {("deadman-3d_hires", "taped")}
LOADABLE = sorted(set(machine.OPCODE_SLOTS) - _WAD_ONLY)


def test_every_registered_map_is_checked_somewhere() -> None:
    """The exclusion above is a routing decision, not an escape hatch."""
    assert set(machine.OPCODE_SLOTS) - set(LOADABLE) == _WAD_ONLY
    for slug, _tier in _WAD_ONLY:
        assert slug not in programs.available(), (
            f"{slug} has a checked-in program now; drop the exclusion"
        )


@pytest.mark.parametrize("key", LOADABLE)
def test_the_registered_map_moves_no_lane_and_only_moves_the_numbers(key) -> None:
    slug, _tier = key
    slots = machine.OPCODE_SLOTS[key]
    _, base = _seek_plan(slug, None)
    _, relabelled = _seek_plan(slug, slots)

    # Rank order — hence, under trim_dead, every lane row — is untouched.
    by_rank = sorted(base.number, key=lambda m: base.row[m])
    assert sorted(relabelled.number, key=lambda m: relabelled.row[m]) == by_rank
    # ...and the numbers really did move, or the entry is dead weight.
    assert relabelled.number != base.number


@pytest.mark.parametrize("key", LOADABLE)
def test_the_registered_map_is_a_strict_win_in_drum_cells(key) -> None:
    """Drum cells were the knob's original and for a while its only objective, and
    a map must still beat the default on them — a registered map that is worse in
    the drum *and* not paying for it elsewhere is dead weight. It is no longer the
    only objective (dispatch ticks are the other; see the registry), so this is a
    floor against the default rather than a claim that the map minimises it."""
    slug, _tier = key
    program, base = _seek_plan(slug, None)
    _, relabelled = _seek_plan(slug, machine.OPCODE_SLOTS[key])

    def cells(p) -> int:
        return sum(len(rommod.token_cells(w)) for w in machine.rom_words(program, p))

    assert cells(relabelled) < cells(base)


def test_a_map_that_reorders_the_lanes_is_refused() -> None:
    """The failure this guards is silent: swapping two lanes' slots swaps their
    rows, which is a ``LANE_ORDER`` change wearing a ROM knob's clothes."""
    key = ("deadman-3d", "taped")
    slots = dict(machine.OPCODE_SLOTS[key])
    slots["LD"], slots["ST"] = slots["ST"], slots["LD"]
    with pytest.raises(machine.MachineError, match="north-to-south order"):
        _seek_plan("deadman-3d", slots)


def test_a_map_missing_a_used_opcode_is_refused_but_extra_names_are_not() -> None:
    """``seek_split`` decides whether ``JMPS`` exists at all, from a threshold the
    registry cannot evaluate, so one map has to serve ``seek`` both ways."""
    key = ("deadman-3d", "taped")
    slots = dict(machine.OPCODE_SLOTS[key])
    short = {m: s for m, s in slots.items() if m != "LD"}
    with pytest.raises(machine.MachineError, match="does not name the used opcodes"):
        _seek_plan("deadman-3d", short)

    program = programs.load("deadman-3d")  # no seek split: no JMPS in this program
    assert "JMPS" not in {op.mnemonic for op in program.ops_used}
    order = list(machine.LANE_ORDER["deadman-3d"])
    p = machine.plan(program, middle_order=order, slots=slots)
    assert "JMPS" not in p.number


def test_two_lanes_may_not_share_a_slot() -> None:
    key = ("deadman-3d", "taped")
    slots = dict(machine.OPCODE_SLOTS[key])
    slots["LD"] = slots["ST"]
    with pytest.raises(machine.MachineError, match="one slot twice"):
        _seek_plan("deadman-3d", slots)


def test_the_knob_is_refused_where_it_would_not_be_row_neutral() -> None:
    """Untrimmed, a lane sits at ``2 * slot + 1``, so relabelling *moves* it."""
    with pytest.raises(machine.MachineError, match="trim_dead"):
        machine.build(
            programs.load("brackets"),
            tape_n=machine.TAPE_SIZE["brackets"],
            trim_dead=False,
            opcode_slots={"LDI": 0},
        )


# ── the map also shapes the decode trie, so the trie has to be walked ────────
#
# New tests rather than assertions bolted onto the ones above: these are a
# different claim about the same registry — that the tree *routes*, and that its
# rows are packed — and each wants to be able to fail on its own.

_DIRS = {"E": (1, 0), "W": (-1, 0), "N": (0, -1), "S": (0, 1)}
_CW = {"E": "S", "S": "W", "W": "N", "N": "E"}
_CCW = {"E": "N", "N": "W", "W": "S", "S": "E"}


def _trie_of(slug: str, slots: dict[str, int], *, pitch: int = 2, straight: bool = False):
    """The trie the generator emits for ``slots``, in interior coordinates.

    ``pitch``/``straight`` mirror :func:`machine.build_cpu`'s ``lane_pitch`` and
    :data:`machine.STRAIGHT_TRIE`. At the default pitch of two the pair is inert —
    a ``d`` needs its up child packed onto its own row and there is no such pair
    two rows apart — which is what makes the flag byte-identical for every machine
    that does not stagger.
    """
    _, p = _seek_plan(slug, slots)
    used = sorted((p.row[m] - 1) // 2 for m in p.number)
    rank = {s: i for i, s in enumerate(used)}
    if pitch == 1:
        gaps = machine._uneven_gaps(p.k, used, straight)
        at = [1]
        for i in range(len(used) - 1):
            at.append(at[-1] + (2 if i in gaps else 1))
        slot_rows = {s: at[rank[s]] for s in used}
    else:
        slot_rows = {s: 1 + 2 * rank[s] for s in used}
    lane_x0 = 4 + 2 * p.k
    entry, cells = machine._uneven_trie(p.k, slot_rows, lane_x0, straight)
    return p, slot_rows, lane_x0, entry, cells


def _walk_trie(cells, bp: int, entry_row: int, lane_x0: int) -> int:
    """Replay a decode from the fetch cell; returns the row it enters a lane on."""
    x, y, d = 5, entry_row, "E"
    for _ in range(400):
        g = cells.get((x, y), " ")
        if g == "x":
            d = _CW[d] if (bp & 1) else _CCW[d]
        elif g == "d":
            # ``SPEC.md``: turn clockwise if BP > 0, **else go straight**. The
            # straight case is the whole point — it is how a node can sit on its
            # own up child's row instead of demanding one above it.
            d = _CW[d] if bp > 0 else d
        elif g == "]":
            bp >>= 1
        elif g == ">":
            d = "E"
        elif g == "<":
            d = "W"
        elif g == "^":
            d = "N"
        elif g in "vV":
            d = "S"
        elif g not in ". ":
            raise AssertionError(f"decode ran onto {g!r} at {(x, y)}")
        dx, dy = _DIRS[d]
        x, y = x + dx, y + dy
        if x >= lane_x0:
            return y
    raise AssertionError("decode never reached a lane")


@pytest.mark.parametrize("key", LOADABLE)
def test_every_opcode_number_decodes_to_its_own_lane(key) -> None:
    """The registry picks the slot *values*, and :func:`machine._uneven_trie`
    splits the slot **space** at each dyadic midpoint — so the values choose every
    branch of the tree, not just the labels on its leaves.

    That makes a bad map fail the worst way available here: the grid builds, every
    pipe binds, and one opcode walks into the *wrong lane* and executes it. So walk
    the tree the generator actually emits and check every landing. Pure — no
    machine build and no simulation, so it belongs in the fast tier.
    """
    slug, _tier = key
    p, slot_rows, lane_x0, entry, cells = _trie_of(slug, machine.OPCODE_SLOTS[key])
    for m, number in p.number.items():
        landed = _walk_trie(cells, number, entry, lane_x0)
        want = slot_rows[(p.row[m] - 1) // 2]
        assert landed == want, f"{m} (number {number}) decodes to row {landed}, not {want}"


@pytest.mark.parametrize("key", LOADABLE)
def test_the_trie_gives_every_node_its_own_row(key) -> None:
    """Why the lane band's pitch of 2 is a floor rather than slack.

    An ``x`` fans out perpendicular on **both** sides, so every internal node has
    one child's subtree strictly above it and the other's strictly below. That is
    the in-order condition, and it forces all ``2n - 1`` nodes onto distinct rows:
    any two of them lie in opposite halves of their common ancestor. The band is
    therefore exactly ``n`` lane rows interleaved with ``n - 1`` ``x`` rows with
    nothing left over — the "gap" rows *are* the decoder. Pinned because that
    height has been read as spare before.
    """
    slug, _tier = key
    p, slot_rows, _lane_x0, _entry, cells = _trie_of(slug, machine.OPCODE_SLOTS[key])

    n = len(slot_rows)
    lane_rows = set(slot_rows.values())
    x_rows = [y for (_x, y), g in cells.items() if g == "x"]
    assert len(x_rows) == n - 1, "a binary trie over n lanes branches n-1 times"
    assert len(set(x_rows)) == n - 1, "two `x` nodes may never share a row"
    assert not (set(x_rows) & lane_rows), "an `x` may not sit on a lane row"
    assert max(lane_rows) - min(lane_rows) + 1 == 2 * n - 1
