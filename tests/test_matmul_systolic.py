"""The systolic MAC chain: bindings, mouths, and the one-stage machine's answer."""
from __future__ import annotations

import pytest

from randomfun2026solvers import matmul_systolic as ms
from randomfun2026solvers.circuit import Collision


# ── the pieces ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("f", range(16))
def test_every_stage_solves_its_bindings(f: int) -> None:
    """A stage's feeder must exist for every position in a 16-long chain.

    `loadf_room` raises rather than emitting a room whose `s` glyphs would bind
    the wrong pipe, so simply building all sixteen is the assertion.
    """
    room = ms.loadf_room(f"L{f}", f)
    assert set(room.ports) >= {"chain_in", "ring_out", "mul_out"}
    assert ("chain_out" in room.ports) == (f > 0)


def test_mul_north_wall_order_is_forced() -> None:
    """a_in west of ring_out west of ring_in — the fact the floor plan turns on.

    MUL's first `r` reads the weight and sits west of the `r` that reads the
    ring, so the weight mouth can only ever be the western one. Everything about
    where TURN may stand follows from this.
    """
    ports = ms.mul_room("M").ports
    assert ports["a_in"][0] == ports["ring_out"][0] == ports["ring_in"][0] == "N"
    assert ports["a_in"][1] < ports["ring_out"][1] < ports["ring_in"][1]


def test_add_reads_the_product_first_so_the_psum_lands_east() -> None:
    """`+` is commutative, and reading the product first is what puts prod_in
    west (fed straight down from MUL) and psum_in east (fed from the east
    channel). That split is what frees the west channel for the chain."""
    ports = ms.add_room("A", first=False).ports
    assert ports["prod_in"][0] == ports["psum_in"][0] == "N"
    assert ports["prod_in"][1] < ports["psum_in"][1]


def test_solver_refuses_an_unsatisfiable_room() -> None:
    """Two ops that want opposite mouths from the same cell cannot be placed."""
    with pytest.raises(Collision):
        ms.solve_ports(
            4, 2,
            [ms.PortSpec("a", "in"), ms.PortSpec("b", "in")],
            [((1, 0), "r", "a"), ((1, 0), "r", "b")],
        )


def test_no_backticks_anywhere() -> None:
    """Backticks pair by column as well as by row; one in a generated room could
    pair with one in another room and swallow a wall glyph at load time."""
    text, _ = ms.probe(1, [[3], [4]], [[5, 6]])
    assert "`" not in text


# ── the stream the array wants ───────────────────────────────────────────────


def test_stream_is_blocks_then_zero_padded_rows() -> None:
    a = [[1, 2], [3, 4]]
    b = [[5, 6], [7, 8]]
    assert ms.stream_for(a, b, 2) == [2, 5, 6, 2, 7, 8, 1, 2, 3, 4]
    # a stage past M gets an all-zero b-block and a zero weight
    assert ms.stream_for([[3], [4]], [[5, 6]], 2) == [2, 5, 6, 2, 0, 0, 3, 0, 4, 0]


def test_expected_matches_the_definition() -> None:
    assert ms.expected([[1, 2], [3, 4]], [[5, 6], [7, 8]]) == [19, 22, 43, 50]


# ── the machine ──────────────────────────────────────────────────────────────


def test_one_stage_machine_builds_with_the_pipes_it_declared() -> None:
    """`probe` runs `check_mouths`, which counts arrowheads-against-a-wall the
    way the runtime does — an extra one raises here rather than at judging."""
    text, expect = ms.probe(1, [[3], [4]], [[5, 6]])
    assert expect == [15, 18, 20, 24]
    assert text.count("|O|") == 1


@pytest.mark.slow
@pytest.mark.parametrize(
    "p,a,b,ticks",
    [(1, [[3], [4]], [[5, 6]], 600),
     (2, [[1, 2], [3, 4]], [[5, 6], [7, 8]], 900)],
)
def test_the_chain_computes_the_product(p, a, b, ticks) -> None:
    """P=1 proves the ring and the multiply; P=2 proves the adder chain and the
    stage-to-stage chain under real contention."""
    from randomfun2026solvers.littleman import Littleman

    text, expect = ms.probe(p, a, b)
    snap = Littleman().tick(text, ticks)
    assert [int(v) for v in snap.output] == expect


def test_lit_free_covers_the_entry_range() -> None:
    """Sources must be able to name every legal matrix entry without a backtick."""
    for n in range(-99, 100):
        assert "`" not in ms.lit_free(n)


def test_the_mirrored_feeder_unblocks_the_floor_plan() -> None:
    """The documented fix, proved before anyone builds it.

    Laying LOADF's INIT row right-to-left moves the ring writes to the east end
    and the chain writes to the west end. `solve_ports` then puts ring_out on
    the east wall (facing TURN, where the floor plan needs it) and chain_out on
    the west wall (facing a chain channel), which is exactly the placement the
    current left-to-right layout cannot reach.
    """
    ports = ms.solve_ports(
        18, 11,
        [ms.PortSpec("chain_in", "in", ("N",)),
         ms.PortSpec("ring_out", "out", ("E",)),
         ms.PortSpec("chain_out", "out", ("W",)),
         ms.PortSpec("mul_out", "out", ("S",))],
        [((14, 0), "s", "ring_out"), ((10, 2), "s", "ring_out"),
         ((4, 2), "s", "chain_out"), ((4, 7), "s", "chain_out"),
         ((14, 5), "s", "mul_out")],
    )
    assert ports["ring_out"][0] == "E" and ports["chain_out"][0] == "W"
