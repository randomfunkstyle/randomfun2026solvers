"""Dead-lane removal and the top return bus (``lm1/machine.py``).

Two opt-in CPU lane-band features, both default-off per slug:

* ``trim_dead`` (:data:`TRIM_DEAD_LANES`): unused decode-trie leaves contribute
  no rows; the trie is pruned and re-routed (:func:`machine._uneven_trie`) while
  the opcode numbering — and so the ROM image — stays byte-identical.
* ``top_bus`` (:data:`TOP_RETURN_BUS`): a second return bus above the band;
  each simple lane returns over whichever bus is cheaper.

The dragon both features must slay is pipe binding: moving or removing rows
changes every ``r``/``s`` Manhattan distance in the band (ARCH.md §7.1). Every
build below runs ``check_bindings`` and ``_check_pipe_count`` inside
``_assemble``, and the engine runs prove the machine still solves the task.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import optimize  # noqa: E402
from randomfun2026solvers.lm1 import machine, programs  # noqa: E402

slow = pytest.mark.slow

#: (trim_dead, top_bus) in every non-default combination.
COMBOS = ((True, False), (False, True), (True, True))


def test_both_registries_default_empty() -> None:
    """Every checked-in machine stays byte-identical unless its slug opts in.

    deadman-3d opted into the trim once the taped tier landed (band 63 -> 41,
    -13.6% on the gate); everything else stays flag-off.
    """
    assert machine.TRIM_DEAD_LANES == {"deadman-3d"}
    assert machine.TOP_RETURN_BUS == set()


def test_flags_off_is_byte_identical_to_the_checked_in_grid() -> None:
    m = machine.build_for("brackets", trim_dead=False, top_bus=False)
    pinned = (REPO / "tasks" / "solutions" / "brackets_cpu.man").read_text(encoding="utf-8")
    assert "\n".join(m.rows) + "\n" == pinned


def test_trim_removes_two_rows_per_dead_leaf_and_keeps_the_rom_image() -> None:
    """brackets uses 15 of 16 leaves: one dead leaf = two rows off the band.

    The trie costs ``k - 1`` extra columns (two per level for the horizontal
    ``]``s), so the *machine* height shrinks by exactly ``2 * dead`` while the
    ROM words — the opcode numbering — do not move at all.
    """
    prog = programs.load("brackets")
    p = machine.plan(prog, middle_order=machine.LANE_ORDER.get("brackets"))
    dead = p.lanes - len(p.number)
    assert dead == 1
    base = machine.build_for("brackets")
    trim = machine.build_for("brackets", trim_dead=True)
    assert trim.height == base.height - 2 * dead
    assert machine.rom_words(prog, p) == machine.rom_words(prog, trim.plan)


def test_top_bus_costs_exactly_one_row() -> None:
    base = machine.build_for("brackets")
    top = machine.build_for("brackets", top_bus=True)
    assert top.height == base.height + 1
    # The bus region exists and sits on the CPU's first interior row.
    tops = [k for k in top.regions if k.endswith("return:topbus")]
    assert tops, sorted(top.regions)


@pytest.mark.parametrize("trim_dead,top_bus", COMBOS)
def test_brackets_still_solves_every_public_case(trim_dead: bool, top_bus: bool) -> None:
    """The engine proof: identical verdicts, and never slower than it must be.

    ``check_bindings`` and the pipe count already ran inside the build; this run
    is the behavioural half — outputs must match the judge's on every case.
    """
    m = machine.build_for("brackets", trim_dead=trim_dead, top_bus=top_bus)
    res = optimize.verify(m.rows, "brackets")
    assert res.passed, [v for v in res.verdicts if not v.passed]


@slow
@pytest.mark.parametrize("trim_dead,top_bus", COMBOS)
def test_plotter_still_draws_every_public_case(trim_dead: bool, top_bus: bool) -> None:
    """A display slug: DSP bindings survive the moved rows too."""
    m = machine.build_for("plotter", trim_dead=trim_dead, top_bus=top_bus)
    res = optimize.verify(m.rows, "plotter")
    assert res.passed, [v for v in res.verdicts if not v.passed]


def test_uneven_trie_shift_counts_route_every_opcode_to_its_own_lane() -> None:
    """Walk the pruned trie cells like the engine would, for a hostile dead set.

    Non-contiguous dead slots force contracted chains on both sides; every used
    opcode number must reach exactly its own lane row with the right number of
    ``]`` shifts along the way.
    """
    k = 5
    used_slots = [0, 3, 4, 9, 17, 18, 19, 30]  # scattered: chains contract deep
    slot_rows = {s: 1 + 2 * i for i, s in enumerate(sorted(used_slots))}
    lane_x0 = 4 + 2 * k
    entry, cells = machine._uneven_trie(k, slot_rows, lane_x0)

    def walk(number: int) -> int:
        """The man: east from the fetch, `x` turns by BP&1, `]` shifts."""
        x, y, dx, dy, bp = 4, entry, 1, 0, number
        for _ in range(10_000):
            x, y = x + dx, y + dy
            if x >= lane_x0:
                return y
            ch = cells.get((x, y))
            assert ch is not None, f"opcode {number} walked into a void at {(x, y)}"
            if ch == "]":
                bp >>= 1
            elif ch == ">":
                dx, dy = 1, 0
            elif ch == "x":
                dx, dy = 0, (1 if bp & 1 else -1)
        raise AssertionError("walk did not terminate")

    for s in used_slots:
        number = machine._bitrev(s, k)
        assert walk(number) == slot_rows[s], f"slot {s} decoded to the wrong lane"


def test_trim_beats_baseline_on_brackets_ticks() -> None:
    """The point of the feature: fewer rows is fewer ticks per instruction."""
    base = optimize.verify(machine.build_for("brackets").rows, "brackets")
    both = optimize.verify(
        machine.build_for("brackets", trim_dead=True, top_bus=True).rows, "brackets"
    )
    assert both.passed and base.passed
    assert both.avg_ticks < base.avg_ticks
