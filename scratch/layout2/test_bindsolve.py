"""Tests for §7.1 as a solved system.

The important ones are the **agreement** tests: the analytic feasible set is
compared against the production ``check_bindings`` at every integer shift in a
domain, on randomised placements. A closed form that agrees with the real checker
everywhere on thousands of cases is validated in the only way that counts; a unit
test on the interval arithmetic alone would pass while the model of the rule was
wrong.

No build is needed for any of this, so it runs in milliseconds:

    .venv/bin/python -m pytest scratch/layout2 -q
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "solvers" / "python"
if str(_SRC) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_SRC))

from randomfun2026solvers.lm1.machine import (  # noqa: E402
    MachineError,
    check_bindings,
)

from .bindsolve import ISet, Ivl, feasible, violations  # noqa: E402

INF = float("inf")


# ── the interval algebra ─────────────────────────────────────────────────────
def test_iset_merges_adjacent_integer_intervals():
    s = ISet([Ivl(0, 3), Ivl(4, 6)])
    assert len(s.parts) == 1 and str(s) == "[0, 6]"


def test_iset_intersection_and_membership():
    a = ISet([Ivl(0, 10)])
    b = ISet([Ivl(-INF, 4), Ivl(8, INF)])
    got = a & b
    assert [str(p) for p in got.parts] == ["[0, 4]", "[8, 10]"]
    assert 3 in got and 5 not in got and 9 in got


def test_empty_intersection_is_empty():
    assert (ISet([Ivl(0, 2)]) & ISet([Ivl(5, 7)])).is_empty


# ── the rule ─────────────────────────────────────────────────────────────────
def _shift(touches, moving, axis, t):
    x, y = touches[moving]
    return {**touches, moving: (x, y + t) if axis == "y" else (x + t, y)}


def _truth(glyphs, touches, moving, axis, t):
    try:
        check_bindings(glyphs, _shift(touches, moving, axis, t))
        return True
    except MachineError:
        return False


def test_tie_is_infeasible_exactly_where_the_checker_says():
    """A tie fails, so the feasible set must have a hole at the tie, not an edge."""
    glyphs = [(10, 10, "r", "rom")]
    touches = {"rom": (10, 0), "mem_resp": (10, 20)}
    # rom at distance 10 - t... a tie happens when |10 - (0+t)| == 10
    fs, _ = feasible(glyphs, touches, moving="rom", axis="y")
    for t in range(-30, 40):
        assert (t in fs) == _truth(glyphs, touches, "rom", "y", t), t


def test_moving_the_wanted_touch_gives_a_single_interval():
    glyphs = [(5, 5, "r", "rom"), (5, 40, "r", "rom")]
    touches = {"rom": (0, 0), "mem_resp": (5, 60), "in": (0, 90)}
    fs, bounds = feasible(glyphs, touches, moving="rom", axis="y")
    assert len(fs.parts) == 1, str(fs)
    assert bounds, "an interval must be attributable to the constraints that set it"


def test_moving_a_rival_gives_two_rays():
    """The non-convex case: 'move it further' can undo a smaller fix."""
    glyphs = [(5, 5, "r", "rom")]
    touches = {"rom": (5, 0), "mem_resp": (5, 3)}
    fs, _ = feasible(glyphs, touches, moving="mem_resp", axis="y")
    assert len(fs.parts) == 2, str(fs)
    for t in range(-40, 40):
        assert (t in fs) == _truth(glyphs, touches, "mem_resp", "y", t), t


def test_violations_reports_every_misbind_not_just_the_first():
    """``check_bindings`` raises on the first; a repair needs all of them."""
    glyphs = [(5, 50, "r", "rom"), (5, 55, "r", "rom"), (5, 5, "r", "rom")]
    touches = {"rom": (5, 0), "mem_resp": (5, 60)}
    v = violations(glyphs, touches)
    assert len(v) == 2
    assert {one.at for one in v} == {(5, 50), (5, 55)}
    with pytest.raises(MachineError):
        check_bindings(glyphs, touches)


@pytest.mark.parametrize("axis", ["x", "y"])
def test_agrees_with_check_bindings_on_random_placements(axis):
    """The validation that matters: exhaustive agreement, many random cases."""
    rng = random.Random(20260730)
    names_in = ["rom", "mem_resp", "in", "stream_resp"]
    for _ in range(300):
        pool = rng.sample(names_in, rng.randint(2, 4))
        touches = {n: (rng.randint(0, 40), rng.randint(0, 40)) for n in pool}
        want = pool[0]
        glyphs = [
            (rng.randint(0, 40), rng.randint(0, 40), "r", want)
            for _ in range(rng.randint(1, 4))
        ]
        moving = rng.choice(pool)
        fs, _ = feasible(glyphs, touches, moving=moving, axis=axis)
        for t in range(-60, 61):
            assert (t in fs) == _truth(glyphs, touches, moving, axis, t), (
                f"axis={axis} moving={moving} t={t} touches={touches} glyphs={glyphs}"
            )


def test_agrees_for_outgoing_glyphs_too():
    """``s`` binds outgoing pipes; the direction filter must be the checker's."""
    rng = random.Random(7)
    names_out = ["mem_req", "out", "dsp_addr"]
    for _ in range(200):
        pool = rng.sample(names_out, rng.randint(2, 3))
        touches = {n: (rng.randint(0, 30), rng.randint(0, 30)) for n in pool}
        glyphs = [(rng.randint(0, 30), rng.randint(0, 30), "s", pool[0])
                  for _ in range(rng.randint(1, 3))]
        moving = rng.choice(pool)
        fs, _ = feasible(glyphs, touches, moving=moving, axis="y")
        for t in range(-40, 41):
            assert (t in fs) == _truth(glyphs, touches, moving, "y", t), t
