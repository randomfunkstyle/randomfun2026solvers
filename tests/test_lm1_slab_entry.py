"""The walk *to* a slab: tight entry columns and the slab pitch (``lm1/machine.py``).

Two opt-in structures-band features, both default-off per slug:

* ``tight_drops`` (:data:`machine.TIGHT_STRUCT_DROPS`): a structured lane's drop
  lands in its own slab's column band instead of east of the whole band.
* ``slab_pitch`` (:data:`machine.SLAB_PITCH`): the staircase's step, floored at
  the eleven columns a branch slab actually occupies.

Both are about the *walk*, not the discard. A structured instruction runs east
from ``lane_x0`` to its drop column, south to its entry row, west along that row
to its slab's ``base``, and — after the discard — climbs at ``base - 1`` and runs
the collector back to the riser at column 1. Those legs telescope to
``2 * drop_x - lane_x0 - 2`` and the slab's own column cancels, so the drop
column is the entire cost and it is paid twice. :func:`test_the_round_trip_is_
twice_the_drop_column` pins that identity; the rest prove the tighter column is
legal — that the drop clears every riser it crosses, that no simple lane can
follow it past the collector, and that the machine still draws every public
frame on the engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import llm_lm1, optimize  # noqa: E402
from randomfun2026solvers.lm1 import machine  # noqa: E402
from randomfun2026solvers.lm1.asm import assemble  # noqa: E402

slow = pytest.mark.slow
SLUG = "little-little-man"


@pytest.fixture(scope="module")
def program():
    text, _ = llm_lm1.build_asm(hot_slots=llm_lm1.HOT[0] * llm_lm1.HOT[1])
    return assemble(text, name=SLUG)


def _cpu(program, *, tight: bool, pitch: int = machine._SLAB_PITCH):
    return machine.build_cpu(
        program,
        machine.plan(program),
        mem_pad=22,
        drain_unit_bits=machine.DRAIN_UNIT_BITS.get(SLUG, 0),
        tight_drops=tight,
        slab_pitch=pitch,
    )


def _drops(cpu) -> dict[str, int]:
    """Drop column per opcode: the lane region ends there (see ``build_cpu``)."""
    return {
        name.split(":", 1)[1]: x + w - 1
        for name, (x, _y, w, _h) in cpu.regions.items()
        if name.startswith("lane:")
    }


def _bases(cpu) -> dict[str, int]:
    return {
        name.split(":", 1)[1]: x
        for name, (x, _y, _w, _h) in cpu.regions.items()
        if name.startswith("slab:")
    }


def test_both_registries_name_only_the_slugs_that_measured_them() -> None:
    """Every checked-in machine stays byte-identical unless its slug opts in.

    ``deadman-3d`` opts into :data:`machine.SLAB_PITCH` but **not** into
    :data:`machine.TIGHT_STRUCT_DROPS`, and the asymmetry is geometry, not
    oversight: ``build`` computes ``tight_drops=not seek and name in
    TIGHT_STRUCT_DROPS``, so the seek drum — which only ``deadman-3d`` has —
    makes that registry unreachable for it. Naming it there would be dead config
    reading as an intent nobody can act on.
    """
    assert machine.TIGHT_STRUCT_DROPS == {SLUG}
    assert machine.SLAB_PITCH == {SLUG: 11}
    # The claim is the *asymmetry* above, so assert that and not an inventory.
    # This line used to read ``== {"deadman-3d": 11}`` and went red the moment
    # ``deadman-3d_hires`` legitimately measured its own pitch — a whole-registry
    # equality breaks on every addition while protecting nothing, which is the
    # failure mode ``a70da0f`` ("pin properties, not bounding boxes") was about.
    assert machine.SEEK_SLAB_PITCH["deadman-3d"] == 11
    assert SLUG not in machine.SEEK_SLAB_PITCH  # the asymmetry this test is named for


def test_the_pitch_registry_is_read_only_on_a_seek_build() -> None:
    """The guard that lets ``deadman-3d`` narrow its pitch without moving the
    classic reference machine.

    Pitch 11 pulls the CPU's east wall west, and §7.1 binds the deepest slab's
    discard ``r`` against the memory-response pipe touching that wall — so a
    narrower CPU is a *closer* rival. The seek build clears the tie because
    :data:`machine.SEEK_MEM_PAD` already sits east of it; the classic build does
    not, and at ``MEM_PAD`` 17 it cannot bind at all. Conditioning on ``seek``
    is what keeps ``build_for(..., seek=False)`` buildable *and* byte-stable.
    """
    seek_m = machine.build_for("deadman-3d", store="taped")
    classic = machine.build_for("deadman-3d", store="taped", seek=False)
    # The seek build takes the narrowed pitch and the pad that clears the tie;
    # the classic one is untouched by either. Stated as the registries rather
    # than as a recorded width: the box moves with every independent routing
    # change, and a frozen number here just fails somebody else's improvement.
    assert seek_m.mem_pad == machine.MEM_PAD_FOR[("deadman-3d", "taped")]
    assert classic.mem_pad == machine.MEM_PAD["deadman-3d"]
    assert seek_m.mem_pad != classic.mem_pad
    # Both still build, which is the half of the guard that has actually broken:
    # at MEM_PAD 17 the classic build cannot bind the deepest slab's discard.
    assert seek_m.rows and classic.rows


def test_the_default_floors_every_structured_drop_east_of_the_band(program) -> None:
    cpu = _cpu(program, tight=False)
    drops, bases = _drops(cpu), _bases(cpu)
    struct_east = machine._STRUCT_X0 + len(bases) * machine._SLAB_PITCH
    for m in bases:
        assert drops[m] > struct_east, (m, drops[m], struct_east)


def test_tight_entries_land_each_slab_inside_its_own_band(program) -> None:
    """The point of the change: the drop is the slab's own column, not the band's east."""
    cpu = _cpu(program, tight=True)
    drops, bases = _drops(cpu), _bases(cpu)
    assert bases == {"JMPF": 2, "BRZ": 15, "BRN": 28}
    assert drops["JMPF"] == 15 and drops["BRZ"] == 16 and drops["BRN"] == 29
    # Each one is inside (or one short of) its own pitch-wide band, which is the
    # condition the default rule replaced with "east of everything".
    for m, base in bases.items():
        assert base < drops[m] <= base + machine._SLAB_PITCH


def test_the_round_trip_is_twice_the_drop_column(program) -> None:
    """East to the drop, west to ``base``, home from ``base - 1``: the base cancels.

    ``(drop - lane_x0) + (drop - base) + (base - 2) == 2 * drop - lane_x0 - 2``.
    So the slab's own column is free and only the drop column is charged, twice —
    which is why moving the drop and not the slab is the whole optimisation.
    """
    lane_x0 = 5 + machine.plan(program).k
    for tight in (False, True):
        cpu = _cpu(program, tight=tight)
        drops, bases = _drops(cpu), _bases(cpu)
        for m, base in bases.items():
            legs = (drops[m] - lane_x0) + (drops[m] - base) + (base - 2)
            assert legs == 2 * drops[m] - lane_x0 - 2
    base_cpu, tight_cpu = _cpu(program, tight=False), _cpu(program, tight=True)
    bd, td = _drops(base_cpu), _drops(tight_cpu)
    walk = {m: (2 * bd[m] - lane_x0 - 2, 2 * td[m] - lane_x0 - 2) for m in _bases(base_cpu)}
    assert walk == {"JMPF": (73, 19), "BRZ": (75, 21), "BRN": (77, 47)}


@pytest.mark.parametrize("pitch", [11, 12, machine._SLAB_PITCH])
def test_no_tight_entry_shares_a_column_with_a_riser(program, pitch: int) -> None:
    """A drop crossing a riser leaves `.` on the collector row, and the riser's man
    then sails north into the lane band instead of turning west for the fetch site.
    """
    cpu = _cpu(program, tight=True, pitch=pitch)
    drops, bases = _drops(cpu), _bases(cpu)
    risers = set()
    for m, base in bases.items():
        risers.add(base - 1)  # every slab's exit riser
        if m != "JMPF":  # the branches' arm columns
            risers |= {base + 3, base + 6, base + 9}
    for m in bases:
        assert drops[m] not in risers, (m, drops[m], sorted(risers))


@pytest.mark.parametrize("pitch", [11, 12, machine._SLAB_PITCH])
def test_no_simple_lane_shares_a_slab_entry_column(program, pitch: int) -> None:
    """``build_cpu`` raises on this itself; assert the tightened rule never trips it."""
    cpu = _cpu(program, tight=True, pitch=pitch)
    drops, bases = _drops(cpu), _bases(cpu)
    entries = {drops[m] for m in bases}
    simple = [drops[m] for m in drops if m not in bases]
    assert not (entries & set(simple)), sorted(entries & set(simple))


def test_the_pitch_floor_is_the_span_a_branch_slab_occupies(program) -> None:
    """Ten works only because two *risers* happen to share a column. Refuse it."""
    assert machine._SLAB_PITCH_FLOOR == 11
    with pytest.raises(machine.MachineError, match="below the 11-column span"):
        _cpu(program, tight=True, pitch=10)


def test_tight_entries_need_the_short_return_and_refuse_the_seek_drum(program) -> None:
    p = machine.plan(program)
    with pytest.raises(machine.MachineError, match="short-return"):
        machine.build_cpu(program, p, tight_drops=True, short_return=False)
    with pytest.raises(machine.MachineError, match="seek drum"):
        machine.build_cpu(program, p, tight_drops=True, seek=True)


def test_a_narrower_pitch_pulls_the_cpu_east_wall_in(program) -> None:
    """The band is what the east wall was pinned to, once the drops came west.

    ``width = ret_x + 1`` and ``ret_x`` is the easternmost drop, so there is never
    a dead column to reclaim — the wall is derived, not inherited. What moves it is
    moving the drops, and the pitch moves the deepest slab's with it.
    """
    for cpu in (_cpu(program, tight=False), _cpu(program, tight=True, pitch=11)):
        occupied = max(x for (x, _y), ch in cpu.cells.items() if ch.strip())
        assert occupied == cpu.width - 1, (occupied, cpu.width)
    assert _cpu(program, tight=True, pitch=11).width < _cpu(program, tight=False).width


@slow
def test_the_shipped_machine_still_draws_every_public_frame() -> None:
    """The engine proof. ``check_bindings`` already ran inside the build."""
    built, _prog, _text = llm_lm1.build_machine()
    assert built.mem_pad == 22, built.mem_pad
    res = optimize.verify(built.rows, SLUG)
    assert res.passed, [v for v in res.cases if not v.passed]
