"""The value-ring machines, run on the reference interpreter.

`reverse-a-list` and `sort-numbers` are the same machine — a list circulating in
a pipe ring — so both are exercised here through one parametrization.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.manast import render
from randomfun2026solvers.reverse_list_ast import (
    RING_CAPACITY_NEEDED,
)
from randomfun2026solvers.reverse_list_ast import (
    build_ast as build_reverse_ast,
)
from randomfun2026solvers.value_ring import build_reverse, build_sort

ROOT = Path(__file__).resolve().parents[1]

MACHINES = {
    "reverse-a-list": (build_reverse, 18),
    "sort-numbers": (build_sort, 25),
}


def solution(slug: str) -> Path:
    return ROOT / "tasks" / "solutions" / f"{slug}_ring.man"


def public_cases(slug: str) -> list[dict]:
    problem = ROOT / "tasks" / "problems" / f"{slug}.json"
    return json.loads(problem.read_text())["publicTestData"]


def case_params() -> list[tuple[str, dict]]:
    return [(slug, c) for slug in MACHINES for c in public_cases(slug)]


def case_ids() -> list[str]:
    return [f"{slug}: {c['name']}" for slug in MACHINES for c in public_cases(slug)]


@pytest.mark.parametrize("slug", sorted(MACHINES))
def test_generator_reproduces_the_committed_grid(slug: str) -> None:
    build, _ = MACHINES[slug]
    assert build() == solution(slug).read_text().rstrip("\n").split("\n")


@pytest.mark.parametrize("slug", sorted(MACHINES))
def test_footprint_does_not_regress(slug: str) -> None:
    """Score is max(w,h)**2 x avgTicks, so the bounding box is half the score."""
    build, side = MACHINES[slug]
    rows = build()
    assert max(max(len(r) for r in rows), len(rows)) <= side


@pytest.mark.parametrize(("slug", "case"), case_params(), ids=case_ids())
def test_public_cases(slug: str, case: dict) -> None:
    """One test case is several rounds; the engine gates them on our output.

    Rounds are joined with `/` for both input and expectation, which is how the
    engine is told where each round ends -- the next round's input is withheld
    until the current round's output is complete.
    """
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    want = [int(t) for r in case["rounds"] for t in r["out"]]
    snap = Littleman().judge(
        solution(slug).read_text(),
        input=inp,
        expected=" / ".join(" ".join(r["out"]) for r in case["rounds"]),
        max_ticks=200_000,
    )
    assert snap.fatal is None, f"fatal: {snap.fatal}"
    assert list(snap.output) == want


def test_reverses_a_single_full_length_list() -> None:
    """n = 16 is the constraint limit, and the worst case for the O(n^2) rotation."""
    vals = [
        -1_000_000,
        1_000_000,
        0,
        -1,
        1,
        999_999,
        -999_999,
        7,
        7,
        42,
        -42,
        3,
        2,
        1,
        0,
        -1_000_000,
    ]
    snap = Littleman().judge(
        solution("reverse-a-list").read_text(),
        input="16 " + " ".join(map(str, vals)),
        expected=" ".join(map(str, reversed(vals))),
        max_ticks=200_000,
    )
    assert list(snap.output) == list(reversed(vals))


def test_reverse_handles_every_legal_length_and_value_bounds() -> None:
    """Exercise both loop bounds for every n, not only the public distribution."""
    machine = FastLittleman(solution("reverse-a-list"))
    basis = [-1_000_000, 1_000_000, 0, -1, 1, 999_999, -999_999]
    for n in range(1, 17):
        vals = [basis[i % len(basis)] for i in range(n)]
        result = machine.run([n, *vals], expected=list(reversed(vals)))
        assert result.passed, (n, result)


def test_reverse_is_authored_as_a_tight_ast_with_enough_ring_capacity() -> None:
    """The canonical generator is structural and has no clipped border slack."""
    ast = build_reverse_ast()
    rows = render(ast)
    assert ast.bbox == (18, 18)
    assert rows == build_reverse()
    width = max(map(len, rows))
    padded = [row.ljust(width) for row in rows]
    assert all(row.strip() for row in padded), "an entirely empty row can be clipped"
    assert all(
        any(row[x] != " " for row in padded) for x in range(width)
    ), "an entirely empty column can be clipped"

    ring = [pipe for pipe in ast.pipes if pipe.id in (2, 3)]
    assert sum(pipe.capacity for pipe in ring) >= RING_CAPACITY_NEEDED


def test_sorts_a_full_length_list_with_duplicates_and_extremes() -> None:
    """n = 16 at both ends of the value range, with ties: one lap per output."""
    vals = [10000, -10000, 0, 10000, -10000, 7, 7, -1, 1, 0, 0, 5, -5, 9999, -9999, 3]
    snap = Littleman().judge(
        solution("sort-numbers").read_text(),
        input="16 " + " ".join(map(str, vals)),
        expected=" ".join(map(str, sorted(vals))),
        max_ticks=200_000,
    )
    assert list(snap.output) == sorted(vals)


@pytest.mark.parametrize("slug", sorted(MACHINES))
def test_every_pipe_op_binds_to_the_intended_pipe(slug: str) -> None:
    """Nearest-pipe binding is the language's biggest source of silent wrongness.

    The worker has four pipes: input and output on its north wall, ring-forward
    and ring-return on its east wall. Reads compete only with the other
    *incoming* pipe and sends only with the other *outgoing* one, so the
    invariant that pins the whole layout down is a census: exactly one `s`
    reaches the output pipe and exactly two `r`s the input pipe -- everything
    else talks to the ring.
    """
    lm = Littleman()
    path = solution(slug)
    rows = path.read_text().rstrip("\n").split("\n")
    analysis = lm.analyze(path)
    pipes = {
        tuple(seg.pos.as_tuple() for seg in pipe.path): pipe
        for pipe in analysis.pipes
    }
    io_rooms = {
        room_id
        for room_id, room in enumerate(analysis.rooms)
        if any(
            rows[y][x] in "IO"
            for y in range(room.min_.y, room.max_.y + 1)
            for x in range(room.min_.x, room.max_.x + 1)
        )
    }
    io_pipes = {
        cells
        for cells, pipe in pipes.items()
        if pipe.src in io_rooms or pipe.dst in io_rooms
    }
    assert len(io_pipes) == 2, "exactly one pipe must attach to each I/O room"

    io_sends, io_reads = 0, 0
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch not in "rs":
                continue
            cells = tuple(c.as_tuple() for c in lm.route(path, x, y))
            assert cells in pipes, f"'{ch}' at ({x},{y}) binds to no pipe"
            if cells in io_pipes:
                if ch == "s":
                    io_sends += 1
                else:
                    io_reads += 1
    assert io_sends == 1, f"{io_sends} sends bound to an I/O pipe, want 1"
    assert io_reads == 2, f"{io_reads} reads bound to an I/O pipe, want 2"
