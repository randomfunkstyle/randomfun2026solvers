"""Tests for the program optimizer (randomfun2026solvers.optimize)."""

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

from randomfun2026solvers.layout import Canvas  # noqa: E402
from randomfun2026solvers.manparse import Program, Room  # noqa: E402
from randomfun2026solvers.optimize import (  # noqa: E402
    CapacityRouter,
    OptimizeError,
    optimize,
    trim_margins,
    verify,
)

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="needs Node + littleman/lm.mjs",
)


def test_trim_margins_pure() -> None:
    # Pure model test (no Node): a grid padded with blank border rows/cols crops.
    prog = Program(
        width=6,
        height=5,
        rooms=[
            Room(id=0, min=(2, 1), max=(4, 3), content=["+-+", "|@|", "+-+"]),
        ],
    )
    # to_grid renders room at (2,1); trim should shift it to origin.
    cands = trim_margins(prog)
    assert len(cands) == 1
    assert cands[0].grid == ["+-+", "|@|", "+-+"]


def test_capacity_router_pads_to_target() -> None:
    # Pure test (no Node): a short straight path is folded up to a length floor,
    # staying a valid set of distinct in-bounds cells.
    router = CapacityRouter(min_len={})
    canvas = Canvas()  # empty → every cell free
    path = [(0, 0), (1, 0), (2, 0), (3, 0)]  # length 4
    padded = router._pad_path(path, target=20, canvas=canvas, bounds=(-30, -30, 30, 30))
    assert padded is not None
    assert len(padded) >= 20
    assert len(set(padded)) == len(padded)  # no cell reused
    assert padded[0] == path[0] and padded[-1] == path[-1]  # endpoints fixed


@node_required
def test_verify_baseline_passes() -> None:
    r = verify((EXAMPLES / "atoi.man").read_text(), "atoi")
    assert r.passed
    assert r.n_passed == len(r.cases) > 0
    assert r.avg_ticks and r.avg_ticks > 0


@node_required
def test_verify_rejects_wrong_output() -> None:
    # A room that never sends → emits nothing → fails atoi (which expects output).
    broken = "+-+  +-----+  +-+\n|I|>>|@ H   |>>|O|\n+-+  +-----+  +-+"
    assert not verify(broken, "atoi").passed


@node_required
def test_optimize_refuses_broken_input() -> None:
    broken = "+-+  +-----+  +-+\n|I|>>|@ H   |>>|O|\n+-+  +-----+  +-+"
    with pytest.raises(OptimizeError):
        optimize(broken, "atoi")


@node_required
def test_optimize_atoi_improves_and_verifies() -> None:
    res = optimize(EXAMPLES / "atoi.man", "atoi", max_sweeps=1)
    assert res.passed
    assert res.score is not None and res.base_score is not None
    # Re-layout squares the extreme 78×6 grid → a real footprint win.
    assert res.score < res.base_score
    # The returned grid independently verifies.
    assert verify(res.render(), "atoi").passed


@node_required
def test_optimize_memory_stays_correct() -> None:
    # memory's long pipes are load-bearing buffers; every shrink breaks it, so the
    # optimizer must return a still-passing grid (worst case, unchanged).
    res = optimize(EXAMPLES / "memory.man", "memory", max_sweeps=1)
    assert res.passed
    assert res.score is not None and res.base_score is not None
    assert res.score <= res.base_score
