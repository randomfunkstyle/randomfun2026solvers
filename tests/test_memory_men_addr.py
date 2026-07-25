"""Address-as-data man-memory: identical tiles, the address handed out at birth.

The claim this file has to defend is *uniformity* — every decoder is the same ten
glyphs and every cell the same nine, so nothing about the grid says which band is
address 5 except three glyphs of spawner arithmetic. Two things follow, and both
are checked against the **reference** engine: the tiles really are identical, and
an access costs the same at every address.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.memory_men_addr import (
    BAND,
    CELL_TILE,
    DECODER_TILE,
    ROUTER_ROWS,
    band_room,
    build_addr,
    tile_x0,
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


def _tiles(rows, mains, tile):
    """The text actually stamped in each band, cropped to the tile's box."""
    w = max(len(r) for r in tile)
    x0 = tile_x0(tile is DECODER_TILE)
    return [
        tuple(rows[main - 1 + dy][x0 : x0 + w].ljust(w) for dy in range(BAND)) for main in mains
    ]


@pytest.mark.parametrize("tile", [DECODER_TILE, CELL_TILE])
def test_every_band_holds_the_identical_tile(tile):
    # The whole point of moving the address out of the code: cell 15 is not one
    # glyph wider than cell 0, so bands can eventually be packed side by side.
    rows, mains = band_room(8, tile, increment=tile is DECODER_TILE)
    stamped = _tiles(rows, mains, tile)
    assert stamped == [tuple(tile)] * 8


def test_the_decoder_compares_with_the_hand_a_cell_cannot_spare():
    # `~` reads B, and B is exactly what a value-holding cell has no room for —
    # which is why the decoder is a second man in a second room at all.
    assert DECODER_TILE[0].startswith(">r~X"), DECODER_TILE[0]
    assert "".join(DECODER_TILE).count("s") == 2, "forwards op and value, nothing else"
    assert DECODER_TILE[1].count("r") == 3, "two swallowed unselected, one op selected"
    # a cell never sees an address, only its own pipe
    assert "~" not in "".join(CELL_TILE)


@pytest.mark.parametrize("tile", [DECODER_TILE, CELL_TILE])
def test_a_tile_is_a_ring_that_wastes_no_cell_on_going_home(tile):
    # The perimeter *is* the program: twelve cells, each one a glyph the man had
    # to run or a turn he had to take. The lane-with-a-return-corridor it replaces
    # spent 8 of its 20 ticks walking back west past nothing.
    assert [len(r) for r in tile] == [5, 5, 5]
    perimeter = tile[0] + tile[1][0] + tile[1][-1] + tile[2]
    assert " " not in perimeter, f"a blank on the ring is a wasted tick: {tile}"
    # the man is born facing east on the west side and turns straight into it
    assert tile[1][0] == "^" and tile[2][0] == "^"
    # ...and the other op is the three interior cells, entered off the ring
    assert tile[1][1:4].strip(), "the interior lane has to do something"


def test_the_spawner_hands_out_one_address_per_band():
    rows, mains = band_room(6, DECODER_TILE, increment=True)
    # row 0 turns the spawner south; then each band is return, main, branch
    assert mains == [BAND * j + 2 for j in range(6)]
    # `+ M 1` once per gap between `Y`s, and A=1 pinned before the first one
    assert sum(r.count("Y") for r in rows) == 6
    assert sum(r.count("+") for r in rows) == 5
    assert sum(r.count("M") for r in rows) == 5
    assert sum(r.count("1") for r in rows) == 6
    assert sum(r.count("H") for r in rows) == 1  # no band left to seed
    # the cell room walks the same path with nothing on it
    plain, _ = band_room(6, CELL_TILE, increment=False)
    assert sum(r.count("+") for r in plain) == 0


def test_the_router_broadcasts_three_words_and_owns_no_other_pipe():
    joined = "\n".join(ROUTER_ROWS)
    # addr and op go out from the down leg; the third `S` is *shared* by both
    # lanes on the way home, because after `W` a READ's A is still the 0 it sent
    # as its op — the dummy value costs no literal and no second lane.
    assert joined.count("S") == 3, "addr, op, and one shared final word"
    assert "0" not in joined, "the dummy value is free, not a literal"
    # `U` is a receive and a turn in one cell: the room's only incoming pipe is on
    # the north wall, so every `U` here faces the man south, which is the turn
    # both receives needed anyway.
    assert joined.count("U") == 2, "the op and the value, each with its turn"
    # If the router had an outgoing pipe of its own, `S` would broadcast into it.
    assert "R" not in joined
    # three columns. The room spans the field whatever happens — every band needs
    # a pipe off its east wall — so rows here are free and columns are not.
    assert max(len(r) for r in ROUTER_ROWS) == 3


@pytest.mark.parametrize("n", [1, 2, 4])
def test_every_pipe_has_a_source_room(n):
    analysis = Littleman().analyze(build_addr(n).source())
    assert all(p.src >= 0 for p in analysis.pipes), [p.src for p in analysis.pipes]
    # broadcast, forward and answer per cell, plus input and output
    assert len(analysis.pipes) == 3 * n + 2


def test_fast_and_reference_agree_on_a_write_then_read():
    src = build_addr(4).source()
    fast = FastLittleman(src).run(input="1 3 42 0 3", expected=[42], max_ticks=40000)
    assert fast.passed, (fast.fatal, fast.output)
    snap = Littleman().judge(src, input="1 3 42 0 3", expected=[42], max_ticks=40000)
    assert snap.output == [42], snap.output


def test_a_hundred_cells_load_and_answer_at_both_ends():
    # The increment chain has to survive 99 splits, and address 99 is reached by
    # the same three glyphs as address 0.
    src = build_addr(100).source()
    fast = FastLittleman(src).run(
        input="1 99 7 1 0 -3 0 99 0 0 0 50", expected=[7, -3, 0], max_ticks=200000
    )
    assert fast.passed, (fast.fatal, fast.output)


@pytest.mark.slow
def test_sixteen_cells_start_at_zero_on_the_reference_engine():
    src = build_addr(16).source()
    stream = " ".join(f"0 {a}" for a in range(16))
    snap = Littleman().judge(src, input=stream, expected=[0] * 16, max_ticks=300000)
    assert snap.output == [0] * 16, snap.output


@pytest.mark.slow
def test_sixteen_cells_hold_sixteen_values_on_the_reference_engine():
    src = build_addr(16).source()
    want = [100 + a for a in range(16)]
    stream = " ".join(f"1 {a} {100 + a}" for a in range(16))
    stream += " " + " ".join(f"0 {a}" for a in range(16))
    snap = Littleman().judge(src, input=stream, expected=want, max_ticks=300000)
    assert snap.output == want, snap.output


@pytest.mark.slow
@pytest.mark.parametrize("seed", [1, 2])
def test_sixteen_cells_answer_random_streams_on_the_reference_engine(seed):
    src = build_addr(16).source()
    stream, want = _stream(16, 40, seed)
    snap = Littleman().judge(src, input=stream, expected=want, max_ticks=400000)
    assert snap.output == want, (snap.output, want)


@pytest.mark.slow
def test_every_address_costs_the_same():
    # `memory_men_bcast` was flat at 42 except for address 15, which paid 52 for
    # the longest shift chain. Identical tiles have no such tail.
    src = build_addr(16).source()
    lm = Littleman()

    def per_op(addr, write):
        t = []
        for k in (4, 12):
            inp = (f"1 {addr} 5 " * k + f"0 {addr}") if write else f"0 {addr} " * k
            want = [5] if write else [0] * k
            snap = lm.judge(src, input=inp, expected=want, max_ticks=300000)
            assert snap.output == want, (addr, snap.output)
            t.append(snap.step)
        return (t[1] - t[0]) / 8

    # The router's lap is the pacer and a tile's is 12, so both numbers are the
    # router's: 16 for a READ, 18 for the WRITE's detour onto its second `U`.
    assert [per_op(a, False) for a in (0, 1, 8, 15)] == [16.0] * 4
    assert [per_op(a, True) for a in (0, 8, 15)] == [18.0] * 3


def test_the_checked_in_grid_matches_the_generator():
    path = Path("littleman/examples/memory-men-addr-16.man")
    if not path.is_file():  # pragma: no cover - only when run from another cwd
        pytest.skip(f"{path} not reachable from this working directory")
    assert path.read_text(encoding="utf-8").rstrip("\n") == build_addr(16).source(), (
        "regenerate with: uv run python -m randomfun2026solvers.memory_men_addr "
        "--cells 16 --man littleman/examples/memory-men-addr-16.man "
        "--html littleman/examples/memory-men-addr-16.html "
        "--json littleman/examples/memory-men-addr-16.json"
    )


@pytest.mark.slow
def test_the_engine_agrees_that_decoder_j_was_born_holding_j():
    # The overlay's addresses are a claim about three glyphs of arithmetic on the
    # spawner's way between two `Y`s, and nothing in the grid repeats it. So check
    # it the only honest way: let the spawner finish and read the men's hands.
    built = build_addr(16)
    assert built.debug is not None
    regions = {r.name: r for r in built.debug.regions if r.name.startswith("decoder addr ")}
    assert len(regions) == 16
    snap = Littleman().tick(built.source(), 200)
    assert snap.fatal is None, snap.fatal
    for j in range(16):
        box = regions[f"decoder addr {j}"]
        inside = [
            r
            for r in snap.entities.runners
            if box.x <= r.pos.x < box.x + box.w and box.y <= r.pos.y < box.y + box.h
        ]
        assert len(inside) == 1, (j, [(r.pos.x, r.pos.y) for r in inside])
        assert inside[0].b == j, (j, inside[0].b)
    # 34 men at rest: sixteen decoders, sixteen cells, the router and the
    # collector. Both spawners are gone — the last band's west child is born on
    # an `H`, and a halted man leaves the snapshot.
    assert len(snap.entities.runners) == 34


def test_the_overlay_names_every_address_and_the_increment():
    built = build_addr(16)
    assert built.debug is not None
    side = built.debug.to_dict()
    names = {r["name"] for r in side["regions"]}
    assert {f"decoder addr {j}" for j in range(16)} <= names
    assert {f"cell addr {j}" for j in range(16)} <= names
    rows = list(built.rows)
    # the circle that explains the addresses must sit on the `+` that makes them
    inc = [c for c in side["circles"] if c["name"] == "the increment"]
    assert len(inc) == 1
    assert rows[inc[0]["cy"]][inc[0]["cx"]] == "+"
    splits = [c for c in side["circles"] if c["name"].startswith("split")]
    assert len(splits) == 16
    for circle in splits:
        assert rows[circle["cy"]][circle["cx"]] == "Y", circle["name"]
