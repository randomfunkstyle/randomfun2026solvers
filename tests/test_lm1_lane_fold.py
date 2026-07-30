"""The vertical fold: a lane's micro-program run *south* (``lm1/machine.py``).

A lane has always been drawn as a row of glyphs, then a ``v``, then a descent of
``.`` to the collector. But a man executes whatever cell he steps on, and he was
going to walk that descent anyway — so the glyphs after the last band-anchored
one can go **down the drop column** for nothing, and the lane stops going east
that much sooner. ``drop_x`` is charged twice per execution (east to the turn,
west along the collector), so every column the fold buys back is two ticks on
every instruction of that opcode.

The mechanism is what these tests pin, on a program that needs no WAD:

* :func:`test_the_folded_lane_executes_the_same_micro_program` walks the grid the
  way the man does and checks the *path* still spells the opcode. This is the one
  that matters — a re-laid micro-program can bind every pipe and execute the
  wrong sequence, and nothing else in the build would notice.
* :func:`test_no_lane_is_ever_lengthened_by_the_fold` pins the "only when it
  saves at least one" rule: a fold makes its column exclusive, which costs the
  lanes above a column, so a fold that saved nothing would be a pure loss.
* :func:`test_a_folded_glyph_never_stands_where_another_man_walks` is the safety
  property. A folded glyph is an *operation* on somebody else's row.

:data:`machine.FOLDED_LANES` names ``deadman-3d_hires`` men-v3 only, so every
other checked-in machine stays byte-identical; that is pinned too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import llm_lm1  # noqa: E402
from randomfun2026solvers.lm1 import machine  # noqa: E402
from randomfun2026solvers.lm1.asm import assemble  # noqa: E402

SLUG = "little-little-man"
#: The glyphs that steer rather than compute. Everything else the man steps on is
#: an operation, and the order he meets them in *is* the micro-program.
_STEER = set(">v<^.")
_STEP = {">": (1, 0), "v": (0, 1), "<": (-1, 0), "^": (0, -1)}


@pytest.fixture(scope="module")
def program():
    text, _ = llm_lm1.build_asm(hot_slots=llm_lm1.HOT[0] * llm_lm1.HOT[1])
    return assemble(text, name=SLUG)


def _cpu(program, *, fold: bool):
    return machine.build_cpu(
        program,
        machine.plan(program),
        mem_pad=22,
        drain_unit_bits=machine.DRAIN_UNIT_BITS.get(SLUG, 0),
        fold_lanes=fold,
    )


def _lane_x0(cpu) -> int:
    return min(x for name, (x, _y, _w, _h) in cpu.regions.items() if name.startswith("lane:"))


def _rows(cpu) -> dict[str, int]:
    return {
        name.split(":", 1)[1]: y
        for name, (_x, y, _w, _h) in cpu.regions.items()
        if name.startswith("lane:")
    }


def _drops(cpu) -> dict[str, int]:
    return {
        name.split(":", 1)[1]: x + w - 1
        for name, (x, _y, w, _h) in cpu.regions.items()
        if name.startswith("lane:")
    }


def _flat_opcodes(program, plan):
    """The simple lanes — the structured ones end in a slab, not on the collector."""
    return {
        m: machine.hw_micro(plan.sem[m])
        for m in plan.number
        if machine.hw_micro(plan.sem[m])
    }


def _walk(cpu, x: int, y: int, stop: int) -> list[str]:
    """Follow a man east from ``(x, y)`` to row ``stop``, listing what he executes."""
    dx, dy = 1, 0
    seen: list[str] = []
    for _ in range(4 * (cpu.width + cpu.height)):
        glyph = cpu.cells.get((x, y), " ")
        if glyph in _STEP:
            dx, dy = _STEP[glyph]
        elif glyph not in _STEER and glyph != " ":
            seen.append(glyph)
        x, y = x + dx, y + dy
        if y == stop:
            return seen
    raise AssertionError(f"the man never reached row {stop}")


def test_the_registry_names_only_the_slug_that_measured_it() -> None:
    """Everything not named here regenerates byte-for-byte, fold code or no.

    Both hires tiers are named now — the taped one at -0.21% on the 21-round tour
    against men-v3's -0.27%, which is the smallest gap of the five dispatch levers
    because what folds is a property of the *program*'s micro-programs and not of
    the tier. So the pin asserts the **slug**: ``deadman-3d``'s three grids are
    hash-pinned and share this code path, and they are what must not move. A tier
    joining after it has been measured is not a regression.
    """
    assert {slug for slug, _tier in machine.FOLDED_LANES} == {"deadman-3d_hires"}


def test_the_default_build_is_untouched(program) -> None:
    flat = _cpu(program, fold=False)
    assert flat.cells == machine.build_cpu(
        program,
        machine.plan(program),
        mem_pad=22,
        drain_unit_bits=machine.DRAIN_UNIT_BITS.get(SLUG, 0),
    ).cells


def test_the_folded_lane_executes_the_same_micro_program(program) -> None:
    """The gate that a binding check cannot give you: the *path* still spells the opcode.

    The man is walked from ``lane_x0`` exactly as the trie delivers him — east,
    turning wherever the grid turns him — until he reaches the collector row. What
    he executes on the way must be ``hw_micro``'s glyph sequence, in order,
    whether the lane laid it along the row or down the drop.
    """
    plan = machine.plan(program)
    cpu = _cpu(program, fold=True)
    lane_x0, rows, drops = _lane_x0(cpu), _rows(cpu), _drops(cpu)
    collector = max(rows.values()) + 1
    folded = []
    for m, micro in _flat_opcodes(program, plan).items():
        walked = _walk(cpu, lane_x0, rows[m], collector)
        assert walked == [g for g, _band in micro], (m, walked, micro)
        if cpu.cells.get((drops[m], rows[m] + 1), ".") not in _STEER:
            folded.append(m)
    assert folded, "no lane put a glyph in its drop column — the test proves nothing"


def test_no_lane_is_ever_lengthened_by_the_fold(program) -> None:
    """A fold costs the lanes above a column, so it is only taken when it saves one."""
    flat, fold = _drops(_cpu(program, fold=False)), _drops(_cpu(program, fold=True))
    assert set(flat) == set(fold)
    for m in flat:
        assert fold[m] <= flat[m], (m, flat[m], fold[m])
    assert any(fold[m] < flat[m] for m in flat), "the fold bought nothing here"


def test_a_folded_glyph_never_stands_where_another_man_walks(program) -> None:
    """The fold's one real hazard, stated directly.

    A folded glyph is an operation sitting on a row that belongs to another lane.
    That lane's man runs east from ``lane_x0`` to his own ``drop_x``, so the only
    safe columns are the ones strictly east of where he turns south.
    """
    cpu = _cpu(program, fold=True)
    lane_x0, rows, drops = _lane_x0(cpu), _rows(cpu), _drops(cpu)
    owner = {y: m for m, y in rows.items()}
    for m, y in rows.items():
        column = drops[m]
        for below in range(y + 1, max(rows.values()) + 1):
            glyph = cpu.cells.get((column, below), ".")
            if glyph in _STEER or glyph == " " or below not in owner:
                continue
            assert column > drops[owner[below]], (m, owner[below], column, below)
            assert column > lane_x0
