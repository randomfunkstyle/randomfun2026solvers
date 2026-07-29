"""pytest form of the ladder, for anyone who prefers it to ``python -m ...run``.

Not collected by the default suite — ``pyproject.toml`` sets ``testpaths =
["tests"]`` and nothing here is wired into a build.  Run it explicitly:

    uv run pytest scratch/layout1 -q
    uv run pytest scratch/layout1 -q -m ""     # including the store case
"""

from __future__ import annotations

import pytest

from .bind import MachineError, check_layout
from .geom import Layout, Placed
from .ladder import ladder, rung5
from .solve import solve


@pytest.mark.parametrize("rung", ladder(), ids=lambda r: r.name)
def test_rung_matches_its_known_answer(rung):
    rep = solve(rung.problem)
    assert rep.best is not None, f"no feasible layout: {rep.summary()}"
    ok, saw = rung.check(rep.best, rep)
    assert ok, f"known: {rung.known}\ngot:   {saw}"


def test_the_trap_really_is_a_trap():
    """Rung 5 proves nothing unless the rejected twin is genuinely equal-cost."""
    from .trap import price_trap

    prob = rung5().problem
    cost, err = price_trap(prob)
    assert err is not None, "the trap candidate binds correctly — it is not a trap"
    assert "must bind" in err
    rep = solve(prob)
    assert rep.best is not None
    assert cost == pytest.approx(rep.best.weighted_cells), (
        "the trap must be indistinguishable to a packer, or the rung proves only "
        "that the solver prefers a cheaper layout"
    )


def test_growing_a_side_that_carries_a_glyph_is_a_model_error():
    """A room may reach its caller; a wall with an ``s`` on it may not move."""
    from .model import Block, Port

    with pytest.raises(ValueError, match="binding glyph"):
        Block("gate", 26, 7, ports=(Port("local", "E", 1, "s", "feed"),),
              grow=frozenset({"E"}), grow_max=4)


def test_check_bindings_is_the_production_function():
    """Guard against the checker quietly becoming a local reimplementation."""
    import randomfun2026solvers.lm1.machine as machine

    from . import bind

    assert bind.check_bindings is machine.check_bindings
    assert MachineError is machine.MachineError


def test_the_gates_measured_three_cell_margin_is_reproduced():
    """The taped gate's tightest ``s`` is (19, 2): 8 from local, 11 from downstream.

    ``TAPED_CHAIN_REACH``'s note: move the local attachment ``L`` rows off the body
    and at ``L = 4`` the north write arm binds the downstream pipe, so reads land in
    the wrong bank with no error at all.  Through the real checker the model gives
    ``L = 0, 1, 2`` legal — one tighter than the note, and correctly so: at
    ``L = 3`` the two distances are **equal**, and ``check_bindings`` refuses a tie
    rather than trusting reading order to resolve it.  The margin is three cells,
    and the last of the three is the one you may not spend.
    """
    from .store import s3_feed

    prob = s3_feed(0, 43, 1.0)
    gate = prob.block("gate0")
    ok = []
    for lift in range(0, 6):
        placed = {
            b.name: Placed(b, b.xs[0], b.ys[0], (lift if b is gate else 0, 0, 0, 0))
            for b in prob.blocks
        }
        offsets = {
            ("gate0", "local"): 1 - lift,
            ("gate0", "down"): 6,
            ("bank", "in"): 10,
            ("next", "in"): 3,
        }
        try:
            check_layout(Layout(prob, placed, offsets))
            ok.append(lift)
        except MachineError:
            pass
    assert ok == [0, 1, 2], f"the margin moved: legal lifts were {ok}"


@pytest.mark.slow
def test_the_store_request_legs():
    from .store import run_store

    assert run_store() == 0
