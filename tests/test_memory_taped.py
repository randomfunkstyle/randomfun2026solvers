"""The taped STORE tier: banked pipe tapes behind a chain of range gates.

The tier exists for the little-man census (a bank is two men, a gate one,
against the man-memory's ~two per slot), so the census is pinned here along
with the semantics: every address lands in the right bank, rebased right, and
comes back with the right value through the collector.

The probes stream requests, and the tier's ordering contract is the machine's
(the CPU blocks on every read; only one answer is ever in flight), so reads
are grouped per bank per run — two banks' rings answer at different speeds, so
*streamed* cross-bank reads can legally come home out of order. The machine
gate in ``test_deadman3d.py`` covers the serial cross-bank mix for real.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402
from randomfun2026solvers.memory_taped import (  # noqa: E402
    bank_gate,
    gate_chain,
    gate_rows,
    taped_plan,
    taped_store_block,
)

#: deadman-3d's shipped plan: hot high addresses in small rings.
PLAN = (128, 128, 40, 33)


def _standalone(block) -> FastLittleman:
    """The block as a complete program: an I room on the request stub, an O
    room on the answer stub — the same wrapper the men-v3 grid block test uses."""
    sx, sy = 6, 4
    grid = {(x + sx, y + sy): ch for (x, y), ch in block.cells.items()}
    ix, iy = block.in_cell[0] + sx, block.in_cell[1] + sy
    ox, oy = block.out_cell[0] + sx, block.out_cell[1] + sy
    for j, row in enumerate(("+-+", "|I|", "+-+")):
        for i, ch in enumerate(row):
            grid[(ix - 4 + i, iy - 1 + j)] = ch
    grid[(ix - 1, iy)] = ">"
    for j, row in enumerate(("+-+", "|O|", "+-+")):
        for i, ch in enumerate(row):
            grid[(ox - 1 + i, j)] = ch
    for y in range(3, oy):
        grid[(ox, y)] = "^"
    w = max(x for x, _ in grid) + 1
    h = max(y for _, y in grid) + 1
    return FastLittleman("\n".join("".join(grid.get((x, y), " ") for x in range(w)) for y in range(h)))


def test_the_plan_covers_the_tape_and_rejects_gaps() -> None:
    assert sum(taped_plan(330, PLAN)) >= 329
    assert taped_plan(330, 4) == [83, 83, 83, 80]
    with pytest.raises(ValueError):
        taped_plan(330, (100, 100))  # 129 addresses uncovered
    with pytest.raises(ValueError):
        taped_plan(330, 1)


def test_the_census_is_the_point() -> None:
    """Two men per bank plus one per gate — that is the tier's whole reason."""
    b = taped_store_block(330, PLAN, skip_batch=2)
    men = sum(1 for ch in b.cells.values() if ch == "@")
    assert men == 2 * len(PLAN) + (len(PLAN) - 1) + 1  # workers+relays, gates, collector
    assert b.pipes == 4 * len(PLAN) + (len(PLAN) - 2)


@pytest.mark.parametrize("skip_batch", [1, 2])
def test_every_address_reads_back_what_was_written(skip_batch: int) -> None:
    """All 329 slots, written through the whole chain then read one bank per
    run (see the module docstring for why reads are grouped per bank)."""
    engine = _standalone(taped_store_block(330, PLAN, skip_batch=skip_batch))
    writes = [x for a in range(1, 330) for x in (1, a, a * 13 + 7)]
    bounds = [1]
    for m in taped_plan(330, PLAN):
        bounds.append(bounds[-1] + m)
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        hi = min(hi, 330)
        reads = [x for a in range(lo, hi) for x in (0, a)]
        want = [a * 13 + 7 for a in range(lo, hi)]
        res = engine.run(writes + reads, expected=want, max_ticks=60_000_000)
        assert res.fatal is None and res.output == want, (
            lo,
            hi,
            res.fatal or res.reason,
            res.output[:5],
        )


# ── the compact gate: the same four arms with the nop spacers deleted ────────
def test_the_compact_gate_is_opt_in_and_five_rows_shorter() -> None:
    """Off by default, so every existing caller's grid is byte-identical."""
    for kwargs in ({}, {"skip_batch": 2}):
        shipped = taped_store_block(330, PLAN, **kwargs)
        default_off = taped_store_block(330, PLAN, compact_gate=False, **kwargs)
        assert default_off.cells == shipped.cells
        compact = taped_store_block(330, PLAN, compact_gate=True, **kwargs)
        # the gate strip is the block's floor, so the block loses the rows too
        assert compact.height == shipped.height - 5
        assert compact.width == shipped.width
        # ... and nothing else: same census, same pipe inventory
        assert compact.pipes == shipped.pipes
        assert sum(1 for c in compact.cells.values() if c == "@") == sum(
            1 for c in shipped.cells.values() if c == "@"
        )
    assert gate_rows(True)[0] == gate_rows(False)[0] - 5


def test_every_gate_send_still_binds_to_the_pipe_it_means() -> None:
    """The gate's whole binding argument is that no ``s`` needs an argument:
    two outgoing pipes on one wall, and the *row* decides which is nearest
    (SPEC.md, "Which pipe do I talk to?"). Compacting moves every arm row, so
    the margins shrink — this is the check that they never cross.

    The pipes' source segments are the cells just east of the east wall, at the
    local / downstream rows; ties break by reading order. The rule needs no arm
    arithmetic: an ``s`` **above** the spine is a local arm and must reach the
    local pipe, one **below** it is a downstream arm and must reach the other.
    """
    for compact in (False, True):
        _h, in_row, local_row, down_row = gate_rows(compact)
        # every bank size the literal's width can produce, and then some —
        # in both gate forms, because `high` changes every arm's text and so
        # every send's column
        for m in (1, 5, 64, 85, 195, 256, 999, 12345):
            for high in (None, m + 1, m + 7, 4 * m, 99999):
                g, w = bank_gate(m, compact=compact, high=high)
                src = {local_row: (w, local_row), down_row: (w, down_row)}
                sends = [(x, y) for (x, y), ch in g.items() if ch == "s"]
                assert len(sends) == 10, (m, high, compact, len(sends))
                assert all(y != in_row for _x, y in sends)
                for x, y in sends:
                    want = local_row if y < in_row else down_row
                    dist = {r: abs(px - x) + abs(py - y) for r, (px, py) in src.items()}
                    nearest = min(src, key=lambda r: (dist[r], r))
                    assert nearest == want, (
                        f"m={m} high={high} compact={compact}: the `s` at {(x, y)} "
                        f"binds to the row-{nearest} pipe, not row {want} ({dist})"
                    )


def test_the_low_gates_send_bindings_are_unchanged() -> None:
    """The original, narrower assertion, kept as its own case."""
    for compact in (False, True):
        _h, in_row, local_row, down_row = gate_rows(compact)
        for m in (1, 5, 64, 85, 195, 256, 999, 12345):
            g, w = bank_gate(m, compact=compact)
            src = {local_row: (w, local_row), down_row: (w, down_row)}
            sends = [(x, y) for (x, y), ch in g.items() if ch == "s"]
            assert len(sends) == 10, (m, compact, len(sends))
            assert all(y != in_row for _x, y in sends)  # the spine sends nothing
            for x, y in sends:
                want = local_row if y < in_row else down_row
                dist = {row: abs(px - x) + abs(py - y) for row, (px, py) in src.items()}
                nearest = min(src, key=lambda row: (dist[row], row))
                assert nearest == want, (
                    f"m={m} compact={compact}: the `s` at {(x, y)} binds to the "
                    f"row-{nearest} pipe, not row {want} ({dist})"
                )


@pytest.mark.parametrize("skip_batch", [1, 2])
def test_the_compact_gate_routes_every_address_the_same(skip_batch: int) -> None:
    """Correctness first: deleting a corridor cell must not lose a request.
    Same probe as the shipped body's, one bank per run."""
    engine = _standalone(
        taped_store_block(330, PLAN, skip_batch=skip_batch, compact_gate=True)
    )
    writes = [x for a in range(1, 330) for x in (1, a, a * 13 + 7)]
    bounds = [1]
    for m in taped_plan(330, PLAN):
        bounds.append(bounds[-1] + m)
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        hi = min(hi, 330)
        reads = [x for a in range(lo, hi) for x in (0, a)]
        want = [a * 13 + 7 for a in range(lo, hi)]
        res = engine.run(writes + reads, expected=want, max_ticks=60_000_000)
        assert res.fatal is None and res.output == want, (
            lo,
            hi,
            res.fatal or res.reason,
            res.output[:5],
        )


# ── the chain order: the hot bank first, which is a different gate form ──────
def test_the_chain_peels_banks_off_an_end_and_says_so_when_it_cannot() -> None:
    """``gate_chain`` is where a reordering bug would hide, so the literals it
    implies are derived here from the sizes alone and compared against it.

    A low gate at chain position ``j`` owns ``1..m`` of the space it was handed
    and forwards ``addr - m``; a high gate owns the top ``m`` of ``1..top`` and
    forwards ``addr`` untouched. Either way the space shrinks by ``m``, so the
    invariant that actually matters is that the ranges **tile** the address
    space exactly once — which is what the second loop checks.
    """
    sizes = [256, 195, 64, 85]
    assert gate_chain(sizes) == [(0, None), (1, None), (2, None), (3, None)]
    assert gate_chain(sizes, (3, 0, 1, 2)) == [(3, 600), (0, None), (1, None), (2, None)]
    assert gate_chain(sizes, (3, 2, 1, 0)) == [(3, 600), (2, 515), (1, 451), (0, None)]
    # a bank in the MIDDLE of what is left cannot be peeled: the gate hands on
    # one contiguous rebased space and its test is one-sided
    with pytest.raises(ValueError, match="claim an END"):
        gate_chain(sizes, (1, 0, 2, 3))
    with pytest.raises(ValueError, match="permutation"):
        gate_chain(sizes, (0, 1, 2, 2))

    # ... and every reachable order lands every address in the SAME bank at the
    # SAME local slot, which is the whole safety property. Walked here in
    # arithmetic (the engine walks it for real two tests down).
    def resolve(order: tuple[int, ...] | None, addr: int) -> tuple[int, int]:
        chain = gate_chain(sizes, order)
        for k, high in chain[:-1]:
            m = sizes[k]
            if high is None:  # low gate: mine is 1..m, forward addr - m
                if addr <= m:
                    return k, addr
                addr -= m
            elif addr > high - m:  # high gate: mine is high-m+1..high
                return k, addr - (high - m)
        return chain[-1][0], addr

    want = {a: resolve(None, a) for a in range(1, sum(sizes) + 1)}
    assert want[1] == (0, 1) and want[256] == (0, 256)
    assert want[257] == (1, 1) and want[516] == (3, 1) and want[600] == (3, 85)
    for order in ((3, 0, 1, 2), (3, 2, 1, 0), (0, 3, 1, 2), (3, 0, 2, 1), (0, 1, 3, 2)):
        assert {a: resolve(order, a) for a in want} == want, order


def test_the_hot_first_chain_is_opt_in_and_costs_no_room() -> None:
    """The reorder is free in every dimension that scores: the high gate form is
    the same shape as the low one, so the block does not move a column or a row,
    and the census and pipe inventory are the plan's, not the order's."""
    shipped = taped_store_block(330, PLAN, skip_batch=2, compact_gate=True)
    assert taped_store_block(
        330, PLAN, skip_batch=2, compact_gate=True, order=(0, 1, 2, 3)
    ).cells == shipped.cells
    hot = taped_store_block(330, PLAN, skip_batch=2, compact_gate=True, order=(3, 0, 1, 2))
    assert (hot.width, hot.height) == (shipped.width, shipped.height)
    assert hot.pipes == shipped.pipes
    assert sum(1 for c in hot.cells.values() if c == "@") == sum(
        1 for c in shipped.cells.values() if c == "@"
    )
    assert hot.cells != shipped.cells  # ... but it IS a different chain


@pytest.mark.parametrize("compact", [False, True])
def test_the_hot_first_chain_resolves_every_address_to_the_same_data(compact: bool) -> None:
    """The load-bearing test. Reordering rewrites every gate's literal, and a
    wrong literal routes a read to the **wrong bank** rather than failing — so
    this writes a distinct value into all 329 addresses through both chains and
    compares them address by address, not bank by bank."""

    def readback(order: tuple[int, ...] | None) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(330, PLAN, skip_batch=2, compact_gate=compact, order=order)
        )
        writes = [x for a in range(1, 330) for x in (1, a, a * 13 + 7)]
        bounds = [1]
        for m in taped_plan(330, PLAN):
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, 330)
            reads = [x for a in range(lo, hi) for x in (0, a)]
            want = [a * 13 + 7 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=60_000_000)
            assert res.fatal is None, (order, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    shipped = readback(None)
    assert len(shipped) == 329
    assert shipped == {a: a * 13 + 7 for a in range(1, 330)}
    assert readback((3, 0, 1, 2)) == shipped


def test_the_shipped_deadman3d_plan_routes_every_one_of_its_600_addresses() -> None:
    """The same load-bearing check, but against the **registry's own** numbers.

    ``PLAN`` above is a scaled-down stand-in; the machine ships a different
    split and a different chain order, and a size or an order that routes at 330
    slots says nothing about one that routes at 601. A wrong gate literal is
    silent — it hands a read to the wrong bank and the bank answers — so this
    reads back every address of the real plan and compares address by address,
    never bank by bank. It takes the numbers from :mod:`lm1.machine` so it
    cannot drift from what ``build_for`` actually places.
    """
    from randomfun2026solvers.lm1 import machine

    n = machine.TAPE_SIZE["deadman-3d"]
    plan = machine.TAPED_BANKS["deadman-3d"]
    order = machine.TAPED_BANK_ORDER[("deadman-3d", "taped")]
    compact = ("deadman-3d", "taped") in machine.TAPED_COMPACT_GATE
    skip = machine.TAPED_SKIP_BATCH["deadman-3d"]

    def readback(order: tuple[int, ...] | None) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(n, plan, skip_batch=skip, compact_gate=compact, order=order)
        )
        writes = [x for a in range(1, n) for x in (1, a, a * 13 + 7)]
        bounds = [1]
        for m in taped_plan(n, plan):
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, n)
            if lo >= hi:
                continue
            reads = [x for a in range(lo, hi) for x in (0, a)]
            want = [a * 13 + 7 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=400_000_000)
            assert res.fatal is None, (order, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    want = {a: a * 13 + 7 for a in range(1, n)}
    address_order = readback(None)
    assert address_order == want, "the plan itself mis-routes before any reorder"
    hot_first = readback(order)
    assert hot_first == want
    # ... and the two chains agree address for address, which is the property
    # the reorder has to preserve and the one a wrong literal breaks quietly.
    assert hot_first == address_order


def test_fresh_slots_read_zero_and_extremes_survive() -> None:
    engine = _standalone(taped_store_block(330, PLAN, skip_batch=2))
    addrs = [1, 128, 129, 256, 257, 296, 297, 329]  # both sides of every seam
    fresh = engine.run(
        [x for a in addrs for x in (0, a)], expected=[0] * len(addrs), max_ticks=10_000_000
    )
    assert fresh.fatal is None and fresh.output == [0] * len(addrs)
    # extremes stay within one bank per pair — cross-bank reads race when
    # streamed (the short last ring answers first); the machine serializes
    edges = engine.run(
        [1, 1, -1000000, 0, 1, 1, 128, 1000000, 0, 128],
        expected=[-1000000, 1000000],
        max_ticks=10_000_000,
    )
    assert edges.fatal is None and edges.output == [-1000000, 1000000]
    top = engine.run(
        [1, 297, -1000000, 0, 297, 1, 329, 1000000, 0, 329],
        expected=[-1000000, 1000000],
        max_ticks=10_000_000,
    )
    assert top.fatal is None and top.output == [-1000000, 1000000]


#: ``deadman-3d_hires``'s configuration, and it is nothing like ``PLAN``: the
#: hi-res family has no :data:`machine.TAPED_BANKS` entry, so it takes
#: :func:`taped_plan`'s **uniform quarters** of its 902-slot tape, and no
#: :data:`machine.TAPED_SKIP_BATCH` entry either, so its banks are batch-1
#: rings.  Both differ from the shipped ``deadman-3d`` chain the tests above
#: cover, and :data:`machine.TAPED_BANK_ORDER` now reorders it.
HIRES_N = 902
HIRES_PLAN = tuple(taped_plan(HIRES_N, 4))


def test_the_hires_bank_plan_is_the_uniform_quarters_the_registry_assumes() -> None:
    """The order in the registry is read off *these* bounds, so pin them."""
    assert HIRES_PLAN == (226, 226, 226, 223)
    assert sum(HIRES_PLAN) >= HIRES_N - 1
    # and the measured traffic order is one the hardware can express: a gate
    # peels a bank off an END of what it is handed, so most permutations do not
    # exist. (scratch/deadman3d-opt/hires_banks.py measures the traffic itself.)
    gate_chain(list(HIRES_PLAN), order=[3, 0, 1, 2])


@pytest.mark.slow
def test_the_hires_hot_first_chain_resolves_every_address_to_the_same_data() -> None:
    """The load-bearing test for ``TAPED_BANK_ORDER["deadman-3d_hires"]``.

    A wrong gate literal does not fail, it routes to the **wrong bank** — so
    this compares address by address rather than bank by bank, over hires' own
    plan and its own batch-1 rings, which no other test in this file exercises.
    """

    def readback(order: tuple[int, ...] | None) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(HIRES_N, HIRES_PLAN, skip_batch=1, compact_gate=True,
                              order=list(order) if order else None)
        )
        writes = [x for a in range(1, HIRES_N) for x in (1, a, (a * 37 + 11) % 9973)]
        bounds = [1]
        for m in HIRES_PLAN:
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, HIRES_N)
            reads = [x for a in range(lo, hi) for x in (0, a)]
            want = [(a * 37 + 11) % 9973 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=400_000_000)
            assert res.fatal is None, (order, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    address_order = readback(None)
    assert address_order == {a: (a * 37 + 11) % 9973 for a in range(1, HIRES_N)}
    assert readback((3, 0, 1, 2)) == address_order
