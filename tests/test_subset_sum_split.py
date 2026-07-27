"""What the two-room `subset-sum` machine has been measured to do.

The one-room build's tests are `tests/test_subset_sum_grid.py`; these are the
ones the *split* adds, and they are all about the thing a second room makes
possible and silent — a ring op binding to the wrong pipe.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from randomfun2026solvers import scoring, subset_sum_split as sp
from randomfun2026solvers.subset_sum_mitm import HR, expected_output, public_cases

REPO = Path(__file__).resolve().parents[1]
GRID = REPO / "tasks" / "solutions" / "subset-sum_split.man"


def _ring_v(values: list[int], target: int) -> list[int]:
    hl = len(values) - HR
    gl = 1 << hl
    return (
        [gl, gl, 1 << HR, 1 << HR]
        + values[:hl]
        + [-1]
        + values[hl:]
        + [-(target + 1), 0]
    )


# ── the fast tier: binding and shape, no engine ──────────────────────────────


def test_every_pipe_op_wins_its_anchor_strictly() -> None:
    """`_op` derives the safe columns from the room's anchors and requires the
    intended pipe to win by a strict margin.

    A tie is broken by reading order, which is not something a block may depend
    on, and the failure is silent: the grid loads, the answer has the right
    shape, and only its values are wrong.  Both rooms are built here, which is
    the whole check — `_op` raises rather than returning a verdict.
    """
    sp.room_a("full")
    sp.room_b("full")


def test_a_forwarding_loop_that_reaches_the_baton_is_refused(monkeypatch) -> None:
    """The bug this check exists for, pinned as a build error.

    Room A's baton sits at column 9 and ring V's send anchor at 21, so the
    midpoint is 15 and a send at 14 goes down the baton.  The first draft laid
    the loop from 14 — the one-room build's `v` band — and every ring-V word
    room A forwarded after handing over went to room B as a baton.
    """
    monkeypatch.setattr(sp, "A_LOOP_LO", 14)
    with pytest.raises(ValueError, match="would bind to the wrong pipe"):
        sp.room_a("full")


def test_the_runtime_sees_exactly_seven_pipe_mouths() -> None:
    """`route-check.mjs` reports the pipes that were *drawn*; the engine builds
    one at every arrowhead whose backward cell is a room border, and both it and
    `lm.mjs analyze` fold a stray one into its neighbour.

    Seven is the whole design: I, O, two per ring, and the baton.  A second room
    doubles the wall length an arrowhead can sit against, so this is counted for
    every stage the generator can emit.
    """
    for stage in ("full", "loadv"):
        sp._mouths(sp.build(stage), expect=7)


def test_the_gap_columns_carry_no_arrowhead_pointing_at_a_wall() -> None:
    """Ring B climbs down the two columns between the rooms.

    A west-pointing arrowhead in the western gap column, on a row where room A
    has a wall beside it, ends the pipe there; an east-pointing one starts a
    second pipe out of room A.  The eastern column is the mirror against room B.
    This is the `column 48 is reserved` lesson of the one-room build, which cost
    a submission, now with two walls to sit against instead of one.
    """
    rows = sp.build("full")
    spans = {
        sp.GAP_FWD: (sp.BAND - 1, sp.BAND + sp.A_IH, "<>"),   # room A is west
        sp.GAP_RET: (sp.BAND - 1, sp.BAND + sp.B_IH, "<>"),   # room B is east
    }
    for x, (top, bot, arrows) in spans.items():
        for y in range(top, min(bot + 1, len(rows))):
            row = rows[y]
            if x < len(row) and row[x] in arrows:
                raise AssertionError(f"{row[x]!r} at ({x},{y}) is beside a room wall")


def test_the_grid_holds_no_backtick() -> None:
    assert "`" not in "\n".join(sp.build("full"))


def test_checked_in_grid_matches_the_generator() -> None:
    assert GRID.read_text(encoding="utf-8") == "\n".join(sp.build("full")) + "\n"


def test_rings_are_longer_than_their_contents() -> None:
    assert sp.B_CAP >= (1 << HR) + 2
    assert sp.V_CAP >= 20 + 7 + 1


# ── the heavy tier: everything below drives an engine ────────────────────────


@pytest.mark.slow
def test_all_seven_pipes_parse() -> None:
    out = subprocess.run(
        ["node", str(REPO / "littleman" / "tools" / "route-check.mjs"), str(GRID)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "ERR" not in out
    import re

    listed = [ln for ln in out.splitlines() if re.match(r"  \d+: \d+ cells", ln)]
    assert len(listed) == 7, listed


@pytest.mark.slow
@pytest.mark.parametrize("case", public_cases(), ids=lambda c: c[0])
def test_ring_v_survives_both_rooms(tmp_path: Path, case) -> None:
    """Stage `loadv`: room A loads the ring, hands over, and room B finds the
    head and pours it out.  Every word, in order, through two pipes and an idle
    forwarding loop — which is the part a one-room build never has to prove."""
    from randomfun2026solvers.littleman import Littleman

    _, values, target, _ = case
    grid = tmp_path / "loadv.man"
    grid.write_text("\n".join(sp.build("loadv")) + "\n", encoding="utf-8")
    inp = " ".join(str(v) for v in [len(values), *values, target])
    want = _ring_v(values, target)
    snap = Littleman().tick(grid, 600_000, input=inp)
    assert snap.model_dump()["output"][: len(want)] == want


@pytest.mark.slow
def test_every_public_case_passes() -> None:
    res = scoring.score_program(GRID, "subset-sum")
    assert len(res.cases) == 7
    assert max(c.ticks for c in res.cases) < 15_000_000


@pytest.mark.slow
def test_the_worst_case_fits_the_cap_with_room() -> None:
    """All values even and the target odd is unsatisfiable inside the stated
    constraints, so phase 2 runs to exhaustion and nothing costs more."""
    adv = [2 * (12345 + 3719 * i % 40000) for i in range(20)]
    prob = {
        "slug": "subset-sum", "scoring": "footprint-tick", "tickCap": 15_000_000,
        "publicTestData": [{
            "name": "adversarial n=20, no solution",
            "rounds": [{"in": [str(len(adv)), *map(str, adv), str(sum(adv) // 3 | 1)],
                        "out": ["0"]}],
        }],
    }
    res = scoring.score_program(GRID, prob)
    assert res.cases[0].ticks * 2 < 15_000_000


@pytest.mark.slow
def test_random_inputs_inside_the_constraints() -> None:
    """The private cases are where this problem was being failed, so sample the
    stated constraints rather than the seven public shapes."""
    import random

    rng = random.Random(20260726)
    cases = []
    for i in range(12):
        n = rng.randint(10, 20)
        vals = [rng.randint(1, 99999) for _ in range(n)]
        tot = sum(vals)
        t = rng.randint(max(101, tot // 10), min(999_999, 3 * tot // 5))
        cases.append({
            "name": f"random n={n} #{i}",
            "rounds": [{"in": [str(n), *map(str, vals), str(t)],
                        "out": [str(v) for v in expected_output(vals, t)]}],
        })
    prob = {"slug": "subset-sum", "scoring": "footprint-tick",
            "tickCap": 15_000_000, "publicTestData": cases}
    res = scoring.score_program(GRID, prob)
    assert max(c.ticks for c in res.cases) < 15_000_000
