"""The scan gadget `subset_sum_mitm` is priced on, measured on the engine.

The meet-in-the-middle design's entire cost is `2^hL * (2^hR + 1) = 1,052,672`
comparisons of a ring word against a query, and against a 15,000,000-tick cap
that is only a machine if a comparison costs about five ticks. This probe is the
smallest grid that performs exactly that comparison, so the constant is a
measurement rather than an argument.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from randomfun2026solvers import scoring
from randomfun2026solvers.subset_sum_scan_probe import (
    IH,
    IW,
    RING_CAP,
    build,
    expected,
    worker,
)

REPO = Path(__file__).resolve().parents[1]
GRID = REPO / "tasks" / "solutions" / "subset-sum_scan_probe.man"

#: Engine-measured, and the whole reason the design is affordable. A pass over
#: `k` words of ring B costs `5k` ticks, flat: the station's two-column loop is
#: ten cells and carries two words a lap.
TICKS_PER_WORD = 5.0


def test_checked_in_grid_matches_the_generator() -> None:
    """A generated grid carries no comments, so drift here is invisible."""
    assert GRID.read_text(encoding="utf-8") == "\n".join(build()) + "\n"


def test_scan_station_is_ten_cells_carrying_two_words() -> None:
    """Five cells a side, `X` at both turning corners, `r s ~` on each straight.

    If this ever becomes four cells a side the loop still runs — it just stops
    re-sending, and ring B quietly empties. Pinning the shape catches that at
    generation time instead of as a wrong answer six phases later.
    """
    c = worker()
    left, right, top = 14, 15, 10
    assert [c.get(right, top + d) for d in range(5)] == ["v", "r", "s", "~", "X"]
    assert [c.get(left, top + d) for d in range(5)] == ["X", "~", "s", "r", "^"]


def test_every_pipe_op_lands_in_its_own_column_band() -> None:
    """`s`/`r` bind to the *nearest* pipe, and all four hang off the north wall.

    So binding is one-dimensional — nearest column — and a block that slides down
    a row cannot silently rebind. The generator asserts this cell by cell; this
    checks the invariant survives on the finished art.
    """
    c = worker()
    for (x, y), ch in c.cell.items():
        if ch == "r":
            assert x <= 9 or x >= 13, f"r at ({x},{y}) is in the ambiguous band"
        if ch == "s":
            assert x <= 9 or x >= 13, f"s at ({x},{y}) is in the ambiguous band"


def test_ring_holds_the_values_the_sentinel_and_a_free_cell() -> None:
    """A ring exactly as long as its contents deadlocks silently on the send."""
    rows = build()
    assert max(len(r) for r in rows) == 70
    assert len(rows) == IH + 9
    assert RING_CAP >= 72


def test_worker_interior_is_what_the_generator_claims() -> None:
    c = worker()
    assert (c.w, c.h) == (IW, IH)


def test_expected_oracle() -> None:
    assert expected([1, 2, 3], 2) == [1]
    assert expected([1, 2, 3], 4) == [0]


# ── the heavy tier: everything below drives the reference engine ─────────────


def _route_check() -> str:
    return subprocess.run(
        ["node", str(REPO / "littleman" / "tools" / "route-check.mjs"), str(GRID)],
        capture_output=True, text=True, check=True,
    ).stdout


@pytest.mark.slow
def test_grid_parses_with_exactly_four_pipes() -> None:
    """A pipe that fails to parse still lets the grid load, so count them."""
    out = _route_check()
    assert out.count("\n  0:") + out.count("\n  1:") == 2
    assert "  3: " in out and "  4: " not in out.split("'")[0]
    assert "ERR" not in out


@pytest.mark.slow
@pytest.mark.parametrize(
    ("values", "query"),
    [
        ([11, 12, 13], 12),
        ([11, 12, 13], 11),
        ([11, 12, 13], 13),
        ([11, 12, 13], 99),
        ([7], 7),
        ([7], 8),
        (list(range(1000, 1064)), 1000),
        (list(range(1000, 1064)), 1063),
        (list(range(1000, 1064)), 999),
    ],
)
def test_probe_answers_correctly_on_the_engine(values: list[int], query: int) -> None:
    case = {
        "name": "probe",
        "rounds": [{"in": [str(len(values)), *map(str, values), str(query)],
                    "out": [str(v) for v in expected(values, query)]}],
    }
    prob = {"slug": "probe", "scoring": "footprint-tick", "tickCap": 200_000,
            "publicTestData": [case]}
    scoring.score_program(GRID, prob)   # raises unless the expected output appears


@pytest.mark.slow
def test_a_scanned_word_costs_exactly_five_ticks() -> None:
    """Move the hit further down a 100-word ring and read the slope off.

    Everything but the scan is identical between these cases — same fill, same
    query read, same answer lane — so the difference is the scan and nothing
    else. 5.000 ticks/word makes the design's ceiling 5,263,360 ticks at n = 20,
    a third of the cap.
    """
    k = 100
    values = [1000 + 7 * i for i in range(k)]
    positions = [0, 20, 40, 60, 80]
    cases = [
        {"name": f"hit@{p}",
         "rounds": [{"in": [str(k), *map(str, values), str(values[p])], "out": ["1"]}]}
        for p in positions
    ]
    prob = {"slug": "probe", "scoring": "footprint-tick", "tickCap": 200_000,
            "publicTestData": cases}
    res = scoring.score_program(GRID, prob)
    ticks = {c.name: c.ticks for c in res.cases}
    for a, b in zip(positions, positions[1:]):
        slope = (ticks[f"hit@{b}"] - ticks[f"hit@{a}"]) / (b - a)
        assert slope == pytest.approx(TICKS_PER_WORD), f"{a}->{b} cost {slope}"
