"""Complete two-bank memory reference machine."""

from __future__ import annotations

from pathlib import Path

import pytest

from randomfun2026solvers.memory_banked_machine import build
from randomfun2026solvers.optimize import verify


def test_banked_machine_debug_map_and_shape_are_synchronized() -> None:
    rows, debug = build()

    assert (max(map(len, rows)), len(rows)) == (113, 72)
    assert {region.name for region in debug.regions} == {
        "dispatcher",
        "low-bank",
        "high-bank",
    }
    assert {lane.name for lane in debug.lanes} == {
        "input",
        "low-command",
        "high-command",
        "low-completion",
        "high-completion",
        "merged-completion",
        "output",
    }


@pytest.mark.slow
def test_banked_machine_passes_all_public_memory_cases() -> None:
    rows, _debug = build()
    result = verify(rows, "memory", tick_cap=2_000_000)

    assert result.passed, result.cases
    assert result.avg_ticks == pytest.approx(15118.57142857143)


def test_checked_in_banked_machine_matches_generator() -> None:
    rows, _debug = build()
    artifact = Path("littleman/examples/memory-banked-machine.man")

    assert artifact.read_text(encoding="utf-8") == "\n".join(rows) + "\n"
