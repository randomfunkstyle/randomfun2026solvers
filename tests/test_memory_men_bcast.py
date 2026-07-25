"""Broadcast-addressed memory: ``S`` out to every cell, ``R`` back from any.

The end-to-end proofs run on the **reference** engine. This design leans on `S`'s
all-or-nothing write and on nearest-pipe binding for sixteen pipes on one wall, so
agreement between the validators is worth checking rather than assuming.
"""

from __future__ import annotations

import random

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.memory_men_bcast import (
    BAND,
    ROUTER_ROWS,
    build_bcast,
    field_rows,
    tile_cols,
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


def test_router_broadcasts_a_one_hot_and_owns_no_other_outgoing_pipe():
    joined = "\n".join(ROUTER_ROWS)
    # `{` builds 1 << addr in one glyph; `S` then reaches every cell at once
    assert joined.count("{") == 2, "one per op lane"
    assert joined.count("S") == 5, "READ broadcasts 2 words, WRITE 3"
    # If the router had an output pipe of its own, `S` would broadcast into it too,
    # which is why answers go through a separate collector.
    assert "R" not in joined


def test_each_cell_shifts_by_its_own_index():
    rows, mains = field_rows(4)
    for j, main in enumerate(mains):
        c = tile_cols(j)
        line = rows[main]
        assert line[c["x0"] : c["x0"] + 3] == ">rb"
        assert line[c["x0"] + 3 : c["x0"] + 3 + j] == "]" * j
        assert line[c["X"]] == "X"
        assert line[c["X"] + 1] == "x"


def test_the_spawner_seeds_every_band_and_then_stops():
    rows, mains = field_rows(16)
    assert len(mains) == 16
    assert sum(r.count("Y") for r in rows) == 16
    assert sum(r.count("H") for r in rows) == 1
    # row 0 is the spawner's turn-in cell, then each band's return row precedes
    # its main line
    assert mains == [2 + BAND * j for j in range(16)]


def test_every_pipe_has_a_source_room():
    # The bug this pins: a router shorter than the field leaves the lower bands'
    # pipes with no source wall, and those cells then bind a *neighbour's* pipe —
    # which looks like the cells stealing each other's words.
    analysis = Littleman().analyze(build_bcast(4).source())
    assert all(p.src >= 0 for p in analysis.pipes), [p.src for p in analysis.pipes]
    assert len(analysis.pipes) == 2 * 4 + 2


def test_fast_and_reference_agree_on_a_write_then_read():
    src = build_bcast(4).source()
    fast = FastLittleman(src).run(input="1 3 42 0 3", expected=[42], max_ticks=40000)
    assert fast.passed, (fast.fatal, fast.output)
    snap = Littleman().judge(src, input="1 3 42 0 3", expected=[42], max_ticks=40000)
    assert snap.output == [42], snap.output


@pytest.mark.slow
def test_sixteen_cells_start_at_zero_on_the_reference_engine():
    src = build_bcast(16).source()
    stream = " ".join(f"0 {a}" for a in range(16))
    snap = Littleman().judge(src, input=stream, expected=[0] * 16, max_ticks=300000)
    assert snap.output == [0] * 16, snap.output


@pytest.mark.slow
def test_sixteen_cells_hold_sixteen_values_on_the_reference_engine():
    src = build_bcast(16).source()
    want = [100 + a for a in range(16)]
    stream = " ".join(f"1 {a} {100 + a}" for a in range(16))
    stream += " " + " ".join(f"0 {a}" for a in range(16))
    snap = Littleman().judge(src, input=stream, expected=want, max_ticks=300000)
    assert snap.output == want, snap.output


@pytest.mark.slow
@pytest.mark.parametrize("seed", [1, 2])
def test_sixteen_cells_answer_random_streams_on_the_reference_engine(seed):
    src = build_bcast(16).source()
    stream, want = _stream(16, 40, seed)
    snap = Littleman().judge(src, input=stream, expected=want, max_ticks=400000)
    assert snap.output == want, (snap.output, want)


@pytest.mark.slow
def test_access_cost_does_not_depend_on_the_address():
    # The whole point: no walk. Only the last cell costs more, because its shift
    # chain is the longest and it paces the broadcast.
    src = build_bcast(16).source()
    lm = Littleman()

    def per_op(addr):
        t = []
        for k in (4, 12):
            snap = lm.judge(src, input=f"0 {addr} " * k, expected=[0] * k, max_ticks=300000)
            assert snap.output == [0] * k
            t.append(snap.step)
        return (t[1] - t[0]) / 8

    flat = [per_op(a) for a in (0, 1, 8)]
    assert flat == [42.0, 42.0, 42.0], flat
    assert per_op(15) == 52.0
