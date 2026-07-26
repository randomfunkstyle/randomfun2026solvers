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
import os
from pathlib import Path

import pytest
from randomfun2026solvers import tcp_ring
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.scoring import score_program

ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "tasks" / "solutions" / "tcp_ring.man"
PROBLEM = ROOT / "tasks" / "problems" / "tcp.json"

#: Committed geometry.  A square: ``mancompact`` cut the grid's dead columns down
#: to the point where width and height bind equally, so neither can be traded for
#: the other without a re-layout.  Both pinned exactly.
GRID_W, GRID_H, AREA2 = 39, 39, 1521

#: Engine-measured tick of the final expected output, per public case.  Pinned
#: exactly rather than as a bound: ticks here are deterministic, and an
#: inequality would hide an improvement as readily as a regression.
CASE_TICKS = {
    "in-order stream": 1767,
    "single max-displacement swap": 5139,
    "drain burst": 5039,
    "loss case": 4319,
    "shortest stream": 237,
    "block-reversed n=32": 10165,
}


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def case_ids() -> list[str]:
    return [c["name"] for c in public_cases()]


def test_generator_reproduces_the_committed_grid() -> None:
    assert tcp_ring.build() == SOLUTION.read_text().rstrip("\n").split("\n")


def test_the_cpu_build_is_left_alone() -> None:
    """The ring machine is a second solution, not a replacement in place.

    111x78 when this was written; 109x74 once the CPU generator started packing the ROM
    with variable-width tokens and letting a simple lane drop at its own micro-program's
    end; 104x74 since ``ADAPTER_TAPE_GAP`` went 6 → 1 and took five columns out of the
    adapter-to-STORE corridor. The point of the assertion is that the LM-1 grid is still
    *there* and still generator-consistent (``test_lm1_machine.py`` pins it against the
    generator), not that its shape never improves — it has now improved three times.
    """
    cpu = ROOT / "tasks" / "solutions" / "tcp_cpu.man"
    rows = cpu.read_text().rstrip("\n").split("\n")
    assert (max(len(r) for r in rows), len(rows)) == (104, 73)


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


@pytest.mark.slow
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
    io_pipes = {tuple(s.pos.as_tuple() for s in p.path) for p in _io_pipes(lm)}
    assert len(io_pipes) == 2, "one pipe from the I room, one into the O room"

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
    ring = _ring_pipes(lm)
    assert len(ring) == 2
    assert sum(len(p.path) for p in ring) >= tcp_ring.RING_WORDS + 1


def _rooms(lm: Littleman) -> tuple[object, object]:
    """``(worker, relay)`` -- the two rooms that are not the 3x3 I/O rooms.

    Identified by size rather than by index, so the band can be re-laid out
    without silently turning these invariants into tautologies.
    """

    def area(r: object) -> int:
        lo, hi = r.min_, r.max_  # type: ignore[attr-defined]
        return (hi.x - lo.x) * (hi.y - lo.y)

    big = sorted(lm.analyze(SOLUTION).rooms, key=area)[-2:]
    return big[-1], big[0]


def _ring_pipes(lm: Littleman) -> list[object]:
    """The two pipes between the worker and the relay -- i.e. the ring itself."""
    worker, relay = _rooms(lm)
    ends = {tuple(r.min_.as_tuple()) for r in (worker, relay)}  # type: ignore[attr-defined]
    rooms = lm.analyze(SOLUTION).rooms
    ids = {i for i, r in enumerate(rooms) if tuple(r.min_.as_tuple()) in ends}
    return [p for p in lm.analyze(SOLUTION).pipes if {p.src, p.dst} <= ids]


def _io_pipes(lm: Littleman) -> list[object]:
    ring = _ring_pipes(lm)
    return [p for p in lm.analyze(SOLUTION).pipes if p not in ring]


def _inside_worker(lm: Littleman, x: int, y: int) -> bool:
    """True for cells in the worker room -- the relay's own ``r``/``s`` are not ours."""
    room = _rooms(lm)[0]
    return room.min_.x < x < room.max_.x and room.min_.y < y < room.max_.y


slow = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the per-packet cost regressions",
)


def _tick_of_nth_output(lm: Littleman, src: str, inp: str, n: int, cap: int = 400_000) -> int:
    """Smallest tick with at least ``n`` output values (output length is monotonic)."""
    hi = 64
    while len(lm.tick(src, hi, input=inp).output) < n:
        hi = min(hi * 2, cap)
        assert hi < cap, f"only {n - 1} values within {cap} ticks"
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if len(lm.tick(src, mid, input=inp).output) >= n:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _k_stores_then_unlock(k: int) -> str:
    """``k`` packets at offsets ``k..1``, then packet 0, which drains all ``k+1``."""
    rounds = [f"{i} {100 + i}" for i in range(k, 0, -1)] + ["0 100"]
    return f"20 {rounds[0]} / " + " / ".join(rounds[1:])


#: Engine-measured per-packet costs.  Both slopes are exact -- every point lies on
#: the line with zero residual -- which is the evidence that the machine's cost is
#: structural rather than data-dependent.
TICKS_PER_INSERT = 278.0
TICKS_PER_EMIT = 44.0


@slow
def test_an_insert_costs_the_same_at_every_offset() -> None:
    """The lap is 16 slots wide whatever ``d`` is, so insert cost must be flat.

    ``STORE`` rotates ``d`` then ``15 - d``, which is the whole reason the header
    comes back aligned -- and it means the *offset* a packet lands at cannot show
    up in the tick count.  A slope that varied with ``k`` here (each packet in the
    sweep arrives at a different offset) would mean the phase was drifting.
    """
    lm, src = Littleman(), SOLUTION.read_text()
    first = {k: _tick_of_nth_output(lm, src, _k_stores_then_unlock(k), 1) for k in (2, 4, 6, 8, 10)}
    slopes = {(a, b): (first[b] - first[a]) / (b - a) for a, b in ((2, 4), (4, 6), (6, 8), (8, 10))}
    assert set(slopes.values()) == {TICKS_PER_INSERT}


@slow
def test_an_emit_costs_one_rotation_plus_its_station() -> None:
    """Draining is one ring rotation and ~12 glyphs per value, measured flat."""
    lm, src = Littleman(), SOLUTION.read_text()
    spans = {}
    for k in (2, 4, 6, 8, 10):
        inp = _k_stores_then_unlock(k)
        spans[k] = _tick_of_nth_output(lm, src, inp, k + 1) - _tick_of_nth_output(lm, src, inp, 1)
    slopes = {(spans[b] - spans[a]) / (b - a) for a, b in ((2, 4), (4, 6), (6, 8), (8, 10))}
    assert slopes == {TICKS_PER_EMIT}


def test_the_grid_has_no_dead_line_left_to_cut() -> None:
    """``mancompact`` is the arbiter of "as small as this layout goes".

    A dead line is a whole row or column of blanks, walls parallel to the cut and
    pipe bodies parallel to the cut -- geometrically free to delete, but *not*
    semantically free, because pulling a wall in can hand an ``s``/``r`` to a
    different pipe with no other symptom.  Running it here means the committed
    footprint is a measured floor for this block arrangement rather than whatever
    the generator's constants happened to be, and it re-checks the two pipes'
    declared slack while it is at it.
    """
    from randomfun2026solvers.mancompact import compact
    from randomfun2026solvers.manstruct import CapacityHint

    lm = Littleman()
    ids = [i for i, p in enumerate(lm.analyze(SOLUTION).pipes) if p in _ring_pipes(lm)]
    res = compact(SOLUTION, capacity=[CapacityHint(tuple(ids), tcp_ring.RING_WORDS + 1)])
    assert res.cuts == [], f"still cuttable: {res.cuts}"
    assert res.after == (GRID_W, GRID_H)
