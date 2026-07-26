"""The grid man-memory with both ports on one wall.

Two claims to defend, and only the engine can settle either. First, that moving
the answer changes nothing about the memory: the same ``0 addr`` / ``1 addr
value`` stream, the same answers, in the same order, at any ``M x N``. Second,
that the block is *placeable* — every pipe binds, both stubs sit on the west
wall, and there is no ``I``/``O`` room in the part the client drops into a slot.

The cost of the turnaround is measured, not argued: judged against the
east-climbing block of ``memory_men_grid_store`` under an identical wrapper it is
a flat **+10 ticks** at every shape (the relay's cycle plus its two stubs), and
the marginal read is unchanged at 15.
"""

from __future__ import annotations

import random

import pytest
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.memory_men_grid import build_grid
from randomfun2026solvers.memory_men_grid_side import (
    _IN_ROW,
    RELAY,
    build_side_grid,
    grid_side_block,
    side_grid_ticks,
)


def _stream(n, ops, seed):
    rng = random.Random(seed)
    mem, stream, want = [0] * n, [], []
    for _ in range(ops):
        addr = rng.randrange(n)
        if rng.random() < 0.5:
            stream += [0, addr]
            want.append(mem[addr])
        else:
            value = rng.randint(-1000000, 1000000)
            stream += [1, addr, value]
            mem[addr] = value
    return stream, want


def _fixed_and_slope(src, lm, lo=1, hi=10, cap=4_000_000):
    t = []
    for k in (lo, hi):
        snap = lm.judge(src, input="0 0 " * k, expected=[0] * k, max_ticks=cap)
        assert list(snap.output) == [0] * k, snap.output
        t.append(snap.step)
    slope = (t[1] - t[0]) / (hi - lo)
    return t[0] - lo * slope, slope


# ── the turnaround ───────────────────────────────────────────────────────────
def test_the_relay_moves_one_value_from_one_room_to_one_room():
    # Not a collector. The strip's `R` has already merged the columns — exactly
    # one decoder speaks per operation — so this needs no fan-in, no fan-out and
    # no addressing: one `r`, one `s`, and a pipe cannot feed another pipe, which
    # is the only reason a room is here at all.
    joined = "".join(RELAY)
    assert joined.count("r") == 1 and joined.count("s") == 1
    assert not set(joined) & set("RS"), "the teleport glyphs would be fan-in/fan-out"
    # `@` is a nop, so a man born in a corner walks straight out through the wall.
    assert RELAY[0][1] == "@" and RELAY[0][0] == ">"


# ── the shape a client sees ──────────────────────────────────────────────────
def test_both_ports_are_on_the_west_wall_two_rows_apart():
    blk = grid_side_block(3, 4)
    assert blk.in_cell[0] == 0 and blk.out_cell[0] == 0, (blk.in_cell, blk.out_cell)
    assert blk.cells[blk.in_cell] == ">", "the request must arrive heading east"
    assert blk.cells[blk.out_cell] == "<", "the answer leaves heading west, same wall"
    assert blk.in_cell[1] == _IN_ROW
    assert blk.out_cell[1] == blk.in_cell[1] + 2


def test_the_answer_port_is_a_parameter_and_cannot_climb_past_the_request():
    # The climb runs up the west margin, so it separates the west wall from the
    # rooms at every row it crosses: it has to stop *below* the request's row.
    for bad in (0, _IN_ROW, _IN_ROW + 1):
        with pytest.raises(ValueError, match="out_row"):
            grid_side_block(2, 2, out_row=bad)
    with pytest.raises(ValueError, match="out_row"):
        grid_side_block(2, 2, out_row=10_000)
    deep = grid_side_block(2, 2, out_row=20)
    assert deep.out_cell == (0, 20) and deep.cells[(0, 20)] == "<"
    assert (0, 19) not in deep.cells, "the climb stops at the stub, it does not run on"


def test_the_placeable_block_carries_no_io_rooms():
    # A littleman program may have at most one `I` and one `O`, and the client
    # owns both; `machine.tape_block` replaces the tape's with stubs the same way.
    blk = grid_side_block(2, 3)
    assert "I" not in blk.cells.values() and "O" not in blk.cells.values()
    assert "I" in build_side_grid(2, 3).cells.values(), "the testable form has them"


def test_the_block_is_the_standalone_with_its_io_rooms_taken_off():
    # Both come out of one stamper, so the thing measured on the engine and the
    # thing a client places cannot drift apart.
    blk, std = grid_side_block(3, 4), build_side_grid(3, 4)
    ox, oy = std.in_cell[0] - blk.in_cell[0], std.in_cell[1] - blk.in_cell[1]
    for (x, y), ch in blk.cells.items():
        assert std.cells.get((ox + x, oy + y)) == ch, (x, y, ch)


@pytest.mark.parametrize(("cols", "rows"), [(2, 2), (3, 5), (4, 3)])
def test_the_pipe_count_is_the_grids_plus_the_turnaround(cols, rows):
    # Per column: one feed down, two per cell, one answer out. Plus the request,
    # the collector's drop into the relay, and the relay's climb back up.
    blk = grid_side_block(cols, rows)
    assert blk.pipes == cols * (2 * rows + 2) + 3


@pytest.mark.parametrize(("cols", "rows"), [(2, 2), (3, 2), (2, 5)])
def test_every_pipe_binds_at_both_ends(cols, rows):
    # A pipe whose first cell does not point away from its room does not fail —
    # it silently is not there. Counting is the only way to know.
    grid = build_side_grid(cols, rows)
    analysis = Littleman().analyze(grid.source())
    assert len(analysis.pipes) == grid.pipes, [p.src for p in analysis.pipes]
    assert all(p.src >= 0 for p in analysis.pipes), [p.src for p in analysis.pipes]
    assert all(p.dst >= 0 for p in analysis.pipes), [p.dst for p in analysis.pipes]


@pytest.mark.parametrize(("cols", "rows"), [(3, 14), (4, 25), (6, 20), (7, 61)])
def test_the_box_is_the_grids_plus_two_columns(cols, rows):
    # The climb is on the west *margin*, not east of the rooms, so it costs no
    # column of its own: the two are the margin the request's corner already
    # needed. Height is `31 + 3N`, one row under the standalone grid's `32 + 3N`
    # because the block has no I/O rooms.
    blk = grid_side_block(cols, rows)
    assert blk.width == build_grid(cols, rows).width + 2
    assert blk.height == 31 + 3 * rows


# ── the memory still answers ─────────────────────────────────────────────────
@pytest.mark.parametrize(("cols", "rows"), [(2, 2), (3, 3), (2, 5)])
def test_a_small_grid_answers_random_streams_on_the_reference_engine(cols, rows):
    grid = build_side_grid(cols, rows)
    stream, want = _stream(cols * rows, 20, seed=cols * 31 + rows)
    snap = Littleman().judge(grid.source(), input=stream, expected=want, max_ticks=400_000)
    assert snap.output == want, (snap.output, want)


def test_an_answer_port_moved_down_the_wall_still_answers():
    grid = build_side_grid(2, 2, out_row=20)
    snap = Littleman().judge(
        grid.source(), input="1 3 42 0 3 0 0", expected=[42, 0], max_ticks=400_000
    )
    assert list(snap.output) == [42, 0], snap.output


def test_a_based_block_answers_the_clients_own_slot_numbers():
    # The reason a second tier is cheap: a column's decoders hold *global*
    # addresses, so a block built at `base` needs no address translation anywhere
    # — the client only has to decide which pipe to send the request down.
    grid = build_side_grid(2, 2, base=1000)
    assert grid.low == 1000 and grid.high == 1004
    snap = Littleman().judge(
        grid.source(),
        input="1 1003 7 0 1003 0 1000",
        expected=[7, 0],
        max_ticks=400_000,
    )
    assert list(snap.output) == [7, 0], snap.output


def test_answers_come_back_in_operation_order_across_columns():
    # The tail the columns share — the relay and the climb — is the same for
    # every answer, so it cannot reorder what the collector merged in order.
    grid = build_side_grid(5, 2)
    writes = " ".join(f"1 {a} {100 + a}" for a in range(10))
    order = (0, 9, 1, 8, 2, 7, 3, 6, 4, 5)
    reads = " ".join(f"0 {a}" for a in order)
    want = [100 + a for a in order]
    snap = Littleman().judge(
        grid.source(), input=f"{writes} {reads}", expected=want, max_ticks=400_000
    )
    assert snap.output == want, snap.output


@pytest.mark.slow
@pytest.mark.parametrize(("cols", "rows"), [(3, 14), (6, 20), (7, 61)])
def test_the_highest_address_round_trips_at_every_shape(cols, rows):
    """42, 120 (top column based at 100) and 427 cells, on the reference engine."""
    grid = build_side_grid(cols, rows)
    top = cols * rows - 1
    snap = Littleman().judge(
        grid.source(),
        input=f"1 {top} 77 0 {top} 1 0 5 0 0 0 {top}",
        expected=[77, 5, 77],
        max_ticks=4_000_000,
    )
    assert list(snap.output) == [77, 5, 77], snap.output


@pytest.mark.slow
def test_the_turnaround_costs_ten_ticks_and_nothing_per_read():
    """The one number the design has to justify: what same-side routing costs.

    Judged at one read against ten, the same-side block is a flat ten ticks
    behind ``memory_men_grid_store``'s east-climbing one at every shape — the
    relay's eight-cell cycle plus its two stubs — and the marginal read does not
    move at all, because the tail is a pipeline stage like any other.

        3x14  154 -> 164     4x25  242 -> 252
        6x20  204 -> 214     7x61  532 -> 542
    """
    lm = Littleman()
    for cols, rows in [(3, 14), (4, 25)]:
        fixed, slope = _fixed_and_slope(build_side_grid(cols, rows).source(), lm)
        assert slope == 15.0, (cols, rows, slope)
        assert fixed == pytest.approx(side_grid_ticks(cols, rows), abs=3), (cols, rows, fixed)
