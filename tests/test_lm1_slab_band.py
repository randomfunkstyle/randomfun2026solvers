"""The packed slab staircase and the sparse collector (``lm1/machine.py``).

Two levers on the same band, both opt-in per ``(slug, tier)`` and both empty for
every machine that does not name itself:

* :data:`machine.PACKED_SLAB_BAND` steps the staircase by what each slab *draws*
  instead of by one uniform :data:`machine.SEEK_SLAB_PITCH`. The pitch is floored
  at eleven — "the columns a branch slab actually occupies" — and only a branch
  occupies eleven; a classic jump draws three and a seek jump draws none. On
  ``deadman-3d_hires`` that is 22 columns spent on two slabs that draw 3 between
  them, and the whole band east of them sits that much further east than it needs.
* :data:`machine.SPARSE_COLLECTOR` draws the return row's ``<`` only where a man
  *arrives* on it. A westbound man keeps his heading over a ``.``, so the run is
  overwhelmingly turns that turn nobody.

These pin the arithmetic, which needs no WAD and no build. The tick numbers and
the geometry they produce are in the registries' own notes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1 import machine  # noqa: E402
from randomfun2026solvers.lm1.isa import Sem  # noqa: E402


class _Sems:
    """The only part of a ``_Plan`` :func:`machine._slab_bases` reads."""

    def __init__(self, sem: dict[str, Sem]) -> None:
        self.sem = sem


HIRES_BAND = _Sems(
    {
        "JMPS": Sem.JUMP_SEEK,
        "JMPF": Sem.JUMP,
        "BRZ": Sem.BR_ZERO,
        "BRN": Sem.BR_NEG,
    }
)
ORDER = ["JMPS", "JMPF", "BRZ", "BRN"]


def test_only_a_branch_spans_the_pitch_floor() -> None:
    """The floor of 11 is a branch's span and nothing else's.

    ``_SLAB_PITCH_FLOOR`` is the exit riser at ``base - 1`` through the ``neg``
    arm at ``base + 9``. :func:`machine._slab_east_span` counts east of ``base``,
    so a branch is 9 and the step the caller takes is ``9 + 2 == 11`` — the floor
    exactly. That identity is what makes the packed step safe: it is the same
    arithmetic, applied per slab instead of once.
    """
    span = machine._slab_east_span
    assert span(HIRES_BAND, "BRZ", 0, None, True) == 9
    assert span(HIRES_BAND, "BRN", 0, None, True) == 9
    assert 9 + 2 == machine._SLAB_PITCH_FLOOR
    # A counted-discard jump is `a<` at base/base+1, and its riser is the next
    # slab's `base - 1`, so it needs three columns rather than eleven.
    assert span(HIRES_BAND, "JMPF", 0, None, True) == 1
    # A seek jump draws nothing at all — but only while its turn is relocated east
    # by SEEK_TAKEN_DROP_EAST. Without that the turn falls back to `base` and the
    # drop runs the full depth of the band, so it keeps a branch's span.
    assert span(HIRES_BAND, "JMPS", 0, None, True) == 0
    assert span(HIRES_BAND, "JMPS", 0, None, False) == 9


def test_the_unpacked_band_is_the_shipped_staircase() -> None:
    """``packed=False`` must reproduce ``struct_x0 + i * pitch`` exactly.

    This is the byte-identity guarantee for every machine not in the registry:
    the helper replaced an inline expression, and it has to still be it.
    """
    bases, east = machine._slab_bases(HIRES_BAND, ORDER, 2, 11)
    assert bases == {"JMPS": 2, "JMPF": 13, "BRZ": 24, "BRN": 35}
    assert east == 2 + 4 * 11 == 46


def test_the_packed_band_walks_the_branches_seventeen_columns_west() -> None:
    """The two jumps stop paying a branch's pitch and everything east follows."""
    bases, east = machine._slab_bases(
        HIRES_BAND, ORDER, 2, 11, packed=True, jump_east=True
    )
    assert bases == {"JMPS": 2, "JMPF": 4, "BRZ": 7, "BRN": 18}
    # `struct_east` keeps its meaning — the first column east of every body — which
    # for the unpacked band is `base_last + pitch` and here is `base_last + 9 + 2`.
    assert east == 29
    shipped, shipped_east = machine._slab_bases(HIRES_BAND, ORDER, 2, 11)
    assert shipped["BRZ"] - bases["BRZ"] == 17
    assert shipped["BRN"] - bases["BRN"] == 17
    assert shipped_east - east == 17


def test_no_slab_body_ever_reaches_the_next_slabs_riser() -> None:
    """The invariant the staircase rests on, checked against the packed step.

    Slab ``i``'s entry row spans ``[base_i, drop_x_i]`` and crosses every
    shallower body; what must never happen is the reverse — a body reaching into
    the next slab's exit riser column at ``base - 1``. The step is ``span + 2``
    for exactly this, and one column of slack is all there is.
    """
    for packed in (False, True):
        bases, _ = machine._slab_bases(
            HIRES_BAND, ORDER, 2, 11, packed=packed, jump_east=True
        )
        for a, b in zip(ORDER, ORDER[1:]):
            east_edge = bases[a] + machine._slab_east_span(HIRES_BAND, a, 0, None, True)
            assert east_edge < bases[b] - 1, (packed, a, b)


@pytest.mark.parametrize("gap", [0, 1, 2, 5, 9])
def test_the_seek_jumps_gap_only_moves_what_is_east_of_it(gap: int) -> None:
    """``seek_jump_gap`` buys the seek jump's turn a column near its drop.

    It is paid once, by the slabs east of the jump, and never by the jump itself
    — which is why the sweep in :data:`machine.PACKED_SLAB_BAND` is a straight
    translation of the band and not a reshaping of it.
    """
    bases, east = machine._slab_bases(
        HIRES_BAND, ORDER, 2, 11, packed=True, jump_east=True, seek_jump_gap=gap
    )
    assert bases["JMPS"] == 2
    assert bases["JMPF"] == 4 + gap
    assert bases["BRZ"] == 7 + gap
    assert bases["BRN"] == 18 + gap
    assert east == 29 + gap


def test_both_levers_are_keyed_to_hires_only() -> None:
    """``deadman-3d``'s three grids are hash-pinned; neither lever may reach them.

    Both registries are keyed on ``(slug, tier)`` and both name only
    ``deadman-3d_hires``. A bare-slug key or a ``deadman-3d`` key would move a
    machine that is checked in byte-for-byte.
    """
    for registry in (machine.PACKED_SLAB_BAND, machine.SPARSE_COLLECTOR):
        assert registry, "an empty registry means the lever shipped switched off"
        for key in registry:
            assert isinstance(key, tuple) and len(key) == 2, key
            assert key[0] == "deadman-3d_hires", key
        assert {k[1] for k in registry} == {"men-v3", "taped"}, (
            "the two tiers are kept at parity deliberately; see the registry notes"
        )
