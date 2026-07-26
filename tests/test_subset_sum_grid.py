"""What `subset_sum_grid` has actually been measured to do.

The machine is built stage by stage and every stage is read straight off the
engine before the next one is written, because a ring machine's mistakes are
silent: a mis-bound `s` still runs, a ring one word too short deadlocks without
a message, and a wrong header word only shows up as a wrong answer six phases
later.  The two stages below are the ones the solver's whole search reads from.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from randomfun2026solvers import subset_sum_grid as ssg
from randomfun2026solvers.subset_sum_mitm import HR, public_cases

REPO = Path(__file__).resolve().parents[1]


def _ring_v(values: list[int], target: int) -> list[int]:
    """What ring V must hold after loading: `n + 7` words, header first."""
    hl = len(values) - HR
    gl = 1 << hl
    return (
        [gl, gl, 1 << HR, 1 << HR]
        + values[:hl]
        + [-1]
        + values[hl:]
        + [-(target + 1), 0]
    )


def _ring_b(values: list[int]) -> list[int]:
    """What ring B must hold: every right-half subset sum, biased, then `-1`."""
    sums = [1]
    for v in values[len(values) - HR:]:
        sums = [x for y in sums for x in (y, y + v)]
    return [*sums, -1]


# ── the fast tier: shape and binding, no engine ──────────────────────────────


def test_every_pipe_op_is_inside_a_band() -> None:
    """`_op` is the only way an `r`/`s` reaches the grid, and it refuses strays.

    The bands are derived from the anchor columns, so this also pins that a
    block moved sideways cannot quietly rebind: the midpoints are 11.5/13.5 and
    25/26, and every band stops short of both.
    """
    for band, (lo, hi) in ssg.BANDS.items():
        assert lo <= hi
        for anchor in (ssg.IN_COL, ssg.OUT_COL):
            assert (band == "io") == (lo <= anchor <= hi)
        for anchor in (ssg.VRET_COL, ssg.VFWD_COL):
            assert (band == "v") == (lo <= anchor <= hi)
        for anchor in (ssg.BRET_COL, ssg.BFWD_COL):
            assert (band == "b") == (lo <= anchor <= hi)


def test_a_stray_pipe_op_is_refused_at_generation_time() -> None:
    from randomfun2026solvers.circuit import Circuit

    c = Circuit(ssg.IW, ssg.IH)
    with pytest.raises(ValueError, match="outside the 'v' band"):
        ssg.vs(c, ssg.OUT_COL, 0)
    with pytest.raises(ValueError, match="outside the 'io' band"):
        ssg.rin(c, ssg.VRET_COL, 0)


def test_the_grid_holds_no_backtick() -> None:
    """Every constant is reachable from a digit, so the vertical-pairing load
    error that eats multi-digit literals cannot arise."""
    assert "`" not in "\n".join(ssg.build("load"))


def test_rings_are_longer_than_their_contents() -> None:
    """A ring exactly as long as its contents blocks a send behind its own
    backlog and deadlocks in silence, so both are sized with slack."""
    assert ssg.B_CAP >= (1 << HR) + 2
    assert ssg.V_CAP >= 20 + 7 + 1


# ── the heavy tier: everything below drives the reference engine ─────────────


def _pipes(grid: Path) -> str:
    return subprocess.run(
        ["node", str(REPO / "littleman" / "tools" / "route-check.mjs"), str(grid)],
        capture_output=True, text=True, check=True,
    ).stdout


@pytest.mark.slow
def test_all_six_pipes_parse(tmp_path: Path) -> None:
    """A pipe that fails to parse still lets the grid load, so count them."""
    grid = tmp_path / "load.man"
    grid.write_text("\n".join(ssg.build("load")) + "\n", encoding="utf-8")
    out = _pipes(grid)
    assert "ERR" not in out
    import re

    listed = [ln for ln in out.splitlines() if re.match(r"  \d+: \d+ cells", ln)]
    assert len(listed) == 6, listed
    # Both rings must be long enough, and route-check is the only thing that
    # knows how long a drawn pipe actually is.
    lens = sorted(int(re.search(r": (\d+) cells", ln).group(1)) for ln in listed)
    assert lens[-1] + lens[-2] >= ssg.B_CAP


@pytest.mark.slow
@pytest.mark.parametrize("case", public_cases(), ids=lambda c: c[0])
def test_ring_v_loads_exactly(tmp_path: Path, case) -> None:
    """The header, both markers and every value, read off the engine."""
    from randomfun2026solvers.littleman import Littleman

    _, values, target, _ = case
    grid = tmp_path / "load.man"
    grid.write_text("\n".join(ssg.build("load")) + "\n", encoding="utf-8")
    inp = " ".join(str(v) for v in [len(values), *values, target])
    snap = Littleman().tick(grid, 400_000, input=inp)
    assert snap.model_dump()["output"] == _ring_v(values, target)


@pytest.mark.slow
@pytest.mark.parametrize("case", public_cases(), ids=lambda c: c[0])
def test_ring_b_holds_every_right_half_sum(tmp_path: Path, case) -> None:
    """The doubling pass is the one block no later phase can be read back
    through, so it gets its own stage and its own check."""
    from randomfun2026solvers.littleman import Littleman

    _, values, _target, _ = case
    grid = tmp_path / "loadb.man"
    grid.write_text("\n".join(ssg.build("loadb")) + "\n", encoding="utf-8")
    inp = " ".join(str(v) for v in [len(values), *values, 1000])
    snap = Littleman().tick(grid, 900_000, input=inp)
    assert snap.model_dump()["output"] == _ring_b(values)
