"""``OPCODE_SLOTS``: relabel the trie's leaves, move no lane, shrink the drum.

The knob's whole claim is that it is *row-neutral* — a lane's row is its leaf
slot's rank under ``trim_dead``, so relabelling the slots leaves every row, drop
column and lane tick exactly where it was and changes only
``number = _bitrev(slot, k)``. If that claim ever stopped holding, the symptom
would be a silently retuned ``LANE_ORDER`` rather than a failure, so it is
checked here from both ends: the rows really are identical, and a map that would
reorder the lanes is refused.
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
    """The knob exists for one number: cells in the drum's lap. A map that does
    not lower it is a retune of the trie for nothing."""
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
