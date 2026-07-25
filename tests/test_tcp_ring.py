"""The `tcp` ring machine, run round-by-round on the reference interpreter.

`tcp` was the third-largest score in the repo at 111x78 / 92,342 ticks /
**1.138e9**, and ~80% of those ticks were the LM-1 tape emulating a buffer
addressed by ``seq``.  :mod:`randomfun2026solvers.tcp_ring` replaces the whole
CPU with one worker room and a 17-word pipe ring, so the tests here pin the
three things that can silently regress:

* **the grid** -- byte-identical to what the generator emits, and `tcp_cpu.man`
  untouched beside it;
* **the footprint**, because ``max(w, h)**2`` is half the score;
* **every public case**, round by round, on the engine -- with the rounds joined
  by ``/`` so the judge withholds each packet until the previous one's output is
  complete, which is the only way the maximum-delay rule is actually exercised.

Plus the two invariants that are *not* visible in the output: that every pipe op
binds to the pipe it was written for (nearest-pipe binding is decided by column
here, and a wrong bind reads plausible data), and that the ring has capacity for
its resident words (an under-capacity ring deadlocks with no error at all).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers import tcp_ring
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.scoring import score_program

ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "tasks" / "solutions" / "tcp_ring.man"
PROBLEM = ROOT / "tasks" / "problems" / "tcp.json"

#: Committed geometry.  Width binds the score, so both are pinned exactly.
GRID_W, GRID_H, AREA2 = 42, 41, 1764

#: Engine-measured tick of the final expected output, per public case.  Pinned
#: exactly rather than as a bound: ticks here are deterministic, and an
#: inequality would hide an improvement as readily as a regression.
CASE_TICKS = {
    "in-order stream": 1770,
    "single max-displacement swap": 5142,
    "drain burst": 5042,
    "loss case": 4322,
    "shortest stream": 240,
    "block-reversed n=32": 10168,
}


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def case_ids() -> list[str]:
    return [c["name"] for c in public_cases()]


def test_generator_reproduces_the_committed_grid() -> None:
    assert tcp_ring.build() == SOLUTION.read_text().rstrip("\n").split("\n")


def test_the_cpu_build_is_left_alone() -> None:
    """The ring machine is a second solution, not a replacement in place."""
    cpu = ROOT / "tasks" / "solutions" / "tcp_cpu.man"
    rows = cpu.read_text().rstrip("\n").split("\n")
    assert (max(len(r) for r in rows), len(rows)) == (111, 78)


def test_footprint_does_not_regress() -> None:
    rows = tcp_ring.build()
    w, h = max(len(r) for r in rows), len(rows)
    assert (w, h) == (GRID_W, GRID_H)
    assert max(w, h) ** 2 == AREA2


def test_sixteen_slots_is_what_the_problem_allows() -> None:
    """The window size is a consequence of the spec, not a tuning choice.

    ``seq >= want + 16`` is a hard failure, so a stored packet is always within
    15 of the head and 16 slots plus one header word is the whole resident state
    -- against the 52-cell tape the CPU build carried.
    """
    text = PROBLEM.read_text()
    assert "16 or more above" in text
    assert tcp_ring.RING_WORDS == 17


@pytest.mark.parametrize("case", public_cases(), ids=case_ids())
def test_public_cases(case: dict) -> None:
    """Rounds joined with ``/``: the next packet is withheld until we drain."""
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    want = [int(t) for r in case["rounds"] for t in r["out"]]
    snap = Littleman().judge(
        SOLUTION.read_text(),
        input=inp,
        expected=" / ".join(" ".join(r["out"]) for r in case["rounds"]),
        max_ticks=400_000,
    )
    assert snap.fatal is None, f"fatal: {snap.fatal}"
    assert list(snap.output) == want


def test_the_score_beats_the_cpu_build_by_two_orders_of_magnitude() -> None:
    result = score_program(SOLUTION, PROBLEM)
    assert {c.name: c.ticks for c in result.cases} == CASE_TICKS
    assert result.area2 == AREA2
    assert result.score == pytest.approx(AREA2 * (sum(CASE_TICKS.values()) / 6))
    assert result.score < 1.138e9 / 100


def test_every_pipe_op_binds_to_the_pipe_it_was_written_for() -> None:
    """All four pipes sit on the north wall, so binding is decided by column.

    A north anchor is ``|x - col| + y + 1`` away from every cell, so the ``y``
    term cancels and "nearest pipe" is one-dimensional at every row: columns at
    or west of ``SPLIT`` reach input/output, columns east of it reach the ring.
    That is what makes the census below a *checkable* invariant rather than a
    hope -- and a wrong bind here would read a plausible value, not fault.
    """
    lm = Littleman()
    rows = SOLUTION.read_text().rstrip("\n").split("\n")
    pipes = {tuple(s.pos.as_tuple() for s in p.path): p for p in lm.analyze(SOLUTION).pipes}
    io_pipes = {cells for cells, p in pipes.items() if len(cells) == 4}
    assert len(io_pipes) == 2, "input and output are the two 4-cell pipes"

    census = {"io": 0, "ring": 0}
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch not in "rs" or not _inside_worker(lm, x, y):
                continue
            cells = tuple(c.as_tuple() for c in lm.route(SOLUTION, x, y))
            assert cells in pipes, f"{ch!r} at ({x},{y}) binds to no pipe"
            side = "io" if cells in io_pipes else "ring"
            census[side] += 1
            want = "io" if x - 1 <= tcp_ring.SPLIT else "ring"
            assert side == want, f"{ch!r} at ({x},{y}) bound to {side}, wanted {want}"
    # 4 input reads (n, seq, and val on each of the store and drain paths) and
    # 3 output sends (-1, the awaited packet, and the drain loop's emit).
    assert census == {"io": 7, "ring": 19}


def test_the_ring_holds_its_resident_words() -> None:
    """Under capacity a pipe ring deadlocks with no error, so assert the slack."""
    lm = Littleman()
    ring = [p for p in lm.analyze(SOLUTION).pipes if len(p.path) != 4]
    assert len(ring) == 2
    assert sum(len(p.path) for p in ring) >= tcp_ring.RING_WORDS + 1


def _inside_worker(lm: Littleman, x: int, y: int) -> bool:
    """True for cells in the worker room -- the relay's own ``r``/``s`` are not ours."""
    def area(r: object) -> int:
        lo, hi = r.min_, r.max_  # type: ignore[attr-defined]
        return (hi.x - lo.x) * (hi.y - lo.y)

    room = max(lm.analyze(SOLUTION).rooms, key=area)
    return room.min_.x < x < room.max_.x and room.min_.y < y < room.max_.y
