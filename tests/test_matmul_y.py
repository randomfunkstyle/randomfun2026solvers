"""The two primitives a ``Y``-parallel ``matmul`` is built from.

Design: ``docs/superpowers/specs/2026-07-26-matmul-y-parallel-design.md``.

What is worth pinning here is *behaviour*, not any measured cost (``AGENTS.md``):
that men sharing a cycle cannot reorder a FIFO, and that the ADDER computes the
right column sums. The throughput numbers that motivate the design live in the
spec and in commit messages, because improving them is not a test failure.

The engine-backed cases are marked slow: they shell out to Node, and the fast
tier is a loop you run dozens of times an hour.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import matmul_y  # noqa: E402
from randomfun2026solvers.lm1.machine import MachineError, _Grid  # noqa: E402

LM = REPO / "littleman"
VALID_OPS = set("0123456789 `.MWN+-*/%&|~{}<>^vVXxYdabmq]sSrRUH")
STRUCTURAL = set("+-|<>^v=:")

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not (LM / "lm.mjs").exists(),
    reason="node and littleman/lm.mjs required",
)


def _judge(grid: str, values: list[int], expected: list[int], tmp: Path) -> tuple[list[int], int]:
    """Run to the settle tick. ``judge``, not ``run``: this machine never halts.

    You pass the moment the last correct value is emitted (``SPEC.md``), and a
    working matmul loops back for the next row instead of halting — so ``run``
    would report a tick-cap error on a perfectly good grid.
    """
    path = tmp / "probe.man"
    path.write_text(grid, encoding="utf-8")
    out = subprocess.run(
        ["node", "lm.mjs", "judge", str(path),
         "--input", " ".join(map(str, values)),
         "--expected", " ".join(map(str, expected)),
         "--json", "--max-ticks", "60000"],
        cwd=LM, capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0, out.stderr.strip()[:400]
    snap = json.loads(out.stdout)
    return [int(v) for v in snap["output"]], snap["step"]


# ── the ADDER ────────────────────────────────────────────────────────────────
def test_the_adder_grid_is_made_of_legal_glyphs() -> None:
    grid, _ = matmul_y.build_adder_probe()
    bad = {ch for row in grid.split("\n") for ch in row} - VALID_OPS - STRUCTURAL - {"@", "I", "O"}
    assert not bad, f"grid contains glyphs the interpreter rejects: {sorted(bad)}"


def test_every_adder_pipe_is_long_enough_for_its_job() -> None:
    """Ring C must hold a whole row of C or the ADDER blocks mid-row.

    A stall here is silent on the real engine — the man just stops — so it is a
    build-time assertion rather than something a case is expected to catch.
    """
    _, caps = matmul_y.build_adder_probe()
    assert min(caps.values()) >= 2, f"a pipe is shorter than the minimum: {caps}"
    assert min(caps["cout"], caps["cin"]) >= 17, f"ring C holds too little: {caps}"


def test_the_adders_west_pipes_are_ordered_so_they_cannot_cross() -> None:
    """``cin`` topmost, then ``prod``, then ``cmd`` — see the module docstring.

    ``cin`` arrives from the relay *above* and prod/cmd from MAIN *below*; the
    only assignment where none of the three horizontal legs crosses another's
    column is this one. Reordering these constants silently produces a grid that
    fails to load, so the intent is pinned rather than left in a comment.
    """
    assert matmul_y.A_CIN < matmul_y.A_PROD < matmul_y.A_CMD
    assert matmul_y.A_COUT < matmul_y.A_OUT
    assert matmul_y.PROD_CLIMB < matmul_y.CMD_CLIMB
    assert matmul_y.CIN_DROP < matmul_y.ADDER_AT[0]


def test_a_cycle_too_small_to_close_is_refused() -> None:
    cells: dict[tuple[int, int], str] = {}
    put = matmul_y._writer(cells, "probe")
    with pytest.raises(MachineError, match="too small to close"):
        matmul_y.counted_cycle(put, (3, 4), 2, 4, {})
    with pytest.raises(MachineError, match="too small to close"):
        matmul_y.counted_cycle(put, (3, 7), 2, 2, {})


@node_required
@pytest.mark.slow
@pytest.mark.parametrize(
    ("k", "m", "products"),
    [
        (2, 3, [[1, 2], [10, 20], [100, 200]]),
        (3, 2, [[1, 2, 3], [-10, -20, -30]]),
        (4, 4, [[1, 1, 1, 1], [2, -2, 2, -2], [30, 30, -30, -30], [-99, 99, 99, -99]]),
        (16, 2, [list(range(1, 17)), [-1] * 16]),
        (2, 16, [[i, -i] for i in range(16)]),
    ],
)
def test_the_adder_accumulates_a_row_of_c(k, m, products, tmp_path) -> None:
    """Seed on t=0, accumulate M-1 passes, emit K values — negatives included."""
    grid, _ = matmul_y.build_adder_probe()
    values, expected = matmul_y.adder_case(k, m, products)
    got, _ticks = _judge(grid, values, expected, tmp_path)
    assert got == expected


# ── men on a cycle ───────────────────────────────────────────────────────────
def _ring(span: int, men: int) -> str:
    """One room, one clockwise cycle, ``men`` runners seeded onto it by ``Y``.

    Deliberately the simplest thing that exercises the claim: the cycle reads the
    input pipe and writes the output pipe, so the output *is* the read order.
    """
    north, south, west = 6, 10, 2
    east = west + span - 1
    g = _Grid()
    cells: dict[tuple[int, int], str] = {}
    put = matmul_y._writer(cells, "ring")

    put(west, north, ">")
    put(east, north, "v")
    put(east, south, "<")
    put(west, south, "^")
    put(west + 4, north, "r")
    put(west + span // 2, north, "s")
    for x in range(west + 1, east):
        put(x, north, " ")
        put(x, south, " ")
    for y in range(north + 1, south):
        put(west, y, " ")
        put(east, y, " ")
    matmul_y.seed_chain(put, (west, south + 2), men, south)

    height = max(y for _, y in cells) + 1
    g.room(0, north - 1, east + 2, height + 1)
    g.blit(0, north - 1, {(x, y - north + 1): ch for (x, y), ch in cells.items()})
    g.room(2, 0, 4, 2)
    g.put(3, 1, "I")
    g.draw_pipe([(3, 3), (3, north - 2)])
    g.room(east + 5, north - 1, east + 7, north + 1)
    g.put(east + 6, north, "O")
    g.draw_pipe([(east + 3, north), (east + 4, north)])
    return "\n".join(g.rows()) + "\n"


@node_required
@pytest.mark.slow
@pytest.mark.parametrize("men", [1, 2, 3, 4, 6, 8])
def test_men_sharing_a_cycle_never_reorder_the_fifo(men, tmp_path) -> None:
    """The claim the whole design rests on.

    Men on a 1-D cycle cannot overtake each other, so they pass the ``r`` cell
    and the ``s`` cell in the same fixed rotational order — the reads and the
    writes are the same permutation however the blocking falls out. If this ever
    fails, parallelising the multiply is unsound and the design is dead.
    """
    values = list(range(11, 51))
    got, _ticks = _judge(_ring(24, men), values, values, tmp_path)
    assert got == values


@node_required
@pytest.mark.slow
def test_more_men_on_one_cycle_is_strictly_faster(tmp_path) -> None:
    """Throughput is ``cycle / men``, so doubling the men roughly halves the time.

    A relative comparison inside one run, not a recorded tick count: the absolute
    numbers belong in the spec, but the *scaling* is the reason `Y` is in this
    design at all, and it would be silently lost by a bad seeding change.
    """
    values = list(range(11, 51))
    ticks = {}
    for men in (1, 2, 4):
        got, t = _judge(_ring(24, men), values, values, tmp_path)
        assert got == values
        ticks[men] = t
    assert ticks[2] < ticks[1] * 0.6, ticks
    assert ticks[4] < ticks[2] * 0.6, ticks
