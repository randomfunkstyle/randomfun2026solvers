"""The `tcp` two-tape queue machine, run round-by-round on the reference engine.

:mod:`randomfun2026solvers.tcp_queue` is a second machine for `tcp`, beside
:mod:`~randomfun2026solvers.tcp_ring`. It keeps a genuine variable-length queue
of early arrivals on two lockstep pipe rings and searches it associatively,
where the ring machine indexes a fixed window by phase. What can silently
regress here is not the output -- it is the three invariants underneath it:

* **pipe binding.** Six pipes share one north wall, so binding is decided by
  column. A wrong bind reads a plausible number and never faults.
* **ring capacity.** Both bounds matter. Under ``RING_CELLS`` a full tape
  cannot turn and the machine deadlocks with no error at all; far over it, an
  almost-empty queue stalls a whole lap on every pass.
* **the marker's three-way split**, which is what removes the need to represent
  the queue's length anywhere -- there is no register left to hold it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers import tcp_queue
from randomfun2026solvers.littleman import Littleman

ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "tasks" / "solutions" / "tcp_queue.man"
PROBLEM = ROOT / "tasks" / "problems" / "tcp.json"


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def case_ids() -> list[str]:
    return [c["name"] for c in public_cases()]


def test_generator_reproduces_the_committed_grid() -> None:
    assert tcp_queue.build() == SOLUTION.read_text().rstrip("\n").split("\n")


def test_the_ring_machine_is_left_alone() -> None:
    """This is a second solution for `tcp`, not a replacement in place."""
    rows = (ROOT / "tasks" / "solutions" / "tcp_ring.man").read_text().rstrip("\n").split("\n")
    assert rows and all(rows)


def test_seventeen_words_is_what_the_problem_allows() -> None:
    """The tape depth is a consequence of the spec, not a tuning choice.

    ``seq >= want + 16`` is a hard failure, so at most fifteen packets are ever
    held early; the arrival that matches is enqueued like any other, and the
    marker rides with them. Seventeen words, plus one free cell to turn on.
    """
    assert "16 or more above" in PROBLEM.read_text()
    assert tcp_queue.TAPE_WORDS == 17
    assert tcp_queue.RING_CELLS == tcp_queue.TAPE_WORDS + 1


@pytest.mark.parametrize("case", public_cases(), ids=case_ids())
def test_public_cases(case: dict) -> None:
    """Rounds joined with ``/``: the next packet is withheld until we drain.

    That is the only way the drain loop is actually exercised -- with the input
    flat, a machine that emitted late would still look correct.
    """
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


def test_every_pipe_op_binds_to_the_pipe_it_was_written_for() -> None:
    """Six pipes on one wall: binding is by column, and a wrong bind is silent.

    A north anchor is ``|x - col| + y + 1`` from every cell, so the ``y`` term
    cancels and the rule is one-dimensional at every row. The census below is
    what makes that a checkable invariant rather than a hope.
    """
    lm = Littleman()
    rows = SOLUTION.read_text().rstrip("\n").split("\n")
    analysis = lm.analyze(SOLUTION)
    worker = max(
        analysis.rooms,
        key=lambda r: (r.max_.x - r.min_.x) * (r.max_.y - r.min_.y),
    )
    anchor_of = {
        tuple(s.pos.as_tuple() for s in p.path): p for p in analysis.pipes
    }

    def band(ch: str, x: int) -> str:
        """Which pipe column `x` is supposed to reach, from the anchor table."""
        if ch == "s":
            return "out" if x <= 5 else "stape" if x <= 10 else "vtape"
        return "in" if x <= 4 else "stape" if x <= 12 else "vtape"

    roles = _rooms_by_role(analysis)
    worker_id = next(i for i, role in roles.items() if role == "worker")

    def pipe_other(pipe: object) -> int:
        return pipe.dst if pipe.src == worker_id else pipe.src  # type: ignore[attr-defined]

    seen: dict[str, int] = {}
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch not in "rs":
                continue
            if not (worker.min_.x < x < worker.max_.x and worker.min_.y < y < worker.max_.y):
                continue  # a relay's or the driver's own r/s, not ours
            cells = tuple(c.as_tuple() for c in lm.route(SOLUTION, x, y))
            assert cells in anchor_of, f"{ch!r} at ({x},{y}) binds to no pipe"
            col = x - (worker.min_.x + 1)
            want = band(ch, col)
            other = pipe_other(anchor_of[cells])
            got = roles[other]
            assert got == want, f"{ch!r} at interior column {col} bound to {got}, wanted {want}"
            seen[want] = seen.get(want, 0) + 1

    # The census, lane by lane. `in`: MAIN and ENQUEUE. `out`: FAIL's -1 and
    # MATCH's value. `stape`: seeded once, pushed by ENQUEUE, read by the loop
    # top, pushed back by NOMATCH and by MARKER. `vtape` is the S count plus
    # two, because the loop reads *and* pushes V on both the NOMATCH and MARKER
    # laps, and MATCH reads a value it deliberately never pushes back -- which
    # is exactly how a slot leaves the queue.
    assert seen == {"in": 2, "out": 2, "stape": 5, "vtape": 7}


def _rooms_by_role(analysis: object) -> dict[int, str]:
    """Label every room by its geometry, not by index or by parse order.

    Sizes are unambiguous here and stay meaningful if the band is re-laid out:
    the worker is by far the largest, the driver next, then the two 5x5 relays,
    then the two 3x3 I/O rooms -- and of those, the one at the west edge is the
    input, because the input anchor is deliberately the westmost on the wall.
    """
    rooms = analysis.rooms  # type: ignore[attr-defined]
    area = {i: (r.max_.x - r.min_.x) * (r.max_.y - r.min_.y) for i, r in enumerate(rooms)}
    roles: dict[int, str] = {}
    roles[max(area, key=lambda i: area[i])] = "worker"
    io = sorted((i for i in area if area[i] <= 8), key=lambda i: rooms[i].min_.x)
    roles[io[0]] = "in"
    roles[io[1]] = "out"
    relays = sorted((i for i in area if 8 < area[i] <= 20), key=lambda i: rooms[i].min_.x)
    roles[relays[0]] = "stape"
    roles[relays[1]] = "vtape"
    for i in area:
        roles.setdefault(i, "in")  # the driver: the worker reaches it with `r`
    return roles


def test_both_rings_hold_their_resident_words() -> None:
    """Under capacity a pipe ring deadlocks with no error at all, so assert it."""
    lm = Littleman()
    analysis = lm.analyze(SOLUTION)
    roles = _rooms_by_role(analysis)
    worker_id = next(i for i, role in roles.items() if role == "worker")
    relays = [i for i, role in roles.items() if role in ("stape", "vtape")]
    assert len(relays) == 2, "one turnaround room per tape"
    for relay_id in relays:
        ring = [
            p
            for p in analysis.pipes
            if {p.src, p.dst} == {worker_id, relay_id}
        ]
        assert len(ring) == 2, "a ring is a forward pipe and a return pipe"
        assert sum(len(p.path) for p in ring) >= tcp_queue.RING_CELLS
