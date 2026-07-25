"""Tests for the MEMORY program stated as AST abstractions (``memory_ast.py``).

The point of the abstraction is that facts about the program become checkable
instead of remembered. Two matter most: the register contracts (which hand holds
what across each step) and **pipe affinity** — which pipe each ``s``/``r`` must
resolve to, since ``nearest`` is decided by geometry and a misplaced step silently
re-binds.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.memory_ast import (  # noqa: E402
    Affinity,
    relative_spec,
    spec_for,
)


def test_the_full_lap_design_pays_a_whole_lap_per_operation() -> None:
    """8n ticks regardless of which cell is touched — the dominant cost."""
    spec = spec_for(100)
    assert spec.rotation_ticks() == 800
    assert sum(s.is_loop for s in spec.steps) == 3, "fill + two rotations"


def test_the_relative_design_halves_the_rotation_and_drops_a_loop() -> None:
    """Rotating `delta` averages n/2, and the second rotation disappears."""
    full, rel = spec_for(100), relative_spec(100)
    assert rel.rotation_ticks() * 2 == full.rotation_ticks()
    assert sum(s.is_loop for s in rel.steps) == 2
    assert sum(s.is_branch for s in rel.steps) == 1, "one branch, not two"


def test_the_relative_design_needs_a_register_room() -> None:
    """The head position has nowhere else to live: two hands and an unreadable BP."""
    rel = relative_spec(100)
    reg = [s.name for s in rel.steps if s.affinity is Affinity.REGISTER]
    assert reg, "somewhere must hold `current`"
    assert rel.is_relative
    assert not spec_for(100).is_relative
    # three round trips per operation is what made the earlier attempt lose
    assert len(reg) == 3, reg


def test_every_ring_needs_one_spare_slot() -> None:
    """n values circulating need somewhere to move into; exactly n deadlocks."""
    assert spec_for(100).ring.minimum == 101
    assert spec_for(7).ring.minimum == 8


def test_pipe_affinity_partitions_the_worker_into_zones() -> None:
    """This is why layouts look the way they do, and it is a constraint not a hint."""
    zones = spec_for(100).affinity_zones()
    assert "read_op" in zones[Affinity.INPUT]
    assert "rot1" in zones[Affinity.RING]
    assert "emit" in zones[Affinity.RING_AND_OUTPUT]
    # a step that touches no pipe is free to go anywhere
    assert "branch_op" in zones[Affinity.NONE]


def test_the_control_flow_graph_is_closed() -> None:
    """Every goto names a real step: a dangling edge is an unreachable program."""
    for spec in (spec_for(100), relative_spec(100)):
        assert spec.check() == [], spec.check()
        names = set(spec.by_name())
        for step in spec.steps:
            for target in step.goto:
                assert target in names, (step.name, target)


def test_the_literal_shrinks_for_small_memories() -> None:
    """A one-digit size needs no backticks, which is four cells and four ticks."""
    assert "@7b" == spec_for(7).by_name()["init"].glyphs
    assert "@`100`b" == spec_for(100).by_name()["init"].glyphs
