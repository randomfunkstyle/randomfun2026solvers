"""The four semantics the room-less man-cell rests on, each against the engine.

None of these is inferable from the ASCII: a cell whose `q` poll is wrong still
loads and still answers the first read, and the collision rule in particular reads
the other way in SPEC until you probe it.
"""

from __future__ import annotations

from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.memory_men_field import (
    FIELD_CELL,
    PARK_CELL,
    build_field_probe,
    build_park_probe,
    build_q_cell_probe,
)


def test_park_cell_is_the_smallest_loop_that_can_hold_an_s():
    # A 2x2 pinwheel's four cells are all corners, so it has nowhere to put an `s`;
    # 3x2 is the first shape with a free cell.
    assert len(PARK_CELL) == 2 and all(len(r) == 3 for r in PARK_CELL)
    assert "s" in PARK_CELL[0]


def test_a_parked_man_leaves_his_value_in_the_pipe():
    # Storage is the pipe; the man is only the refresh circuit.
    snap = Littleman().tick(build_park_probe(drain=False), 80)
    runners = snap.entities.runners
    assert len(runners) == 1
    assert runners[0].a == 5
    held = [v.value for pipe in snap.entities.pipes for v in pipe.values]
    assert 5 in held, held
    # ...and he is stationary: the same cell many ticks later
    later = Littleman().tick(build_park_probe(drain=False), 200)
    assert later.entities.runners[0].pos.as_tuple() == runners[0].pos.as_tuple()


def test_draining_the_pipe_makes_him_refill_it():
    result = FastLittleman(build_park_probe(drain=True)).run(max_ticks=300)
    assert result.fatal is None, (result.fatal, result.fatal_pos)
    assert result.output[:6] == [5] * 6, result.output[:6]


def test_men_sharing_one_room_still_get_private_pipes():
    # The load-bearing one: pipe affinity is positional, so a field of cells in a
    # single room is possible at all. Without this it would only ever be a queue.
    snap = Littleman().tick(build_field_probe(3), 60, input="11 22 33")
    assert snap.fatal is None, snap.fatal
    parked = sorted(r.a for r in snap.entities.runners)
    assert parked == [11, 22, 33], parked
    per_pipe = [{v.value for v in p.values} for p in snap.entities.pipes if p.values]
    assert {11} in per_pipe and {22} in per_pipe and {33} in per_pipe, per_pipe


def test_a_moving_man_cannot_evict_a_parked_one():
    # SPEC's "same-cell arrivals kill the participants" means two men *arriving*;
    # a stationary man is not arriving, so a would-be killer just queues behind him.
    # This is why the write path is a `q` poll and not an eviction.
    field = build_field_probe(1)
    snap = Littleman().tick(field, 200, input="11")
    assert snap.fatal is None
    assert all(r.a == 11 for r in snap.entities.runners)


def test_q_lets_a_cell_take_a_write_without_blocking_its_refresh():
    assert "q" in FIELD_CELL[1] and "a" in FIELD_CELL[1]
    result = FastLittleman(build_q_cell_probe()).run(input="77", max_ticks=400)
    assert result.fatal is None, (result.fatal, result.fatal_pos)
    # 0 first: a fresh cell reads zero with no initialisation pass at all.
    assert result.output[0] == 0
    assert result.output[1:8] == [77] * 7, result.output[:8]
