"""Tests for the .man grid parser (randomfun2026solvers.manparse)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
EXAMPLES = REPO / "littleman" / "examples"
LM_MJS = REPO / "littleman" / "lm.mjs"

if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manparse import Program, parse_program  # noqa: E402

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)

ALL_EXAMPLES = ["walk", "io", "echo", "atoi", "memory"]


def _canonical(path: Path) -> list[str]:
    """Source rows, trailing-whitespace stripped (the canonical form to_grid emits)."""
    return [line.rstrip() for line in path.read_text().rstrip("\n").split("\n")]


@node_required
@pytest.mark.parametrize("name", ALL_EXAMPLES)
def test_roundtrip_identity(name: str) -> None:
    path = EXAMPLES / f"{name}.man"
    prog = parse_program(path)
    assert prog.to_grid() == _canonical(path)


@node_required
@pytest.mark.parametrize("name", ALL_EXAMPLES)
def test_to_graph_matches_structure(name: str) -> None:
    path = EXAMPLES / f"{name}.man"
    prog = parse_program(path)
    graph = prog.to_graph()
    assert len(graph.containers) == len(prog.rooms)
    assert len(graph.edges) == len(prog.pipes)
    # Every pipe-op cell binds to some real pipe (id in range) or -1.
    for room in prog.rooms:
        for op in room.pipe_ops:
            assert -1 <= op.pipe_id < len(prog.pipes)


@node_required
def test_room_classification() -> None:
    prog = parse_program(EXAMPLES / "echo.man")
    kinds = sorted(r.kind for r in prog.rooms)
    assert kinds == ["compute", "input", "output"]


def test_program_to_grid_from_blocks() -> None:
    # Pure model test (no Node): a room block round-trips through to_grid.
    prog = Program(
        width=3,
        height=3,
        rooms=[{"id": 0, "min": (0, 0), "max": (2, 2), "content": ["+-+", "|@|", "+-+"]}],
    )
    assert prog.to_grid() == ["+-+", "|@|", "+-+"]


def test_display_panel_round_trips_via_to_grid() -> None:
    # Pure model test (no Node): a display the analyser reports as bare geometry
    # (min/max + captured content) must be repainted by to_grid. Dropping it once
    # corrupted plotter/snake — a pipe feeding the panel then "ends without
    # reaching another room" and the round-tripped grid no longer loads.
    prog = Program(
        width=5,
        height=3,
        displays=[
            {"min": [0, 0], "max": [4, 2], "content": ["+===+", ":   :", "+===+"]}
        ],
    )
    assert prog.to_grid() == ["+===+", ":   :", "+===+"]


# The best archived plotter/snake solutions carry a display panel the analyser
# reports as bare geometry rather than as a room; before the to_grid fix these
# were dropped on the reparse optimize() does up front, so the grid no longer
# loaded and the portfolio capped at 6/8. Pin the byte-exact round-trip.
_SOLUTIONS = REPO / "solutions"
DISPLAY_ARCHIVES = ["plotter", "snake"]


def _lowest_archive(slug: str) -> Path | None:
    d = _SOLUTIONS / slug
    files = sorted(d.glob("*.man")) if d.is_dir() else []
    return files[0] if files else None


@node_required
@pytest.mark.parametrize("slug", DISPLAY_ARCHIVES)
def test_display_archive_round_trip_byte_exact(slug: str) -> None:
    path = _lowest_archive(slug)
    if path is None:
        pytest.skip(f"no archived {slug} solution checked in")
    # bind=False: pipe-op bindings don't affect to_grid output and cost one engine
    # call per instruction cell, so skip them to keep this in the fast tier.
    prog = parse_program(path, bind=False)
    assert prog.displays, f"{slug} archive should expose a display panel"
    assert prog.to_grid() == _canonical(path)
