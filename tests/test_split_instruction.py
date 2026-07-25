"""Published ``Y`` split semantics and fast/reference parity."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.fast_littleman import (  # noqa: E402
    FastLittleman,
    _Machine,
    _Runner,
)
from randomfun2026solvers.littleman import Littleman  # noqa: E402
from randomfun2026solvers.manstruct import Kind, _exits_for  # noqa: E402


OFFICIAL_DEMO = """\
+------+
| >  H |
|      |
|@Y    |
|      |
| >  H |
+------+"""

ASYMMETRIC_HALT = """\
+-------+
| >   H |
|       |
|@Y     |
| H     |
+-------+"""

ORDERED_SIDE_EFFECTS = """\
+--------+
|        |
| >  sH  |
| 1      |  +-+
|@Y      |>>|O|
| 2      |  +-+
| >  sH  |
|        |
+--------+"""

PREEXISTING_RUNNER = """\
+------+  +------+
| >  H |  |@    H|
|      |  +------+
|@Y    |
|      |
| >  H |
+------+"""

THREE_WAY_FANOUT = """\
+------------+
|            |
|   H<Y>H    |
|            |
|@7M5bY      |
|     v      |
|     .      |
|     .      |
|     H      |
+------------+"""


def _python_machine(source: str) -> _Machine:
    return _Machine(FastLittleman(source), [[]], None)


def test_children_are_born_beside_y_and_wait_until_the_next_tick() -> None:
    reference = Littleman().tick(OFFICIAL_DEMO, 2)
    python = _python_machine(OFFICIAL_DEMO)
    python._tick()
    python._tick()

    assert [(r.pos.as_tuple(), r.dir.as_tuple()) for r in reference.entities.runners] == [
        ((2, 4), (0, 1)),
        ((2, 2), (0, -1)),
    ]
    assert [(r.pos, r.direction) for r in python.runners] == [
        ((2, 4), (0, 1)),
        ((2, 2), (0, -1)),
    ]
    assert all(r.blocked for r in python.runners), "newborns move starting next tick"


def test_native_and_python_match_asymmetric_split_timing() -> None:
    reference = Littleman().run(ASYMMETRIC_HALT, max_ticks=20)
    machine = FastLittleman(ASYMMETRIC_HALT)
    python = machine.run([], max_ticks=20, native=False)
    native = machine.run([], max_ticks=20)

    assert reference.step == python.step == native.step == 8
    assert python.halted and native.halted


def test_right_child_wins_a_same_tick_pipe_side_effect() -> None:
    """Right keeps the parent's order slot; left acts last and blocks behind it."""
    reference = Littleman().run(ORDERED_SIDE_EFFECTS, max_ticks=30)
    machine = FastLittleman(ORDERED_SIDE_EFFECTS)

    assert list(reference.output) == [2, 1]
    assert machine.run([], max_ticks=30, native=False).output == [2, 1]
    assert machine.run([], max_ticks=30).output == [2, 1]


def test_left_child_is_newer_than_every_preexisting_runner() -> None:
    reference = Littleman().tick(PREEXISTING_RUNNER, 2)
    python = _python_machine(PREEXISTING_RUNNER)
    python._tick()
    python._tick()

    expected = [
        ((13, 1), (1, 0)),  # runner which existed before the split
        ((2, 4), (0, 1)),  # right child in the parent's old order slot
        ((2, 2), (0, -1)),  # left child is newest
    ]
    assert [(r.pos.as_tuple(), r.dir.as_tuple()) for r in reference.entities.runners] == expected
    assert [(r.pos, r.direction) for r in python.runners] == expected


def test_two_splits_form_a_three_worker_register_fanout() -> None:
    """Small-room prototype for three identical row/column/box operations."""
    reference = Littleman().tick(THREE_WAY_FANOUT, 8)
    python = _python_machine(THREE_WAY_FANOUT)
    for _ in range(8):
        python._tick()

    assert len(reference.entities.runners) == len(python.runners) == 3
    assert all((r.a, r.b, r.backpack) == (5, 7, 5) for r in reference.entities.runners)
    assert all((r.a, r.b, r.bp) == (5, 7, 5) for r in python.runners)
    assert FastLittleman(THREE_WAY_FANOUT).run([], max_ticks=20).halted


def test_birth_into_a_wall_is_fatal_on_the_reference_birth_cell() -> None:
    source = "+---+\n|@Y |\n+---+"
    reference = Littleman().run(source, max_ticks=5)
    python = FastLittleman(source).run([], max_ticks=5, native=False)
    native = FastLittleman(source).run([], max_ticks=5)

    assert reference.fatal is not None and reference.fatal.reason == "wall"
    assert reference.fatal.pos is not None
    assert reference.fatal.pos.as_tuple() == python.fatal_pos == native.fatal_pos == (2, 0)


def test_following_into_a_vacated_cell_is_not_a_collision_but_swapping_is() -> None:
    source = "+-----+\n|@    |\n+-----+"

    following = _python_machine(source)
    following.runners = [
        _Runner(0, 0, (2, 1), direction=(1, 0)),
        _Runner(1, 0, (3, 1), direction=(1, 0)),
    ]
    following._move()
    assert [(r.pos, r.halted) for r in following.runners] == [
        ((3, 1), False),
        ((4, 1), False),
    ]

    swapping = _python_machine(source)
    swapping.runners = [
        _Runner(0, 0, (2, 1), direction=(1, 0)),
        _Runner(1, 0, (3, 1), direction=(-1, 0)),
    ]
    swapping._move()
    assert all(r.halted for r in swapping.runners)


def test_split_is_not_structurally_heading_preserving() -> None:
    exits = _exits_for(Kind.OP, "Y")
    assert set(exits) == {"E", "W", "N", "S"}
    assert all(exit_ is None for exit_ in exits.values())


def test_c_is_not_the_split_glyph() -> None:
    source = "+---+\n|@C |\n+---+"
    reference = Littleman().run(source, max_ticks=5)
    assert reference.fatal is not None and reference.fatal.reason == "bad-op"
    machine = FastLittleman(source)
    assert machine.run([], max_ticks=5, native=False).fatal == "bad-op"
    assert machine.run([], max_ticks=5).fatal == "bad-op"
