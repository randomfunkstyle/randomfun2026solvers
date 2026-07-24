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
